import os


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB_FILE = os.path.join(PROJECT_ROOT, "src", "db", "user_data.db")


def resolve_db_file():
    db_file_env = os.getenv("DB_FILE")
    if db_file_env and os.path.isabs(db_file_env):
        return db_file_env
    if db_file_env:
        return os.path.join(PROJECT_ROOT, db_file_env)
    return DEFAULT_DB_FILE


def add_database_arguments(parser):
    parser.add_argument(
        "-o",
        "--output",
        default=resolve_db_file(),
        help="Target SQLite database. Defaults to DB_FILE or src/db/user_data.db.",
    )
    parser.add_argument(
        "--conflict",
        choices=["update", "ignore", "replace"],
        default="update",
        help="How to handle rows already in the target database.",
    )


def validate_date_range(parser, args):
    if args.since and args.until and args.since > args.until:
        parser.error("--since must be earlier than or equal to --until")
