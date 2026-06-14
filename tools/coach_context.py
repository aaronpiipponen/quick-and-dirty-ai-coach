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
    "injury": ["injury_decisions", "symptom_notes", "surface_exposure", "latest_route", "workouts_next_day", "long_walks", "derived_flags"],
    "load": ["load_summary", "weekly_volume", "recovery_trend", "recent_workouts", "derived_flags"],
    "event": ["event_decisions", "long_walks", "surface_specificity", "latest_route", "pack_fuel_notes", "recovery_trend", "derived_flags"],
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

SPORT_ALIASES = {
    "run": ["running", "trail_running", "treadmill_running"],
    "walk": ["walking"],
    "bike": ["cycling", "road_biking"],
    "strength": ["strength_training", "fitness_equipment"],
    "cardio": ["running", "trail_running", "treadmill_running", "walking", "cycling", "road_biking"],
}

DECISION_TOPICS = {"load", "injury", "event", "nutrition", "gear", "strength"}


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
    parser.add_argument("--brief", action="store_true", help="Equivalent to --detail summary with high-severity preservation.")
    parser.add_argument("--detail", choices=["summary", "standard", "full"], default=None, help="Output depth: summary, standard (default), full.")
    parser.add_argument("--sport", default=None, help="Comma-separated sport names or aliases (run,walk,bike,strength,cardio).")
    parser.add_argument("--topic", default=None, help="Comma-separated decision topics (load,injury,event,nutrition,gear,strength).")
    parser.add_argument("--exercise", default=None, help="Exercise name for targeted strength views.")
    parser.add_argument("--raw", choices=["none", "streams", "segments", "all"], default=None, help="Raw output mode: none (default), streams, segments, all.")
    parser.add_argument("--format", choices=["table", "markdown"], default="table", help="Output format.")
    parser.add_argument("--raw-streams", action="store_true", help="Backwards-compatible alias for --raw streams.")
    return parser.parse_args()


def section_names():
    return {
        "active_decisions", "daily_calories", "day_decisions", "day_health", "day_recovery_context",
        "day_workouts", "decision_context", "derived_flags", "due_reviews", "event_decisions",
        "injury_decisions", "latest_route", "load_summary", "long_session_fueling", "long_walks",
        "nutrition_notes", "pack_fuel_notes", "recent_decisions", "recent_health", "recent_workouts",
        "recovery_trend", "strength_decisions", "strength_gap", "strength_progression",
        "strength_sessions", "strength_sets", "surface_exposure", "surface_specificity",
        "symptom_notes", "weekly_volume", "weighins", "weight_trend", "workout_detail",
        "workout_stream_summary", "workout_surfaces", "workout_weather", "workouts_next_day",
    }


def detail_level(args):
    if args.detail:
        return args.detail
    if args.brief:
        return "summary"
    return "standard"


