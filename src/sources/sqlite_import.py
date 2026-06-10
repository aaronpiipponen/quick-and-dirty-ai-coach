import json
import os
import re
import sqlite3

from db.schema import DATE_COLUMNS, TABLE_COLUMNS


DEFAULT_TABLE_MAP = {table: table for table in TABLE_COLUMNS}
DEFAULT_COLUMN_ALIASES = {
    "daily_summary": {
        "date": ["day", "calendar_date"],
        "resting_hr": ["restingHeartRate", "resting_heart_rate"],
        "max_hr": ["maxHeartRate", "max_heart_rate"],
        "sleep_duration_mins": ["sleep_minutes", "sleep_duration", "sleep_mins"],
        "hrv_last_night_avg": ["lastNightAvg", "overnight_hrv", "hrv"],
        "hrv_weekly_avg": ["weeklyAvg", "weekly_hrv"],
    },
    "workouts": {
        "activity_id": ["activityId", "garmin_id", "id"],
        "date": ["start_date", "activity_date"],
        "sport": ["type", "typeKey", "activity_type"],
        "name": ["activityName", "activity_name"],
        "notes": ["description", "note"],
        "distance_km": ["distance", "kilometers"],
        "avg_hr": ["averageHR", "average_hr"],
        "max_hr": ["maxHR", "maximum_hr"],
    },
    "coach_decisions": {
        "date": ["decision_date", "created_date"],
        "topic": ["category"],
        "decision": ["summary", "call"],
        "reason": ["rationale"],
    },
}


def add_arguments(parser):
    parser.add_argument("-i", "--input", help="Source SQLite database to import from.")
    parser.add_argument("-t", "--table", action="append", choices=sorted(TABLE_COLUMNS), help="Only import this table. Repeatable.")
    parser.add_argument("-m", "--map", dest="mapping_file", help="JSON mapping file for renamed tables/columns.")
    parser.add_argument("--auto-map", action="store_true", help="Infer obvious table/column mappings.")
    parser.add_argument("--print-schema", action="store_true", help="Print source database tables and columns, then exit.")
    parser.add_argument("--plan", action="store_true", help="Print import plan without writing.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count rows without writing.")
    parser.add_argument("--strict", action="store_true", help="Fail when a target column cannot be mapped.")
    parser.add_argument("--skip-streams", action="store_true", help="Skip JSON stream columns during import.")


def fetch(args, conn):
    if not os.path.exists(args.input):
        raise FileNotFoundError(args.input)
    source = sqlite3.connect(f"file:{args.input}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source_schema = get_schema(source)
        if args.print_schema:
            print_schema(source_schema)
            return {"_exit": True}

        mapping = load_mapping(args.mapping_file)
        table_names = args.table or list(TABLE_COLUMNS.keys())
        payload = {table: [] for table in TABLE_COLUMNS}

        for target_table in table_names:
            source_table, column_map = resolve_table_mapping(
                target_table, source_schema, mapping, args.auto_map, args.strict
            )
            if not source_table:
                if args.strict:
                    raise ValueError(f"No source table mapped for {target_table}")
                continue

            rows = read_rows(source, target_table, source_table, column_map, args)
            payload[target_table].extend(rows)
            if args.plan or args.dry_run:
                print(f"{target_table}: {len(rows)} rows from {source_table}")

        return payload
    finally:
        source.close()


def get_schema(conn):
    tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        tables[name] = [row[1] for row in conn.execute(f"PRAGMA table_info({quote_ident(name)})")]
    return tables


def print_schema(schema):
    for table, columns in schema.items():
        print(f"{table}: {', '.join(columns)}")


def load_mapping(path):
    if not path:
        return {"tables": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_table_mapping(target_table, source_schema, mapping, auto_map, strict):
    table_cfg = mapping.get("tables", {}).get(target_table, {})
    source_table = table_cfg.get("old_name") or table_cfg.get("source")
    if not source_table:
        candidates = [DEFAULT_TABLE_MAP[target_table], target_table]
        source_table = first_existing(candidates, source_schema)
    if not source_table and auto_map:
        source_table = first_normalized_match(target_table, source_schema.keys())
    if not source_table:
        return None, {}

    source_columns = source_schema[source_table]
    explicit_columns = table_cfg.get("columns", {})
    column_map = {}
    for target_col in TABLE_COLUMNS[target_table]:
        source_col = explicit_columns.get(target_col)
        if not source_col and target_col in source_columns:
            source_col = target_col
        if not source_col and auto_map:
            source_col = auto_column_match(target_table, target_col, source_columns)
        if source_col:
            column_map[target_col] = source_col
        elif strict and target_col != "decision_id":
            raise ValueError(f"No source column mapped for {target_table}.{target_col}")
    return source_table, column_map


def read_rows(conn, target_table, source_table, column_map, args):
    date_col = DATE_COLUMNS.get(target_table)
    where = []
    params = []
    if date_col and date_col in column_map:
        source_date_col = quote_ident(column_map[date_col])
        if args.since:
            where.append(f"{source_date_col} >= ?")
            params.append(args.since.isoformat())
        if args.until:
            where.append(f"{source_date_col} <= ?")
            params.append(args.until.isoformat())

    sql = f"SELECT * FROM {quote_ident(source_table)}"
    if where:
        sql += " WHERE " + " AND ".join(where)

    rows = []
    for source_row in conn.execute(sql, params):
        row = {}
        for target_col, source_col in column_map.items():
            if args.skip_streams and target_col.endswith("_stream"):
                continue
            row[target_col] = source_row[source_col]
        rows.append(row)
    return rows


def first_existing(candidates, schema):
    return next((candidate for candidate in candidates if candidate in schema), None)


def first_normalized_match(target, candidates):
    target_norm = normalize_name(target)
    return next((candidate for candidate in candidates if normalize_name(candidate) == target_norm), None)


def auto_column_match(table, target_col, source_columns):
    aliases = [target_col] + DEFAULT_COLUMN_ALIASES.get(table, {}).get(target_col, [])
    for alias in aliases:
        matched = first_normalized_match(alias, source_columns)
        if matched:
            return matched
    return None


def normalize_name(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def quote_ident(value):
    return '"' + value.replace('"', '""') + '"'
