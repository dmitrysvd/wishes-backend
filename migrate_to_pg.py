#!/usr/bin/env python3
"""
Migrate data from SQLite to PostgreSQL.

Lightweight replacement for pgloader: uses sqlite3 (stdlib) + psycopg2
and streams data via COPY CSV — minimal memory and CPU usage.

Usage:
    python migrate_to_pg.py [path/to/db.sqlite]

DATABASE_URL must be set in the environment (or .env file).
"""

import csv
import io
import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2

BATCH_SIZE = 500

# Migrate in FK-safe order (parents before children).
# TRUNCATE is done in reverse order (children first).
TABLES = ["user", "wish", "user_following", "push_sending_log"]


def get_sqlite_columns(sqlite_conn: sqlite3.Connection, table: str) -> list[str]:
    cur = sqlite_conn.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cur.fetchall()]


def truncate_tables(pg_cur) -> None:
    # Truncate all at once; CASCADE handles FK dependencies automatically.
    tables_sql = ", ".join(f'"{t}"' for t in reversed(TABLES))
    pg_cur.execute(f"TRUNCATE TABLE {tables_sql} CASCADE")
    print("  All target tables truncated.")


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_cur,
    table: str,
    batch_size: int = BATCH_SIZE,
) -> int:
    columns = get_sqlite_columns(sqlite_conn, table)
    cols_sql = ", ".join(f'"{c}"' for c in columns)

    copy_sql = (
        f'COPY "{table}" ({cols_sql}) FROM STDIN '
        f"WITH (FORMAT CSV, NULL '')"
    )

    sqlite_cur = sqlite_conn.cursor()
    sqlite_cur.execute(f'SELECT {cols_sql} FROM "{table}"')

    total = 0
    while True:
        rows = sqlite_cur.fetchmany(batch_size)
        if not rows:
            break

        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        for row in rows:
            # None → '' so Postgres COPY CSV reads it as NULL
            writer.writerow(['' if v is None else v for v in row])

        buf.seek(0)
        pg_cur.copy_expert(copy_sql, buf)
        total += len(rows)
        print(f"  {table}: {total} rows...", end="\r", flush=True)

    print(f"  {table}: {total} rows migrated.   ")
    return total


def main() -> None:
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else "/app/db.sqlite"

    if not os.path.exists(sqlite_path):
        print(f"Error: SQLite database not found: {sqlite_path}")
        print(f"Usage: {sys.argv[0]} [path/to/db.sqlite]")
        sys.exit(1)

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        print("Error: DATABASE_URL environment variable is not set.")
        sys.exit(1)

    print(f"Source : {sqlite_path}")
    print(f"Target : {pg_url.split('@')[-1]}")  # hide credentials in output

    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg2.connect(pg_url)
    pg_conn.autocommit = False
    pg_cur = pg_conn.cursor()

    try:
        # Disable FK trigger enforcement for this session (requires superuser).
        # Allows inserting data without worrying about FK ordering issues in
        # mixed/corrupted source data.
        pg_cur.execute("SET session_replication_role = replica")

        print("\nTruncating PostgreSQL tables...")
        truncate_tables(pg_cur)

        print("\nMigrating tables:")
        for table in TABLES:
            migrate_table(sqlite_conn, pg_cur, table)

        pg_conn.commit()
        print("\nDone. Migration committed successfully.")

    except Exception as exc:
        pg_conn.rollback()
        print(f"\nError: {exc}")
        raise
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
