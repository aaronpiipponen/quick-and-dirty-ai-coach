import argparse
import os
from pathlib import Path
import sqlite3

try:
    from .schema import SCHEMA_SQL
except ImportError:
    from schema import SCHEMA_SQL


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = PROJECT_ROOT / "src" / "db" / "user_data.db"
SAMPLE_DATA_FILE = Path(__file__).with_name("sample_data.sql")


def connect_database(path):
    conn = sqlite3.connect(path)
    ensure_database(conn)
    return conn


def ensure_database(conn):
    c = conn.cursor()
    for statement in SCHEMA_SQL:
        c.execute(statement)

    conn.commit()


def load_sample_data(conn):
    with open(SAMPLE_DATA_FILE, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def resolve_db_file(path):
    if path:
        db_file = Path(path)
    else:
        db_file = Path(os.getenv("DB_FILE", DEFAULT_DB_FILE))
    return db_file if db_file.is_absolute() else PROJECT_ROOT / db_file


def parse_args():
    parser = argparse.ArgumentParser(description="Initialize the local coaching SQLite database.")
    parser.add_argument("--db", help="SQLite database path. Defaults to DB_FILE or src/db/user_data.db.")
    parser.add_argument("--sample", action="store_true", help="Load fabricated sample data after initializing schema.")
    return parser.parse_args()


def main():
    args = parse_args()
    db_file = resolve_db_file(args.db)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_database(db_file)
    try:
        if args.sample:
            load_sample_data(conn)
            print(f"Initialized sample database: {db_file}")
        else:
            print(f"Initialized database schema: {db_file}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
