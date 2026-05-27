from functools import lru_cache
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_DIR = BACKEND_DIR.parent
WORKSPACE_DIR = PROJECT_DIR.parent

load_dotenv(WORKSPACE_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")


class Settings(BaseSettings):
    apollo_api_keys: str = Field(default="", alias="APOLLO_API_KEYS")
    apollo_account_emails: str = Field(default="", alias="APOLLO_ACCOUNT_EMAILS")
    apollo_email_credit_limits: str = Field(default="", alias="APOLLO_EMAIL_CREDIT_LIMITS")
    default_apollo_account_email: str = Field(default="", alias="DEFAULT_APOLLO_ACCOUNT_EMAIL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_api_key: str = Field(default="", alias="CLAUDE_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL")
    anthropic_model_name: str = Field(default="", alias="ANTHROPIC_MODEL_NAME")
    anthropic_version: str = Field(default="2023-06-01", alias="ANTHROPIC_VERSION")
    master_resume_data_path: Path = Field(
        default=Path(r"e:\Projects\Random\master_resume_data.json"),
        alias="MASTER_RESUME_DATA_PATH",
    )
    cold_email_proof_bank_path: Path = Field(
        default=Path(r"e:\Projects\Random\email bot\shareable\COLD_EMAIL_PROOF_BANK.md"),
        alias="COLD_EMAIL_PROOF_BANK_PATH",
    )
    cold_email_playbook_path: Path = Field(
        default=Path(r"e:\Projects\Random\email bot\shareable\COLD_EMAIL_PLAYBOOK.md"),
        alias="COLD_EMAIL_PLAYBOOK_PATH",
    )
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/gmail/oauth/callback", alias="GOOGLE_REDIRECT_URI"
    )
    gmail_sender_email: str = Field(default="", alias="GMAIL_SENDER_EMAIL")
    tracking_base_url: str = Field(default="", alias="TRACKING_BASE_URL")
    sender_daily_limits: str = Field(default="", alias="SENDER_DAILY_LIMITS")
    sender_display_names: str = Field(default="", alias="SENDER_DISPLAY_NAMES")
    smtp_hosts: str = Field(default="", alias="SMTP_HOSTS")
    smtp_ports: str = Field(default="", alias="SMTP_PORTS")
    smtp_usernames: str = Field(default="", alias="SMTP_USERNAMES")
    smtp_passwords: str = Field(default="", alias="SMTP_PASSWORDS")
    imap_hosts: str = Field(default="", alias="IMAP_HOSTS")
    imap_ports: str = Field(default="", alias="IMAP_PORTS")
    imap_usernames: str = Field(default="", alias="IMAP_USERNAMES")
    imap_passwords: str = Field(default="", alias="IMAP_PASSWORDS")
    apollo_base_url: str = Field(default="https://api.apollo.io/api/v1", alias="APOLLO_BASE_URL")
    apollo_people_search_path: str = Field(
        default="/mixed_people/api_search", alias="APOLLO_PEOPLE_SEARCH_PATH"
    )
    apollo_people_match_path: str = Field(default="/people/match", alias="APOLLO_PEOPLE_MATCH_PATH")
    apollo_bulk_match_path: str = Field(default="/people/bulk_match", alias="APOLLO_BULK_MATCH_PATH")
    database_url: str = Field(default="", alias="DATABASE_URL")
    sqlite_path: Path = Field(default=BACKEND_DIR / "apollo_leads.sqlite3", alias="SQLITE_PATH")
    upload_dir: Path = Field(default=BACKEND_DIR / "uploads", alias="UPLOAD_DIR")
    export_dir: Path = Field(default=BACKEND_DIR / "exports", alias="EXPORT_DIR")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    app_api_token: str = Field(default="", alias="APP_API_TOKEN")
    request_timeout_seconds: float = Field(default=30.0, alias="REQUEST_TIMEOUT_SECONDS")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")

    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    @property
    def api_keys(self) -> List[str]:
        keys = self._split_csv(self.apollo_api_keys)
        for name, value in sorted(os.environ.items()):
            if name != "APOLLO_API_KEYS" and name.endswith("_APOLLO_API_KEYS"):
                keys.extend(self._split_csv(value))
        return keys

    @property
    def account_emails(self) -> List[str]:
        emails = self._split_csv(self.apollo_account_emails)
        if not emails and self.default_apollo_account_email.strip():
            emails = [self.default_apollo_account_email.strip()]

        for name, _value in sorted(os.environ.items()):
            if name != "APOLLO_API_KEYS" and name.endswith("_APOLLO_API_KEYS"):
                prefix = name.removesuffix("_APOLLO_API_KEYS")
                emails_for_key = self._split_csv(os.environ.get(f"{prefix}_ACCOUNT_EMAIL", ""))
                key_count = len(self._split_csv(os.environ.get(name, "")))
                if emails_for_key:
                    emails.extend(emails_for_key)
                else:
                    emails.extend([""] * key_count)

        key_count = len(self.api_keys)
        if len(emails) < key_count:
            emails.extend([""] * (key_count - len(emails)))
        return emails[:key_count]

    @property
    def email_credit_limits(self) -> List[int | None]:
        limits: List[int | None] = []
        for item in self._split_csv(self.apollo_email_credit_limits):
            try:
                limits.append(max(0, int(item)))
            except ValueError:
                limits.append(None)
        key_count = len(self.api_keys)
        if len(limits) < key_count:
            limits.extend([None] * (key_count - len(limits)))
        return limits[:key_count]

    @property
    def claude_key(self) -> str:
        return self.anthropic_api_key.strip() or self.claude_api_key.strip()

    @property
    def claude_model(self) -> str:
        return self.anthropic_model_name.strip() or self.anthropic_model.strip()

    @property
    def display_names(self) -> List[str]:
        return self._pad(self._split_csv(self.sender_display_names), len(self.account_emails), "")

    @property
    def daily_limits(self) -> List[int]:
        limits: list[int] = []
        for item in self._split_csv(self.sender_daily_limits):
            try:
                limits.append(max(1, int(item)))
            except ValueError:
                limits.append(400)
        return [int(item) for item in self._pad(limits, len(self.account_emails), 400)]

    @property
    def smtp_settings(self) -> list[dict]:
        count = len(self.account_emails)
        hosts = self._pad(self._split_csv(self.smtp_hosts), count, "")
        ports = self._pad(self._split_csv(self.smtp_ports), count, "587")
        usernames = self._pad(self._split_csv(self.smtp_usernames), count, "")
        passwords = self._pad(self._split_csv(self.smtp_passwords), count, "")
        return [
            {
                "host": hosts[index],
                "port": int(ports[index] or 587),
                "username": usernames[index],
                "password": passwords[index],
            }
            for index in range(count)
        ]

    @property
    def imap_settings(self) -> list[dict]:
        count = len(self.account_emails)
        hosts = self._pad(self._split_csv(self.imap_hosts), count, "")
        ports = self._pad(self._split_csv(self.imap_ports), count, "993")
        usernames = self._pad(self._split_csv(self.imap_usernames), count, "")
        passwords = self._pad(self._split_csv(self.imap_passwords), count, "")
        return [
            {
                "host": hosts[index],
                "port": int(ports[index] or 993),
                "username": usernames[index],
                "password": passwords[index],
            }
            for index in range(count)
        ]

    @property
    def frontend_origins(self) -> List[str]:
        configured = self._split_csv(self.frontend_origin)
        defaults = [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
        ]
        return list(dict.fromkeys([*configured, *defaults]))

    @staticmethod
    def _pad(items: list, count: int, default):
        padded = list(items)
        if len(padded) < count:
            padded.extend([default] * (count - len(padded)))
        return padded[:count]

    @staticmethod
    def _split_csv(value: str) -> List[str]:
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
