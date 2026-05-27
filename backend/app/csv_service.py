import csv
import io
import re
from datetime import datetime, timezone


def _person_id(person: dict) -> str:
    contact = person.get("contact") or {}
    return str(
        person.get("id")
        or person.get("person_id")
        or person.get("apollo_person_id")
        or contact.get("id")
        or contact.get("person_id")
        or ""
    )


def _best_name(enriched_value: str, preview_value: str) -> str:
    enriched_value = enriched_value or ""
    preview_value = preview_value or ""
    if len(enriched_value.strip()) <= 1 and len(preview_value.strip()) > 1:
        return preview_value
    return enriched_value or preview_value


CSV_FIELDNAMES = ["First Name", "Last Name", "Email"]
AUDIENCE_FIELDNAMES = [
    "First Name",
    "Last Name",
    "Email",
    "Title",
    "Company",
    "LinkedIn",
    "Apollo Person ID",
]


def verified_audience_rows(
    enriched_people: list[dict], preview_people: list[dict] | None = None
) -> list[dict[str, str]]:
    preview_by_id = {
        str(person.get("apollo_person_id")): person
        for person in preview_people or []
        if person.get("apollo_person_id")
    }
    rows: list[dict[str, str]] = []
    for person in enriched_people:
        preview_person = preview_by_id.get(_person_id(person), {})
        email = person.get("email") or person.get("contact", {}).get("email")
        status = (person.get("email_status") or person.get("email_true_status") or "").lower()
        if email and "verified" in status:
            first_name = _best_name(
                person.get("first_name") or "", preview_person.get("first_name") or ""
            )
            last_name = _best_name(
                person.get("last_name") or "", preview_person.get("last_name") or ""
            )
            rows.append(
                {
                    "First Name": first_name,
                    "Last Name": last_name,
                    "Email": email,
                    "Title": preview_person.get("title") or person.get("title") or "",
                    "Company": preview_person.get("company") or person.get("organization", {}).get("name") or "",
                    "LinkedIn": preview_person.get("linkedin_url") or person.get("linkedin_url") or "",
                    "Apollo Person ID": _person_id(person) or preview_person.get("apollo_person_id") or "",
                }
            )
    return rows


def verified_email_rows(
    enriched_people: list[dict], preview_people: list[dict] | None = None
) -> list[dict[str, str]]:
    return [
        {field: row.get(field, "") for field in CSV_FIELDNAMES}
        for row in verified_audience_rows(enriched_people, preview_people)
    ]


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def audience_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=AUDIENCE_FIELDNAMES)
    writer.writeheader()
    writer.writerows([{field: row.get(field, "") for field in AUDIENCE_FIELDNAMES} for row in rows])
    return stream.getvalue().encode("utf-8-sig")


def csv_filename(company: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", company.strip()).strip("_").lower() or "company"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"apollo_leads_{cleaned}_{timestamp}.csv"


def audience_csv_filename(company: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", company.strip()).strip("_").lower() or "company"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"apollo_audience_{cleaned}_{timestamp}.csv"
