import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .config import get_settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_key(key: str) -> str:
    stripped = key.strip()
    if len(stripped) <= 12:
        return f"{stripped[:3]}******{stripped[-3:]}" if len(stripped) >= 6 else "******"
    return f"{stripped[:6]}******{stripped[-6:]}"


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.sqlite_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
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


def sync_accounts(keys: Iterable[str]) -> None:
    now = utc_now()
    with get_db() as conn:
        existing = {row["id"]: row for row in conn.execute("SELECT * FROM apollo_accounts").fetchall()}
        for index, key in enumerate(keys):
            key_masked = mask_key(key)
            row = existing.get(index)
            if row is None:
                conn.execute(
                    """
                    INSERT INTO apollo_accounts
                    (id, key_masked, status, notes, created_at, updated_at)
                    VALUES (?, ?, 'active', '', ?, ?)
                    """,
                    (index, key_masked, now, now),
                )
            elif row["key_masked"] != key_masked:
                conn.execute(
                    """
                    UPDATE apollo_accounts
                    SET key_masked = ?, status = 'active', notes = 'API key changed; status reset.',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (key_masked, now, index),
                )

        configured_indexes = set(range(len(list(keys))))
        for account_id in existing:
            if account_id not in configured_indexes:
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
            SELECT id AS account_index, key_masked AS masked_key, status, last_used_at,
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
        cur = conn.execute(
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
        return int(cur.lastrowid)


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
