from pydantic import BaseModel, Field


class PersonPreview(BaseModel):
    apollo_person_id: str
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    company: str = ""
    linkedin_url: str = ""


class PreviewPeopleRequest(BaseModel):
    company_name: str = ""
    company_domain: str = ""
    titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=lambda: ["United States"])
    target_count: int = Field(default=30, ge=1, le=5000)
    apollo_account_index: int | None = None


class PreviewPeopleResponse(BaseModel):
    people: list[PersonPreview]
    count: int
    account_used: int
    messages: list[str] = Field(default_factory=list)


class DownloadCsvRequest(BaseModel):
    people: list[PersonPreview]
    apollo_account_index: int | None = None


class HealthResponse(BaseModel):
    status: str
    accounts_configured: int


class ApolloAccountCreateRequest(BaseModel):
    account_email: str = Field(min_length=3, max_length=255)
    api_key: str = Field(min_length=6, max_length=500)
    email_credit_limit: int | None = Field(default=None, ge=0, le=100000)
    notes: str = Field(default="", max_length=500)


class CampaignCreateRequest(BaseModel):
    people: list[PersonPreview]
    apollo_account_index: int | None = None
    name: str = "Apollo Outreach Sequence"


class RecipientUpdateRequest(BaseModel):
    status: str | None = None


class GenerateDraftsRequest(BaseModel):
    job_description: str
    instructions: str = ""


class MessageUpdateRequest(BaseModel):
    subject: str
    body_text: str


class TemplateUpdateRequest(BaseModel):
    subject_template: str
    body_template: str


class SequenceTemplatesRequest(BaseModel):
    subject_template: str = ""
    main_body_template: str = ""
    followup_1_body_template: str = ""
    followup_2_body_template: str = ""


class CampaignSettingsRequest(BaseModel):
    sender_account_indexes: list[int] = Field(default_factory=list)
    timezone: str = "America/Los_Angeles"
    opening_days: list[str] = Field(
        default_factory=lambda: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    )
    opening_start_time: str = "09:00"
    opening_end_time: str = "14:00"
    followup_days: list[str] = Field(default_factory=lambda: ["Tuesday", "Thursday"])
    followup_start_time: str = "09:00"
    followup_end_time: str = "14:00"
    min_followup_gap_days: int = Field(default=3, ge=1, le=30)
    track_opens: bool = True
    stop_on_reply: bool = True


class SenderUpdateRequest(BaseModel):
    daily_limit: int = Field(default=400, ge=1, le=2000)
