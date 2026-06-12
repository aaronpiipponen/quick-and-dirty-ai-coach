import argparse
import datetime
import json
import os
import re
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_FILE = PROJECT_ROOT / "src" / "db" / "user_data.db"

PROFILE_SECTIONS = {
    "overview": ["recent_health", "weekly_volume", "recent_workouts", "active_decisions", "due_reviews", "weighins", "derived_flags"],
    "injury": ["injury_decisions", "symptom_notes", "surface_exposure", "workouts_next_day", "long_walks", "derived_flags"],
    "load": ["load_summary", "weekly_volume", "recovery_trend", "recent_workouts", "derived_flags"],
    "event": ["event_decisions", "long_walks", "surface_specificity", "pack_fuel_notes", "recovery_trend", "derived_flags"],
    "nutrition": ["weight_trend", "daily_calories", "long_session_fueling", "nutrition_notes", "derived_flags"],
    "strength": ["strength_sessions", "strength_progression", "strength_gap", "strength_decisions", "derived_flags"],
    "workout": ["workout_detail", "workout_weather", "workout_surfaces", "workout_stream_summary"],
    "day": ["day_health", "day_workouts", "day_decisions", "day_recovery_context"],
}

FLAG_GROUPS = {"recovery", "load", "injury", "nutrition", "strength", "weather"}
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
SYMPTOM_RE = re.compile(r"pain|sensation|gait|knee|hip|heel|foot|feet|blister|ache|sore", re.I)
PACK_FUEL_RE = re.compile(r"pack|load|fuel|food|water|drink|gel|carb|thirst|snack", re.I)
NUTRITION_RE = re.compile(r"fuel|food|bonk|hunger|hungry|thirst|stomach|cramp|energy|drink|carb", re.I)


def resolve_db_file():
    db_file = os.getenv("DB_FILE")
    if not db_file:
        return DEFAULT_DB_FILE
    path = Path(db_file)
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_args():
    parser = argparse.ArgumentParser(description="Print profile-driven coaching context from the local SQLite database.")
    parser.add_argument("--db", type=Path, default=resolve_db_file(), help="SQLite database path.")
    parser.add_argument("--profile", choices=sorted(PROFILE_SECTIONS), default="overview", help="Context profile to run.")
    parser.add_argument("--section", action="append", choices=sorted(section_names()), help="Run only this section. Repeatable.")
    parser.add_argument("--append-sections", action="store_true", help="Append --section values to the selected profile instead of replacing it.")
    parser.add_argument("--date", type=datetime.date.fromisoformat, help="Date for day profile or anchor override.")
    parser.add_argument("--workout", type=int, help="Activity ID for workout profile.")
    parser.add_argument("--since", type=datetime.date.fromisoformat, help="Explicit start date for rolling sections.")
    parser.add_argument("--until", type=datetime.date.fromisoformat, help="Explicit end date for rolling sections.")
    parser.add_argument("--days", type=int, default=14, help="Recent window for rolling context.")
    parser.add_argument("--workouts", type=int, default=10, help="Maximum recent workouts to print.")
    parser.add_argument("--flags", default=None, help="Comma-separated flag groups or 'all'.")
    parser.add_argument("--severity", choices=sorted(SEVERITY_ORDER), default="low", help="Minimum flag severity to print.")
    parser.add_argument("--brief", action="store_true", help="Print fewer rows while preserving high-severity flags and active decisions.")
    parser.add_argument("--format", choices=["table", "markdown"], default="table", help="Output format.")
    parser.add_argument("--raw-streams", action="store_true", help="Print raw workout stream JSON in workout profile.")
    return parser.parse_args()


def section_names():
    return {
        "active_decisions", "daily_calories", "day_decisions", "day_health", "day_recovery_context",
        "day_workouts", "derived_flags", "due_reviews", "event_decisions", "injury_decisions", "load_summary",
        "long_session_fueling", "long_walks", "nutrition_notes", "pack_fuel_notes", "recent_health",
        "recent_workouts", "recovery_trend", "strength_decisions", "strength_gap", "strength_progression",
        "strength_sessions", "surface_exposure", "surface_specificity", "symptom_notes", "weekly_volume",
        "weighins", "weight_trend", "workout_detail", "workout_stream_summary", "workout_surfaces",
        "workout_weather", "workouts_next_day",
    }


def validate_args(args):
    if args.days <= 0:
        raise SystemExit("--days must be greater than 0")
    if args.workouts <= 0:
        raise SystemExit("--workouts must be greater than 0")
    if args.since and args.until and args.since > args.until:
        raise SystemExit("--since must be earlier than or equal to --until")
    if args.profile == "workout" and not args.workout:
        raise SystemExit("--profile workout requires --workout ACTIVITY_ID")
    if args.profile == "day" and not args.date:
        raise SystemExit("--profile day requires --date YYYY-MM-DD")


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_anchor_date(conn, args):
    if args.until:
        return args.until.isoformat()
    if args.date and args.profile != "day":
        return args.date.isoformat()
    anchor = scalar(
        conn,
        """
        SELECT MAX(date)
        FROM (
            SELECT MAX(date) AS date FROM daily_summary
            UNION ALL
            SELECT MAX(date) AS date FROM workouts
        )
        """,
    )
    if not anchor:
        raise SystemExit("No daily summary or workout rows found.")
    return anchor


