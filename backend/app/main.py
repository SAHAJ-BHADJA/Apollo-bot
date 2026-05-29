import json
import secrets
import threading
import time

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from .account_manager import AccountManager
from .apollo_client import ApolloAPIError
from .config import get_settings
from .csv_service import csv_bytes, csv_filename, verified_email_rows
from .database import (
    create_search_run,
    init_db,
    list_sender_accounts,
    sync_sender_accounts,
    update_latest_search_verified_count,
)
from .gmail_client import GmailAuthError, GmailClient
from .models import (
    CampaignCreateRequest,
    CampaignSettingsRequest,
    DownloadCsvRequest,
    GenerateDraftsRequest,
    HealthResponse,
    MessageUpdateRequest,
    PreviewPeopleRequest,
    PreviewPeopleResponse,
    RecipientUpdateRequest,
    SenderUpdateRequest,
    SequenceTemplatesRequest,
    TemplateUpdateRequest,
)
from .scheduler_service import SchedulerService
from .sequence_service import SequenceService


settings = get_settings()
app = FastAPI(title="Apollo Lead Extractor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):51\d{2}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Verified-Email-Count", "X-Account-Used", "X-Messages"],
)


PUBLIC_PATH_PREFIXES = (
    "/health",
    "/gmail/oauth/start",
    "/gmail/oauth/callback",
    "/track/open/",
)


@app.middleware("http")
async def require_app_token(request: Request, call_next):
    if request.method == "OPTIONS" or not settings.app_api_token.strip():
        return await call_next(request)
    if request.url.path.startswith(PUBLIC_PATH_PREFIXES):
        return await call_next(request)
    supplied = request.headers.get("X-App-Token", "")
    if not secrets.compare_digest(supplied, settings.app_api_token):
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid app token."})
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    init_db()
    AccountManager(settings)
    sync_sender_accounts(settings.account_emails, settings.display_names, settings.daily_limits)
    start_scheduler_loop()


def start_scheduler_loop() -> None:
    if getattr(app.state, "scheduler_started", False):
        return
    app.state.scheduler_started = True

    def loop() -> None:
        scheduler = SchedulerService(settings)
        while True:
            try:
                scheduler.tick(limit=25)
            except Exception:
                pass
            time.sleep(60)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def api_error(error: ApolloAPIError) -> HTTPException:
    status_code = 400
    if "No Apollo API keys" in error.message:
        status_code = 503
    elif error.status_code in {401, 403, 429}:
        status_code = error.status_code
    return HTTPException(status_code=status_code, detail=error.message)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", accounts_configured=len(settings.api_keys))


@app.get("/accounts")
def accounts() -> list[dict]:
    return AccountManager(settings).accounts()


@app.get("/senders")
def sender_accounts() -> list[dict]:
    sync_sender_accounts(settings.account_emails, settings.display_names, settings.daily_limits)
    return list_sender_accounts()


@app.get("/gmail/status")
def gmail_status() -> dict:
    return GmailClient(settings).status()


@app.get("/gmail/oauth/start")
def gmail_oauth_start() -> RedirectResponse:
    try:
        return RedirectResponse(GmailClient(settings).auth_url())
    except GmailAuthError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/gmail/oauth/callback")
def gmail_oauth_callback(code: str | None = None, error: str | None = None) -> HTMLResponse:
    if error:
        raise HTTPException(status_code=400, detail=error)
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code.")
    try:
        status = GmailClient(settings).exchange_code(code)
        return HTMLResponse(
            f"""
            <html>
              <body style="font-family: system-ui; padding: 32px;">
                <h1>Gmail connected</h1>
                <p>{status['email']} is connected. You can close this tab and return to the app.</p>
              </body>
            </html>
            """
        )
    except GmailAuthError as oauth_error:
        raise HTTPException(status_code=400, detail=str(oauth_error)) from oauth_error


@app.patch("/senders/{sender_id}")
def update_sender(sender_id: int, payload: SenderUpdateRequest) -> list[dict]:
    from .database import get_db, utc_now

    with get_db() as conn:
        conn.execute(
            "UPDATE sender_accounts SET daily_limit = ?, updated_at = ? WHERE id = ?",
            (payload.daily_limit, utc_now(), sender_id),
        )
    return sender_accounts()


@app.post("/preview-people", response_model=PreviewPeopleResponse)
def preview_people(payload: PreviewPeopleRequest) -> PreviewPeopleResponse:
    if not payload.company_name.strip() and not payload.company_domain.strip():
        raise HTTPException(status_code=422, detail="Company name or company domain is required.")

    try:
        manager = AccountManager(settings)
        people, result = manager.search_people(
            payload.company_name,
            payload.company_domain,
            payload.titles,
            payload.locations,
            payload.target_count,
            payload.apollo_account_index,
        )
        create_search_run(
            payload.company_name,
            payload.company_domain,
            payload.titles,
            payload.target_count,
            len(people),
            result.account_index,
        )
        return PreviewPeopleResponse(
            people=people,
            count=len(people),
            account_used=result.account_index,
            messages=result.messages,
        )
    except ApolloAPIError as error:
        raise api_error(error) from error


