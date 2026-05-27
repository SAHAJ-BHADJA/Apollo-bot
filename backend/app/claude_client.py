import json
import re
from typing import Any

import httpx

from .config import Settings, get_settings


class ClaudeGenerationError(ValueError):
    pass


OUTREACH_STYLE = """
You are an elite outreach strategist writing high reply rate cold outreach for a software engineering candidate.

Primary objective:
- Maximize opens and replies.
- Avoid spam/promotions inbox.
- Position Sahaj as technically relevant.
- Create a low-pressure conversation.
- Make the emails feel human, specific, and non-template.

Think like:
- a recruiter
- a hiring manager
- a senior engineer
- an outbound strategist
- a deliverability expert

Internal workflow before writing:
1. Infer what the company builds and what engineering problem the team is likely hiring to solve.
2. Analyze the JD for explicit skills, hidden expectations, ownership level, architecture complexity, and team pain points.
3. Analyze Sahaj's resume/master experience from the provided instructions and select only one primary signal, optionally one secondary signal.
4. Map company problem to Sahaj's strongest relevant signal. The email should feel like "I have worked on similar problems," not "here is my resume."
5. Optimize for deliverability.

Deliverability rules:
- No links.
- No attachments mentioned.
- No emojis.
- No excessive punctuation.
- No all caps.
- No long paragraphs.
- No resume dump.
- No spam trigger words.
- Avoid these phrases entirely: referral request, urgent, applying for, job application, opportunity.
- Avoid generic openings like "I came across your work" or "I wanted to reach out."
- Avoid "job description I pasted."

Subject lines:
- Natural and low pressure.
- Under 6 words.
- Good examples: "quick question", "backend systems question", "curious about your team", "infrastructure question".
- Bad examples: "referral request", "job application", "hiring inquiry", "urgent opportunity".

Email writing rules:
- Main email: 60 to 100 words.
- Follow-ups: 45 to 85 words.
- Maximum 3 short paragraphs.
- Conversational tone.
- No buzzwords.
- No corporate jargon.
- No fluff.
- One thoughtful question.
- Easy to reply to quickly.
- Sign as "Sahaj".

Template rules:
- Use {{first_name}} exactly in the greeting.
- You may use {{last_name}} if helpful.
- Do not use any other template variables.
- Do not use a literal recipient name.
- Keep the body reusable across all recipients at the same company.
- Return templates, not already-rendered emails.
"""

AUTO_EMAIL_SPEC_RULES = """
Use the attached/provided context files as source of truth:
1. master_resume_data.json: full resume data with bullets, metrics, context.
2. COLD_EMAIL_PROOF_BANK.md: proof lines with metrics and role mappings.
3. COLD_EMAIL_PLAYBOOK.md: email rules, templates, banned phrases.

Workflow:
1. Analyze the job description.
2. Analyze contacts and infer their type from title: recruiter, engineering_manager, engineer, alumni if obvious.
3. Use the playbook rules and proof bank.
4. Pick one proof line only.
5. Match proof to JD keywords and contact audience.
6. Write a reusable campaign template for this recipient group.

Email rules:
- Main Email must be 50-90 words.
- Follow-up 1 must be 40-60 words and add a new angle.
- Follow-up 2 must be 30-50 words and be a final note.
- Lead with a trigger for this company/team/role.
- One proof line only.
- End with a micro-ask they can answer quickly.
- No resume attachment mentioned in email 1.
- Simple subject line like "Question about [Company] [role]" or "Quick question about [Team]".

Banned phrases:
- directionally relevant
- my background is strongest in
- my closest match is
- the kind of work I want to keep doing
- your background stood out to me

Contact type angles:
- recruiter: ask about routing, eligibility, or process.
- engineering_manager: ask about team fit and ownership.
- engineer: ask for practical path advice.

Important:
- The app will send follow-ups as replies in the same thread using the main subject.
- Return only JSON. Do not return JavaScript spec syntax.
"""


class ClaudeClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def generate_campaign_sequence(
        self,
        company: str,
        job_description: str,
        instructions: str,
        contacts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        if not self.settings.claude_key:
            raise ClaudeGenerationError("Claude API key is missing. Add ANTHROPIC_API_KEY to .env.")

        context_files = self._context_files()
        prompt = {
            "company": company,
            "job_description": job_description,
            "user_instructions": instructions,
            "style_rules": OUTREACH_STYLE,
            "auto_email_spec_rules": AUTO_EMAIL_SPEC_RULES,
            "context_files": context_files,
            "contacts_to_email": self._contact_brief(contacts or []),
            "personalization_tokens": [
                "{{first_name}}",
                "{{last_name}}",
            ],
            "required_output": {
                "emails": [
                    {
                        "step_number": 1,
                        "step_name": "Main Email",
                        "subject": "natural subject under 6 words",
                        "body_text": "60-100 words, starts with Hi {{first_name}},",
                    },
                    {
                        "step_number": 2,
                        "step_name": "Follow-up 1",
                        "subject": "natural subject under 6 words",
                        "body_text": "45-85 words, starts with Hi {{first_name}},",
                    },
                    {
                        "step_number": 3,
                        "step_name": "Follow-up 2",
                        "subject": "natural subject under 6 words",
                        "body_text": "45-85 words, starts with Hi {{first_name}},",
                    },
                ]
            },
        }
        system = (
            "You draft recruiting outreach email sequences. "
            "Return only valid JSON with an emails array. "
            "No markdown fences. No commentary. No generic fallback copy."
        )
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.settings.claude_key,
                        "anthropic-version": self.settings.anthropic_version,
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.settings.claude_model,
                        "max_tokens": 3500,
                        "temperature": 0.55,
                        "system": system,
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "Generate one 3-step outreach sequence template for all recipients. "
                                    "Use {{first_name}} where the recipient name belongs. "
                                    "Use the resume, proof bank, and playbook context files in the payload. "
                                    "Use only one proof line from the proof bank. "
                                    "Do not ask for a referral. Create a low-pressure technical conversation. "
                                    "Return JSON only for this data:\n"
                                    f"{json.dumps(prompt, ensure_ascii=True)}"
                                ),
                            }
                        ],
                    },
                )
            if response.status_code >= 400:
                raise ClaudeGenerationError(
                    f"Claude API failed with HTTP {response.status_code}: {response.text[:500]}"
                )
            text = "".join(block.get("text", "") for block in response.json().get("content", []))
            parsed = self._parse_json(text)
            emails = parsed.get("emails", parsed if isinstance(parsed, list) else [])
            return self._normalize_sequence(emails)
        except ClaudeGenerationError:
            raise
        except Exception as error:
            raise ClaudeGenerationError(f"Claude generation failed: {error}") from error

    def generate_sequence(
        self,
        recipient: dict[str, Any],
        job_description: str,
        instructions: str,
    ) -> list[dict[str, str]]:
        company = recipient.get("company") or "the company"
        template = self.generate_campaign_sequence(company, job_description, instructions)
        return [render_template(item, recipient) for item in template]

    def _context_files(self) -> dict[str, str]:
        return {
            "master_resume_data_json": self._read_context_file(self.settings.master_resume_data_path),
            "cold_email_proof_bank_md": self._read_context_file(self.settings.cold_email_proof_bank_path),
            "cold_email_playbook_md": self._read_context_file(self.settings.cold_email_playbook_path),
        }

    @staticmethod
    def _read_context_file(path, max_chars: int = 90000) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            return ""
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[truncated]"
        return text

    @staticmethod
    def _contact_brief(contacts: list[dict[str, Any]], limit: int = 40) -> list[dict[str, str]]:
        return [
            {
                "first_name": contact.get("first_name", ""),
                "last_name": contact.get("last_name", ""),
                "title": contact.get("title", ""),
                "company": contact.get("company", ""),
                "inferred_type": infer_contact_type(contact.get("title", "")),
            }
            for contact in contacts[:limit]
        ]

    @staticmethod
    def _parse_json(text: str) -> Any:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ClaudeGenerationError(f"Claude returned non-JSON text: {text[:500]}")

    @staticmethod
    def _normalize_sequence(emails: list[dict]) -> list[dict[str, str]]:
        if len(emails) < 3:
            raise ClaudeGenerationError("Claude returned fewer than 3 emails.")
        normalized: list[dict[str, str]] = []
        names = ["Opening email", "Follow-up 1", "Follow-up 2"]
        for index in range(3):
            source = emails[index] if index < len(emails) else {}
            subject = str(source.get("subject") or "").strip()
            body_text = str(source.get("body_text") or source.get("body") or "").strip()
            if not subject or not body_text:
                raise ClaudeGenerationError("Claude returned an email with a missing subject or body.")
            normalized.append(
                {
                    "step_number": index + 1,
                    "step_name": str(source.get("step_name") or names[index]).replace(
                        "Opening email", "Main Email"
                    ),
                    "subject": subject,
                    "body_text": body_text,
                }
            )
        return normalized


def infer_contact_type(title: str) -> str:
    normalized = (title or "").lower()
    if any(term in normalized for term in ["recruit", "talent", "people", "hr"]):
        return "recruiter"
    if any(term in normalized for term in ["manager", "head", "director", "vp ", "chief", "lead"]):
        return "engineering_manager"
    if any(term in normalized for term in ["engineer", "developer", "architect", "staff", "principal"]):
        return "engineer"
    return "unknown"


def render_template(item: dict[str, str], recipient: dict[str, Any]) -> dict[str, str]:
    replacements = {
        "{{first_name}}": recipient.get("first_name") or "there",
        "{{last_name}}": recipient.get("last_name") or "",
    }
    rendered = dict(item)
    for token, value in replacements.items():
        rendered["subject"] = rendered["subject"].replace(token, value)
        rendered["body_text"] = rendered["body_text"].replace(token, value)
    return rendered
