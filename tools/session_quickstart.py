import argparse
import os
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_FILE = PROJECT_ROOT / "src" / "db" / "user_data.db"


def resolve_db_file():
    db_file = os.getenv("DB_FILE")
    if not db_file:
        return DEFAULT_DB_FILE
    path = Path(db_file)
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_args():
    parser = argparse.ArgumentParser(description="Print a compact coaching session database quickstart.")
    parser.add_argument("--db", type=Path, default=resolve_db_file(), help="SQLite database path.")
    parser.add_argument("--days", type=int, default=14, help="Recent window for health/workout context.")
    parser.add_argument("--workouts", type=int, default=10, help="Maximum recent workouts to print.")
    return parser.parse_args()


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


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


def main():
    args = parse_args()
    db_file = args.db.expanduser().resolve()
    if not db_file.exists():
        raise SystemExit(f"Database not found: {db_file}")
    if args.days <= 0:
        raise SystemExit("--days must be greater than 0")
    if args.workouts <= 0:
        raise SystemExit("--workouts must be greater than 0")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
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

        print("# Coaching Session Quickstart")
        print(f"Database: {db_file}")
        print(f"Anchor date: {anchor}")
        print(f"Window: last {args.days} days")
        print("Read coach/user Markdown files first; use this as database orientation, not a full analysis.")

        recent_health = rows(
            conn,
            """
            SELECT date, weight_kg, resting_hr,
                   ROUND(sleep_duration_mins / 60.0, 1) AS sleep_hrs,
                   sleep_score, hrv_last_night_avg AS hrv, hrv_status,
                   training_readiness AS readiness, training_load AS load
            FROM daily_summary
            WHERE date >= date(?, ?)
            ORDER BY date DESC
            LIMIT 7
            """,
            (anchor, f"-{args.days - 1} days"),
        )
        print_rows(
            "Recent Health",
            recent_health,
            [
                ("date", 10),
                ("weight_kg", 9),
                ("resting_hr", 10),
                ("sleep_hrs", 9),
                ("sleep_score", 11),
                ("hrv", 6),
                ("hrv_status", 12),
                ("readiness", 9),
                ("load", 6),
            ],
        )

        weekly_volume = rows(
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
            WHERE date >= date(?, '-27 days')
            GROUP BY week
            ORDER BY week DESC
            LIMIT 4
            """,
            (anchor,),
        )
        print_rows(
            "Last 4 Workout Weeks",
            weekly_volume,
            [("week", 9), ("sessions", 8), ("km", 7), ("moving_hrs", 10), ("z1", 6), ("z2", 6), ("z3plus", 7)],
        )

        recent_workouts = rows(
            conn,
            """
            SELECT w.date, w.sport, w.name, w.distance_km AS km,
                   moving_duration_mins AS moving_min,
                   w.avg_moving_pace AS pace, w.avg_hr,
                   w.zone2_mins AS z2,
                   w.zone3_mins + w.zone4_mins + w.zone5_mins AS z3plus,
                   r.hard_surface_km AS hard_km,
                   r.soft_surface_km AS soft_km,
                   r.distance_unknown_km AS unknown_km,
                   w.notes
            FROM workouts w
            LEFT JOIN workout_routes r ON r.activity_id = w.activity_id
            WHERE w.date >= date(?, ?)
            ORDER BY w.date DESC, w.activity_id DESC
            LIMIT ?
            """,
            (anchor, f"-{args.days - 1} days", args.workouts),
        )
        print_rows(
            "Recent Workouts",
            recent_workouts,
            [
                ("date", 10),
                ("sport", 12),
                ("km", 6),
                ("moving_min", 10),
                ("pace", 7),
                ("avg_hr", 6),
                ("z2", 5),
                ("z3plus", 7),
                ("hard_km", 7),
                ("soft_km", 7),
                ("unknown_km", 10),
                ("notes", 72),
            ],
        )

        decisions = rows(
            conn,
            """
            SELECT decision_id AS id, date, topic, decision, next_review_date AS review
            FROM coach_decisions
            WHERE status = 'active'
            ORDER BY COALESCE(next_review_date, date), decision_id
            LIMIT 8
            """,
        )
        print_rows(
            "Active Coach Decisions",
            decisions,
            [("id", 4), ("date", 10), ("topic", 10), ("review", 10), ("decision", 86)],
        )

        weighins = rows(
            conn,
            """
            SELECT date, weight_kg
            FROM daily_summary
            WHERE weight_kg IS NOT NULL
            ORDER BY date DESC
            LIMIT 5
            """,
        )
        print_rows("Latest Weigh-Ins", weighins, [("date", 10), ("weight_kg", 9)])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