def row_limit(args, standard, summary=None, full=None):
    d = detail_level(args)
    if d == "summary":
        s = summary if summary is not None else max(3, standard // 2)
        if args.brief:
            return s
        return s
    if d == "full":
        return full if full is not None else max(standard, min(standard * 2, 30))
    return standard


def selected_sports(args):
    if not args.sport:
        return None
    result = []
    for token in args.sport.split(","):
        token = token.strip().lower()
        if token in SPORT_ALIASES:
            result.extend(SPORT_ALIASES[token])
        else:
            result.append(token)
    return list(dict.fromkeys(result))


def sport_where(sports, prefix="w"):
    if not sports:
        return "", []
    placeholders = ", ".join("?" for _ in sports)
    col = f"{prefix}.sport" if prefix else "sport"
    return f"AND {col} IN ({placeholders})", list(sports)


def selected_topics(args):
    if not args.topic:
        return None
    result = []
    for token in args.topic.split(","):
        token = token.strip().lower()
        if token not in DECISION_TOPICS:
            raise SystemExit(f"Unknown decision topic: {token}. Valid: {', '.join(sorted(DECISION_TOPICS))}")
        result.append(token)
    return result


def raw_enabled(args, kind):
    raw_val = args.raw
    if raw_val is None and args.raw_streams:
        raw_val = "streams"
    if raw_val is None:
        raw_val = "none"
    if kind == "streams" and raw_val in ("streams", "all"):
        return True
    if kind == "segments" and raw_val in ("segments", "all"):
        return True
    return False


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
    sections = selected_sections(args)
    has_sport_sections = bool(set(sections) & {
        "recent_workouts", "weekly_volume", "load_summary", "long_walks",
        "surface_exposure", "surface_specificity", "workouts_next_day", "latest_route",
    })
    if args.sport and not has_sport_sections:
        raise SystemExit("--sport is only valid with workout-derived sections: recent_workouts, weekly_volume, load_summary, long_walks, surface_exposure, surface_specificity, workouts_next_day")
    has_decision_sections = bool(set(sections) & {
        "active_decisions", "recent_decisions", "decision_context",
        "injury_decisions", "event_decisions", "strength_decisions",
    })
    if args.topic and not has_decision_sections:
        raise SystemExit("--topic is only valid with decision sections: active_decisions, recent_decisions, decision_context, injury_decisions, event_decisions, strength_decisions")
    has_strength_sections = bool(set(sections) & {"strength_progression", "strength_sets"})
    if args.exercise and not has_strength_sections:
        raise SystemExit("--exercise is only valid with strength sections: strength_progression, strength_sets")
    has_segment_sections = bool(set(sections) & {"workout_surfaces"})
    if raw_enabled(args, "segments") and not has_segment_sections and args.profile != "workout":
        raise SystemExit("--raw segments requires a workout context (workout profile with --workout, or --section workout_surfaces)")
    has_stream_sections = bool(set(sections) & {"workout_stream_summary", "day_health"})
    if raw_enabled(args, "streams") and not has_stream_sections and args.profile not in ("workout", "day"):
        raise SystemExit("--raw streams requires a workout or day context with stream-producing sections")


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


def section_recent_health(conn, args, anchor):
    d = detail_level(args)
    if d == "summary":
        data = rows(
            conn,
            """
            SELECT date, resting_hr, sleep_score, hrv_last_night_avg AS hrv, training_readiness AS readiness
            FROM daily_summary
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, row_limit(args, 7, 5)),
        )
        print_rows("Recent Health", data, [("date", 10), ("resting_hr", 10), ("sleep_score", 11), ("hrv", 6), ("readiness", 9)])
    elif d == "full":
        data = rows(
            conn,
            """
            SELECT date, weight_kg, resting_hr, max_hr, total_steps,
                   calories_active, intensity_minutes,
                   ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
                   sleep_score, hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
                   hrv_status, training_readiness AS readiness, training_load AS load,
                   training_status_feedback, training_load_balance_feedback
            FROM daily_summary
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, row_limit(args, 7, 5, 14)),
        )
        print_rows("Recent Health", data, [
            ("date", 10), ("weight_kg", 9), ("resting_hr", 10), ("max_hr", 7),
            ("total_steps", 11), ("calories_active", 15), ("intensity_minutes", 17),
            ("sleep_hrs", 9), ("sleep_score", 11), ("hrv", 6), ("hrv_7d", 7),
            ("hrv_status", 12), ("readiness", 9), ("load", 6),
            ("training_status_feedback", 26), ("training_load_balance_feedback", 30),
        ])
    else:
        data = rows(
            conn,
            """
            SELECT date, weight_kg, resting_hr,
                   ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
                   sleep_score, hrv_last_night_avg AS hrv, hrv_status,
                   training_readiness AS readiness, training_load AS load,
                   calories_active, intensity_minutes
            FROM daily_summary
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, row_limit(args, 7, 5)),
        )
        print_rows("Recent Health", data, [("date", 10), ("weight_kg", 9), ("resting_hr", 10), ("sleep_hrs", 9), ("sleep_score", 11), ("hrv", 6), ("hrv_status", 12), ("readiness", 9), ("load", 6), ("calories_active", 15), ("intensity_minutes", 17)])


def section_weekly_volume(conn, args, anchor):
    d = detail_level(args)
    weeks = 4 if d == "summary" else (8 if d == "full" else 6)
    sport_clause, sport_params = sport_where(selected_sports(args), prefix="")
    data = rows(
        conn,
        f"""
        SELECT strftime('%Y-W%W', date) AS week,
               COUNT(*) AS sessions,
               ROUND(SUM(distance_km), 1) AS km,
               ROUND(SUM(moving_duration_mins) / 60.0, 1) AS moving_hrs,
               SUM(zone1_mins) AS z1,
               SUM(zone2_mins) AS z2,
               SUM(zone3_mins + zone4_mins + zone5_mins) AS z3plus
               {" , SUM(zone3_mins) AS z3, SUM(zone4_mins) AS z4, SUM(zone5_mins) AS z5, SUM(zone4_mins + zone5_mins) AS high_int, ROUND(AVG(avg_hr)) AS avg_hr" if d == "full" else ""}
        FROM workouts
        WHERE date >= date(?, ?) {sport_clause}
        GROUP BY week
        ORDER BY week DESC
        LIMIT ?
        """,
        (anchor, f"-{weeks * 7 - 1} days", *sport_params, weeks),
    )
    if d == "full":
        print_rows("Workout Weeks", data, [("week", 9), ("sessions", 8), ("km", 7), ("moving_hrs", 10), ("z1", 6), ("z2", 6), ("z3plus", 7), ("z3", 6), ("z4", 6), ("z5", 6), ("high_int", 8), ("avg_hr", 6)])
    else:
        print_rows("Workout Weeks", data, [("week", 9), ("sessions", 8), ("km", 7), ("moving_hrs", 10), ("z1", 6), ("z2", 6), ("z3plus", 7)])


def section_recent_workouts(conn, args, anchor):
    d = detail_level(args)
    limit = row_limit(args, args.workouts, 5, 20)
    sport_clause, sport_params = sport_where(selected_sports(args))
    if d == "full":
        data = rows(
            conn,
            f"""
            SELECT w.activity_id AS id, w.date, w.sport, w.name,
                   w.distance_km AS km, moving_duration_mins AS moving_min,
                   w.total_duration_mins, w.elapsed_duration_mins,
                   w.avg_pace, w.avg_moving_pace AS pace, w.avg_hr,
                   w.calories,
                   w.zone2_mins AS z2, w.zone3_mins + w.zone4_mins + w.zone5_mins AS z3plus,
                   w.zone4_mins + w.zone5_mins AS high_intensity,
                   r.hard_surface_km AS hard_km, r.soft_surface_km AS soft_km,
                   r.distance_unknown_km AS unknown_km,
                   ROUND(ww.avg_temp_c, 1) AS temp_c, ROUND(ww.avg_humidity_pct, 0) AS humidity,
                   ROUND(ww.precipitation_mm, 1) AS precip_mm, ROUND(ww.max_wind_gust_kmh, 0) AS wind_gust,
                   w.notes
            FROM workouts w
            LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
            LEFT JOIN workout_weather ww ON ww.activity_id = w.activity_id
            WHERE w.date BETWEEN ? AND ? {sport_clause}
            ORDER BY w.date DESC, w.activity_id DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, *sport_params, limit),
        )
        print_rows("Recent Workouts", data, [
            ("id", 11), ("date", 10), ("sport", 12), ("name", 20),
            ("km", 6), ("moving_min", 10), ("total_duration_mins", 19), ("elapsed_duration_mins", 21),
            ("avg_pace", 9), ("pace", 7), ("avg_hr", 6), ("calories", 8),
            ("z2", 5), ("z3plus", 7), ("high_intensity", 14), ("hard_km", 7), ("soft_km", 7), ("unknown_km", 10),
            ("temp_c", 7), ("humidity", 8), ("precip_mm", 9), ("wind_gust", 9),
            ("notes", 72),
        ])
    elif d == "summary":
        data = rows(
            conn,
            f"""
            SELECT w.activity_id AS id, w.date, w.sport, w.distance_km AS km,
                   moving_duration_mins AS moving_min, w.avg_moving_pace AS pace, w.avg_hr, w.notes
            FROM workouts w
            WHERE w.date BETWEEN ? AND ? {sport_clause}
            ORDER BY w.date DESC, w.activity_id DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, *sport_params, limit),
        )
        print_rows("Recent Workouts", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("moving_min", 10), ("pace", 7), ("avg_hr", 6), ("notes", 50)])
    else:
        data = recent_workout_rows(conn, args, anchor, limit)
        print_rows("Recent Workouts", data, workout_columns())


def recent_workout_rows(conn, args, anchor, limit):
    sport_clause, sport_params = sport_where(selected_sports(args))
    return rows(
        conn,
        f"""
        SELECT w.activity_id AS id, w.date, w.sport, w.distance_km AS km,
               moving_duration_mins AS moving_min, w.avg_moving_pace AS pace, w.avg_hr,
               w.zone2_mins AS z2, w.zone3_mins + w.zone4_mins + w.zone5_mins AS z3plus,
               r.hard_surface_km AS hard_km, r.soft_surface_km AS soft_km,
               r.distance_unknown_km AS unknown_km, w.notes
        FROM workouts w
        LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ? {sport_clause}
        ORDER BY w.date DESC, w.activity_id DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, *sport_params, limit),
    )