def window_start(anchor, args):
    if args.since:
        return args.since.isoformat()
    anchor_date = datetime.date.fromisoformat(anchor)
    return (anchor_date - datetime.timedelta(days=args.days - 1)).isoformat()


def clip(value, width=72):
    if value is None:
        return ""
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "..."


def fmt(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def print_rows(title, data, columns):
    print(f"\n## {title}")
    if not data:
        print("No rows.")
        return
    widths = {col: len(col) for col, _ in columns}
    rendered = []
    for row in data:
        rendered_row = []
        for col, width in columns:
            value = clip(fmt(row.get(col)), width)
            widths[col] = min(max(widths[col], len(value)), width)
            rendered_row.append((col, value))
        rendered.append(rendered_row)
    print(" | ".join(col.ljust(widths[col]) for col, _ in columns))
    print(" | ".join("-" * widths[col] for col, _ in columns))
    for row in rendered:
        print(" | ".join(value.ljust(widths[col]) for col, value in row))


def print_lines(title, lines):
    print(f"\n## {title}")
    if not lines:
        print("No rows.")
        return
    for line in lines:
        print(line)


def limit_for(args, default, brief=None):
    if not args.brief:
        return default
    return brief if brief is not None else max(3, default // 2)


def section_recent_health(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT date, weight_kg, resting_hr,
               ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
               sleep_score, hrv_last_night_avg AS hrv, hrv_status,
               training_readiness AS readiness, training_load AS load
        FROM daily_summary
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit_for(args, 7, 5)),
    )
    print_rows("Recent Health", data, [("date", 10), ("weight_kg", 9), ("resting_hr", 10), ("sleep_hrs", 9), ("sleep_score", 11), ("hrv", 6), ("hrv_status", 12), ("readiness", 9), ("load", 6)])


def section_weekly_volume(conn, args, anchor):
    weeks = 4 if args.brief else 6
    data = rows(
        conn,
        """
        SELECT strftime('%Y-W%W', date) AS week,
               COUNT(*) AS sessions,
               ROUND(SUM(distance_km), 1) AS km,
               ROUND(SUM(moving_duration_mins) / 60.0, 1) AS moving_hrs,
               SUM(zone1_mins) AS z1,
               SUM(zone2_mins) AS z2,
               SUM(zone3_mins + zone4_mins + zone5_mins) AS z3plus
        FROM workouts
        WHERE date >= date(?, ?)
        GROUP BY week
        ORDER BY week DESC
        LIMIT ?
        """,
        (anchor, f"-{weeks * 7 - 1} days", weeks),
    )
    print_rows("Workout Weeks", data, [("week", 9), ("sessions", 8), ("km", 7), ("moving_hrs", 10), ("z1", 6), ("z2", 6), ("z3plus", 7)])


def section_recent_workouts(conn, args, anchor):
    data = recent_workout_rows(conn, args, anchor, limit_for(args, args.workouts, 5))
    print_rows("Recent Workouts", data, workout_columns())


def recent_workout_rows(conn, args, anchor, limit):
    return rows(
        conn,
        """
        SELECT w.activity_id AS id, w.date, w.sport, w.distance_km AS km,
               moving_duration_mins AS moving_min, w.avg_moving_pace AS pace, w.avg_hr,
               w.zone2_mins AS z2, w.zone3_mins + w.zone4_mins + w.zone5_mins AS z3plus,
               r.hard_surface_km AS hard_km, r.soft_surface_km AS soft_km,
               r.distance_unknown_km AS unknown_km, w.notes
        FROM workouts w
        LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ?
        ORDER BY w.date DESC, w.activity_id DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit),
    )


def workout_columns():
    return [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("moving_min", 10), ("pace", 7), ("avg_hr", 6), ("z2", 5), ("z3plus", 7), ("hard_km", 7), ("soft_km", 7), ("unknown_km", 10), ("notes", 72)]


def section_active_decisions(conn, args, anchor, topics=None, title="Active Coach Decisions"):
    params = []
    where = ["status = 'active'"]
    if topics:
        where.append(f"topic IN ({', '.join('?' for _ in topics)})")
        params.extend(topics)
    data = rows(
        conn,
        f"""
        SELECT decision_id AS id, date, topic, next_review_date AS review, decision
        FROM coach_decisions
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(next_review_date, date), decision_id
        LIMIT ?
        """,
        (*params, limit_for(args, 8, 5)),
    )
    print_rows(title, data, [("id", 4), ("date", 10), ("topic", 10), ("review", 10), ("decision", 86)])


def due_review_rows(conn, anchor):
    return rows(
        conn,
        """
        SELECT decision_id AS id, date, topic, next_review_date AS review, decision
        FROM coach_decisions
        WHERE status = 'active'
          AND next_review_date IS NOT NULL
          AND next_review_date <= ?
        ORDER BY next_review_date, decision_id
        """,
        (anchor,),
    )


def section_due_reviews(conn, args, anchor):
    data = due_review_rows(conn, anchor)
    if not data:
        print_lines("Due Decision Reviews", ["No due reviews."])
        return
    ids = ", ".join(str(row["id"]) for row in data)
    lines = [f"{len(data)} due review(s): ids {ids}"]
    for row in data[:limit_for(args, 5, 3)]:
        lines.append(f"id {row['id']} | review {row['review']} | topic {row['topic']} | {clip(row['decision'], 72)}")
    print_lines("Due Decision Reviews", lines)


def section_weighins(conn, args, anchor):
    data = rows(conn, "SELECT date, weight_kg FROM daily_summary WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT ?", (limit_for(args, 5, 3),))
    print_rows("Latest Weigh-Ins", data, [("date", 10), ("weight_kg", 9)])


def section_weight_trend(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT date, weight_kg,
               ROUND(weight_kg - LAG(weight_kg) OVER (ORDER BY date), 1) AS change_kg
        FROM daily_summary
        WHERE weight_kg IS NOT NULL
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit_for(args, 8, 5),),
    )
    print_rows("Weight Trend", data, [("date", 10), ("weight_kg", 9), ("change_kg", 9)])


def section_load_summary(conn, args, anchor):
    data = []
    for days in (7, 14, 28):
        row = rows(
            conn,
            """
            SELECT ? AS window_days, COUNT(*) AS sessions, ROUND(SUM(distance_km), 1) AS km,
                   ROUND(SUM(moving_duration_mins) / 60.0, 1) AS moving_hrs,
                   SUM(zone1_mins) AS z1, SUM(zone2_mins) AS z2,
                   SUM(zone3_mins + zone4_mins + zone5_mins) AS z3plus
            FROM workouts
            WHERE date BETWEEN date(?, ?) AND ?
            """,
            (days, anchor, f"-{days - 1} days", anchor),
        )[0]
        data.append(row)
    print_rows("Rolling Load Summary", data, [("window_days", 11), ("sessions", 8), ("km", 7), ("moving_hrs", 10), ("z1", 6), ("z2", 6), ("z3plus", 7)])


def section_recovery_trend(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT date, resting_hr, hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
               training_readiness AS readiness, sleep_score,
               ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs, training_load AS load
        FROM daily_summary
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit_for(args, 14, 7)),
    )
    print_rows("Recovery Trend", data, [("date", 10), ("resting_hr", 10), ("hrv", 6), ("hrv_7d", 7), ("readiness", 9), ("sleep_score", 11), ("sleep_hrs", 9), ("load", 6)])


def section_surface_exposure(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT w.date, ROUND(SUM(r.hard_surface_km), 1) AS hard_km,
               ROUND(SUM(r.soft_surface_km), 1) AS soft_km,
               ROUND(SUM(r.distance_unknown_km), 1) AS unknown_km,
               ROUND(SUM(w.distance_km), 1) AS total_km
        FROM workouts w
        JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ?
        GROUP BY w.date
        ORDER BY w.date DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit_for(args, 12, 7)),
    )
    print_rows("Surface Exposure By Day", data, [("date", 10), ("hard_km", 8), ("soft_km", 8), ("unknown_km", 10), ("total_km", 8)])


def section_surface_specificity(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT ? AS since, ? AS until,
               ROUND(SUM(r.hard_surface_km), 1) AS hard_km,
               ROUND(SUM(r.soft_surface_km), 1) AS soft_km,
               ROUND(SUM(r.distance_unknown_km), 1) AS unknown_km,
               ROUND(SUM(r.surface_total_km), 1) AS surface_total_km,
               ROUND(100.0 * SUM(r.hard_surface_km) / NULLIF(SUM(r.surface_total_km), 0), 1) AS hard_pct,
               ROUND(100.0 * SUM(r.soft_surface_km) / NULLIF(SUM(r.surface_total_km), 0), 1) AS soft_pct
        FROM workouts w
        JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ?
        """,
        (window_start(anchor, args), anchor, window_start(anchor, args), anchor),
    )
    print_rows("Surface Specificity", data, [("since", 10), ("until", 10), ("hard_km", 8), ("soft_km", 8), ("unknown_km", 10), ("surface_total_km", 16), ("hard_pct", 8), ("soft_pct", 8)])


def section_symptom_notes(conn, args, anchor):
    data = note_rows(conn, args, anchor, SYMPTOM_RE, limit_for(args, 10, 5))
    print_rows("Symptom Notes", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("notes", 100)])


