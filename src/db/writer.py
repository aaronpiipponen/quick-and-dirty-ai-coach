from .schema import PRIMARY_KEYS, TABLE_COLUMNS


WRITE_ORDER = [
    "daily_summary",
    "workouts",
    "strength_sets",
    "workout_routes",
    "workout_weather",
    "coach_decisions",
]


def write_payload(conn, payload, conflict="update", dry_run=False):
    counts = {table: len(payload.get(table, [])) for table in WRITE_ORDER}
    strength_deletes = payload.get("delete_strength_activity_ids", [])
    route_weather_deletes = payload.get("delete_route_weather_activity_ids", [])
    if dry_run:
        return counts

    c = conn.cursor()
    for activity_id in strength_deletes:
        c.execute("DELETE FROM strength_sets WHERE activity_id = ?", (activity_id,))
    for activity_id in route_weather_deletes:
        c.execute("DELETE FROM workout_routes WHERE activity_id = ?", (activity_id,))
        c.execute("DELETE FROM workout_weather WHERE activity_id = ?", (activity_id,))

    for table in WRITE_ORDER:
        rows = payload.get(table, [])
        if not rows:
            continue
        for row in rows:
            insert_row(c, table, row, conflict)

    conn.commit()
    return counts


def insert_row(cursor, table, row, conflict):
    valid_columns = [col for col in TABLE_COLUMNS[table] if col in row]
    if table == "coach_decisions" and row.get("decision_id") is None:
        valid_columns = [col for col in valid_columns if col != "decision_id"]

    if not valid_columns:
        return

    cols = ", ".join(valid_columns)
    placeholders = ", ".join(["?"] * len(valid_columns))
    values = tuple(row.get(col) for col in valid_columns)

    if conflict == "ignore":
        sql = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
    elif conflict == "replace":
        sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
    else:
        pk_cols = PRIMARY_KEYS[table]
        update_cols = [col for col in valid_columns if col not in pk_cols]
        if not update_cols:
            sql = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
        else:
            updates = ", ".join(f"{col}=excluded.{col}" for col in update_cols)
            conflict_target = ", ".join(pk_cols)
            sql = (
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_target}) DO UPDATE SET {updates}"
            )

    cursor.execute(sql, values)