@app.post("/download-csv")
def download_csv(payload: DownloadCsvRequest) -> Response:
    if not payload.people:
        raise HTTPException(status_code=422, detail="Preview people before downloading CSV.")

    people = [person.model_dump() for person in payload.people]
    try:
        manager = AccountManager(settings)
        enriched, result = manager.reveal_emails(people, payload.apollo_account_index)
        rows = verified_email_rows(enriched, people)
        company = next((person.get("company") for person in people if person.get("company")), "company")
        filename = csv_filename(company)
        update_latest_search_verified_count(
            [person.get("apollo_person_id", "") for person in people],
            len(rows),
            result.account_index,
        )
        return Response(
            content=csv_bytes(rows),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Verified-Email-Count": str(len(rows)),
                "X-Account-Used": str(result.account_index),
                "X-Messages": json.dumps(result.messages),
            },
        )
    except ApolloAPIError as error:
        raise api_error(error) from error


@app.post("/campaigns/from-preview")
def create_campaign_from_preview(payload: CampaignCreateRequest) -> dict:
    if not payload.people:
        raise HTTPException(status_code=422, detail="Preview people before creating a sequence.")
    people = [person.model_dump() for person in payload.people]
    try:
        manager = AccountManager(settings)
        enriched, result = manager.reveal_emails(people, payload.apollo_account_index)
        campaign = SequenceService(settings).create_campaign_from_enriched(payload.name, people, enriched)
        campaign["account_used"] = result.account_index
        campaign["messages_from_accounts"] = result.messages
        campaign["audience_csv_download_url"] = f"/campaigns/{campaign['campaign']['id']}/audience-csv"
        return campaign
    except ApolloAPIError as error:
        raise api_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/campaigns")
def campaigns() -> list[dict]:
    return SequenceService(settings).list_campaigns()


@app.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: int) -> dict:
    try:
        return SequenceService(settings).get_campaign(campaign_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: int) -> dict:
    try:
        return SequenceService(settings).pause_campaign(campaign_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: int) -> dict:
    try:
        return SequenceService(settings).resume_campaign(campaign_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/cancel-remaining")
def cancel_campaign_remaining(campaign_id: int) -> dict:
    try:
        return SequenceService(settings).cancel_remaining(campaign_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/reschedule-overdue")
def reschedule_campaign_overdue(campaign_id: int) -> dict:
    try:
        return SequenceService(settings).reschedule_overdue(campaign_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/campaigns/{campaign_id}/audience-csv")
def campaign_audience_csv(campaign_id: int) -> FileResponse:
    try:
        path, filename = SequenceService(settings).get_audience_csv_file(campaign_id)
        return FileResponse(path, media_type="text/csv; charset=utf-8", filename=filename)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.patch("/campaigns/{campaign_id}/recipients/{recipient_id}")
def update_recipient(campaign_id: int, recipient_id: int, payload: RecipientUpdateRequest) -> dict:
    try:
        return SequenceService(settings).update_recipient_status(
            campaign_id, recipient_id, payload.status or "ready"
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/generate-drafts")
def generate_drafts(campaign_id: int, payload: GenerateDraftsRequest) -> dict:
    if not payload.job_description.strip():
        raise HTTPException(status_code=422, detail="Paste a job description before generating emails.")
    try:
        return SequenceService(settings).generate_drafts(
            campaign_id, payload.job_description, payload.instructions
        )
    except ValueError as error:
        error_text = str(error).lower()
        if "already running" in error_text:
            status_code = 409
        elif "claude" in error_text:
            status_code = 502
        else:
            status_code = 404
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@app.patch("/campaigns/{campaign_id}/messages/{message_id}")
def update_message(campaign_id: int, message_id: int, payload: MessageUpdateRequest) -> dict:
    try:
        return SequenceService(settings).update_message(
            campaign_id, message_id, payload.subject, payload.body_text
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.patch("/campaigns/{campaign_id}/templates/{step_number}")
def update_template(campaign_id: int, step_number: int, payload: TemplateUpdateRequest) -> dict:
    try:
        return SequenceService(settings).update_template(
            campaign_id,
            step_number,
            payload.subject_template,
            payload.body_template,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.put("/campaigns/{campaign_id}/templates")
def save_sequence_templates(campaign_id: int, payload: SequenceTemplatesRequest) -> dict:
    try:
        return SequenceService(settings).save_sequence_templates(
            campaign_id,
            payload.subject_template,
            payload.main_body_template,
            payload.followup_1_body_template,
            payload.followup_2_body_template,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/attachments")
async def upload_campaign_attachment(campaign_id: int, file: UploadFile = File(...)) -> dict:
    try:
        content = await file.read()
        return SequenceService(settings).add_attachment(
            campaign_id,
            file.filename or "attachment",
            file.content_type or "application/octet-stream",
            content,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.delete("/campaigns/{campaign_id}/attachments/{attachment_id}")
def delete_campaign_attachment(campaign_id: int, attachment_id: int) -> dict:
    try:
        return SequenceService(settings).delete_attachment(campaign_id, attachment_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.patch("/campaigns/{campaign_id}/settings")
def update_settings(campaign_id: int, payload: CampaignSettingsRequest) -> dict:
    try:
        return SequenceService(settings).update_settings(campaign_id, payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/campaigns/{campaign_id}/launch")
def launch_campaign(campaign_id: int, payload: CampaignSettingsRequest) -> dict:
    try:
        service = SequenceService(settings)
        service.update_settings(campaign_id, payload.model_dump())
        return service.launch_campaign(campaign_id, payload.sender_account_indexes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/scheduler/tick")
def scheduler_tick() -> dict:
    return SchedulerService(settings).tick(limit=50)


@app.post("/scheduler/check-replies")
def scheduler_check_replies() -> dict:
    return SchedulerService(settings).check_replies()


@app.get("/track/open/{token}.png")
def track_open(token: str) -> Response:
    SequenceService(settings).mark_opened(token)
    pixel = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return Response(content=pixel, media_type="image/png")
