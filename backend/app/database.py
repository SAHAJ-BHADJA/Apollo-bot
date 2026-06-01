import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import get_settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_key(key: str) -> str:
    stripped = key.strip()
    if len(stripped) <= 12:
        return f"{stripped[:3]}******{stripped[-3:]}" if len(stripped) >= 6 else "******"
    return f"{stripped[:6]}******{stripped[-6:]}"


class DatabaseCursor:
    def __init__(self, cursor: Any, lastrowid: int | None = None):
        self.cursor = cursor
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, "lastrowid", None)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class DatabaseConnection:
    def __init__(self, raw: Any, dialect: str):
        self.raw = raw
        self.dialect = dialect

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> DatabaseCursor:
        params = tuple(params or ())
        if self.dialect == "postgres":
            sql = self._postgres_sql(sql)
        cursor = self.raw.execute(sql, params)
        return DatabaseCursor(cursor)

    def insert_and_get_id(self, sql: str, params: Iterable[Any] | None = None) -> int:
        params = tuple(params or ())
        if self.dialect == "postgres":
            cursor = self.raw.execute(f"{self._postgres_sql(sql).rstrip()} RETURNING id", params)
            row = cursor.fetchone()
            return int(row["id"])
        cursor = self.raw.execute(sql, params)
        return int(cursor.lastrowid)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    @staticmethod
    def _postgres_sql(sql: str) -> str:
        converted = _postgres_ddl(sql).replace("?", "%s")
        if "INSERT OR IGNORE INTO" in converted:
            converted = converted.replace("INSERT OR IGNORE INTO", "INSERT INTO", 1)
            converted = f"{converted.rstrip()} ON CONFLICT DO NOTHING"
        return converted


def _postgres_ddl(sql: str) -> str:
    return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")


