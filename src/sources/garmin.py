from collections import defaultdict
import datetime
import json
import os
import shutil
import time

from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError

from sources.common import add_database_arguments, validate_date_range
from sources.workout_surfaces import analyze_route_surfaces, surface_sync_enabled
from sources.workout_weather import fetch_open_meteo_weather, summarize_weather


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARDCODED_BACKFILL_DATE = datetime.date.fromisoformat(os.getenv("BACKFILL_DATE", "2026-01-01"))
GARMIN_TOKENSTORE = os.getenv("GARMIN_TOKENSTORE", os.path.join(PROJECT_ROOT, ".garminconnect"))
if not os.path.isabs(GARMIN_TOKENSTORE):
    GARMIN_TOKENSTORE = os.path.join(PROJECT_ROOT, GARMIN_TOKENSTORE)
GARMIN_RETRY_ATTEMPTS = int(os.getenv("GARMIN_RETRY_ATTEMPTS", "5"))
GARMIN_RETRY_MIN_WAIT = float(os.getenv("GARMIN_RETRY_MIN_WAIT", "2"))
GARMIN_RETRY_MAX_WAIT = float(os.getenv("GARMIN_RETRY_MAX_WAIT", "30"))
DOWNSAMPLE_INTERVAL_SECS = float(os.getenv("DOWNSAMPLE_INTERVAL_SECS", "300"))

HELP = "Sync Garmin health and workout data."
DESCRIPTION = "Sync daily health summaries, workouts, routes, weather, and strength data from Garmin."
EPILOG = """examples:
  python src/sync.py garmin
  python src/sync.py garmin --date 2026-06-10
  python src/sync.py garmin --workout 123456789 --downsample 60
  python src/sync.py garmin --routes-only --since 2026-06-01
  python src/sync.py garmin --since 2026-06-01 --until 2026-06-07
"""

CARDIO_SPORTS = {
    "running", "walking", "cycling", "road_biking", "indoor_cycling",
    "treadmill_running", "trail_running", "open_water_swimming", "pool_swimming",
}
STRENGTH_SPORTS = {"strength_training", "fitness_equipment"}


def add_arguments(parser):
    add_database_arguments(parser)
    parser.add_argument(
        "--since",
        type=datetime.date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Start date filter for Garmin daily summaries and workouts.",
    )
    parser.add_argument(
        "--until",
        type=datetime.date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="End date filter for Garmin daily summaries and workouts.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "-d",
        "--date",
        type=datetime.date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Sync only this specific Garmin date.",
    )
    target.add_argument(
        "-w",
        "--workout",
        type=int,
        metavar="ACTIVITY_ID",
        help="Sync only this Garmin workout id.",
    )
    parser.add_argument(
        "--downsample",
        type=float,
        default=DOWNSAMPLE_INTERVAL_SECS,
        metavar="SECONDS",
        help="Workout stream downsampling interval in seconds.",
    )
    parser.add_argument(
        "--skip-surfaces",
        action="store_true",
        help="Skip OSM-based workout surface matching for this sync.",
    )
    parser.add_argument(
        "--routes-only",
        action="store_true",
        help="Refresh route and surface data for existing workouts in the target database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch and rewrite workouts even when equivalent enriched rows already exist.",
    )


def validate_args(parser, args):
    validate_date_range(parser, args)
    if args.date and (args.since or args.until):
        parser.error("--date cannot be combined with --since or --until")
    if args.workout and (args.since or args.until):
        parser.error("--workout cannot be combined with --since or --until")
    if args.downsample <= 0:
        parser.error("--downsample must be greater than 0")
    if args.routes_only and args.skip_surfaces:
        parser.error("--routes-only cannot be combined with --skip-surfaces")


def login_with_cache(api, tokenstore):
    os.makedirs(tokenstore, exist_ok=True)
    try:
        api.login(tokenstore=tokenstore)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        print(f"Cached Garmin login failed ({e}); clearing token cache and re-authenticating.")
        shutil.rmtree(tokenstore, ignore_errors=True)
        os.makedirs(tokenstore, exist_ok=True)
        api.login(tokenstore=tokenstore)