def note_rows(conn, args, anchor, pattern, limit):
    candidates = rows(
        conn,
        """
        SELECT activity_id AS id, date, sport, distance_km AS km, notes
        FROM workouts
        WHERE date BETWEEN ? AND ? AND notes IS NOT NULL AND TRIM(notes) != ''
        ORDER BY date DESC, activity_id DESC
        LIMIT 80
        """,
        (window_start(anchor, args), anchor),
    )
    return [row for row in candidates if pattern.search(row.get("notes") or "")][:limit]


def section_pack_fuel_notes(conn, args, anchor):
    data = note_rows(conn, args, anchor, PACK_FUEL_RE, limit_for(args, 10, 5))
    print_rows("Pack/Fuel Notes", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("notes", 100)])


def section_nutrition_notes(conn, args, anchor):
    data = note_rows(conn, args, anchor, NUTRITION_RE, limit_for(args, 10, 5))
    print_rows("Nutrition Notes", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("notes", 100)])


def section_workouts_next_day(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT w.activity_id AS id, w.date, w.sport, w.distance_km AS km,
               r.hard_surface_km AS hard_km, r.soft_surface_km AS soft_km,
               w.notes, d.resting_hr AS next_rhr, d.hrv_last_night_avg AS next_hrv,
               d.training_readiness AS next_readiness, d.sleep_score AS next_sleep
        FROM workouts w
        LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
        LEFT JOIN daily_summary d ON d.date = date(w.date, '+1 day')
        WHERE w.date BETWEEN ? AND ?
        ORDER BY w.date DESC, w.activity_id DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit_for(args, args.workouts, 6)),
    )
    print_rows("Workouts With Next-Day Health", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("hard_km", 8), ("soft_km", 8), ("next_rhr", 8), ("next_hrv", 8), ("next_readiness", 14), ("next_sleep", 10), ("notes", 72)])