@contextmanager
def get_db() -> Iterator[DatabaseConnection]:
    settings = get_settings()
    if settings.database_url.strip():
        from psycopg import connect
        from psycopg.rows import dict_row

        raw = connect(settings.database_url.strip(), row_factory=dict_row)
        conn = DatabaseConnection(raw, "postgres")
    else:
        Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(settings.sqlite_path, timeout=30)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA busy_timeout = 30000")
        try:
            raw.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        raw.execute("PRAGMA foreign_keys = ON")
        conn = DatabaseConnection(raw, "sqlite")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_column(conn: DatabaseConnection, table: str, column: str, definition: str) -> None:
    if conn.dialect == "postgres":
        existing = {
            row["column_name"]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table,),
            ).fetchall()
        }
    else:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS apollo_accounts (
                id INTEGER PRIMARY KEY,
                key_masked TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                last_used_at TEXT,
                total_preview_requests INTEGER NOT NULL DEFAULT 0,
                total_email_reveal_requests INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_column(conn, "apollo_accounts", "account_email", "TEXT")
        ensure_column(conn, "apollo_accounts", "encrypted_api_key", "TEXT")
        ensure_column(conn, "apollo_accounts", "source", "TEXT NOT NULL DEFAULT 'env'")
        ensure_column(conn, "apollo_accounts", "email_credit_limit", "INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT,
                company_domain TEXT,
                selected_titles_json TEXT NOT NULL,
                target_count INTEGER NOT NULL,
                preview_count INTEGER NOT NULL DEFAULT 0,
                verified_email_count INTEGER NOT NULL DEFAULT 0,
                account_used INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                job_description TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
                opening_days_json TEXT NOT NULL DEFAULT '["Monday","Tuesday","Wednesday","Thursday","Friday"]',
                opening_start_time TEXT NOT NULL DEFAULT '09:00',
                opening_end_time TEXT NOT NULL DEFAULT '14:00',
                followup_days_json TEXT NOT NULL DEFAULT '["Tuesday","Thursday"]',
                followup_start_time TEXT NOT NULL DEFAULT '09:00',
                followup_end_time TEXT NOT NULL DEFAULT '14:00',
                min_followup_gap_days INTEGER NOT NULL DEFAULT 3,
                track_opens INTEGER NOT NULL DEFAULT 1,
                stop_on_reply INTEGER NOT NULL DEFAULT 1,
                launched_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_column(conn, "campaigns", "audience_csv_path", "TEXT")
        ensure_column(conn, "campaigns", "audience_csv_filename", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                apollo_person_id TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                linkedin_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ready',
                replied_at TEXT,
                bounced_at TEXT,
                unsubscribed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                step_number INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                body_text TEXT NOT NULL DEFAULT '',
                body_html TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                scheduled_at TEXT,
                sent_at TEXT,
                opened_at TEXT,
                open_count INTEGER NOT NULL DEFAULT 0,
                replied_at TEXT,
                skipped_reason TEXT NOT NULL DEFAULT '',
                tracking_token TEXT NOT NULL UNIQUE,
                sender_account_index INTEGER,
                provider_message_id TEXT,
                provider_thread_id TEXT,
                rfc_message_id TEXT,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
                FOREIGN KEY(recipient_id) REFERENCES campaign_recipients(id)
            )
            """
        )
        ensure_column(conn, "email_messages", "provider_message_id", "TEXT")
        ensure_column(conn, "email_messages", "provider_thread_id", "TEXT")
        ensure_column(conn, "email_messages", "rfc_message_id", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_message_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                step_number INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                subject_template TEXT NOT NULL DEFAULT '',
                body_template TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(campaign_id, step_number),
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sender_accounts (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ready',
                daily_limit INTEGER NOT NULL DEFAULT 400,
                sent_today INTEGER NOT NULL DEFAULT 0,
                last_reset_date TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                recipient_id INTEGER,
                message_id INTEGER,
                event_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gmail_tokens (
                email TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at TEXT,
                scope TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            )
            """
        )


def sync_accounts(
    keys: Iterable[str],
    emails: Iterable[str] | None = None,
    credit_limits: Iterable[int | None] | None = None,
) -> None:
    from .config import get_settings
    from .crypto_service import SecretBox

    key_list = list(keys)
    email_list = list(emails or [])
    credit_limit_list = list(credit_limits or [])
    secret_box = SecretBox(get_settings())
    now = utc_now()
    with get_db() as conn:
        existing = {row["id"]: row for row in conn.execute("SELECT * FROM apollo_accounts").fetchall()}
        for index, key in enumerate(key_list):
            key_masked = mask_key(key)
            encrypted_api_key = secret_box.encrypt(key)
            account_email = email_list[index] if index < len(email_list) else ""
            email_credit_limit = (
                credit_limit_list[index] if index < len(credit_limit_list) else None
            )
            row = existing.get(index)
            if row is None:
                conn.execute(
                    """
                    INSERT INTO apollo_accounts
                    (id, key_masked, account_email, encrypted_api_key, source, email_credit_limit,
                     status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'env', ?, 'active', '', ?, ?)
                    """,
                    (index, key_masked, account_email, encrypted_api_key, email_credit_limit, now, now),
                )
            elif (
                row["key_masked"] != key_masked
                or row["account_email"] != account_email
                or not row["encrypted_api_key"]
                or row["email_credit_limit"] != email_credit_limit
                or row["source"] != "env"
            ):
                conn.execute(
                    """
                    UPDATE apollo_accounts
                    SET key_masked = ?, account_email = ?, encrypted_api_key = ?, source = 'env',
                        email_credit_limit = ?, status = 'active',
                        notes = CASE WHEN key_masked != ? THEN 'API key changed; status reset.' ELSE notes END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        key_masked,
                        account_email,
                        encrypted_api_key,
                        email_credit_limit,
                        key_masked,
                        now,
                        index,
                    ),
                )

        configured_indexes = set(range(len(key_list)))
        for account_id, row in existing.items():
            if row["source"] == "env" and account_id not in configured_indexes:
                conn.execute(
                    """
                    UPDATE apollo_accounts
                    SET status = 'failed', notes = 'API key no longer configured.', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, account_id),
                )


def list_accounts() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id AS account_index, key_masked AS masked_key, account_email, source,
                   email_credit_limit, status, last_used_at,
                   total_preview_requests, total_email_reveal_requests, notes,
                   COALESCE((
                       SELECT SUM(verified_email_count)
                       FROM search_runs
                       WHERE search_runs.account_used = apollo_accounts.id
                   ), 0) AS total_verified_emails_exported
            FROM apollo_accounts
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_apollo_account_key(account_index: int) -> str | None:
    from .config import get_settings
    from .crypto_service import SecretBox

    with get_db() as conn:
        row = conn.execute(
            "SELECT encrypted_api_key FROM apollo_accounts WHERE id = ?",
            (account_index,),
        ).fetchone()
    if not row or not row["encrypted_api_key"]:
        return None
    return SecretBox(get_settings()).decrypt(row["encrypted_api_key"])


