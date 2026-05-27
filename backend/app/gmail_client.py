import base64
import mimetypes
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import urlencode

import httpx

from .config import Settings, get_settings
from .database import get_db, utc_now


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GmailAuthError(Exception):
    pass


class GmailClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(
            self.settings.google_client_id.strip()
            and self.settings.google_client_secret.strip()
            and self.settings.gmail_sender_email.strip()
        )

    def auth_url(self) -> str:
        if not self.is_configured():
            raise GmailAuthError("Google OAuth is not configured in .env.")
        params = {
            "client_id": self.settings.google_client_id.strip(),
            "redirect_uri": self.settings.google_redirect_uri.strip(),
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "login_hint": self.settings.gmail_sender_email.strip(),
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": self.settings.google_client_id.strip(),
                    "client_secret": self.settings.google_client_secret.strip(),
                    "redirect_uri": self.settings.google_redirect_uri.strip(),
                    "grant_type": "authorization_code",
                },
            )
        if response.status_code >= 400:
            raise GmailAuthError(response.text)
        data = response.json()
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            existing = self._token_row(self.settings.gmail_sender_email.strip())
            refresh_token = existing.get("refresh_token")
        if not refresh_token:
            raise GmailAuthError("Google did not return a refresh token. Reconnect with prompt=consent.")
        self._save_token(
            self.settings.gmail_sender_email.strip(),
            data["access_token"],
            refresh_token,
            int(data.get("expires_in", 3600)),
            data.get("scope", ""),
        )
        return {"email": self.settings.gmail_sender_email.strip(), "connected": True}

    def status(self) -> dict:
        email = self.settings.gmail_sender_email.strip()
        row = self._token_row(email)
        return {
            "email": email,
            "configured": self.is_configured(),
            "connected": bool(row),
            "scope": row.get("scope", ""),
            "expires_at": row.get("expires_at"),
        }

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
        sender_email = self._sender_email(account_index)
        token = self._access_token(sender_email)
        display_name = (
            self.settings.display_names[account_index]
            if account_index < len(self.settings.display_names)
            else ""
        )

        message = EmailMessage()
        message["From"] = formataddr((display_name, sender_email)) if display_name else sender_email
        message["To"] = to_email
        message["Subject"] = subject
        rfc_message_id = make_msgid(domain=sender_email.split("@")[-1])
        message["Message-ID"] = rfc_message_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
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
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"raw": encoded, **({"threadId": thread_id} if thread_id else {})},
            )
        if response.status_code >= 400:
            raise GmailAuthError(response.text)
        data = response.json()
        return {
            "provider_message_id": data.get("id", ""),
            "provider_thread_id": data.get("threadId", thread_id or ""),
            "rfc_message_id": rfc_message_id,
        }

    def has_reply_from(self, account_index: int, recipient_email: str) -> bool:
        query = f'from:{recipient_email} newer_than:90d -from:mailer-daemon -from:postmaster'
        return self._has_message(account_index, query)

    def has_bounce_for(self, account_index: int, recipient_email: str) -> bool:
        query = (
            f'("{recipient_email}" OR {recipient_email}) '
            '(from:mailer-daemon OR from:postmaster OR subject:(undeliverable OR "delivery failure")) newer_than:90d'
        )
        return self._has_message(account_index, query)

    def _has_message(self, account_index: int, query: str) -> bool:
        sender_email = self._sender_email(account_index)
        token = self._access_token(sender_email)
        with httpx.Client(timeout=30) as client:
            response = client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "maxResults": 1},
            )
        if response.status_code >= 400:
            return False
        return bool(response.json().get("messages"))

    def _sender_email(self, account_index: int) -> str:
        if self.settings.gmail_sender_email.strip():
            return self.settings.gmail_sender_email.strip()
        if account_index < len(self.settings.account_emails):
            return self.settings.account_emails[account_index]
        raise GmailAuthError("No Gmail sender email configured.")

    def _access_token(self, email: str) -> str:
        row = self._token_row(email)
        if not row:
            raise GmailAuthError("Gmail OAuth is not connected.")
        expires_at = row.get("expires_at")
        if expires_at:
            expires = datetime.fromisoformat(expires_at)
            if expires > datetime.now(timezone.utc) + timedelta(minutes=2):
                return row["access_token"]
        return self._refresh_token(email, row["refresh_token"])

    def _refresh_token(self, email: str, refresh_token: str) -> str:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self.settings.google_client_id.strip(),
                    "client_secret": self.settings.google_client_secret.strip(),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if response.status_code >= 400:
            raise GmailAuthError(response.text)
        data = response.json()
        self._save_token(
            email,
            data["access_token"],
            refresh_token,
            int(data.get("expires_in", 3600)),
            data.get("scope", ""),
        )
        return data["access_token"]

    def _save_token(
        self, email: str, access_token: str, refresh_token: str, expires_in: int, scope: str
    ) -> None:
        now = utc_now()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 30))
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO gmail_tokens
                (email, access_token, refresh_token, expires_at, scope, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (email, access_token, refresh_token, expires_at.isoformat(), scope, now, now),
            )

    @staticmethod
    def _token_row(email: str) -> dict:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM gmail_tokens WHERE email = ?", (email,)).fetchone()
            return dict(row) if row else {}
