import imaplib
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from .config import Settings, get_settings
from .gmail_client import GmailAuthError, GmailClient


class SenderUnavailable(Exception):
    pass


class EmailSender:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.gmail = GmailClient(self.settings)

    def can_send(self, account_index: int) -> tuple[bool, str]:
        if self._uses_gmail_api(account_index):
            status = self.gmail.status()
            if status["configured"] and status["connected"]:
                return True, ""
            return False, "Paused: inbox disconnected"
        smtp = self._smtp(account_index)
        imap = self._imap(account_index)
        if not smtp.get("host") or not smtp.get("username") or not smtp.get("password"):
            return False, "Paused: inbox disconnected"
        if not imap.get("host") or not imap.get("username") or not imap.get("password"):
            return False, "Paused: inbox disconnected"
        return True, ""

    def send(
        self,
        account_index: int,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: str,
        attachments: list[dict] | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict:
        ok, reason = self.can_send(account_index)
        if not ok:
            raise SenderUnavailable(reason)

        if self._uses_gmail_api(account_index):
            try:
                return self.gmail.send(
                    account_index,
                    to_email,
                    subject,
                    body_text,
                    body_html,
                    attachments,
                    thread_id,
                    in_reply_to,
                )
            except GmailAuthError as exc:
                raise SenderUnavailable(str(exc)) from exc

        email = self.settings.account_emails[account_index]
        display_name = self.settings.display_names[account_index] if account_index < len(self.settings.display_names) else ""
        smtp = self._smtp(account_index)

        message = EmailMessage()
        message["From"] = formataddr((display_name, email)) if display_name else email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body_text)
        message.add_alternative(body_html, subtype="html")
        for attachment in attachments or []:
            path = Path(attachment["stored_path"])
            if not path.exists():
                continue
            content_type = attachment.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = content_type.split("/", 1) if "/" in content_type else ("application", "octet-stream")
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=attachment.get("filename") or path.name,
            )

        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as server:
            server.starttls()
            server.login(smtp["username"], smtp["password"])
            server.send_message(message)
        return {"provider_message_id": "", "provider_thread_id": "", "rfc_message_id": ""}

    def has_reply_from(self, account_index: int, recipient_email: str) -> bool:
        if self._uses_gmail_api(account_index):
            return self.gmail.has_reply_from(account_index, recipient_email)
        ok, _reason = self.can_send(account_index)
        if not ok:
            return False
        imap = self._imap(account_index)
        try:
            with imaplib.IMAP4_SSL(imap["host"], imap["port"]) as mailbox:
                mailbox.login(imap["username"], imap["password"])
                mailbox.select("INBOX")
                status, data = mailbox.search(None, "FROM", f'"{recipient_email}"')
                return status == "OK" and bool(data and data[0])
        except Exception:
            return False

    def has_bounce_for(self, account_index: int, recipient_email: str) -> bool:
        if self._uses_gmail_api(account_index):
            return self.gmail.has_bounce_for(account_index, recipient_email)
        ok, _reason = self.can_send(account_index)
        if not ok:
            return False
        imap = self._imap(account_index)
        try:
            with imaplib.IMAP4_SSL(imap["host"], imap["port"]) as mailbox:
                mailbox.login(imap["username"], imap["password"])
                mailbox.select("INBOX")
                status, data = mailbox.search(None, "TEXT", f'"{recipient_email}"')
                if status != "OK" or not data or not data[0]:
                    return False
                for message_id in data[0].split()[-10:]:
                    fetch_status, fetched = mailbox.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                    if fetch_status != "OK" or not fetched:
                        continue
                    header = fetched[0][1].decode("utf-8", errors="ignore").lower()
                    if any(term in header for term in ["mailer-daemon", "postmaster", "delivery status", "undeliverable", "delivery failure"]):
                        return True
        except Exception:
            return False
        return False

    def _smtp(self, account_index: int) -> dict:
        settings = self.settings.smtp_settings
        return settings[account_index] if account_index < len(settings) else {}

    def _imap(self, account_index: int) -> dict:
        settings = self.settings.imap_settings
        return settings[account_index] if account_index < len(settings) else {}

    def _uses_gmail_api(self, account_index: int) -> bool:
        if not self.settings.gmail_sender_email.strip():
            return False
        return (
            account_index < len(self.settings.account_emails)
            and self.settings.account_emails[account_index].lower()
            == self.settings.gmail_sender_email.strip().lower()
        )