def section_long_walks(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT activity_id AS id, date, sport, distance_km AS km, moving_duration_mins AS moving_min,
               avg_moving_pace AS pace, avg_hr, notes
        FROM workouts
        WHERE date BETWEEN date(?, '-89 days') AND ?
          AND (distance_km >= 10 OR moving_duration_mins >= 120)
        ORDER BY distance_km DESC, moving_duration_mins DESC
        LIMIT ?
        """,
        (anchor, anchor, limit_for(args, 10, 5)),
    )
    print_rows("Long Sessions", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("moving_min", 10), ("pace", 7), ("avg_hr", 6), ("notes", 72)])


def section_daily_calories(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT date, weight_kg, calories_active, total_steps, intensity_minutes
        FROM daily_summary
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit_for(args, 14, 7)),
    )
    print_rows("Daily Calories And Activity", data, [("date", 10), ("weight_kg", 9), ("calories_active", 15), ("total_steps", 11), ("intensity_minutes", 17)])


def section_long_session_fueling(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT activity_id AS id, date, sport, distance_km AS km, moving_duration_mins AS moving_min,
               calories, avg_hr, notes
        FROM workouts
        WHERE date BETWEEN ? AND ? AND (distance_km >= 15 OR moving_duration_mins >= 150)
        ORDER BY date DESC, activity_id DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, limit_for(args, 8, 5)),
    )
    print_rows("Long-Session Fueling Context", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("moving_min", 10), ("calories", 8), ("avg_hr", 6), ("notes", 72)])


def section_strength_sessions(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT w.activity_id AS id, w.date, w.name, COUNT(s.set_order) AS sets,
               ROUND(SUM(COALESCE(s.duration_secs, 0)) / 60.0, 1) AS set_mins
        FROM workouts w
        LEFT JOIN strength_sets s ON s.activity_id = w.activity_id
        WHERE w.sport IN ('strength_training', 'fitness_equipment')
        GROUP BY w.activity_id
        ORDER BY w.date DESC, w.activity_id DESC
        LIMIT ?
        """,
        (limit_for(args, 8, 4),),
    )
    print_rows("Strength Sessions", data, [("id", 11), ("date", 10), ("name", 28), ("sets", 5), ("set_mins", 8)])


def section_strength_progression(conn, args, anchor):
    data = rows(
        conn,
        """
        WITH ranked AS (
            SELECT w.date, s.exercise_name, s.reps, s.weight_kg, s.duration_secs,
                   ROW_NUMBER() OVER (PARTITION BY s.exercise_name ORDER BY w.date DESC, w.activity_id DESC, s.set_order DESC) AS rn
            FROM strength_sets s
            JOIN workouts w ON w.activity_id = s.activity_id
            WHERE s.exercise_name IS NOT NULL
        )
        SELECT date, exercise_name, reps, weight_kg, duration_secs
        FROM ranked
        WHERE rn = 1
        ORDER BY exercise_name
        LIMIT ?
        """,
        (limit_for(args, 20, 10),),
    )
    print_rows("Latest Strength By Exercise", data, [("date", 10), ("exercise_name", 36), ("reps", 5), ("weight_kg", 9), ("duration_secs", 13)])


def section_strength_gap(conn, args, anchor):
    latest = scalar(conn, "SELECT MAX(date) FROM workouts WHERE sport IN ('strength_training', 'fitness_equipment')")
    if not latest:
        print_lines("Strength Gap", ["No strength sessions found."])
        return
    gap = (datetime.date.fromisoformat(anchor) - datetime.date.fromisoformat(latest)).days
    print_rows("Strength Gap", [{"latest_strength": latest, "days_since": gap}], [("latest_strength", 15), ("days_since", 10)])