def workout_columns():
    return [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("moving_min", 10), ("pace", 7), ("avg_hr", 6), ("z2", 5), ("z3plus", 7), ("hard_km", 7), ("soft_km", 7), ("unknown_km", 10), ("notes", 72)]


def section_active_decisions(conn, args, anchor, topics=None, title=None):
    d = detail_level(args)
    topics = topics or selected_topics(args)
    params = []
    where = ["status = 'active'"]
    if topics:
        where.append(f"topic IN ({', '.join('?' for _ in topics)})")
        params.extend(topics)
    display_title = title or "Active Coach Decisions"
    if d == "full":
        data = rows(
            conn,
            f"""
            SELECT decision_id AS id, date, topic, decision, reason,
                   linked_activity_id, linked_date, next_review_date AS review
            FROM coach_decisions
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(next_review_date, date), decision_id
            LIMIT ?
            """,
            (*params, row_limit(args, 8, 5, 16)),
        )
        print_rows(display_title, data, [("id", 4), ("date", 10), ("topic", 10), ("decision", 46), ("reason", 36), ("linked_activity_id", 18), ("linked_date", 12), ("review", 10)])
    else:
        data = rows(
            conn,
            f"""
            SELECT decision_id AS id, date, topic, next_review_date AS review, decision
            FROM coach_decisions
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(next_review_date, date), decision_id
            LIMIT ?
            """,
            (*params, row_limit(args, 8, 5)),
        )
        print_rows(display_title, data, [("id", 4), ("date", 10), ("topic", 10), ("review", 10), ("decision", 86)])


def section_recent_decisions(conn, args, anchor):
    d = detail_level(args)
    topics = selected_topics(args)
    params = []
    where_parts = []
    if topics:
        where_parts.append(f"topic IN ({', '.join('?' for _ in topics)})")
        params.extend(topics)
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    if d == "full":
        data = rows(
            conn,
            f"""
            SELECT decision_id AS id, date, topic, status, decision, reason,
                   linked_activity_id, linked_date, next_review_date AS review
            FROM coach_decisions
            {where_sql}
            {"AND" if where_sql else "WHERE"} date BETWEEN ? AND ?
            ORDER BY date DESC, decision_id DESC
            LIMIT ?
            """,
            (*params, window_start(anchor, args), anchor, row_limit(args, 15, 5, 30)),
        )
        print_rows("Recent Decisions", data, [("id", 4), ("date", 10), ("topic", 10), ("status", 10), ("decision", 40), ("reason", 30), ("linked_activity_id", 18), ("linked_date", 12), ("review", 10)])
    else:
        data = rows(
            conn,
            f"""
            SELECT decision_id AS id, date, topic, status, next_review_date AS review, decision
            FROM coach_decisions
            {where_sql}
            {"AND" if where_sql else "WHERE"} date BETWEEN ? AND ?
            ORDER BY date DESC, decision_id DESC
            LIMIT ?
            """,
            (*params, window_start(anchor, args), anchor, row_limit(args, 15, 5, 25)),
        )
        print_rows("Recent Decisions", data, [("id", 4), ("date", 10), ("topic", 10), ("status", 10), ("review", 10), ("decision", 70)])


