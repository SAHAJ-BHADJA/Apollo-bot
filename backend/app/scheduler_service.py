from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import Settings, get_settings
from .database import get_db, record_event, sync_sender_accounts, utc_now
from .email_sender import EmailSender, SenderUnavailable
from .sequence_service import SequenceService


class SchedulerService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        sync_sender_accounts(
            self.settings.account_emails,
            self.settings.display_names,
            self.settings.daily_limits,
        )
        self.sender = EmailSender(self.settings)
        self.sequence = SequenceService(self.settings)

    def reset_daily_counts(self) -> None:
        today = date.today().isoformat()
        with get_db() as conn:
            conn.execute(
                """
                UPDATE sender_accounts
                SET sent_today = 0, last_reset_date = ?, updated_at = ?
                WHERE last_reset_date IS NULL OR last_reset_date != ?
                """,
                (today, utc_now(), today),
            )

    def tick(self, limit: int = 25) -> dict:
        self.reset_daily_counts()
        self.check_replies()
        self.pause_high_bounce_senders()
        sent = 0
        skipped = 0
        paused = 0
        now_dt = datetime.now(timezone.utc)

        with get_db() as conn:
            scheduled = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT m.*, r.email, r.status AS recipient_status, c.stop_on_reply, c.timezone,
                           c.status AS campaign_status, c.followup_days_json, c.followup_start_time, c.followup_end_time,
                           c.min_followup_gap_days, c.opening_days_json, c.opening_start_time,
                           c.opening_end_time, s.daily_limit, s.sent_today, s.status AS sender_status
                    FROM email_messages m
                    JOIN campaign_recipients r ON r.id = m.recipient_id
                    JOIN campaigns c ON c.id = m.campaign_id
                    JOIN sender_accounts s ON s.id = m.sender_account_index
                    WHERE m.status = 'scheduled'
                      AND c.status = 'launched'
                    ORDER BY m.scheduled_at ASC, m.id ASC
                    LIMIT ?
                    """,
                    (max(limit * 10, 100),),
                ).fetchall()
            ]
        due = [
            message
            for message in scheduled
            if self._scheduled_is_due(message.get("scheduled_at"), now_dt)
        ][:limit]

        for message in due:
            if not self._inside_send_window(message, now_dt):
                self._reschedule(message)
                paused += 1
                continue
            if message["recipient_status"] in {"replied", "bounced", "unsubscribed", "removed"}:
                self._skip(message, f"recipient {message['recipient_status']}")
                skipped += 1
                continue
            if int(message["sent_today"]) >= int(message["daily_limit"]):
                self._reschedule(message)
                paused += 1
                continue
            ok, reason = self.sender.can_send(int(message["sender_account_index"]))
            if not ok:
                self._pause_sender(int(message["sender_account_index"]), reason)
                self._reschedule(message)
                paused += 1
                continue

            try:
                previous = self._previous_sent_message(message)
                send_result = self.sender.send(
                    int(message["sender_account_index"]),
                    message["email"],
                    message["subject"],
                    message["body_text"],
                    message["body_html"],
                    self._attachments_for_campaign(int(message["campaign_id"])),
                    previous.get("provider_thread_id"),
                    previous.get("rfc_message_id"),
                )
                self._mark_sent(message, send_result)
                sent += 1
            except SenderUnavailable as exc:
                self._pause_sender(int(message["sender_account_index"]), str(exc))
                self._reschedule(message)
                paused += 1
            except Exception as exc:
                self._mark_failed(message, str(exc))

        return {"sent": sent, "skipped": skipped, "paused_or_rescheduled": paused, "checked": len(due)}

    def check_replies(self) -> dict:
        marked = 0
        bounced = 0
        with get_db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT DISTINCT r.id AS recipient_id, r.email, r.campaign_id, m.sender_account_index
                    FROM campaign_recipients r
                    JOIN email_messages m ON m.recipient_id = r.id
                    WHERE r.status NOT IN ('replied', 'removed', 'bounced', 'unsubscribed')
                      AND m.status IN ('sent', 'opened')
                    """
                ).fetchall()
            ]
        for row in rows:
            if row["sender_account_index"] is None:
                continue
            if self.sender.has_bounce_for(int(row["sender_account_index"]), row["email"]):
                self.sequence.mark_bounced(int(row["campaign_id"]), int(row["recipient_id"]))
                bounced += 1
                continue
            if self.sender.has_reply_from(int(row["sender_account_index"]), row["email"]):
                self.sequence.mark_replied(int(row["campaign_id"]), int(row["recipient_id"]))
                marked += 1
        return {"replies_marked": marked, "bounces_marked": bounced}

    def pause_high_bounce_senders(self) -> None:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT s.id,
                       SUM(CASE WHEN e.event_type = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN e.event_type = 'bounced' THEN 1 ELSE 0 END) AS bounce_count
                FROM sender_accounts s
                LEFT JOIN email_messages m ON m.sender_account_index = s.id
                LEFT JOIN email_events e ON e.message_id = m.id
                GROUP BY s.id
                """
            ).fetchall()
            for row in rows:
                sent_count = int(row["sent_count"] or 0)
                bounce_count = int(row["bounce_count"] or 0)
                if sent_count >= 20 and bounce_count / sent_count >= 0.05:
                    conn.execute(
                        """
                        UPDATE sender_accounts
                        SET status = 'paused', notes = 'Paused: high bounce rate', updated_at = ?
                        WHERE id = ?
                        """,
                        (utc_now(), row["id"]),
                    )

    @staticmethod
    def _previous_sent_message(message: dict) -> dict:
        if int(message["step_number"]) <= 1:
            return {}
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT provider_thread_id, rfc_message_id
                FROM email_messages
                WHERE campaign_id = ? AND recipient_id = ? AND step_number < ?
                  AND status IN ('sent', 'opened', 'replied')
                ORDER BY step_number DESC
                LIMIT 1
                """,
                (message["campaign_id"], message["recipient_id"], message["step_number"]),
            ).fetchone()
            return dict(row) if row else {}

    @staticmethod
    def _scheduled_is_due(scheduled_at: str | None, now_dt: datetime) -> bool:
        if not scheduled_at:
            return False
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_at)
        except ValueError:
            return False
        if scheduled_dt.tzinfo is None:
            scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
        return scheduled_dt.astimezone(timezone.utc) <= now_dt

    @staticmethod
    def _inside_send_window(message: dict, now_dt: datetime) -> bool:
        tz = ZoneInfo(message["timezone"])
        local_now = now_dt.astimezone(tz)
        weekday = local_now.strftime("%A")
        if int(message["step_number"]) <= 1:
            days_json = message["opening_days_json"]
            start_text = message["opening_start_time"]
            end_text = message["opening_end_time"]
        else:
            days_json = message["followup_days_json"]
            start_text = message["followup_start_time"]
            end_text = message["followup_end_time"]
        try:
            import json

            days = set(json.loads(days_json))
        except (TypeError, ValueError):
            return False
        if weekday not in days:
            return False
        start = datetime.strptime(start_text, "%H:%M").time()
        end = datetime.strptime(end_text, "%H:%M").time()
        return start <= local_now.time() < end

    def _mark_sent(self, message: dict, send_result: dict | None = None) -> None:
        now = utc_now()
        result = send_result or {}
        with get_db() as conn:
            conn.execute(
                """
                UPDATE email_messages
                SET status = 'sent', sent_at = ?, provider_message_id = ?,
                    provider_thread_id = ?, rfc_message_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    result.get("provider_message_id", ""),
                    result.get("provider_thread_id", ""),
                    result.get("rfc_message_id", ""),
                    now,
                    message["id"],
                ),
            )
            conn.execute(
                """
                UPDATE sender_accounts
                SET sent_today = sent_today + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, message["sender_account_index"]),
            )
        record_event("sent", message["campaign_id"], message["recipient_id"], message["id"])

    @staticmethod
    def _attachments_for_campaign(campaign_id: int) -> list[dict]:
        with get_db() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM campaign_attachments WHERE campaign_id = ? ORDER BY id ASC",
                    (campaign_id,),
                ).fetchall()
            ]

    def _skip(self, message: dict, reason: str) -> None:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE email_messages
                SET status = 'skipped', skipped_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (reason, utc_now(), message["id"]),
            )
        record_event("skipped", message["campaign_id"], message["recipient_id"], message["id"], {"reason": reason})

    def _mark_failed(self, message: dict, error: str) -> None:
        with get_db() as conn:
            conn.execute(
                "UPDATE email_messages SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (error[:500], utc_now(), message["id"]),
            )
        record_event("failed", message["campaign_id"], message["recipient_id"], message["id"], {"error": error[:500]})

    def _pause_sender(self, account_index: int, reason: str) -> None:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE sender_accounts
                SET status = 'paused', notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (reason, utc_now(), account_index),
            )

    def _reschedule(self, message: dict) -> None:
        next_time = self.sequence.next_send_time(message, int(message["step_number"]), message["scheduled_at"])
        with get_db() as conn:
            conn.execute(
                """
                UPDATE email_messages
                SET scheduled_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_time, utc_now(), message["id"]),
            )