def section_workout_detail(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT activity_id AS id, date, sport, name, total_duration_mins, moving_duration_mins,
               elapsed_duration_mins, distance_km AS km, avg_pace, avg_moving_pace, avg_hr, max_hr,
               avg_cadence, elevation_gain, elevation_loss, steps, calories,
               zone1_mins AS z1, zone2_mins AS z2, zone3_mins + zone4_mins + zone5_mins AS z3plus,
               downsampling_rate_secs, notes
        FROM workouts
        WHERE activity_id = ?
        """,
        (args.workout,),
    )
    print_rows("Workout Detail", data, [("id", 11), ("date", 10), ("sport", 12), ("name", 28), ("total_duration_mins", 19), ("moving_duration_mins", 20), ("elapsed_duration_mins", 21), ("km", 6), ("avg_pace", 9), ("avg_moving_pace", 15), ("avg_hr", 6), ("max_hr", 6), ("avg_cadence", 12), ("elevation_gain", 14), ("elevation_loss", 14), ("steps", 7), ("calories", 8), ("z1", 5), ("z2", 5), ("z3plus", 7), ("downsampling_rate_secs", 22), ("notes", 160)])


def section_workout_weather(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT weather_source, avg_temp_c, min_temp_c, max_temp_c, avg_humidity_pct,
               precipitation_mm, rain_mm, snowfall_mm, avg_wind_kmh, max_wind_gust_kmh,
               weather_codes_json
        FROM workout_weather
        WHERE activity_id = ?
        """,
        (args.workout,),
    )
    print_rows("Workout Weather", data, [("weather_source", 20), ("avg_temp_c", 10), ("min_temp_c", 10), ("max_temp_c", 10), ("avg_humidity_pct", 16), ("precipitation_mm", 16), ("rain_mm", 8), ("snowfall_mm", 11), ("avg_wind_kmh", 12), ("max_wind_gust_kmh", 19), ("weather_codes_json", 18)])


def section_workout_surfaces(conn, args, anchor):
    route = rows(
        conn,
        """
        SELECT point_count, surface_source, surface_total_km, hard_surface_km, soft_surface_km,
               distance_asphalt_km, distance_concrete_km, distance_paved_other_km,
               distance_gravel_km, distance_trail_km, distance_unknown_km
        FROM workout_routes
        WHERE activity_id = ?
        """,
        (args.workout,),
    )
    print_rows("Workout Surface Summary", route, [("point_count", 11), ("surface_source", 16), ("surface_total_km", 16), ("hard_surface_km", 15), ("soft_surface_km", 15), ("distance_asphalt_km", 19), ("distance_concrete_km", 20), ("distance_paved_other_km", 23), ("distance_gravel_km", 18), ("distance_trail_km", 17), ("distance_unknown_km", 19)])
    segs = rows(
        conn,
        """
        SELECT surface, surface_confidence, COUNT(*) AS segments, ROUND(SUM(distance_km), 2) AS km,
               ROUND(AVG(match_distance_m), 1) AS avg_match_m
        FROM workout_surface_segments
        WHERE activity_id = ?
        GROUP BY surface, surface_confidence
        ORDER BY km DESC
        """,
        (args.workout,),
    )
    print_rows("Workout Surface Segments", segs, [("surface", 12), ("surface_confidence", 18), ("segments", 8), ("km", 7), ("avg_match_m", 11)])


def section_workout_stream_summary(conn, args, anchor):
    row = conn.execute("SELECT hr_stream, pace_stream, elevation_stream, cadence_stream FROM workouts WHERE activity_id = ?", (args.workout,)).fetchone()
    if not row:
        print_rows("Workout Stream Summary", [], [("stream", 10), ("points", 6), ("min", 8), ("avg", 8), ("max", 8)])
        return
    out = []
    for name in ("hr_stream", "pace_stream", "elevation_stream", "cadence_stream"):
        raw = row[name]
        vals = load_json_list(raw)
        nums = [pace_to_seconds(v) if name == "pace_stream" else v for v in vals]
        nums = [float(v) for v in nums if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace('.', '', 1).isdigit())]
        if nums:
            out.append({"stream": name.replace("_stream", ""), "points": len(vals), "min": round(min(nums), 1), "avg": round(sum(nums) / len(nums), 1), "max": round(max(nums), 1)})
        else:
            out.append({"stream": name.replace("_stream", ""), "points": len(vals), "min": None, "avg": None, "max": None})
    print_rows("Workout Stream Summary", out, [("stream", 10), ("points", 6), ("min", 8), ("avg", 8), ("max", 8)])
    if args.raw_streams:
        print_lines("Raw Workout Streams", [f"{key}: {row[key] or ''}" for key in row.keys()])


def load_json_list(raw):
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def pace_to_seconds(value):
    if not isinstance(value, str) or ":" not in value:
        return value
    mins, secs = value.split(":", 1)
    try:
        return int(mins) * 60 + int(secs)
    except ValueError:
        return value


def section_day_health(conn, args, anchor):
    day = args.date.isoformat()
    data = rows(
        conn,
        """
        SELECT date, weight_kg, resting_hr, max_hr, total_steps, calories_active,
               intensity_minutes, ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
               sleep_score, hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
               hrv_status, training_readiness AS readiness, training_load AS load
        FROM daily_summary
        WHERE date = ?
        """,
        (day,),
    )
    print_rows("Day Health", data, [("date", 10), ("weight_kg", 9), ("resting_hr", 10), ("max_hr", 7), ("total_steps", 11), ("calories_active", 15), ("intensity_minutes", 17), ("sleep_hrs", 9), ("sleep_score", 11), ("hrv", 6), ("hrv_7d", 7), ("hrv_status", 12), ("readiness", 9), ("load", 6)])


