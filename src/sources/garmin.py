from collections import defaultdict
import datetime
import json
import os
import shutil
import time
import urllib.parse
import urllib.request

from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectConnectionError


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARDCODED_BACKFILL_DATE = datetime.date.fromisoformat(os.getenv("BACKFILL_DATE", "2026-01-01"))
GARMIN_TOKENSTORE = os.getenv("GARMIN_TOKENSTORE", os.path.join(PROJECT_ROOT, ".garminconnect"))
if not os.path.isabs(GARMIN_TOKENSTORE):
    GARMIN_TOKENSTORE = os.path.join(PROJECT_ROOT, GARMIN_TOKENSTORE)
GARMIN_RETRY_ATTEMPTS = int(os.getenv("GARMIN_RETRY_ATTEMPTS", "5"))
GARMIN_RETRY_MIN_WAIT = float(os.getenv("GARMIN_RETRY_MIN_WAIT", "2"))
GARMIN_RETRY_MAX_WAIT = float(os.getenv("GARMIN_RETRY_MAX_WAIT", "30"))

WEATHER_HOURLY_FIELDS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]

CARDIO_SPORTS = {
    "running", "walking", "cycling", "road_biking", "indoor_cycling",
    "treadmill_running", "trail_running", "open_water_swimming", "pool_swimming",
}
STRENGTH_SPORTS = {"strength_training", "fitness_equipment"}


def add_arguments(parser):
    pass


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


def summarize_route(points, sample_limit=250):
    if not points:
        return None
    lats = [p[1] for p in points]
    lons = [p[2] for p in points]
    step = max(1, len(points) // sample_limit)
    sampled = [
        {"t": iso_utc(ts), "lat": round(lat, 6), "lon": round(lon, 6)}
        for ts, lat, lon in points[::step]
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


def fetch_json(url, params):
    full_url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    with urllib.request.urlopen(full_url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_open_meteo_weather(lat, lon, start_iso, end_iso):
    start_dt = datetime.datetime.fromisoformat(start_iso)
    end_dt = datetime.datetime.fromisoformat(end_iso)
    params = {
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "start_date": start_dt.date().isoformat(),
        "end_date": end_dt.date().isoformat(),
        "hourly": WEATHER_HOURLY_FIELDS,
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
    }
    endpoints = [
        ("open-meteo-archive", "https://archive-api.open-meteo.com/v1/archive"),
        ("open-meteo-forecast", "https://api.open-meteo.com/v1/forecast"),
    ]
    last_error = None
    for source, url in endpoints:
        try:
            data = fetch_json(url, params)
            if data.get("hourly"):
                return source, data
        except Exception as e:
            last_error = e
    raise RuntimeError(f"No Open-Meteo weather data returned: {last_error}")


def summarize_weather(source, data, start_iso, end_iso):
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    start_dt = datetime.datetime.fromisoformat(start_iso).replace(tzinfo=None)
    end_dt = datetime.datetime.fromisoformat(end_iso).replace(tzinfo=None)
    selected = []
    for i, value in enumerate(times):
        hour_dt = datetime.datetime.fromisoformat(value)
        if start_dt.replace(minute=0, second=0) <= hour_dt <= end_dt:
            selected.append(i)
    if not selected:
        selected = list(range(len(times)))

    def values(field):
        raw = hourly.get(field, [])
        return [raw[i] for i in selected if i < len(raw) and raw[i] is not None]

    def avg(field):
        vals = values(field)
        return round(sum(vals) / len(vals), 1) if vals else None

    def total(field):
        vals = values(field)
        return round(sum(vals), 1) if vals else None

    temps = values("temperature_2m")
    gusts = values("wind_gusts_10m")
    codes = sorted(set(values("weather_code")))
    raw_selected = {"time": [times[i] for i in selected]}
    for field in WEATHER_HOURLY_FIELDS:
        raw = hourly.get(field, [])
        raw_selected[field] = [raw[i] for i in selected if i < len(raw)]
    return {
        "weather_source": source,
        "avg_temp_c": avg("temperature_2m"),
        "min_temp_c": round(min(temps), 1) if temps else None,
        "max_temp_c": round(max(temps), 1) if temps else None,
        "avg_humidity_pct": avg("relative_humidity_2m"),
        "precipitation_mm": total("precipitation"),
        "rain_mm": total("rain"),
        "snowfall_mm": total("snowfall"),
        "avg_wind_kmh": avg("wind_speed_10m"),
        "max_wind_gust_kmh": round(max(gusts), 1) if gusts else None,
        "weather_codes_json": json.dumps(codes),
        "raw_hourly_json": json.dumps(raw_selected),
    }


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
    if args.workout:
        print(f"Single-workout mode: syncing {args.workout}")
        print(f"Workout stream downsampling: {args.downsample} sec")
        act_payload = fetch_workout(api, args.workout, args.downsample)
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
        merge_payload(payload, build_workout_payload(api, act, args.downsample))
    return payload


def empty_payload():
    return {
        "daily_summary": [],
        "workouts": [],
        "strength_sets": [],
        "workout_routes": [],
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


def fetch_workout(api, workout_id, downsample):
    print(f"Fetching workout {workout_id}...")
    return build_workout_payload(api, api.get_activity(workout_id), downsample)


def build_workout_payload(api, act, downsample):
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
        add_route_and_weather(payload, activity_id, details)

    print(f"  -> Prepared {act_date_str} {sport} (id: {activity_id})")
    return payload


def add_route_and_weather(payload, activity_id, details):
    route = summarize_route(extract_route_points(details))
    if not route:
        payload["delete_route_weather_activity_ids"].append(activity_id)
        return
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
