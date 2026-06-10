import sqlite3

from .schema import SCHEMA_SQL


def connect_database(path):
    conn = sqlite3.connect(path)
    ensure_database(conn)
    return conn


def ensure_database(conn):
    c = conn.cursor()
    for statement in SCHEMA_SQL:
        c.execute(statement)

    conn.commit()
