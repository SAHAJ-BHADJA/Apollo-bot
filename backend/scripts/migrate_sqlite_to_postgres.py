import os
import sqlite3
import sys
from pathlib import Path

from psycopg import connect
from psycopg.rows import dict_row


TABLES = [
    "apollo_accounts",
    "search_runs",
    "app_state",
    "campaigns",
    "campaign_recipients",
    "email_messages",
    "campaign_message_templates",
    "sender_accounts",
    "email_events",
    "gmail_tokens",
    "campaign_attachments",
]

SERIAL_TABLES = [
    "search_runs",
    "campaigns",
    "campaign_recipients",
    "email_messages",
    "campaign_message_templates",
    "email_events",
    "campaign_attachments",
]


def placeholders(count: int) -> str:
    return ", ".join(["%s"] * count)


def main() -> int:
    backend_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_dir))

    from app.database import init_db

    sqlite_path = Path(os.environ.get("SQLITE_PATH", "apollo_leads.sqlite3"))
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not sqlite_path.exists():
        print(f"SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 1
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 1

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    init_db()
    pg_conn = connect(database_url, row_factory=dict_row)

    try:
        with pg_conn.transaction():
            for table in TABLES:
                rows = [dict(row) for row in sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()]
                if not rows:
                    print(f"{table}: 0 rows")
                    continue
                columns = list(rows[0].keys())
                column_sql = ", ".join(columns)
                conflict_sql = "ON CONFLICT DO NOTHING"
                sql = (
                    f"INSERT INTO {table} ({column_sql}) "
                    f"VALUES ({placeholders(len(columns))}) {conflict_sql}"
                )
                for row in rows:
                    pg_conn.execute(sql, tuple(row[column] for column in columns))
                print(f"{table}: copied {len(rows)} rows")

            for table in SERIAL_TABLES:
                pg_conn.execute(
                    """
                    SELECT setval(
                        pg_get_serial_sequence(%s, 'id'),
                        COALESCE((SELECT MAX(id) FROM """ + table + """), 1),
                        true
                    )
                    """,
                    (table,),
                )
        print("Migration complete.")
        return 0
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