def section_day_workouts(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT w.activity_id AS id, w.date, w.sport, w.distance_km AS km,
               moving_duration_mins AS moving_min, w.avg_moving_pace AS pace, w.avg_hr,
               r.hard_surface_km AS hard_km, r.soft_surface_km AS soft_km, w.notes
        FROM workouts w
        LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date = ?
        ORDER BY w.activity_id DESC
        """,
        (args.date.isoformat(),),
    )
    print_rows("Day Workouts", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("moving_min", 10), ("pace", 7), ("avg_hr", 6), ("hard_km", 8), ("soft_km", 8), ("notes", 100)])


def section_day_decisions(conn, args, anchor):
    day = args.date.isoformat()
    data = rows(
        conn,
        """
        SELECT decision_id AS id, date, topic, status, next_review_date AS review, decision
        FROM coach_decisions
        WHERE date = ? OR linked_date = ? OR next_review_date = ?
        ORDER BY date, decision_id
        """,
        (day, day, day),
    )
    print_rows("Day Decisions", data, [("id", 4), ("date", 10), ("topic", 10), ("status", 10), ("review", 10), ("decision", 100)])


def section_day_recovery_context(conn, args, anchor):
    day = args.date.isoformat()
    data = rows(
        conn,
        """
        SELECT date, resting_hr, hrv_last_night_avg AS hrv, training_readiness AS readiness,
               sleep_score, ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs, training_load AS load
        FROM daily_summary
        WHERE date BETWEEN date(?, '-1 day') AND date(?, '+1 day')
        ORDER BY date
        """,
        (day, day),
    )
    print_rows("Previous/Next Day Recovery", data, [("date", 10), ("resting_hr", 10), ("hrv", 6), ("readiness", 9), ("sleep_score", 11), ("sleep_hrs", 9), ("load", 6)])


def section_derived_flags(conn, args, anchor):
    flags = collect_flags(conn, args, anchor)
    min_sev = SEVERITY_ORDER[args.severity]
    flags = [f for f in flags if SEVERITY_ORDER[f["severity"]] >= min_sev]
    flags.sort(key=lambda f: (-SEVERITY_ORDER[f["severity"]], f["date"], f["group"]))
    limit = 8 if args.brief else 16
    print_rows("Derived Flags", flags[:limit], [("date", 10), ("group", 10), ("severity", 8), ("flag", 110)])


def selected_flag_groups(args, profile):
    if args.flags:
        if args.flags.strip().lower() == "all":
            return FLAG_GROUPS
        return {item.strip() for item in args.flags.split(",") if item.strip()} & FLAG_GROUPS
    defaults = {
        "overview": {"recovery", "load", "injury"},
        "injury": {"injury", "load", "recovery", "weather"},
        "load": {"load", "recovery"},
        "event": {"injury", "load", "recovery", "nutrition", "weather"},
        "nutrition": {"nutrition"},
        "strength": {"strength", "injury"},
    }
    return defaults.get(profile, {"recovery", "load", "injury"})


def collect_flags(conn, args, anchor):
    groups = selected_flag_groups(args, args.profile)
    flags = []
    if "recovery" in groups:
        flags.extend(recovery_flags(conn, args, anchor))
    if "load" in groups:
        flags.extend(load_flags(conn, args, anchor))
    if "injury" in groups:
        flags.extend(injury_flags(conn, args, anchor))
    if "nutrition" in groups:
        flags.extend(nutrition_flags(conn, args, anchor))
    if "strength" in groups:
        flags.extend(strength_flags(conn, args, anchor))
    if "weather" in groups:
        flags.extend(weather_flags(conn, args, anchor))
    return flags


def flag(date, group, severity, text):
    return {"date": date or "", "group": group, "severity": severity, "flag": text}


def recovery_flags(conn, args, anchor):
    data = rows(conn, "SELECT date, resting_hr, sleep_score, training_readiness, hrv_last_night_avg, hrv_weekly_avg FROM daily_summary WHERE date BETWEEN ? AND ? ORDER BY date DESC", (window_start(anchor, args), anchor))
    recent_rhrs = [r["resting_hr"] for r in data[3:] if r.get("resting_hr") is not None]
    baseline = sum(recent_rhrs) / len(recent_rhrs) if recent_rhrs else None
    out = []
    for row in data[:7]:
        date = row["date"]
        if baseline and row.get("resting_hr") and row["resting_hr"] >= baseline + 5:
            out.append(flag(date, "recovery", "high", f"Resting HR {row['resting_hr']} is >=5 bpm above recent baseline {baseline:.1f}."))
        if row.get("sleep_score") is not None and row["sleep_score"] < 60:
            out.append(flag(date, "recovery", "high", f"Sleep score is low at {row['sleep_score']}."))
        if row.get("training_readiness") is not None and row["training_readiness"] < 50:
            out.append(flag(date, "recovery", "medium", f"Training readiness is low at {row['training_readiness']}."))
        if row.get("hrv_last_night_avg") and row.get("hrv_weekly_avg") and row["hrv_last_night_avg"] < row["hrv_weekly_avg"] * 0.85:
            out.append(flag(date, "recovery", "medium", f"HRV {row['hrv_last_night_avg']} is materially below weekly average {row['hrv_weekly_avg']}."))
    if len(data) >= 3:
        readiness = [r.get("training_readiness") for r in data[:3]]
        if all(v is not None for v in readiness) and readiness[0] < readiness[1] < readiness[2]:
            out.append(flag(data[0]["date"], "recovery", "medium", f"Training readiness has fallen 3 days in a row: {readiness[::-1]} -> {readiness[0]}."))
    return out


def load_flags(conn, args, anchor):
    current = scalar(conn, "SELECT COALESCE(SUM(distance_km), 0) FROM workouts WHERE date BETWEEN date(?, '-6 days') AND ?", (anchor, anchor)) or 0
    previous = scalar(conn, "SELECT COALESCE(SUM(distance_km), 0) FROM workouts WHERE date BETWEEN date(?, '-13 days') AND date(?, '-7 days')", (anchor, anchor)) or 0
    out = []
    if previous > 0 and current > previous * 1.25:
        out.append(flag(anchor, "load", "medium", f"7-day distance {current:.1f} km is >25% above previous 7 days {previous:.1f} km."))
    z = rows(conn, "SELECT COALESCE(SUM(zone2_mins), 0) AS z2, COALESCE(SUM(zone3_mins + zone4_mins + zone5_mins), 0) AS z3plus FROM workouts WHERE date BETWEEN date(?, '-6 days') AND ?", (anchor, anchor))[0]
    if (z["z3plus"] or 0) >= 30:
        out.append(flag(anchor, "load", "medium", f"Recent high-intensity leakage is {z['z3plus']} min Zone 3+."))
    workouts = rows(conn, "SELECT date, distance_km, moving_duration_mins FROM workouts WHERE date BETWEEN ? AND ? ORDER BY date", (window_start(anchor, args), anchor))
    by_date = {}
    for row in workouts:
        by_date.setdefault(row["date"], 0)
        by_date[row["date"]] += row.get("distance_km") or 0
    dates = sorted(by_date)
    for prev, cur in zip(dates, dates[1:]):
        if (datetime.date.fromisoformat(cur) - datetime.date.fromisoformat(prev)).days == 1 and by_date[prev] >= 10 and by_date[cur] >= 10:
            out.append(flag(cur, "load", "medium", f"Back-to-back long-session days: {prev} {by_date[prev]:.1f} km and {cur} {by_date[cur]:.1f} km."))
    return out


def injury_flags(conn, args, anchor):
    out = []
    for row in note_rows(conn, args, anchor, SYMPTOM_RE, 8):
        if is_reassuring_symptom_note(row.get("notes") or ""):
            continue
        sev = "high" if re.search(r"gait|pain|knee|heel", row.get("notes") or "", re.I) else "medium"
        out.append(flag(row["date"], "injury", sev, f"Symptom note on {row['sport']} {row.get('km') or ''} km: {clip(row.get('notes'), 86)}"))
    data = rows(conn, "SELECT w.date, SUM(r.hard_surface_km) AS hard_km FROM workouts w JOIN workout_routes r ON r.activity_id = w.activity_id WHERE w.date BETWEEN ? AND ? GROUP BY w.date ORDER BY w.date DESC", (window_start(anchor, args), anchor))
    hard_vals = [r["hard_km"] for r in data if r.get("hard_km") is not None]
    avg = sum(hard_vals) / len(hard_vals) if hard_vals else 0
    for row in data[:5]:
        if avg > 0 and row.get("hard_km") and row["hard_km"] > max(avg * 1.8, 6):
            out.append(flag(row["date"], "injury", "medium", f"Hard-surface exposure {row['hard_km']:.1f} km is a spike vs recent average {avg:.1f} km."))
    return out


def is_reassuring_symptom_note(text):
    text_lower = text.lower()
    positive_terms = ["no pain", "no weird sensations", "gait was normal", "pain-free", "symptom-free"]
    negative_terms = ["started feeling", "mild pain", "worse", "limp", "changed gait", "affecting gait"]
    return any(term in text_lower for term in positive_terms) and not any(term in text_lower for term in negative_terms)


def nutrition_flags(conn, args, anchor):
    out = []
    long_sessions = rows(conn, "SELECT date, distance_km, moving_duration_mins, notes FROM workouts WHERE date BETWEEN ? AND ? AND (distance_km >= 15 OR moving_duration_mins >= 150) ORDER BY date DESC", (window_start(anchor, args), anchor))
    for row in long_sessions[:5]:
        if not NUTRITION_RE.search(row.get("notes") or ""):
            out.append(flag(row["date"], "nutrition", "low", f"Long session {row.get('distance_km') or 0:.1f} km has no fueling note recorded."))
    weights = rows(conn, "SELECT date, weight_kg FROM daily_summary WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 5")
    if len(weights) >= 2 and weights[0]["weight_kg"] <= weights[-1]["weight_kg"] - 1.5:
        out.append(flag(weights[0]["date"], "nutrition", "medium", f"Weight dropped {weights[-1]['weight_kg'] - weights[0]['weight_kg']:.1f} kg across latest weigh-ins."))
    return out


def strength_flags(conn, args, anchor):
    latest = scalar(conn, "SELECT MAX(date) FROM workouts WHERE sport IN ('strength_training', 'fitness_equipment')")
    if not latest:
        return [flag(anchor, "strength", "medium", "No strength sessions found in database.")]
    gap = (datetime.date.fromisoformat(anchor) - datetime.date.fromisoformat(latest)).days
    if gap >= 14:
        return [flag(anchor, "strength", "medium", f"Last strength session was {gap} days ago on {latest}.")]
    return []


def weather_flags(conn, args, anchor):
    data = rows(
        conn,
        """
        SELECT w.date, w.activity_id, ww.avg_temp_c, ww.avg_humidity_pct, ww.precipitation_mm, ww.max_wind_gust_kmh
        FROM workouts w
        JOIN workout_weather ww ON ww.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ?
        ORDER BY w.date DESC
        LIMIT 20
        """,
        (window_start(anchor, args), anchor),
    )
    out = []
    for row in data:
        if row.get("avg_temp_c") is not None and row.get("avg_humidity_pct") is not None and row["avg_temp_c"] >= 20 and row["avg_humidity_pct"] >= 75:
            out.append(flag(row["date"], "weather", "medium", f"Heat/humidity stress on workout {row['activity_id']}: {row['avg_temp_c']} C and {row['avg_humidity_pct']}% humidity."))
        if row.get("precipitation_mm") and row["precipitation_mm"] >= 5:
            out.append(flag(row["date"], "weather", "medium", f"Wet-session stress on workout {row['activity_id']}: {row['precipitation_mm']} mm precipitation."))
        if row.get("max_wind_gust_kmh") and row["max_wind_gust_kmh"] >= 35:
            out.append(flag(row["date"], "weather", "low", f"Wind stress on workout {row['activity_id']}: gusts {row['max_wind_gust_kmh']} km/h."))
    return out


def run_section(name, conn, args, anchor):
    if name == "active_decisions":
        return section_active_decisions(conn, args, anchor)
    if name == "injury_decisions":
        return section_active_decisions(conn, args, anchor, ["injury", "load", "event"], "Active Injury/Load Decisions")
    if name == "event_decisions":
        return section_active_decisions(conn, args, anchor, ["event", "load", "injury"], "Active Event Decisions")
    if name == "strength_decisions":
        return section_active_decisions(conn, args, anchor, ["strength", "injury"], "Strength-Relevant Decisions")
    func = SECTION_FUNCS.get(name)
    if not func:
        raise SystemExit(f"Unknown section: {name}")
    return func(conn, args, anchor)


SECTION_FUNCS = {
    "daily_calories": section_daily_calories,
    "day_decisions": section_day_decisions,
    "day_health": section_day_health,
    "day_recovery_context": section_day_recovery_context,
    "day_workouts": section_day_workouts,
    "derived_flags": section_derived_flags,
    "due_reviews": section_due_reviews,
    "load_summary": section_load_summary,
    "long_session_fueling": section_long_session_fueling,
    "long_walks": section_long_walks,
    "nutrition_notes": section_nutrition_notes,
    "pack_fuel_notes": section_pack_fuel_notes,
    "recent_health": section_recent_health,
    "recent_workouts": section_recent_workouts,
    "recovery_trend": section_recovery_trend,
    "strength_gap": section_strength_gap,
    "strength_progression": section_strength_progression,
    "strength_sessions": section_strength_sessions,
    "surface_exposure": section_surface_exposure,
    "surface_specificity": section_surface_specificity,
    "symptom_notes": section_symptom_notes,
    "weekly_volume": section_weekly_volume,
    "weighins": section_weighins,
    "weight_trend": section_weight_trend,
    "workout_detail": section_workout_detail,
    "workout_stream_summary": section_workout_stream_summary,
    "workout_surfaces": section_workout_surfaces,
    "workout_weather": section_workout_weather,
    "workouts_next_day": section_workouts_next_day,
}


def selected_sections(args):
    profile_sections = list(PROFILE_SECTIONS[args.profile])
    if args.section and args.append_sections:
        return profile_sections + [s for s in args.section if s not in profile_sections]
    if args.section:
        return args.section
    return profile_sections


def main():
    args = parse_args()
    validate_args(args)
    db_file = args.db.expanduser().resolve()
    if not db_file.exists():
        raise SystemExit(f"Database not found: {db_file}")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        anchor = args.date.isoformat() if args.profile == "day" else get_anchor_date(conn, args)
        print("# Coach Context")
        print(f"Database: {db_file}")
        print(f"Profile: {args.profile}")
        print(f"Anchor date: {anchor}")
        print(f"Window: {window_start(anchor, args)} to {anchor}")
        print("Use this as coaching context; query deeper only when the answer requires raw detail.")
        for section in selected_sections(args):
            run_section(section, conn, args, anchor)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