def section_decision_context(conn, args, anchor):
    d = detail_level(args)
    topics = selected_topics(args)
    params = []
    where_parts = []
    if topics:
        where_parts.append(f"d.topic IN ({', '.join('?' for _ in topics)})")
        params.extend(topics)
    where_sql = f"{' AND '.join(where_parts)} AND" if where_parts else ""
    data = rows(
        conn,
        f"""
        SELECT d.decision_id AS id, d.date, d.topic, d.status, d.decision,
               d.reason, d.linked_activity_id, d.linked_date,
               w.date AS workout_date, w.sport, w.name, w.distance_km AS km, w.avg_hr
        FROM coach_decisions d
        LEFT JOIN workouts w ON w.activity_id = d.linked_activity_id
        WHERE {where_sql} d.date BETWEEN ? AND ?
        ORDER BY d.date DESC, d.decision_id DESC
        LIMIT ?
        """,
        (*params, window_start(anchor, args), anchor, row_limit(args, 15, 5, 25)),
    )
    print_rows("Decision Context", data, [("id", 4), ("date", 10), ("topic", 10), ("status", 10), ("decision", 34), ("reason", 24), ("linked_activity_id", 18), ("workout_date", 12), ("sport", 12), ("name", 18), ("km", 6), ("avg_hr", 6)])


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
    for row in data[:row_limit(args, 5, 3)]:
        lines.append(f"id {row['id']} | review {row['review']} | topic {row['topic']} | {clip(row['decision'], 72)}")
    print_lines("Due Decision Reviews", lines)


def section_weighins(conn, args, anchor):
    data = rows(conn, "SELECT date, weight_kg FROM daily_summary WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT ?", (row_limit(args, 5, 3),))
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
        (row_limit(args, 8, 5, args.days),),
    )
    print_rows("Weight Trend", data, [("date", 10), ("weight_kg", 9), ("change_kg", 9)])


def section_load_summary(conn, args, anchor):
    d = detail_level(args)
    sport_clause, sport_params = sport_where(selected_sports(args), prefix="")
    data = []
    for days in (7, 14, 28):
        row = rows(
            conn,
            f"""
            SELECT ? AS window_days, COUNT(*) AS sessions, ROUND(SUM(distance_km), 1) AS km,
                   ROUND(SUM(moving_duration_mins) / 60.0, 1) AS moving_hrs,
                   SUM(zone1_mins) AS z1, SUM(zone2_mins) AS z2,
                   SUM(zone3_mins + zone4_mins + zone5_mins) AS z3plus
            FROM workouts
            WHERE date BETWEEN date(?, ?) AND ? {sport_clause}
            """,
            (days, anchor, f"-{days - 1} days", anchor, *sport_params),
        )[0]
        data.append(row)
    print_rows("Rolling Load Summary", data, [("window_days", 11), ("sessions", 8), ("km", 7), ("moving_hrs", 10), ("z1", 6), ("z2", 6), ("z3plus", 7)])


def section_recovery_trend(conn, args, anchor):
    d = detail_level(args)
    if d == "full":
        data = rows(
            conn,
            """
            SELECT date, resting_hr, hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
                   hrv_status, training_readiness AS readiness,
                   sleep_score, ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
                   sleep_deep_mins, sleep_light_mins, sleep_rem_mins,
                   training_load AS load, training_status_feedback, training_load_balance_feedback,
                   stress_stream, body_battery_stream, respiration_stream
            FROM daily_summary
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, row_limit(args, 14, 7, 28)),
        )
        print_rows("Recovery And Sleep Trend", data, [
            ("date", 10), ("resting_hr", 10), ("hrv", 6), ("hrv_7d", 7),
            ("hrv_status", 12), ("readiness", 9), ("sleep_score", 11), ("sleep_hrs", 9),
            ("sleep_deep_mins", 14), ("sleep_light_mins", 15), ("sleep_rem_mins", 14),
            ("load", 6), ("training_status_feedback", 26), ("training_load_balance_feedback", 30),
            ("stress_stream", 14), ("body_battery_stream", 18), ("respiration_stream", 16),
        ])
    elif d == "summary":
        data = rows(
            conn,
            """
            SELECT date, resting_hr, hrv_last_night_avg AS hrv, training_readiness AS readiness
            FROM daily_summary
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, row_limit(args, 14, 7)),
        )
        print_rows("Recovery Trend", data, [("date", 10), ("resting_hr", 10), ("hrv", 6), ("readiness", 9)])
    else:
        data = rows(
            conn,
            """
            SELECT date, resting_hr, hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
                   training_readiness AS readiness, sleep_score,
                   ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs, training_load AS load,
                   stress_stream, body_battery_stream, respiration_stream
            FROM daily_summary
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (window_start(anchor, args), anchor, row_limit(args, 14, 7)),
        )
        print_rows("Recovery Trend", data, [("date", 10), ("resting_hr", 10), ("hrv", 6), ("hrv_7d", 7), ("readiness", 9), ("sleep_score", 11), ("sleep_hrs", 9), ("load", 6), ("stress_stream", 14), ("body_battery_stream", 18), ("respiration_stream", 16)])


def section_surface_exposure(conn, args, anchor):
    sport_clause, sport_params = sport_where(selected_sports(args))
    data = rows(
        conn,
        f"""
        SELECT w.date, ROUND(SUM(r.hard_surface_km), 1) AS hard_km,
               ROUND(SUM(r.soft_surface_km), 1) AS soft_km,
               ROUND(SUM(r.distance_unknown_km), 1) AS unknown_km,
               ROUND(SUM(w.distance_km), 1) AS total_km
        FROM workouts w
        JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ? {sport_clause}
        GROUP BY w.date
        ORDER BY w.date DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, *sport_params, row_limit(args, 12, 7, 20)),
    )
    print_rows("Surface Exposure By Day", data, [("date", 10), ("hard_km", 8), ("soft_km", 8), ("unknown_km", 10), ("total_km", 8)])


