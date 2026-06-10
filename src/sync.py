import argparse
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

def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync normalized coaching data into a local SQLite database."
    )
    subparsers = parser.add_subparsers(dest="source", metavar="source", required=True)

    for source_name in source_names():
        source_module = load_source(source_name)
        source_parser = subparsers.add_parser(
            source_name,
            help=getattr(source_module, "HELP", None),
            description=getattr(source_module, "DESCRIPTION", None),
            epilog=getattr(source_module, "EPILOG", None),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        source_module.add_arguments(source_parser)
        source_parser.set_defaults(source_module=source_module, source_parser=source_parser)

    args = parser.parse_args()
    source_module = args.source_module

    if hasattr(source_module, "validate_args"):
        source_module.validate_args(args.source_parser, args)
    return args, source_module


def main():
    args, source_module = parse_args()
    read_only = source_module.is_read_only(args) if hasattr(source_module, "is_read_only") else False

    conn = None if read_only else connect_database(args.output)
    try:
        payload = source_module.fetch(args, conn)
        if payload.get("_exit"):
            return
        dry_run = bool(read_only)
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
