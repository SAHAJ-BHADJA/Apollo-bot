from dataclasses import dataclass, field

from .apollo_client import ApolloAPIError, ApolloClient
from .config import Settings, get_settings
from .database import (
    create_apollo_account,
    get_apollo_account_key,
    get_state,
    list_accounts,
    mark_account_used,
    set_state,
    sync_accounts,
    update_account_status,
)


ROTATABLE_STATUSES = {"empty", "rate_limited", "failed"}


@dataclass
class AccountResult:
    account_index: int
    messages: list[str] = field(default_factory=list)


class AccountManager:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        sync_accounts(
            self.settings.api_keys,
            self.settings.account_emails,
            self.settings.email_credit_limits,
        )

    def _ensure_keys(self) -> None:
        if not any(account["status"] != "failed" for account in list_accounts()):
            raise ApolloAPIError("No Apollo API keys configured. Add APOLLO_API_KEYS to .env.", "failed")

    def _client(self, account_index: int) -> ApolloClient:
        self._ensure_keys()
        api_key = get_apollo_account_key(account_index)
        if not api_key:
            raise ApolloAPIError(f"Apollo account {account_index} is not configured.", "failed")
        return ApolloClient(api_key, self.settings)

    def accounts(self) -> list[dict]:
        sync_accounts(
            self.settings.api_keys,
            self.settings.account_emails,
            self.settings.email_credit_limits,
        )
        accounts = list_accounts()
        active_index = get_state("active_account_index")
        for account in accounts:
            index = account["account_index"]
            credit_limit = account.get("email_credit_limit")
            used = int(account.get("total_verified_emails_exported") or 0)
            account["email_credit_limit"] = credit_limit
            account["estimated_email_credits_remaining"] = (
                max(0, credit_limit - used) if credit_limit is not None else None
            )
            account["is_active"] = str(index) == active_index and account["status"] == "active"
        if accounts and not any(account["is_active"] for account in accounts):
            first_active = next((account for account in accounts if account["status"] == "active"), accounts[0])
            first_active["is_active"] = True
        return accounts

    def current_index(self, requested_index: int | None = None) -> int:
        self._ensure_keys()
        if requested_index is not None:
            if not any(account["account_index"] == requested_index for account in self.accounts()):
                raise ApolloAPIError(f"Apollo account {requested_index} is not configured.", "failed")
            return requested_index
        active = get_state("active_account_index")
        if active is not None:
            try:
                index = int(active)
                account = next((item for item in self.accounts() if item["account_index"] == index), None)
                if account and account["status"] == "active":
                    return index
            except ValueError:
                pass
        return self.next_available_index(exclude=[])

    def next_available_index(self, exclude: list[int]) -> int:
        self._ensure_keys()
        excluded = set(exclude)
        accounts = self.accounts()
        for account in accounts:
            if account["account_index"] not in excluded and account["status"] == "active":
                return account["account_index"]
        for account in accounts:
            if account["account_index"] not in excluded:
                return account["account_index"]
        raise ApolloAPIError("All configured Apollo accounts appear empty, limited, or failed.", "empty")

    def set_active(self, account_index: int) -> None:
        set_state("active_account_index", str(account_index))

    def add_account(
        self,
        account_email: str,
        api_key: str,
        email_credit_limit: int | None = None,
        notes: str = "",
    ) -> dict:
        account = create_apollo_account(
            account_email.strip(),
            api_key.strip(),
            email_credit_limit,
            notes.strip(),
        )
        self.set_active(account["account_index"])
        return account

    def handle_account_error(self, account_index: int, error: ApolloAPIError) -> str:
        status = error.account_status if error.account_status in ROTATABLE_STATUSES else "failed"
        update_account_status(account_index, status, error.message)
        return error.message

    def search_people(
        self,
        company_name: str,
        company_domain: str,
        titles: list[str],
        locations: list[str],
        target_count: int,
        requested_index: int | None = None,
    ) -> tuple[list[dict], AccountResult]:
        tried: list[int] = []
        messages: list[str] = []
        allow_rotation = requested_index is None
        account_index = self.current_index(requested_index)

        while True:
            try:
                people = self._client(account_index).search_people(
                    company_name, company_domain, titles, locations, target_count
                )
                mark_account_used(account_index, "preview")
                update_account_status(account_index, "active", "")
                self.set_active(account_index)
                return people, AccountResult(account_index=account_index, messages=messages)
            except ApolloAPIError as error:
                failure = self.handle_account_error(account_index, error)
                tried.append(account_index)
                messages.append(f"Apollo account {account_index} failed: {failure}")
                if not allow_rotation:
                    raise ApolloAPIError(
                        f"Selected Apollo account {account_index} failed: {failure}",
                        error.account_status,
                        error.status_code,
                    ) from error
                try:
                    next_index = self.next_available_index(tried)
                except ApolloAPIError as final_error:
                    detail = " ".join(messages)
                    raise ApolloAPIError(
                        f"{final_error.message} {detail}".strip(),
                        final_error.account_status,
                        final_error.status_code,
                    ) from error
                messages.append(
                    f"Apollo account {account_index} appears empty or limited. Switching to account {next_index}."
                )
                account_index = next_index

    def reveal_emails(
        self,
        people: list[dict],
        requested_index: int | None = None,
    ) -> tuple[list[dict], AccountResult]:
        tried: list[int] = []
        messages: list[str] = []
        allow_rotation = requested_index is None
        account_index = self.current_index(requested_index)

        while True:
            try:
                enriched = self._client(account_index).reveal_emails(people)
                mark_account_used(account_index, "email_reveal")
                update_account_status(account_index, "active", "")
                self.set_active(account_index)
                return enriched, AccountResult(account_index=account_index, messages=messages)
            except ApolloAPIError as error:
                failure = self.handle_account_error(account_index, error)
                tried.append(account_index)
                messages.append(f"Apollo account {account_index} failed: {failure}")
                if not allow_rotation:
                    raise ApolloAPIError(
                        f"Selected Apollo account {account_index} failed: {failure}",
                        error.account_status,
                        error.status_code,
                    ) from error
                try:
                    next_index = self.next_available_index(tried)
                except ApolloAPIError as final_error:
                    detail = " ".join(messages)
                    raise ApolloAPIError(
                        f"{final_error.message} {detail}".strip(),
                        final_error.account_status,
                        final_error.status_code,
                    ) from error
                messages.append(
                    f"Apollo account {account_index} appears empty or limited. Switching to account {next_index}."
                )
                account_index = next_index