def create_apollo_account(
    account_email: str,
    api_key: str,
    email_credit_limit: int | None = None,
    notes: str = "",
) -> dict:
    from .config import get_settings
    from .crypto_service import SecretBox

    now = utc_now()
    key_masked = mask_key(api_key)
    encrypted_api_key = SecretBox(get_settings()).encrypt(api_key)
    with get_db() as conn:
        existing = conn.execute(
            """
            SELECT id FROM apollo_accounts
            WHERE source != 'env'
              AND (key_masked = ? OR LOWER(COALESCE(account_email, '')) = LOWER(?))
            ORDER BY id ASC
            LIMIT 1
            """,
            (key_masked, account_email),
        ).fetchone()
        if existing:
            account_id = int(existing["id"])
            conn.execute(
                """
                UPDATE apollo_accounts
                SET account_email = ?, key_masked = ?, encrypted_api_key = ?, source = 'user',
                    email_credit_limit = ?, status = 'active', notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    account_email,
                    key_masked,
                    encrypted_api_key,
                    email_credit_limit,
                    notes,
                    now,
                    account_id,
                ),
            )
        else:
            row = conn.execute("SELECT COALESCE(MAX(id), -1) + 1 AS next_id FROM apollo_accounts").fetchone()
            account_id = int(row["next_id"])
            conn.execute(
                """
                INSERT INTO apollo_accounts
                (id, key_masked, account_email, encrypted_api_key, source, email_credit_limit,
                 status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'user', ?, 'active', ?, ?, ?)
                """,
                (
                    account_id,
                    key_masked,
                    account_email,
                    encrypted_api_key,
                    email_credit_limit,
                    notes,
                    now,
                    now,
                ),
            )
    return next(account for account in list_accounts() if account["account_index"] == account_id)


def set_state(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )


def get_state(key: str, default: str | None = None) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def mark_account_used(account_index: int, usage_type: str) -> None:
    if usage_type not in {"preview", "email_reveal"}:
        raise ValueError("usage_type must be preview or email_reveal")
    column = "total_preview_requests" if usage_type == "preview" else "total_email_reveal_requests"
    now = utc_now()
    with get_db() as conn:
        conn.execute(
            f"""
            UPDATE apollo_accounts
            SET last_used_at = ?, {column} = {column} + 1, updated_at = ?
            WHERE id = ?
            """,
            (now, now, account_index),
        )


def update_account_status(account_index: int, status: str, notes: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE apollo_accounts
            SET status = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, notes, utc_now(), account_index),
        )


def create_search_run(
    company_name: str,
    company_domain: str,
    titles: list[str],
    target_count: int,
    preview_count: int,
    account_used: int | None,
) -> int:
    with get_db() as conn:
        return conn.insert_and_get_id(
            """
            INSERT INTO search_runs
            (company_name, company_domain, selected_titles_json, target_count, preview_count,
             verified_email_count, account_used, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                company_name,
                company_domain,
                json.dumps(titles),
                target_count,
                preview_count,
                account_used,
                utc_now(),
            ),
        )


def update_latest_search_verified_count(
    people_ids: list[str], verified_email_count: int, account_used: int | None
) -> None:
    if not people_ids:
        return
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM search_runs ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE search_runs
                SET verified_email_count = ?, account_used = COALESCE(?, account_used)
                WHERE id = ?
                """,
                (verified_email_count, account_used, row["id"]),
            )


def sync_sender_accounts(emails: list[str], display_names: list[str], daily_limits: list[int]) -> None:
    now = utc_now()
    with get_db() as conn:
        existing = {row["id"]: row for row in conn.execute("SELECT * FROM sender_accounts").fetchall()}
        for index, email in enumerate(emails):
            if not email:
                continue
            display_name = display_names[index] if index < len(display_names) else ""
            daily_limit = daily_limits[index] if index < len(daily_limits) else 400
            if index not in existing:
                conn.execute(
                    """
                    INSERT INTO sender_accounts
                    (id, email, display_name, status, daily_limit, sent_today, last_reset_date,
                     notes, created_at, updated_at)
                    VALUES (?, ?, ?, 'ready', ?, 0, NULL, '', ?, ?)
                    """,
                    (index, email, display_name, daily_limit, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE sender_accounts
                    SET email = ?, display_name = ?, daily_limit = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (email, display_name, daily_limit, now, index),
                )


def list_sender_accounts() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM sender_accounts ORDER BY id ASC").fetchall()
        return [dict(row) for row in rows]


def record_event(
    event_type: str,
    campaign_id: int | None = None,
    recipient_id: int | None = None,
    message_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO email_events
            (campaign_id, recipient_id, message_id, event_type, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (campaign_id, recipient_id, message_id, event_type, json.dumps(metadata or {}), utc_now()),
        )