def section_surface_specificity(conn, args, anchor):
    sport_clause, sport_params = sport_where(selected_sports(args))
    data = rows(
        conn,
        f"""
        SELECT ? AS since, ? AS until,
               ROUND(SUM(r.hard_surface_km), 1) AS hard_km,
               ROUND(SUM(r.soft_surface_km), 1) AS soft_km,
               ROUND(SUM(r.distance_unknown_km), 1) AS unknown_km,
               ROUND(SUM(r.surface_total_km), 1) AS surface_total_km,
               ROUND(100.0 * SUM(r.hard_surface_km) / NULLIF(SUM(r.surface_total_km), 0), 1) AS hard_pct,
               ROUND(100.0 * SUM(r.soft_surface_km) / NULLIF(SUM(r.surface_total_km), 0), 1) AS soft_pct
        FROM workouts w
        JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ? {sport_clause}
        """,
        (window_start(anchor, args), anchor, window_start(anchor, args), anchor, *sport_params),
    )
    print_rows("Surface Specificity", data, [("since", 10), ("until", 10), ("hard_km", 8), ("soft_km", 8), ("unknown_km", 10), ("surface_total_km", 16), ("hard_pct", 8), ("soft_pct", 8)])


def section_symptom_notes(conn, args, anchor):
    data = note_rows(conn, args, anchor, SYMPTOM_RE, row_limit(args, 10, 5))
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
    data = note_rows(conn, args, anchor, PACK_FUEL_RE, row_limit(args, 10, 5))
    print_rows("Pack/Fuel Notes", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("notes", 100)])


def section_nutrition_notes(conn, args, anchor):
    data = note_rows(conn, args, anchor, NUTRITION_RE, row_limit(args, 10, 5))
    print_rows("Nutrition Notes", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("notes", 100)])


def section_workouts_next_day(conn, args, anchor):
    sport_clause, sport_params = sport_where(selected_sports(args))
    data = rows(
        conn,
        f"""
        SELECT w.activity_id AS id, w.date, w.sport, w.distance_km AS km,
               r.hard_surface_km AS hard_km, r.soft_surface_km AS soft_km,
               w.notes, d.resting_hr AS next_rhr, d.hrv_last_night_avg AS next_hrv,
               d.training_readiness AS next_readiness, d.sleep_score AS next_sleep
        FROM workouts w
        LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
        LEFT JOIN daily_summary d ON d.date = date(w.date, '+1 day')
        WHERE w.date BETWEEN ? AND ? {sport_clause}
        ORDER BY w.date DESC, w.activity_id DESC
        LIMIT ?
        """,
        (window_start(anchor, args), anchor, *sport_params, row_limit(args, args.workouts, 6)),
    )
    print_rows("Workouts With Next-Day Health", data, [("id", 11), ("date", 10), ("sport", 12), ("km", 6), ("hard_km", 8), ("soft_km", 8), ("next_rhr", 8), ("next_hrv", 8), ("next_readiness", 14), ("next_sleep", 10), ("notes", 72)])


def section_long_walks(conn, args, anchor):
    sport_clause, sport_params = sport_where(selected_sports(args), prefix="")
    data = rows(
        conn,
        f"""
        SELECT activity_id AS id, date, sport, distance_km AS km, moving_duration_mins AS moving_min,
               avg_moving_pace AS pace, avg_hr, notes
        FROM workouts
        WHERE date BETWEEN date(?, '-89 days') AND ?
          AND (distance_km >= 10 OR moving_duration_mins >= 120) {sport_clause}
        ORDER BY distance_km DESC, moving_duration_mins DESC
        LIMIT ?
        """,
        (anchor, anchor, *sport_params, row_limit(args, 10, 5, 20)),
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
        (window_start(anchor, args), anchor, row_limit(args, 14, 7)),
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
        (window_start(anchor, args), anchor, row_limit(args, 8, 5)),
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
        (row_limit(args, 8, 4, 15),),
    )
    print_rows("Strength Sessions", data, [("id", 11), ("date", 10), ("name", 28), ("sets", 5), ("set_mins", 8)])


