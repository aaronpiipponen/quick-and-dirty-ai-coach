import argparse
import datetime
import os
import sys

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from db.init import connect_database
from db.writer import write_payload
from sources import load_source, source_names

load_dotenv()

DEFAULT_DB_FILE = os.path.join(PROJECT_ROOT, "src", "db", "user_data.db")
DOWNSAMPLE_INTERVAL_SECS = float(os.getenv("DOWNSAMPLE_INTERVAL_SECS", "300"))


def parse_args():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-S", "--source", default="garmin", choices=source_names())
    known, _ = pre.parse_known_args()

    parser = argparse.ArgumentParser(description="Sync normalized coaching data into a local SQLite database.")
    parser.add_argument("-S", "--source", default="garmin", choices=source_names(), help="Data source adapter to use.")
    parser.add_argument("-o", "--output", default=resolve_db_file(), help="Target SQLite database. Defaults to DB_FILE or src/db/user_data.db.")
    parser.add_argument("--since", type=datetime.date.fromisoformat, metavar="YYYY-MM-DD", help="Start date filter for date-based sync/import.")
    parser.add_argument("--until", type=datetime.date.fromisoformat, metavar="YYYY-MM-DD", help="End date filter for date-based sync/import.")
    parser.add_argument("--conflict", choices=["update", "ignore", "replace"], default="update", help="How to handle rows already in the target database.")

    target = parser.add_mutually_exclusive_group()
    target.add_argument("-d", "--date", type=datetime.date.fromisoformat, metavar="YYYY-MM-DD", help="Sync only this specific date.")
    target.add_argument("-w", "--workout", type=int, metavar="ACTIVITY_ID", help="Sync only this Garmin workout id.")
    parser.add_argument("--downsample", type=float, default=DOWNSAMPLE_INTERVAL_SECS, metavar="SECONDS", help="Workout stream downsampling interval in seconds.")

    source_module = load_source(known.source)
    source_module.add_arguments(parser)
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        parser.print_help()
        sys.exit(0)
    args = parser.parse_args()

    if args.downsample <= 0:
        parser.error("--downsample must be greater than 0")
    if args.since and args.until and args.since > args.until:
        parser.error("--since must be earlier than or equal to --until")
    if args.date and (args.since or args.until):
        parser.error("--date cannot be combined with --since or --until")
    if args.workout and (args.since or args.until):
        parser.error("--workout cannot be combined with --since or --until")
    if args.source == "sqlite_import" and (args.date or args.workout):
        parser.error("sqlite_import uses --since/--until date filters; --date and --workout are Garmin-only")
    if args.source == "sqlite_import" and not getattr(args, "input", None):
        parser.error("sqlite_import requires --input")
    return args, source_module


def resolve_db_file():
    db_file_env = os.getenv("DB_FILE")
    if db_file_env and os.path.isabs(db_file_env):
        return db_file_env
    if db_file_env:
        return os.path.join(PROJECT_ROOT, db_file_env)
    return DEFAULT_DB_FILE


def main():
    args, source_module = parse_args()
    read_only_import = args.source == "sqlite_import" and (
        getattr(args, "print_schema", False) or getattr(args, "plan", False) or getattr(args, "dry_run", False)
    )

    conn = None if read_only_import else connect_database(args.output)
    try:
        payload = source_module.fetch(args, conn)
        if payload.get("_exit"):
            return
        dry_run = bool(read_only_import)
        counts = write_payload(conn, payload, conflict=args.conflict, dry_run=dry_run)
        action = "Planned" if dry_run else "Synced"
        print(f"{action} rows: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
        if not dry_run:
            print("Sync complete.")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
