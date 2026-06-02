import time
from typing import Any

import httpx

from .config import Settings


class ApolloAPIError(Exception):
    def __init__(self, message: str, account_status: str = "failed", status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.account_status = account_status
        self.status_code = status_code


class ApolloClient:
    def __init__(self, api_key: str, settings: Settings):
        self.api_key = api_key
        self.settings = settings
        self.base_url = settings.apollo_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": self.api_key,
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _classify_error(self, response: httpx.Response) -> ApolloAPIError:
        body = response.text.lower()
        if response.status_code == 401 or "invalid api key" in body or "unauthorized" in body:
            return ApolloAPIError("Apollo rejected this API key.", "failed", response.status_code)
        if response.status_code == 403:
            if "api_inaccessible" in body or "not accessible with this api_key on a free plan" in body:
                return ApolloAPIError(
                    "Apollo People Search API is not accessible with this API key on a free plan.",
                    "empty",
                    response.status_code,
                )
            if "credit" in body or "plan" in body or "limit" in body:
                return ApolloAPIError("Apollo account has no credits or is plan-limited.", "empty", response.status_code)
            return ApolloAPIError("Apollo account is forbidden for this endpoint.", "failed", response.status_code)
        if response.status_code == 429 or "rate limit" in body or "too many requests" in body:
            return ApolloAPIError("Apollo account is currently rate limited.", "rate_limited", response.status_code)
        if "credit" in body or "insufficient" in body or "exceeded" in body:
            return ApolloAPIError("Apollo account has no credits or has hit an account limit.", "empty", response.status_code)
        return ApolloAPIError(f"Apollo API request failed with HTTP {response.status_code}.", "failed", response.status_code)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, Any]] | dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries):
            try:
                with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                    response = client.request(
                        method,
                        self._url(path),
                        headers=self._headers(),
                        params=params,
                        json=json,
                    )
                if response.status_code < 400:
                    return response.json()
                error = self._classify_error(response)
                if error.account_status in {"empty", "rate_limited", "failed"} and response.status_code < 500:
                    raise error
                last_error = error
            except httpx.TimeoutException as exc:
                last_error = ApolloAPIError(f"Apollo network timeout: {exc}", "rate_limited")
            except httpx.NetworkError as exc:
                last_error = ApolloAPIError(f"Apollo network failure: {exc}", "failed")

            if attempt < self.settings.max_retries - 1:
                time.sleep(2**attempt)

        if isinstance(last_error, ApolloAPIError):
            raise last_error
        raise ApolloAPIError("Apollo API request failed.", "failed")

    @staticmethod
    def _clean_domain(domain: str) -> str:
        cleaned = domain.strip().lower()
        cleaned = cleaned.removeprefix("https://").removeprefix("http://").removeprefix("www.")
        return cleaned.split("/")[0]

    @staticmethod
    def _normalize_person(raw: dict[str, Any]) -> dict[str, str]:
        organization = raw.get("organization") or {}
        company = raw.get("organization_name") or organization.get("name") or ""
        return {
            "apollo_person_id": str(raw.get("id") or raw.get("person_id") or ""),
            "first_name": raw.get("first_name") or "",
            "last_name": raw.get("last_name") or raw.get("last_name_obfuscated") or "",
            "title": raw.get("title") or "",
            "company": company,
            "linkedin_url": raw.get("linkedin_url") or "",
        }

    def search_people(
        self,
        company_name: str,
        company_domain: str,
        titles: list[str],
        locations: list[str],
        target_count: int,
    ) -> list[dict[str, str]]:
        people: list[dict[str, str]] = []
        page = 1
        per_page = min(100, max(1, target_count))
        domain = self._clean_domain(company_domain) if company_domain else ""

        while len(people) < target_count and page <= 500:
            params: list[tuple[str, Any]] = [
                ("page", page),
                ("per_page", per_page),
                ("include_similar_titles", "true"),
                ("contact_email_status[]", "verified"),
            ]
            for location in locations:
                if location.strip():
                    params.append(("person_locations[]", location.strip()))
            for title in titles:
                if title.strip():
                    params.append(("person_titles[]", title.strip()))
            if domain:
                params.append(("q_organization_domains_list[]", domain))
            elif company_name.strip():
                params.append(("q_keywords", company_name.strip()))

            data = self._request("POST", self.settings.apollo_people_search_path, params=params)
            raw_people = data.get("people") or data.get("contacts") or []
            if not raw_people:
                break
            for raw in raw_people:
                normalized = self._normalize_person(raw)
                if normalized["apollo_person_id"]:
                    people.append(normalized)
                if len(people) >= target_count:
                    break
            if len(raw_people) < per_page:
                break
            page += 1

        return people[:target_count]

    def reveal_email(self, person: dict[str, Any]) -> dict[str, Any] | None:
        person_id = person.get("apollo_person_id") or person.get("id")
        if not person_id:
            return None
        data = self._request(
            "POST",
            self.settings.apollo_people_match_path,
            params=[
                ("id", person_id),
                ("reveal_personal_emails", "false"),
                ("reveal_phone_number", "false"),
            ],
        )
        return data.get("person") or data.get("contact")

    def reveal_emails(self, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for start in range(0, len(people), 10):
            batch = people[start : start + 10]
            details = [
                {"id": person.get("apollo_person_id")}
                for person in batch
                if person.get("apollo_person_id")
            ]
            if not details:
                continue
            data = self._request(
                "POST",
                self.settings.apollo_bulk_match_path,
                params=[("reveal_personal_emails", "false"), ("reveal_phone_number", "false")],
                json={"details": details},
            )
            matches.extend(data.get("matches") or [])
        return matches