def section_strength_progression(conn, args, anchor):
    d = detail_level(args)
    exercise = args.exercise
    if exercise:
        data = rows(
            conn,
            """
            SELECT w.date, w.activity_id AS id, s.exercise_name, s.set_order,
                   s.category, s.reps, s.weight_kg, s.duration_secs,
                   w.notes, w.total_duration_mins, w.avg_hr
            FROM strength_sets s
            JOIN workouts w ON w.activity_id = s.activity_id
            WHERE s.exercise_name = ?
            ORDER BY w.date, w.activity_id, s.set_order
            LIMIT ?
            """,
            (exercise, row_limit(args, 30, 10, 60)),
        )
        if d == "full":
            print_rows(f"Strength Progression: {exercise}", data, [("date", 10), ("id", 11), ("exercise_name", 36), ("set_order", 9), ("category", 12), ("reps", 5), ("weight_kg", 9), ("duration_secs", 13), ("notes", 72), ("total_duration_mins", 19), ("avg_hr", 6)])
        else:
            print_rows(f"Strength Progression: {exercise}", data, [("date", 10), ("set_order", 9), ("reps", 5), ("weight_kg", 9), ("duration_secs", 13)])
    else:
        if d == "full":
            data = rows(
                conn,
                """
                WITH ranked AS (
                    SELECT w.date, w.activity_id, w.name, s.exercise_name, s.category,
                           s.reps, s.weight_kg, s.duration_secs, s.set_order,
                           ROW_NUMBER() OVER (PARTITION BY s.exercise_name ORDER BY w.date DESC, w.activity_id DESC, s.set_order DESC) AS rn
                    FROM strength_sets s
                    JOIN workouts w ON w.activity_id = s.activity_id
                    WHERE s.exercise_name IS NOT NULL
                )
                SELECT date, activity_id AS id, name, exercise_name, category, set_order, reps, weight_kg, duration_secs
                FROM ranked
                WHERE rn = 1
                ORDER BY exercise_name
                LIMIT ?
                """,
                (row_limit(args, 20, 10, 40),),
            )
            print_rows("Latest Strength By Exercise", data, [("date", 10), ("id", 11), ("name", 18), ("exercise_name", 36), ("category", 12), ("set_order", 9), ("reps", 5), ("weight_kg", 9), ("duration_secs", 13)])
        else:
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
                (row_limit(args, 20, 10),),
            )
            print_rows("Latest Strength By Exercise", data, [("date", 10), ("exercise_name", 36), ("reps", 5), ("weight_kg", 9), ("duration_secs", 13)])


def section_strength_sets(conn, args, anchor):
    d = detail_level(args)
    exercise = args.exercise
    base_where = ""
    base_params = []
    if exercise:
        base_where = "AND s.exercise_name = ?"
        base_params = [exercise]
    data = rows(
        conn,
        f"""
        SELECT w.date, w.activity_id AS id, w.name,
               s.set_order, s.exercise_name, s.category,
               s.reps, s.weight_kg, s.duration_secs,
               w.notes, w.total_duration_mins, w.avg_hr
        FROM strength_sets s
        JOIN workouts w ON w.activity_id = s.activity_id
        WHERE w.sport IN ('strength_training', 'fitness_equipment') {base_where}
        ORDER BY w.date DESC, w.activity_id DESC, s.set_order
        LIMIT ?
        """,
        (*base_params, row_limit(args, 30, 10, 60)),
    )
    print_rows("Strength Set Details", data, [("date", 10), ("id", 11), ("name", 18), ("set_order", 9), ("exercise_name", 36), ("category", 12), ("reps", 5), ("weight_kg", 9), ("duration_secs", 13), ("notes", 72), ("total_duration_mins", 19), ("avg_hr", 6)])


def section_strength_gap(conn, args, anchor):
    latest = scalar(conn, "SELECT MAX(date) FROM workouts WHERE sport IN ('strength_training', 'fitness_equipment')")
    if not latest:
        print_lines("Strength Gap", ["No strength sessions found."])
        return
    gap = (datetime.date.fromisoformat(anchor) - datetime.date.fromisoformat(latest)).days
    print_rows("Strength Gap", [{"latest_strength": latest, "days_since": gap}], [("latest_strength", 15), ("days_since", 10)])