def init_api():
    api = Garmin(
        os.environ["GARMIN_USERNAME"],
        os.environ["GARMIN_PASSWORD"],
        retry_attempts=GARMIN_RETRY_ATTEMPTS,
        retry_min_wait=GARMIN_RETRY_MIN_WAIT,
        retry_max_wait=GARMIN_RETRY_MAX_WAIT,
    )
    login_with_cache(api, GARMIN_TOKENSTORE)
    return api


def get_dates_to_sync(conn, start_date, end_date):
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM daily_summary")
    row = c.fetchone()
    latest = row[0] if row and row[0] else None
    sync_from = datetime.date.fromisoformat(latest) - datetime.timedelta(days=1) if latest else start_date
    return date_range(sync_from, end_date)


def bucket_by_hour(values):
    buckets = defaultdict(list)
    for ts, val in values:
        if val is None:
            continue
        buckets[(ts // 3_600_000) * 3_600_000].append(val)
    return [(hour_ts, int(sum(vals) / len(vals))) for hour_ts, vals in sorted(buckets.items())]


def bucket_by_hour_float(values):
    buckets = defaultdict(list)
    for ts, val in values:
        if val is None:
            continue
        buckets[(ts // 3_600_000) * 3_600_000].append(float(val))
    return [(hour_ts, round(sum(vals) / len(vals), 1)) for hour_ts, vals in sorted(buckets.items())]


def find_first_key(obj, candidate_keys):
    if isinstance(obj, dict):
        for key in candidate_keys:
            value = obj.get(key)
            if value not in (None, "", []):
                return value
        for value in obj.values():
            found = find_first_key(value, candidate_keys)
            if found not in (None, "", []):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_first_key(value, candidate_keys)
            if found not in (None, "", []):
                return found
    return None


def seconds_to_minutes(value):
    if value is None:
        return None
    return int(value // 60)


def extract_respiration_stream(respiration_data):
    if not respiration_data:
        return None
    source = respiration_data.get("respirationAveragesValuesArray") or respiration_data.get("respirationValuesArray") or []
    pairs = []
    for row in source:
        if not isinstance(row, list) or len(row) < 2:
            continue
        ts, respiration = row[0], row[1]
        if isinstance(ts, (int, float)) and isinstance(respiration, (int, float)) and respiration > 0:
            pairs.append([int(ts), float(respiration)])
    vals = [v for _, v in bucket_by_hour_float(pairs)]
    return json.dumps(vals) if vals else None


def first_primary_or_first(items):
    if isinstance(items, dict):
        values = list(items.values())
    elif isinstance(items, list):
        values = items
    else:
        return None
    return next((item for item in values if item.get("primaryTrainingDevice")), None) or (values[0] if values else None)


def extract_training_status_metrics(training_status_data):
    if not training_status_data:
        return None, None, None, None
    most_recent_status = training_status_data.get("mostRecentTrainingStatus") or {}
    status_map = most_recent_status.get("latestTrainingStatusData") or {}
    status_entry = first_primary_or_first(status_map) or {}
    load_dto = status_entry.get("acuteTrainingLoadDTO") or {}
    most_recent_balance = training_status_data.get("mostRecentTrainingLoadBalance") or {}
    balance_map = most_recent_balance.get("metricsTrainingLoadBalanceDTOMap") or {}
    balance_entry = first_primary_or_first(balance_map) or {}
    return (
        status_entry.get("trainingStatus"),
        status_entry.get("trainingStatusFeedbackPhrase"),
        load_dto.get("dailyTrainingLoadAcute"),
        balance_entry.get("trainingBalanceFeedbackPhrase"),
    )


def iso_utc(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.UTC).replace(microsecond=0).isoformat()


def metric_descriptor_indices(details):
    return {
        desc.get("key"): desc.get("metricsIndex")
        for desc in details.get("metricDescriptors", [])
        if desc.get("key") and desc.get("metricsIndex") is not None
    }


def extract_route_points(details):
    indices = metric_descriptor_indices(details)
    lat_idx = indices.get("directLatitude")
    lon_idx = indices.get("directLongitude")
    ts_idx = indices.get("directTimestamp")
    if lat_idx is None or lon_idx is None or ts_idx is None:
        return []
    points = []
    for row in details.get("activityDetailMetrics", []):
        metrics = row.get("metrics", [])
        if len(metrics) <= max(lat_idx, lon_idx, ts_idx):
            continue
        lat = metrics[lat_idx]
        lon = metrics[lon_idx]
        ts = metrics[ts_idx]
        if lat is None or lon is None or ts is None:
            continue
        points.append((int(ts), float(lat), float(lon)))
    return points


def summarize_route(points, interval_secs):
    if not points:
        return None
    lats = [p[1] for p in points]
    lons = [p[2] for p in points]
    sampled = [
        {"t": iso_utc(ts), "lat": round(lat, 6), "lon": round(lon, 6)}
        for ts, lat, lon in downsample_route_points(points, interval_secs)
    ]
    return {
        "start_time_utc": iso_utc(points[0][0]),
        "end_time_utc": iso_utc(points[-1][0]),
        "point_count": len(points),
        "start_lat": points[0][1],
        "start_lon": points[0][2],
        "end_lat": points[-1][1],
        "end_lon": points[-1][2],
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "center_lat": sum(lats) / len(lats),
        "center_lon": sum(lons) / len(lons),
        "sampled_points_json": json.dumps(sampled),
    }


def downsample_route_points(points, interval_secs):
    if not points:
        return []
    interval_ms = int(interval_secs * 1000)
    sampled = [points[0]]
    next_ts = points[0][0] + interval_ms
    for point in points[1:-1]:
        if point[0] >= next_ts:
            sampled.append(point)
            next_ts = point[0] + interval_ms
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def downsample_metrics(details, total_duration_mins, interval_secs):
    metrics_array = details.get("activityDetailMetrics", [])
    descriptors = details.get("metricDescriptors", [])
    if not metrics_array or not descriptors or total_duration_mins <= 0:
        return {}
    indices = {"hr": None, "speed": None, "elevation": None, "cadence": None}
    for desc in descriptors:
        key = desc.get("key", "")
        idx = desc.get("metricsIndex")
        if key == "directHeartRate":
            indices["hr"] = idx
        elif key == "directElevation":
            indices["elevation"] = idx
        elif key == "directSpeed":
            indices["speed"] = idx
        elif key in ["directDoubleCadence", "directRunCadence", "directBikeCadence", "directWalkingCadence"]:
            indices["cadence"] = idx

    target_buckets = max(1, int((total_duration_mins * 60) / interval_secs))
    chunk_size = max(1, len(metrics_array) // target_buckets)
    downsampled = {}
    for i in range(0, len(metrics_array), chunk_size):
        bucket = [row["metrics"] for row in metrics_array[i:i + chunk_size] if "metrics" in row]
        if not bucket:
            continue
        if indices["hr"] is not None:
            valid = [r[indices["hr"]] for r in bucket if len(r) > indices["hr"] and r[indices["hr"]] is not None]
            if valid:
                downsampled.setdefault("hr", []).append(int(sum(valid) / len(valid)))
        if indices["cadence"] is not None:
            valid = [r[indices["cadence"]] for r in bucket if len(r) > indices["cadence"] and r[indices["cadence"]] is not None]
            if valid:
                downsampled.setdefault("cadence", []).append(int(sum(valid) / len(valid)))
        if indices["elevation"] is not None:
            valid = [r[indices["elevation"]] for r in bucket if len(r) > indices["elevation"] and r[indices["elevation"]] is not None]
            if valid:
                downsampled.setdefault("elevation", []).append(int(sum(valid) / len(valid)))
        if indices["speed"] is not None:
            valid = [
                r[indices["speed"]]
                for r in bucket
                if len(r) > indices["speed"] and r[indices["speed"]] is not None and r[indices["speed"]] > 0
            ]
            if valid:
                avg_speed_ms = sum(valid) / len(valid)
                pace_secs = 1000 / avg_speed_ms
                downsampled.setdefault("pace", []).append(f"{int(pace_secs // 60)}:{int(pace_secs % 60):02d}")
    return downsampled


def fetch(args, conn):
    api = init_api()
    payload = empty_payload()
    if args.routes_only:
        backfill_route_payload(api, conn, payload, args)
        return payload
    if args.workout:
        print(f"Single-workout mode: syncing {args.workout}")
        print(f"Workout stream downsampling: {args.downsample} sec")
        act_payload = fetch_workout(api, args.workout, args.downsample, args.skip_surfaces)
        merge_payload(payload, act_payload)
        return payload

    target_date = args.date
    if args.since or args.until:
        start = args.since or args.until
        end = args.until or args.since
        dates = date_range(start, end)
        workout_start = start
        workout_end = end
    elif target_date:
        print(f"Single-date mode: syncing {target_date}")
        dates = [target_date]
        workout_start = workout_end = target_date
    else:
        today = datetime.date.today()
        dates = get_dates_to_sync(conn, HARDCODED_BACKFILL_DATE, today)
        workout_start, workout_end = get_workout_range(conn, HARDCODED_BACKFILL_DATE, today)

    print(f"Workout stream downsampling: {args.downsample} sec")
    for date_obj in dates:
        row = fetch_daily_row(api, date_obj)
        if row:
            payload["daily_summary"].append(row)
            print(f"Synced {date_obj.isoformat()}")
            time.sleep(1)

    print(f"Fetching workouts from {workout_start} to {workout_end}...")
    activities = api.get_activities_by_date(workout_start.isoformat(), workout_end.isoformat())
    for act in activities:
        if conn and not args.force and workout_enrichment_complete(conn, act, args.downsample, args.skip_surfaces):
            activity_id = act.get("activityId")
            print(f"Skipping already enriched workout {activity_id}")
            continue
        merge_payload(payload, build_workout_payload(api, act, args.downsample, args.skip_surfaces))
    return payload


def empty_payload():
    return {
        "daily_summary": [],
        "workouts": [],
        "strength_sets": [],
        "workout_routes": [],
        "workout_surface_segments": [],
        "workout_weather": [],
        "coach_decisions": [],
        "delete_strength_activity_ids": [],
        "delete_route_weather_activity_ids": [],
    }


def merge_payload(target, source):
    for key, rows in source.items():
        target.setdefault(key, []).extend(rows)


def date_range(start, end):
    if start > end:
        raise ValueError("-since must be earlier than or equal to -until")
    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += datetime.timedelta(days=1)
    return dates


def get_workout_range(conn, backfill_date, today):
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM workouts")
    row = c.fetchone()
    if row and row[0]:
        return datetime.date.fromisoformat(row[0]) - datetime.timedelta(days=1), today
    return backfill_date, today


def workout_enrichment_complete(conn, act, downsample, skip_surfaces):
    activity_id = act.get("activityId")
    activity_type = act.get("activityType") or act.get("activityTypeDTO") or {}
    sport = activity_type.get("typeKey")
    if not activity_id or not sport:
        return False

    row = conn.execute(
        "SELECT downsampling_rate_secs FROM workouts WHERE activity_id = ?",
        (activity_id,),
    ).fetchone()
    if not row or row[0] is None or float(row[0]) != float(downsample):
        return False

    if sport in STRENGTH_SPORTS:
        return True
    if sport not in CARDIO_SPORTS:
        return True

    route = conn.execute(
        """
        SELECT r.activity_id, r.surface_source, COUNT(s.sample_order) AS surface_segments,
               ww.activity_id AS weather_activity_id
        FROM workout_routes r
        LEFT JOIN workout_surface_segments s ON s.activity_id = r.activity_id
        LEFT JOIN workout_weather ww ON ww.activity_id = r.activity_id
        WHERE r.activity_id = ?
        GROUP BY r.activity_id
        """,
        (activity_id,),
    ).fetchone()
    if not route:
        return False
    if route[3] is None:
        return False
    if not skip_surfaces and surface_sync_enabled() and (route[1] is None or route[2] == 0):
        return False
    return True


def backfill_route_payload(api, conn, payload, args):
    if conn is None:
        raise ValueError("--routes-only requires a writable target database")
    rows = route_backfill_workouts(conn, args)
    print(f"Routes-only mode: processing {len(rows)} existing workouts")
    print(f"Route stream downsampling: {args.downsample} sec")
    for row in rows:
        activity_id = row["activity_id"]
        print(f"Processing route data for {row['date']} {row['sport']} (id: {activity_id})...")
        try:
            details = api.get_activity_details(activity_id)
            add_route_surfaces(payload, activity_id, details, args.downsample)
        except Exception as e:
            print(f"  Route data unavailable for activity {activity_id}: {e}")


def route_backfill_workouts(conn, args):
    where = [
        f"w.sport IN ({', '.join('?' for _ in CARDIO_SPORTS)})",
        "r.activity_id IS NOT NULL",
    ]
    params = list(CARDIO_SPORTS)
    if args.workout:
        where.append("w.activity_id = ?")
        params.append(args.workout)
    if args.date:
        where.append("w.date = ?")
        params.append(args.date.isoformat())
    if args.since:
        where.append("w.date >= ?")
        params.append(args.since.isoformat())
    if args.until:
        where.append("w.date <= ?")
        params.append(args.until.isoformat())
    if not any([args.workout, args.date, args.since, args.until]):
        where.append("r.surface_source IS NULL")
    sql = f"""
        SELECT w.activity_id, w.date, w.sport
        FROM workouts w
        JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE {' AND '.join(where)}
        ORDER BY w.date, w.activity_id
    """
    conn.row_factory = None
    return [
        {"activity_id": activity_id, "date": date, "sport": sport}
        for activity_id, date, sport in conn.execute(sql, params).fetchall()
    ]


def fetch_daily_row(api, date_obj):
    date_str = date_obj.isoformat()
    try:
        stats = api.get_stats(date_str)
        comp = api.get_body_composition(date_str)
        sumry = api.get_user_summary(date_str)

        weight_raw = None
        if comp and comp.get("dateWeightList"):
            weight_raw = comp["dateWeightList"][0].get("weight")
        weight_kg = round(weight_raw / 1000, 1) if weight_raw and weight_raw > 0 else None

        vo2_max = None
        try:
            max_metrics = api.get_max_metrics(date_str)
            if isinstance(max_metrics, list) and max_metrics:
                vo2_max = max_metrics[0].get("generic", {}).get("vo2MaxPreciseValue")
            elif isinstance(max_metrics, dict):
                vo2_max = max_metrics.get("generic", {}).get("vo2MaxPreciseValue")
        except Exception:
            vo2_max = sumry.get("vo2Max") if sumry else None

        calories_active = (sumry or {}).get("activeKilocalories") or (stats or {}).get("activeKilocalories")
        intensity_minutes = None
        if stats:
            mod = stats.get("moderateIntensityMinutes") or 0
            vig = stats.get("vigorousIntensityMinutes") or 0
            if mod or vig:
                intensity_minutes = mod + (vig * 2)

        sleep_dur_mins = sleep_score = None
        sleep_deep_mins = sleep_light_mins = sleep_rem_mins = sleep_awake_mins = None
        try:
            sleep_data = api.get_sleep_data(date_str)
            if sleep_data:
                dto = sleep_data.get("dailySleepDTO", {})
                sleep_secs = dto.get("sleepTimeSeconds")
                if sleep_secs:
                    sleep_dur_mins = int(sleep_secs // 60)
                scores = dto.get("sleepScores", {})
                overall = scores.get("overall", {}) if isinstance(scores, dict) else {}
                sleep_score = overall.get("value") if isinstance(overall, dict) else int(overall) if isinstance(overall, (int, float)) else None
                sleep_deep_mins = seconds_to_minutes(dto.get("deepSleepSeconds"))
                sleep_light_mins = seconds_to_minutes(dto.get("lightSleepSeconds"))
                sleep_rem_mins = seconds_to_minutes(dto.get("remSleepSeconds"))
                sleep_awake_mins = seconds_to_minutes(dto.get("awakeSleepSeconds"))
        except Exception as e:
            print(f"  Sleep data unavailable for {date_str}: {e}")

        hrv_last_night_avg = hrv_weekly_avg = hrv_status = None
        try:
            hrv_data = api.get_hrv_data(date_str)
            if hrv_data:
                hrv_last_night_avg = find_first_key(hrv_data, ["lastNightAvg", "lastNightAverage", "lastNightAverageHRV", "lastNightAverageHrv", "hrvLastNightAverage"])
                hrv_weekly_avg = find_first_key(hrv_data, ["weeklyAvg", "weeklyAverage", "weeklyAverageHRV", "weeklyAverageHrv", "hrvWeeklyAverage"])
                hrv_status = find_first_key(hrv_data, ["status", "hrvStatus", "weeklyAvgStatus", "lastNightStatus"])
        except Exception as e:
            print(f"  HRV data unavailable for {date_str}: {e}")

        training_status = training_status_feedback = training_load = training_load_balance_feedback = None
        try:
            values = extract_training_status_metrics(api.get_training_status(date_str))
            training_status, training_status_feedback, training_load, training_load_balance_feedback = values
        except Exception as e:
            print(f"  Training status data unavailable for {date_str}: {e}")

        training_readiness = None
        try:
            training_readiness = find_first_key(api.get_morning_training_readiness(date_str), ["score", "trainingReadinessScore", "readinessScore"])
        except Exception as e:
            print(f"  Training readiness data unavailable for {date_str}: {e}")

        stress_stream = body_battery_stream = None
        try:
            res = api.get_stress_data(date_str)
            if res:
                if "stressValuesArray" in res:
                    clean = [[v[0], v[1]] for v in res["stressValuesArray"] if v[1] is not None and v[1] >= 0]
                    vals = [v for _, v in bucket_by_hour(clean)]
                    stress_stream = json.dumps(vals) if vals else None
                if "bodyBatteryValuesArray" in res:
                    bb = [[v[0], v[2]] for v in res["bodyBatteryValuesArray"]]
                    vals = [v for _, v in bucket_by_hour(bb)]
                    body_battery_stream = json.dumps(vals) if vals else None
        except Exception as e:
            print(f"  Stress/body battery data unavailable for {date_str}: {e}")

        respiration_stream = None
        try:
            respiration_stream = extract_respiration_stream(api.get_respiration_data(date_str))
        except Exception as e:
            print(f"  Respiration data unavailable for {date_str}: {e}")

        return {
            "date": date_str,
            "weight_kg": weight_kg,
            "vo2_max": vo2_max,
            "resting_hr": (stats or {}).get("restingHeartRate"),
            "max_hr": (stats or {}).get("maxHeartRate"),
            "total_steps": (stats or {}).get("totalSteps"),
            "calories_active": calories_active,
            "intensity_minutes": intensity_minutes,
            "sleep_duration_mins": sleep_dur_mins,
            "sleep_score": sleep_score,
            "sleep_deep_mins": sleep_deep_mins,
            "sleep_light_mins": sleep_light_mins,
            "sleep_rem_mins": sleep_rem_mins,
            "sleep_awake_mins": sleep_awake_mins,
            "stress_stream": stress_stream,
            "body_battery_stream": body_battery_stream,
            "respiration_stream": respiration_stream,
            "hrv_last_night_avg": hrv_last_night_avg,
            "hrv_weekly_avg": hrv_weekly_avg,
            "hrv_status": hrv_status,
            "training_status": training_status,
            "training_status_feedback": training_status_feedback,
            "training_load_balance_feedback": training_load_balance_feedback,
            "training_load": training_load,
            "training_readiness": training_readiness,
        }
    except Exception as e:
        print(f"Error on {date_str}: {e}")
        return None


def fetch_workout(api, workout_id, downsample, skip_surfaces=False):
    print(f"Fetching workout {workout_id}...")
    return build_workout_payload(api, api.get_activity(workout_id), downsample, skip_surfaces)


def build_workout_payload(api, act, downsample, skip_surfaces=False):
    payload = empty_payload()
    summary = act.get("summaryDTO") or {}

    def field(name, default=None):
        return act.get(name, summary.get(name, default))

    activity_id = act["activityId"]
    act_date_str = field("startTimeLocal")[:10]
    sport = (act.get("activityType") or act.get("activityTypeDTO"))["typeKey"]
    name = act.get("activityName") or sport.replace("_", " ").capitalize()
    notes = act.get("description")
    total_duration_mins = field("duration", 0) / 60
    moving_duration_mins = field("movingDuration", 0) / 60 if field("movingDuration") else None
    elapsed_duration_mins = field("elapsedDuration", 0) / 60 if field("elapsedDuration") else None

    print(f"Processing {sport} on {act_date_str}...")
    zones = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    try:
        for zone in api.get_activity_hr_in_timezones(activity_id) or []:
            z = zone.get("zoneNumber", 0)
            if 1 <= z <= 5:
                zones[z] = int(zone.get("secsInZone", 0) // 60)
    except Exception:
        pass

    total_cal = field("calories", 0)
    active_cal = field("activeCalories") or max(0, total_cal - (field("bmrCalories") or 0))
    distance_km = avg_pace = avg_cadence = elevation_gain = elevation_loss = steps = None
    avg_moving_pace = None
    if sport in CARDIO_SPORTS:
        dist = field("distance", 0)
        distance_km = round(dist / 1000, 2) if dist else None
        spd = field("averageSpeed", 0)
        if spd and spd > 0:
            ps = 1000 / spd
            avg_pace = f"{int(ps // 60)}:{int(ps % 60):02d}"
        avg_cadence = field("averageRunningCadenceInStepsPerMinute") or field("averageRunCadence") or field("averageBikingCadenceInRevPerMinute")
        elevation_gain = field("elevationGain")
        elevation_loss = field("elevationLoss")
        steps = field("steps")
        if moving_duration_mins and distance_km and moving_duration_mins > 0:
            secs_per_km = (moving_duration_mins * 60) / distance_km
            avg_moving_pace = f"{int(secs_per_km // 60)}:{int(secs_per_km % 60):02d}"

    hr_stream = pace_stream = elevation_stream = cadence_stream = None
    details = None
    try:
        details = api.get_activity_details(activity_id)
        graphs = downsample_metrics(details, total_duration_mins, downsample)
        hr_stream = json.dumps(graphs["hr"]) if graphs.get("hr") else None
        pace_stream = json.dumps(graphs["pace"]) if graphs.get("pace") else None
        elevation_stream = json.dumps(graphs["elevation"]) if graphs.get("elevation") else None
        cadence = graphs.get("cadence")
        if cadence and avg_cadence and (sum(cadence) / len(cadence)) < (avg_cadence * 0.75):
            cadence = [v * 2 for v in cadence]
        cadence_stream = json.dumps(cadence) if cadence else None
    except Exception as e:
        print(f"  Streams unavailable for activity {activity_id}: {e}")

    payload["workouts"].append({
        "activity_id": activity_id,
        "date": act_date_str,
        "sport": sport,
        "name": name,
        "notes": notes,
        "total_duration_mins": round(total_duration_mins, 1),
        "distance_km": distance_km,
        "avg_pace": avg_pace,
        "avg_hr": field("averageHR"),
        "max_hr": field("maxHR"),
        "avg_cadence": avg_cadence,
        "elevation_gain": elevation_gain,
        "elevation_loss": elevation_loss,
        "steps": steps,
        "calories": int(active_cal),
        "zone1_mins": zones[1],
        "zone2_mins": zones[2],
        "zone3_mins": zones[3],
        "zone4_mins": zones[4],
        "zone5_mins": zones[5],
        "hr_stream": hr_stream,
        "pace_stream": pace_stream,
        "elevation_stream": elevation_stream,
        "cadence_stream": cadence_stream,
        "moving_duration_mins": round(moving_duration_mins, 1) if moving_duration_mins else None,
        "elapsed_duration_mins": round(elapsed_duration_mins, 1) if elapsed_duration_mins else None,
        "avg_moving_pace": avg_moving_pace,
        "downsampling_rate_secs": downsample,
    })

    payload["delete_strength_activity_ids"].append(activity_id)
    if sport in STRENGTH_SPORTS:
        try:
            sets_data = api.get_activity_exercise_sets(activity_id)
            set_order = 1
            for s in (sets_data or {}).get("exerciseSets", []):
                if s.get("setType") != "ACTIVE" or not s.get("exercises"):
                    continue
                weight_raw = s.get("weight")
                payload["strength_sets"].append({
                    "activity_id": activity_id,
                    "set_order": set_order,
                    "exercise_name": s["exercises"][0].get("name"),
                    "category": s["exercises"][0].get("category"),
                    "reps": s.get("repetitionCount"),
                    "weight_kg": round(weight_raw / 1000, 2) if weight_raw else None,
                    "duration_secs": round(s.get("duration", 0)),
                })
                set_order += 1
        except Exception as e:
            print(f"  Exercise sets unavailable for activity {activity_id}: {e}")

    if details and sport in CARDIO_SPORTS:
        add_route_weather_and_surfaces(payload, activity_id, details, downsample, skip_surfaces=skip_surfaces)

    print(f"  -> Prepared {act_date_str} {sport} (id: {activity_id})")
    return payload


def add_route_weather_and_surfaces(payload, activity_id, details, downsample, skip_surfaces=False):
    points = extract_route_points(details)
    route = summarize_route(points, downsample)
    if not route:
        payload["delete_route_weather_activity_ids"].append(activity_id)
        return
    if not skip_surfaces:
        add_surface_rows(payload, activity_id, points, route, downsample)
    payload["workout_routes"].append({"activity_id": activity_id, **route})
    try:
        source, weather_data = fetch_open_meteo_weather(route["center_lat"], route["center_lon"], route["start_time_utc"], route["end_time_utc"])
        weather = summarize_weather(source, weather_data, route["start_time_utc"], route["end_time_utc"])
        payload["workout_weather"].append({
            "activity_id": activity_id,
            "latitude": route["center_lat"],
            "longitude": route["center_lon"],
            "start_time_utc": route["start_time_utc"],
            "end_time_utc": route["end_time_utc"],
            **weather,
        })
    except Exception as e:
        print(f"  Weather unavailable for activity {activity_id}: {e}")


def add_route_surfaces(payload, activity_id, details, downsample):
    points = extract_route_points(details)
    route = summarize_route(points, downsample)
    if not route:
        return
    add_surface_rows(payload, activity_id, points, route, downsample)
    payload["workout_routes"].append({"activity_id": activity_id, **route})


def add_surface_rows(payload, activity_id, points, route, downsample):
    try:
        surface_segments, surface_summary = analyze_route_surfaces(points, downsample)
        route.update(surface_summary)
        for segment in surface_segments:
            payload["workout_surface_segments"].append({"activity_id": activity_id, **segment})
    except Exception as e:
        print(f"  Surfaces unavailable for activity {activity_id}: {e}")
