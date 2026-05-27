import html
import json
import secrets
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .claude_client import ClaudeClient, render_template
from .config import Settings, get_settings
from .csv_service import audience_csv_bytes, audience_csv_filename, verified_audience_rows
from .database import get_db, record_event, utc_now


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DEFAULT_TEMPLATE_STEPS = [
    (1, "Main Email"),
    (2, "Follow-up 1"),
    (3, "Follow-up 2"),
]


def _row_dict(row) -> dict:
    return dict(row) if row is not None else {}


def text_to_html(body_text: str, tracking_url: str | None = None) -> str:
    escaped = html.escape(body_text).replace("\n", "<br>")
    pixel = ""
    if tracking_url:
        pixel = f'<img src="{html.escape(tracking_url)}" width="1" height="1" alt="" style="display:none" />'
    return f"<html><body>{escaped}{pixel}</body></html>"


class SequenceService:
    _draft_lock = threading.Lock()

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def create_campaign_from_enriched(
        self,
        name: str,
        preview_people: list[dict],
        enriched_people: list[dict],
    ) -> dict:
        rows = verified_audience_rows(enriched_people, preview_people)
        now = utc_now()
        with get_db() as conn:
            campaign_id = conn.insert_and_get_id(
                """
                INSERT INTO campaigns (name, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (name.strip() or "Apollo Outreach Sequence", now, now),
            )
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO campaign_recipients
                    (campaign_id, apollo_person_id, first_name, last_name, email, title, company,
                     linkedin_url, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                    """,
                    (
                        campaign_id,
                        row.get("Apollo Person ID", ""),
                        row["First Name"],
                        row["Last Name"],
                        row["Email"],
                        row.get("Title", ""),
                        row.get("Company", ""),
                        row.get("LinkedIn", ""),
                        now,
                        now,
                    ),
                )
        self.ensure_default_templates(campaign_id)
        self.archive_audience_csv(campaign_id)
        record_event("campaign_created", campaign_id=campaign_id, metadata={"recipients": len(rows)})
        return self.get_campaign(campaign_id)

    def ensure_default_templates(self, campaign_id: int) -> None:
        now = utc_now()
        with get_db() as conn:
            for step_number, step_name in DEFAULT_TEMPLATE_STEPS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO campaign_message_templates
                    (campaign_id, step_number, step_name, subject_template, body_template,
                     created_at, updated_at)
                    VALUES (?, ?, ?, '', '', ?, ?)
                    """,
                    (campaign_id, step_number, step_name, now, now),
                )

    def archive_audience_csv(self, campaign_id: int) -> tuple[Path, str]:
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            recipients = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM campaign_recipients WHERE campaign_id = ? ORDER BY id ASC",
                    (campaign_id,),
                ).fetchall()
            ]

        company = next((row.get("company") for row in recipients if row.get("company")), campaign["name"])
        filename = campaign["audience_csv_filename"] or audience_csv_filename(company)
        export_dir = self.settings.export_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / filename
        rows = [
            {
                "First Name": recipient.get("first_name", ""),
                "Last Name": recipient.get("last_name", ""),
                "Email": recipient.get("email", ""),
                "Title": recipient.get("title", ""),
                "Company": recipient.get("company", ""),
                "LinkedIn": recipient.get("linkedin_url", ""),
                "Apollo Person ID": recipient.get("apollo_person_id", ""),
            }
            for recipient in recipients
            if recipient.get("status") != "removed"
        ]
        path.write_bytes(audience_csv_bytes(rows))
        with get_db() as conn:
            conn.execute(
                """
                UPDATE campaigns
                SET audience_csv_path = ?, audience_csv_filename = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(path), filename, utc_now(), campaign_id),
            )
        record_event("audience_csv_archived", campaign_id=campaign_id, metadata={"filename": filename})
        return path, filename

    def get_audience_csv_file(self, campaign_id: int) -> tuple[Path, str]:
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            filename = campaign["audience_csv_filename"] or f"campaign_{campaign_id}_audience.csv"
            path_text = campaign["audience_csv_path"] or ""

        path = Path(path_text) if path_text else Path()
        if path_text and path.exists():
            return path, filename
        return self.archive_audience_csv(campaign_id)

    def get_campaign(self, campaign_id: int) -> dict:
        with get_db() as conn:
            campaign = _row_dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone())
            if not campaign:
                raise ValueError("Campaign not found.")
            recipients = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM campaign_recipients WHERE campaign_id = ? ORDER BY id ASC",
                    (campaign_id,),
                ).fetchall()
            ]
            messages = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM email_messages WHERE campaign_id = ? ORDER BY recipient_id, step_number",
                    (campaign_id,),
                ).fetchall()
            ]
            templates = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM campaign_message_templates
                    WHERE campaign_id = ?
                    ORDER BY step_number ASC
                    """,
                    (campaign_id,),
                ).fetchall()
            ]
            attachments = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM campaign_attachments WHERE campaign_id = ? ORDER BY id ASC",
                    (campaign_id,),
                ).fetchall()
            ]
            events = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM email_events
                    WHERE campaign_id = ?
                    ORDER BY created_at DESC
                    LIMIT 100
                    """,
                    (campaign_id,),
                ).fetchall()
            ]
            stats = self._stats(conn, campaign_id)
        campaign["opening_days"] = json.loads(campaign.pop("opening_days_json"))
        campaign["followup_days"] = json.loads(campaign.pop("followup_days_json"))
        return {
            "campaign": campaign,
            "recipients": recipients,
            "messages": messages,
            "templates": templates,
            "attachments": attachments,
            "events": events,
            "stats": stats,
        }

    def list_campaigns(self) -> list[dict]:
        with get_db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                """
                SELECT c.*, COUNT(r.id) AS recipient_count
                FROM campaigns c
                LEFT JOIN campaign_recipients r ON r.campaign_id = c.id
                GROUP BY c.id
                ORDER BY c.created_at DESC
                """
                ).fetchall()
            ]
            for row in rows:
                row["stats"] = self._stats(conn, int(row["id"]))
                row["opening_days"] = json.loads(row.pop("opening_days_json"))
                row["followup_days"] = json.loads(row.pop("followup_days_json"))
            return rows

    def pause_campaign(self, campaign_id: int) -> dict:
        now = utc_now()
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            conn.execute(
                """
                UPDATE campaigns
                SET status = 'paused', updated_at = ?
                WHERE id = ?
                """,
                (now, campaign_id),
            )
        record_event("campaign_paused", campaign_id=campaign_id)
        return self.get_campaign(campaign_id)

    def resume_campaign(self, campaign_id: int) -> dict:
        now = utc_now()
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            scheduled = conn.execute(
                "SELECT COUNT(*) AS count FROM email_messages WHERE campaign_id = ? AND status = 'scheduled'",
                (campaign_id,),
            ).fetchone()["count"]
            draft = conn.execute(
                "SELECT COUNT(*) AS count FROM email_messages WHERE campaign_id = ? AND status = 'draft'",
                (campaign_id,),
            ).fetchone()["count"]
            next_status = "launched" if scheduled else "draft" if draft else "completed"
            conn.execute(
                """
                UPDATE campaigns
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_status, now, campaign_id),
            )
        record_event("campaign_resumed", campaign_id=campaign_id, metadata={"status": next_status})
        return self.get_campaign(campaign_id)

    def cancel_remaining(self, campaign_id: int) -> dict:
        now = utc_now()
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            conn.execute(
                """
                UPDATE email_messages
                SET status = 'canceled', skipped_reason = 'campaign remaining emails canceled',
                    updated_at = ?
                WHERE campaign_id = ? AND status IN ('draft', 'scheduled')
                """,
                (now, campaign_id),
            )
            conn.execute(
                """
                UPDATE campaigns
                SET status = 'canceled', updated_at = ?
                WHERE id = ?
                """,
                (now, campaign_id),
            )
        record_event("campaign_remaining_canceled", campaign_id=campaign_id)
        return self.get_campaign(campaign_id)

    def reschedule_overdue(self, campaign_id: int) -> dict:
        now_dt = datetime.now(timezone.utc)
        now = utc_now()
        moved = 0
        with get_db() as conn:
            campaign = _row_dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone())
            if not campaign:
                raise ValueError("Campaign not found.")
            messages = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM email_messages
                    WHERE campaign_id = ? AND status = 'scheduled'
                    ORDER BY scheduled_at ASC
                    """,
                    (campaign_id,),
                ).fetchall()
            ]
            for message in messages:
                if not self._scheduled_is_due(message.get("scheduled_at"), now_dt):
                    continue
                next_time = self.next_send_time(campaign, int(message["step_number"]), message["scheduled_at"])
                conn.execute(
                    """
                    UPDATE email_messages
                    SET scheduled_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_time, now, message["id"]),
                )
                moved += 1
        record_event("campaign_overdue_rescheduled", campaign_id=campaign_id, metadata={"moved": moved})
        result = self.get_campaign(campaign_id)
        result["rescheduled"] = moved
        return result

    def update_recipient_status(self, campaign_id: int, recipient_id: int, status: str) -> dict:
        now = utc_now()
        with get_db() as conn:
            conn.execute(
                """
                UPDATE campaign_recipients
                SET status = ?, updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (status, now, recipient_id, campaign_id),
            )
        self.archive_audience_csv(campaign_id)
        return self.get_campaign(campaign_id)

    def generate_drafts(self, campaign_id: int, job_description: str, instructions: str) -> dict:
        if not self._draft_lock.acquire(blocking=False):
            raise ValueError("Draft generation is already running. Please wait for it to finish.")
        now = utc_now()
        try:
            claude = ClaudeClient(self.settings)
            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE campaigns
                    SET job_description = ?, instructions = ?, status = 'generating', updated_at = ?
                    WHERE id = ?
                    """,
                    (job_description, instructions, now, campaign_id),
                )
                recipients = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT * FROM campaign_recipients
                        WHERE campaign_id = ? AND status NOT IN ('removed', 'bounced', 'unsubscribed')
                        ORDER BY id ASC
                        """,
                        (campaign_id,),
                    ).fetchall()
                ]

            company = next((recipient.get("company") for recipient in recipients if recipient.get("company")), "")
            template_sequence = claude.generate_campaign_sequence(
                company, job_description, instructions, recipients
            )
            generated: list[tuple[dict, dict]] = []
            for recipient in recipients:
                for item in template_sequence:
                    generated.append((recipient, render_template(item, recipient)))

            insert_now = utc_now()
            main_subject = template_sequence[0]["subject"]
            with get_db() as conn:
                conn.execute("DELETE FROM email_messages WHERE campaign_id = ?", (campaign_id,))
                conn.execute("DELETE FROM campaign_message_templates WHERE campaign_id = ?", (campaign_id,))
                for item in template_sequence:
                    subject_template = main_subject
                    conn.execute(
                        """
                        INSERT INTO campaign_message_templates
                        (campaign_id, step_number, step_name, subject_template, body_template,
                         created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(campaign_id, step_number) DO UPDATE SET
                            step_name = excluded.step_name,
                            subject_template = excluded.subject_template,
                            body_template = excluded.body_template,
                            updated_at = excluded.updated_at
                        """,
                        (
                            campaign_id,
                            item["step_number"],
                            "Main Email" if item["step_number"] == 1 else item["step_name"],
                            subject_template,
                            item["body_text"],
                            insert_now,
                            insert_now,
                        ),
                    )
                for recipient, item in generated:
                    item = {**item, "subject": main_subject}
                    token = secrets.token_urlsafe(32)
                    tracking_url = self._tracking_url(token)
                    body_html = text_to_html(item["body_text"], tracking_url)
                    conn.execute(
                        """
                        INSERT INTO email_messages
                        (campaign_id, recipient_id, step_number, step_name, subject, body_text, body_html,
                         status, tracking_token, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            recipient["id"],
                            item["step_number"],
                            item["step_name"],
                            item["subject"],
                            item["body_text"],
                            body_html,
                            token,
                            insert_now,
                            insert_now,
                        ),
                    )
                conn.execute(
                    """
                    UPDATE campaigns
                    SET status = 'draft', updated_at = ?
                    WHERE id = ?
                    """,
                    (insert_now, campaign_id),
                )
            record_event("drafts_generated", campaign_id=campaign_id, metadata={"recipients": len(recipients)})
            return self.get_campaign(campaign_id)
        finally:
            self._draft_lock.release()

    def save_sequence_templates(
        self,
        campaign_id: int,
        subject_template: str,
        main_body_template: str,
        followup_1_body_template: str,
        followup_2_body_template: str,
    ) -> dict:
        now = utc_now()
        templates = [
            {
                "step_number": 1,
                "step_name": "Main Email",
                "subject": subject_template,
                "body_text": main_body_template,
            },
            {
                "step_number": 2,
                "step_name": "Follow-up 1",
                "subject": subject_template,
                "body_text": followup_1_body_template,
            },
            {
                "step_number": 3,
                "step_name": "Follow-up 2",
                "subject": subject_template,
                "body_text": followup_2_body_template,
            },
        ]
        if not subject_template.strip():
            raise ValueError("Subject is required.")
        if not main_body_template.strip():
            raise ValueError("Main email body is required.")
        if not followup_1_body_template.strip():
            raise ValueError("Follow-up 1 body is required.")
        if not followup_2_body_template.strip():
            raise ValueError("Follow-up 2 body is required.")
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            recipients = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM campaign_recipients
                    WHERE campaign_id = ? AND status != 'removed'
                    ORDER BY id ASC
                    """,
                    (campaign_id,),
                ).fetchall()
            ]
            if not recipients:
                raise ValueError("No active recipients in this campaign.")
            for item in templates:
                conn.execute(
                    """
                    INSERT INTO campaign_message_templates
                    (campaign_id, step_number, step_name, subject_template, body_template,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(campaign_id, step_number) DO UPDATE SET
                        step_name = excluded.step_name,
                        subject_template = excluded.subject_template,
                        body_template = excluded.body_template,
                        updated_at = excluded.updated_at
                    """,
                    (
                        campaign_id,
                        item["step_number"],
                        item["step_name"],
                        item["subject"],
                        item["body_text"],
                        now,
                        now,
                    ),
                )
            existing = {
                (row["recipient_id"], row["step_number"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM email_messages WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchall()
            }
            for recipient in recipients:
                for item in templates:
                    key = (recipient["id"], item["step_number"])
                    rendered = render_template(item, recipient)
                    existing_message = existing.get(key)
                    if existing_message:
                        if existing_message["status"] not in {"draft", "scheduled"}:
                            continue
                        body_html = text_to_html(
                            rendered["body_text"],
                            self._tracking_url(existing_message["tracking_token"]),
                        )
                        conn.execute(
                            """
                            UPDATE email_messages
                            SET step_name = ?, subject = ?, body_text = ?, body_html = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                item["step_name"],
                                rendered["subject"],
                                rendered["body_text"],
                                body_html,
                                now,
                                existing_message["id"],
                            ),
                        )
                    else:
                        token = secrets.token_urlsafe(32)
                        body_html = text_to_html(rendered["body_text"], self._tracking_url(token))
                        conn.execute(
                            """
                            INSERT INTO email_messages
                            (campaign_id, recipient_id, step_number, step_name, subject, body_text,
                             body_html, status, tracking_token, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
                            """,
                            (
                                campaign_id,
                                recipient["id"],
                                item["step_number"],
                                item["step_name"],
                                rendered["subject"],
                                rendered["body_text"],
                                body_html,
                                token,
                                now,
                                now,
                            ),
                        )
            conn.execute(
                """
                UPDATE campaigns
                SET status = 'draft', updated_at = ?
                WHERE id = ?
                """,
                (now, campaign_id),
            )
        record_event("templates_saved", campaign_id=campaign_id)
        return self.get_campaign(campaign_id)

    def update_template(
        self, campaign_id: int, step_number: int, subject_template: str, body_template: str
    ) -> dict:
        now = utc_now()
        with get_db() as conn:
            template = conn.execute(
                """
                SELECT * FROM campaign_message_templates
                WHERE campaign_id = ? AND step_number = ?
                """,
                (campaign_id, step_number),
            ).fetchone()
            if not template:
                raise ValueError("Template not found. Generate drafts first.")
            conn.execute(
                """
                UPDATE campaign_message_templates
                SET subject_template = ?, body_template = ?, updated_at = ?
                WHERE campaign_id = ? AND step_number = ?
                """,
                (subject_template, body_template, now, campaign_id, step_number),
            )
            recipients = {
                row["id"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM campaign_recipients WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchall()
            }
            messages = conn.execute(
                """
                SELECT * FROM email_messages
                WHERE campaign_id = ? AND step_number = ?
                  AND status IN ('draft', 'scheduled')
                """,
                (campaign_id, step_number),
            ).fetchall()
            item = {
                "step_number": step_number,
                "step_name": template["step_name"],
                "subject": subject_template,
                "body_text": body_template,
            }
            for message in messages:
                recipient = recipients.get(message["recipient_id"])
                if not recipient:
                    continue
                rendered = render_template(item, recipient)
                body_html = text_to_html(
                    rendered["body_text"], self._tracking_url(message["tracking_token"])
                )
                conn.execute(
                    """
                    UPDATE email_messages
                    SET subject = ?, body_text = ?, body_html = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (rendered["subject"], rendered["body_text"], body_html, now, message["id"]),
                )
        record_event(
            "template_updated",
            campaign_id=campaign_id,
            metadata={"step_number": step_number},
        )
        return self.get_campaign(campaign_id)

    def add_attachment(
        self, campaign_id: int, filename: str, content_type: str, content: bytes
    ) -> dict:
        safe_name = Path(filename).name
        if not safe_name:
            raise ValueError("Attachment filename is required.")
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("Attachment must be 10MB or smaller.")
        upload_dir = self.settings.upload_dir / str(campaign_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        unique_name = f"{secrets.token_hex(8)}_{safe_name}"
        stored_path = upload_dir / unique_name
        stored_path.write_bytes(content)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO campaign_attachments
                (campaign_id, filename, stored_path, content_type, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    safe_name,
                    str(stored_path),
                    content_type or "application/octet-stream",
                    len(content),
                    utc_now(),
                ),
            )
        record_event("attachment_added", campaign_id=campaign_id, metadata={"filename": safe_name})
        return self.get_campaign(campaign_id)

    def delete_attachment(self, campaign_id: int, attachment_id: int) -> dict:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM campaign_attachments WHERE id = ? AND campaign_id = ?",
                (attachment_id, campaign_id),
            ).fetchone()
            if not row:
                raise ValueError("Attachment not found.")
            path = Path(row["stored_path"])
            conn.execute(
                "DELETE FROM campaign_attachments WHERE id = ? AND campaign_id = ?",
                (attachment_id, campaign_id),
            )
        if path.exists():
            path.unlink()
        record_event("attachment_deleted", campaign_id=campaign_id, metadata={"attachment_id": attachment_id})
        return self.get_campaign(campaign_id)

    def update_message(self, campaign_id: int, message_id: int, subject: str, body_text: str) -> dict:
        now = utc_now()
        with get_db() as conn:
            row = conn.execute(
                "SELECT tracking_token FROM email_messages WHERE id = ? AND campaign_id = ?",
                (message_id, campaign_id),
            ).fetchone()
            if not row:
                raise ValueError("Message not found.")
            body_html = text_to_html(body_text, self._tracking_url(row["tracking_token"]))
            conn.execute(
                """
                UPDATE email_messages
                SET subject = ?, body_text = ?, body_html = ?, updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (subject, body_text, body_html, now, message_id, campaign_id),
            )
        return self.get_campaign(campaign_id)

    def update_settings(self, campaign_id: int, settings: dict) -> dict:
        now = utc_now()
        with get_db() as conn:
            conn.execute(
                """
                UPDATE campaigns
                SET timezone = ?, opening_days_json = ?, opening_start_time = ?, opening_end_time = ?,
                    followup_days_json = ?, followup_start_time = ?, followup_end_time = ?,
                    min_followup_gap_days = ?, track_opens = ?, stop_on_reply = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    settings["timezone"],
                    json.dumps(settings["opening_days"]),
                    settings["opening_start_time"],
                    settings["opening_end_time"],
                    json.dumps(settings["followup_days"]),
                    settings["followup_start_time"],
                    settings["followup_end_time"],
                    settings["min_followup_gap_days"],
                    1 if settings["track_opens"] else 0,
                    1 if settings["stop_on_reply"] else 0,
                    now,
                    campaign_id,
                ),
            )
        return self.get_campaign(campaign_id)

    def launch_campaign(self, campaign_id: int, sender_indexes: list[int]) -> dict:
        now = utc_now()
        with get_db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not campaign:
                raise ValueError("Campaign not found.")
            messages = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM email_messages WHERE campaign_id = ? ORDER BY recipient_id, step_number",
                    (campaign_id,),
                ).fetchall()
            ]
            if not messages:
                raise ValueError("Generate drafts before launching.")
            senders = sender_indexes or [row["id"] for row in conn.execute("SELECT id FROM sender_accounts").fetchall()]
            if not senders:
                raise ValueError("No sender accounts configured.")
            by_recipient: dict[int, list[dict]] = {}
            for message in messages:
                by_recipient.setdefault(message["recipient_id"], []).append(message)
            for recipient_id, recipient_messages in by_recipient.items():
                previous_at = None
                for message in sorted(recipient_messages, key=lambda item: item["step_number"]):
                    sender_index = senders[(recipient_id + message["step_number"]) % len(senders)]
                    scheduled_at = self.next_send_time(dict(campaign), message["step_number"], previous_at)
                    conn.execute(
                        """
                        UPDATE email_messages
                        SET status = 'scheduled', scheduled_at = ?, sender_account_index = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (scheduled_at, sender_index, now, message["id"]),
                    )
                    previous_at = scheduled_at
            conn.execute(
                "UPDATE campaigns SET status = 'launched', launched_at = ?, updated_at = ? WHERE id = ?",
                (now, now, campaign_id),
            )
        record_event("campaign_launched", campaign_id=campaign_id, metadata={"sender_indexes": sender_indexes})
        return self.get_campaign(campaign_id)

    def next_send_time(
        self, campaign: dict, step_number: int, previous_sent_or_scheduled_at: str | None = None
    ) -> str:
        tz = ZoneInfo(campaign["timezone"])
        now = datetime.now(tz)
        if previous_sent_or_scheduled_at:
            base = datetime.fromisoformat(previous_sent_or_scheduled_at).astimezone(tz)
        else:
            base = now

        days = json.loads(campaign["opening_days_json"] if "opening_days_json" in campaign else json.dumps(campaign["opening_days"]))
        start_text = campaign["opening_start_time"]
        end_text = campaign["opening_end_time"]
        earliest = now
        if step_number > 1:
            days = json.loads(campaign["followup_days_json"] if "followup_days_json" in campaign else json.dumps(campaign["followup_days"]))
            start_text = campaign["followup_start_time"]
            end_text = campaign["followup_end_time"]
            earliest = base + timedelta(days=int(campaign["min_followup_gap_days"]), seconds=1)

        start = time.fromisoformat(start_text)
        end = time.fromisoformat(end_text)
        candidate = max(now, earliest)
        for offset in range(90):
            day = (candidate + timedelta(days=offset)).date()
            day_name = WEEKDAY_NAMES[day.weekday()]
            if day_name not in days:
                continue
            start_dt = datetime.combine(day, start, tz)
            end_dt = datetime.combine(day, end, tz)
            if candidate <= start_dt:
                return start_dt.isoformat()
            if start_dt <= candidate < end_dt:
                return candidate.isoformat()
        return candidate.isoformat()

    def mark_opened(self, token: str) -> None:
        now = utc_now()
        with get_db() as conn:
            row = conn.execute("SELECT * FROM email_messages WHERE tracking_token = ?", (token,)).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE email_messages
                SET open_count = open_count + 1, opened_at = COALESCE(opened_at, ?), status = CASE
                    WHEN status = 'sent' THEN 'opened'
                    ELSE status
                END, updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
        record_event("opened", campaign_id=row["campaign_id"], recipient_id=row["recipient_id"], message_id=row["id"])

    def mark_replied(self, campaign_id: int, recipient_id: int, message_id: int | None = None) -> None:
        now = utc_now()
        with get_db() as conn:
            conn.execute(
                """
                UPDATE campaign_recipients
                SET status = 'replied', replied_at = COALESCE(replied_at, ?), updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (now, now, recipient_id, campaign_id),
            )
            conn.execute(
                """
                UPDATE email_messages
                SET status = 'skipped', skipped_reason = 'recipient replied', updated_at = ?
                WHERE recipient_id = ? AND campaign_id = ? AND status IN ('draft', 'scheduled')
                """,
                (now, recipient_id, campaign_id),
            )
            if message_id:
                conn.execute(
                    """
                    UPDATE email_messages
                    SET replied_at = COALESCE(replied_at, ?), status = 'replied', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, message_id),
                )
        record_event("replied", campaign_id=campaign_id, recipient_id=recipient_id, message_id=message_id)

    def mark_bounced(self, campaign_id: int, recipient_id: int, message_id: int | None = None) -> None:
        now = utc_now()
        with get_db() as conn:
            conn.execute(
                """
                UPDATE campaign_recipients
                SET status = 'bounced', bounced_at = COALESCE(bounced_at, ?), updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (now, now, recipient_id, campaign_id),
            )
            conn.execute(
                """
                UPDATE email_messages
                SET status = 'skipped', skipped_reason = 'recipient bounced', updated_at = ?
                WHERE recipient_id = ? AND campaign_id = ? AND status IN ('draft', 'scheduled')
                """,
                (now, recipient_id, campaign_id),
            )
        record_event("bounced", campaign_id=campaign_id, recipient_id=recipient_id, message_id=message_id)

    def _tracking_url(self, token: str) -> str | None:
        if not self.settings.tracking_base_url:
            return None
        return f"{self.settings.tracking_base_url.rstrip('/')}/track/open/{token}.png"

    @staticmethod
    def _stats(conn, campaign_id: int) -> dict:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM email_messages
            WHERE campaign_id = ?
            GROUP BY status
            """,
            (campaign_id,),
        ).fetchall()
        stats = {row["status"]: row["count"] for row in rows}
        for status in [
            "draft",
            "scheduled",
            "sent",
            "opened",
            "replied",
            "skipped",
            "failed",
            "canceled",
        ]:
            stats.setdefault(status, 0)
        stats["recipients"] = conn.execute(
            "SELECT COUNT(*) AS count FROM campaign_recipients WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()["count"]
        stats["active_recipients"] = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM campaign_recipients
            WHERE campaign_id = ? AND status NOT IN ('removed', 'bounced', 'unsubscribed')
            """,
            (campaign_id,),
        ).fetchone()["count"]
        stats["replied_recipients"] = conn.execute(
            "SELECT COUNT(*) AS count FROM campaign_recipients WHERE campaign_id = ? AND status = 'replied'",
            (campaign_id,),
        ).fetchone()["count"]
        stats["bounced_recipients"] = conn.execute(
            "SELECT COUNT(*) AS count FROM campaign_recipients WHERE campaign_id = ? AND status = 'bounced'",
            (campaign_id,),
        ).fetchone()["count"]
        stats["open_events"] = conn.execute(
            "SELECT COALESCE(SUM(open_count), 0) AS count FROM email_messages WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()["count"]
        stats["last_sent_at"] = conn.execute(
            "SELECT MAX(sent_at) AS value FROM email_messages WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()["value"]
        scheduled_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT scheduled_at
                FROM email_messages
                WHERE campaign_id = ? AND status = 'scheduled' AND scheduled_at IS NOT NULL
                """,
                (campaign_id,),
            ).fetchall()
        ]
        parsed = [
            (SequenceService._parse_scheduled_at(row["scheduled_at"]), row["scheduled_at"])
            for row in scheduled_rows
            if row["scheduled_at"]
        ]
        parsed = [item for item in parsed if item[0] is not None]
        stats["next_scheduled_at"] = min(parsed, key=lambda item: item[0])[1] if parsed else None
        now_dt = datetime.now(timezone.utc)
        stats["due_now"] = sum(
            1
            for row in scheduled_rows
            if SequenceService._scheduled_is_due(row.get("scheduled_at"), now_dt)
        )
        return stats

    @staticmethod
    def _parse_scheduled_at(scheduled_at: str | None) -> datetime | None:
        if not scheduled_at:
            return None
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_at)
        except ValueError:
            return None
        if scheduled_dt.tzinfo is None:
            scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
        return scheduled_dt.astimezone(timezone.utc)

    @staticmethod
    def _scheduled_is_due(scheduled_at: str | None, now_dt: datetime) -> bool:
        scheduled_dt = SequenceService._parse_scheduled_at(scheduled_at)
        if not scheduled_dt:
            return False
        return scheduled_dt <= now_dt.astimezone(timezone.utc)