def section_latest_route(conn, args, anchor):
    sport_clause, sport_params = sport_where(selected_sports(args), prefix="w")
    row = rows(
        conn,
        f"""
        SELECT w.activity_id AS id, w.date, w.sport, w.name, w.distance_km AS km,
               r.point_count, r.surface_source, r.surface_total_km,
               r.hard_surface_km, r.soft_surface_km, r.distance_unknown_km,
               r.start_lat, r.start_lon, r.end_lat, r.end_lon,
               r.center_lat, r.center_lon,
               r.distance_asphalt_km, r.distance_concrete_km, r.distance_paved_other_km,
               r.distance_gravel_km, r.distance_trail_km
        FROM workouts w
        JOIN workout_routes r ON r.activity_id = w.activity_id
        WHERE w.date BETWEEN ? AND ? {sport_clause}
        ORDER BY w.date DESC, w.activity_id DESC
        LIMIT 1
        """,
        (window_start(anchor, args), anchor, *sport_params),
    )
    if not row:
        print_lines("Latest Routed Workout", ["No workouts with route data found in window."])
        return
    print_rows("Latest Routed Workout", row, [
        ("id", 11), ("date", 10), ("sport", 12), ("name", 20), ("km", 6),
        ("point_count", 11), ("surface_source", 16), ("surface_total_km", 16),
        ("hard_surface_km", 15), ("soft_surface_km", 15), ("distance_unknown_km", 19),
        ("start_lat", 9), ("start_lon", 9), ("end_lat", 9), ("end_lon", 9),
        ("center_lat", 10), ("center_lon", 10),
        ("distance_asphalt_km", 19), ("distance_concrete_km", 20), ("distance_paved_other_km", 23),
        ("distance_gravel_km", 18), ("distance_trail_km", 17),
    ])


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
    d = detail_level(args)
    if d == "full":
        data = rows(
            conn,
            """
            SELECT weather_source, latitude, longitude,
                   avg_temp_c, min_temp_c, max_temp_c, avg_humidity_pct,
                   precipitation_mm, rain_mm, snowfall_mm, avg_wind_kmh, max_wind_gust_kmh,
                   weather_codes_json
            FROM workout_weather
            WHERE activity_id = ?
            """,
            (args.workout,),
        )
        print_rows("Workout Weather", data, [("weather_source", 20), ("latitude", 8), ("longitude", 8), ("avg_temp_c", 10), ("min_temp_c", 10), ("max_temp_c", 10), ("avg_humidity_pct", 16), ("precipitation_mm", 16), ("rain_mm", 8), ("snowfall_mm", 11), ("avg_wind_kmh", 12), ("max_wind_gust_kmh", 19), ("weather_codes_json", 18)])
    else:
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
    d = detail_level(args)
    if d == "full":
        route = rows(
            conn,
            """
            SELECT point_count, surface_source, surface_total_km, hard_surface_km, soft_surface_km,
                   distance_asphalt_km, distance_concrete_km, distance_paved_other_km,
                   distance_gravel_km, distance_trail_km, distance_unknown_km,
                   start_lat, start_lon, end_lat, end_lon, center_lat, center_lon
            FROM workout_routes
            WHERE activity_id = ?
            """,
            (args.workout,),
        )
        print_rows("Workout Surface Summary", route, [("point_count", 11), ("surface_source", 16), ("surface_total_km", 16), ("hard_surface_km", 15), ("soft_surface_km", 15), ("distance_asphalt_km", 19), ("distance_concrete_km", 20), ("distance_paved_other_km", 23), ("distance_gravel_km", 18), ("distance_trail_km", 17), ("distance_unknown_km", 19), ("start_lat", 9), ("start_lon", 9), ("end_lat", 9), ("end_lon", 9), ("center_lat", 10), ("center_lon", 10)])
    else:
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

    if raw_enabled(args, "segments"):
        ordered = rows(
            conn,
            """
            SELECT sample_order, start_time_utc, end_time_utc, distance_km,
                   surface, surface_confidence, raw_surface, raw_highway, raw_tracktype,
                   match_distance_m
            FROM workout_surface_segments
            WHERE activity_id = ?
            ORDER BY sample_order
            """,
            (args.workout,),
        )
        cap = 200
        total = len(ordered)
        if total > cap:
            print_rows(f"Raw Ordered Surface Segments (showing {cap} of {total})", ordered[:cap], [("sample_order", 12), ("start_time_utc", 22), ("end_time_utc", 22), ("distance_km", 10), ("surface", 12), ("surface_confidence", 18), ("raw_surface", 14), ("raw_highway", 14), ("raw_tracktype", 14), ("match_distance_m", 15)])
            print(f"  ... {total - cap} segments omitted. Use --workout to inspect a single workout.")
        else:
            print_rows("Raw Ordered Surface Segments", ordered, [("sample_order", 12), ("start_time_utc", 22), ("end_time_utc", 22), ("distance_km", 10), ("surface", 12), ("surface_confidence", 18), ("raw_surface", 14), ("raw_highway", 14), ("raw_tracktype", 14), ("match_distance_m", 15)])
    elif d == "full":
        ordered_count = scalar(conn, "SELECT COUNT(*) FROM workout_surface_segments WHERE activity_id = ?", (args.workout,)) or 0
        if ordered_count > 0:
            preview = rows(
                conn,
                """
                SELECT sample_order, distance_km, surface, surface_confidence
                FROM workout_surface_segments
                WHERE activity_id = ?
                ORDER BY sample_order
                LIMIT 6
                """,
                (args.workout,),
            )
            print_rows(f"Surface Segments Preview ({ordered_count} total, showing first 6)", preview, [("sample_order", 12), ("distance_km", 10), ("surface", 12), ("surface_confidence", 18)])


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
    if raw_enabled(args, "streams"):
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
    d = detail_level(args)
    day = args.date.isoformat()
    if d == "full":
        data = rows(
            conn,
            """
            SELECT date, weight_kg, resting_hr, max_hr, total_steps, calories_active,
                   intensity_minutes, ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
                   sleep_score, sleep_deep_mins, sleep_light_mins, sleep_rem_mins, sleep_awake_mins,
                   hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
                   hrv_status, training_status, training_status_feedback,
                   training_load_balance_feedback, training_readiness AS readiness, training_load AS load
            FROM daily_summary
            WHERE date = ?
            """,
            (day,),
        )
        print_rows("Day Health", data, [
            ("date", 10), ("weight_kg", 9), ("resting_hr", 10), ("max_hr", 7),
            ("total_steps", 11), ("calories_active", 15), ("intensity_minutes", 17),
            ("sleep_hrs", 9), ("sleep_score", 11),
            ("sleep_deep_mins", 14), ("sleep_light_mins", 15), ("sleep_rem_mins", 14), ("sleep_awake_mins", 14),
            ("hrv", 6), ("hrv_7d", 7), ("hrv_status", 12),
            ("training_status", 15), ("training_status_feedback", 26), ("training_load_balance_feedback", 30),
            ("readiness", 9), ("load", 6),
        ])
        for stream_name, label in [("stress_stream", "Stress"), ("body_battery_stream", "Body Battery"), ("respiration_stream", "Respiration")]:
            raw_val = scalar(conn, f"SELECT {stream_name} FROM daily_summary WHERE date = ?", (day,))
            vals = load_json_list(raw_val)
            nums = [float(v) for v in vals if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace('.', '', 1).replace('-', '', 1).isdigit())]
            if nums:
                summary = [{"stream": label, "points": len(vals), "min": round(min(nums), 1), "avg": round(sum(nums) / len(nums), 1), "max": round(max(nums), 1), "first": nums[0], "last": nums[-1]}]
            else:
                summary = [{"stream": label, "points": len(vals), "min": None, "avg": None, "max": None, "first": None, "last": None}]
            print_rows(f"Day {label} Stream Summary", summary, [("stream", 14), ("points", 6), ("min", 8), ("avg", 8), ("max", 8), ("first", 8), ("last", 8)])
        if raw_enabled(args, "streams"):
            for stream_name, label in [("stress_stream", "Stress"), ("body_battery_stream", "Body Battery"), ("respiration_stream", "Respiration")]:
                raw_val = scalar(conn, f"SELECT {stream_name} FROM daily_summary WHERE date = ?", (day,))
                if raw_val:
                    print_lines(f"Raw {label} Stream", [raw_val])
    else:
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
    d = detail_level(args)
    if d == "full":
        data = rows(
            conn,
            """
            SELECT date, resting_hr, hrv_last_night_avg AS hrv, hrv_weekly_avg AS hrv_7d,
                   hrv_status, training_readiness AS readiness,
                   sleep_score, ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
                   sleep_deep_mins, sleep_light_mins, sleep_rem_mins,
                   training_load AS load, training_status_feedback, training_load_balance_feedback
            FROM daily_summary
            WHERE date BETWEEN date(?, '-1 day') AND date(?, '+1 day')
            ORDER BY date
            """,
            (day, day),
        )
        print_rows("Previous/Next Day Recovery", data, [
            ("date", 10), ("resting_hr", 10), ("hrv", 6), ("hrv_7d", 7), ("hrv_status", 12),
            ("readiness", 9), ("sleep_score", 11), ("sleep_hrs", 9),
            ("sleep_deep_mins", 14), ("sleep_light_mins", 15), ("sleep_rem_mins", 14),
            ("load", 6), ("training_status_feedback", 26), ("training_load_balance_feedback", 30),
        ])
    else:
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
    limit = 6 if detail_level(args) == "summary" else (16 if detail_level(args) == "full" else 10)
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
    "decision_context": section_decision_context,
    "derived_flags": section_derived_flags,
    "due_reviews": section_due_reviews,
    "latest_route": section_latest_route,
    "load_summary": section_load_summary,
    "long_session_fueling": section_long_session_fueling,
    "long_walks": section_long_walks,
    "nutrition_notes": section_nutrition_notes,
    "pack_fuel_notes": section_pack_fuel_notes,
    "recent_decisions": section_recent_decisions,
    "recent_health": section_recent_health,
    "recent_workouts": section_recent_workouts,
    "recovery_trend": section_recovery_trend,
    "strength_decisions": section_active_decisions,
    "strength_gap": section_strength_gap,
    "strength_progression": section_strength_progression,
    "strength_sessions": section_strength_sessions,
    "strength_sets": section_strength_sets,
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
    if args.profile == "strength" and detail_level(args) == "full":
        if "strength_sets" not in profile_sections:
            profile_sections.append("strength_sets")
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
        print(f"Detail: {detail_level(args)}")
        print(f"Anchor date: {anchor}")
        print(f"Window: {window_start(anchor, args)} to {anchor}")
        sports = selected_sports(args)
        if sports:
            print(f"Sport filter: {', '.join(sports)}")
        topics = selected_topics(args)
        if topics:
            print(f"Topic filter: {', '.join(topics)}")
        if args.exercise:
            print(f"Exercise: {args.exercise}")
        raw_val = args.raw
        if raw_val is None and args.raw_streams:
            raw_val = "streams"
        if raw_val and raw_val != "none":
            print(f"Raw: {raw_val}")
        print("Use this as coaching context; query deeper only when the answer requires raw detail.")
        for section in selected_sections(args):
            run_section(section, conn, args, anchor)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
