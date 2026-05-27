# Apollo Lead Extractor

Local full-stack app for previewing Apollo people search results without revealing emails, exporting verified emails, and turning the verified audience into a local email sequence.

## Stack

- Frontend: React + Vite
- Backend: FastAPI
- Storage: SQLite
- CSV generation: Python `csv`

## Setup Backend

```bash
cd lead-extractor/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The backend reads `.env` from either the workspace root or `lead-extractor/backend/.env`.

## Create `.env`

Create a `.env` file with one or more Apollo API keys:

```env
APOLLO_API_KEYS=key1,key2,key3
APOLLO_ACCOUNT_EMAILS=account1@example.com,account2@example.com,account3@example.com
APOLLO_EMAIL_CREDIT_LIMITS=100,100,100
APP_API_TOKEN=generate_a_long_random_value
```

Multiple keys are comma-separated. The app stores only masked keys in SQLite and never sends full keys to the frontend.
Account emails are optional labels for the local dropdown. Put them in the same order as the keys.
Email credit limits are optional local estimates. If you add them, the UI shows estimated remaining email credits by subtracting verified emails exported by this app. Apollo credits used outside this app are not included in that estimate.

`APP_API_TOKEN` protects private backend endpoints when the backend is hosted. Use the same value in the frontend as `VITE_APP_API_TOKEN`.

## Claude + Email Sequence Setup

Claude is used only by the backend:

```env
ANTHROPIC_API_KEY=your_claude_key
ANTHROPIC_MODEL=claude-sonnet-4-5
MASTER_RESUME_DATA_PATH=e:\Projects\Random\master_resume_data.json
COLD_EMAIL_PROOF_BANK_PATH=e:\Projects\Random\email bot\shareable\COLD_EMAIL_PROOF_BANK.md
COLD_EMAIL_PLAYBOOK_PATH=e:\Projects\Random\email bot\shareable\COLD_EMAIL_PLAYBOOK.md
```

The draft suggestion button reads those local context files, the pasted job description, and campaign recipients. It uses the proof bank/playbook to suggest a main email plus two follow-ups. The final editable templates still live in the Content step, and only `{{first_name}}` / `{{last_name}}` are supported as merge variables.

For Gmail or Google Workspace accounts, use Google OAuth and the Gmail API. The connected account sends emails and reads replies so future follow-ups are skipped.

```env
SENDER_DISPLAY_NAMES=Your Name,Your Name
SENDER_DAILY_LIMITS=400,400
GOOGLE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_CLIENT_SECRET=your_google_oauth_client_secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/gmail/oauth/callback
GMAIL_SENDER_EMAIL=account1@example.com
```

Every sender defaults to `400` total emails per day, including opening emails and follow-ups. You can edit per account with `SENDER_DAILY_LIMITS`.

Connect Gmail by starting the backend, opening:

```text
http://127.0.0.1:8000/gmail/oauth/start
```

Open tracking uses a hidden 1x1 pixel. A public URL is required for recipients outside your machine:

```env
TRACKING_BASE_URL=https://your-public-tracking-url.example.com
```

Do not use `localhost` for real open tracking; recipients cannot reach it.

## Default Sequence Rules

Campaigns are created with these defaults:

- Opening emails: Monday-Friday, 9:00 AM-2:00 PM Pacific time
- Follow-ups: Tuesday and Thursday, 9:00 AM-2:00 PM Pacific time
- Follow-up gap: more than 3 days since the previous email
- Stop on reply: enabled
- Open tracking: enabled
- Daily sender cap: 400 total emails per sender account

If the time window closes or the daily limit is hit, unsent messages remain scheduled and are moved to the next valid sending window.

If a recipient replies, all remaining unsent follow-ups for that recipient are marked `skipped`.

If Gmail OAuth is not connected, the sender is paused as `Paused: inbox disconnected`; the app will not send because it cannot safely detect replies.

## Campaign Attachments

The Content step supports file uploads up to 10MB per file. Uploaded campaign attachments are stored locally under:

```text
backend/uploads/<campaign_id>/
```

Every uploaded attachment is included on every outgoing email in that campaign. Use this for files you truly want recipients to receive, such as a resume PDF. Do not upload internal instruction files unless you want recipients to receive them too; paste those instructions into the Claude instructions box instead.

## Saved Audience CSVs

When you click **Create Email Sequence**, the app reveals verified emails once, creates the campaign, saves the finalized audience locally, and downloads that CSV immediately. This lets you come back later without calling Apollo again for the same audience.

Saved sequence audience CSVs live under:

```text
backend/exports/
```

The Audience step also has **Download audience CSV** to download the saved campaign audience again.

## Run Backend

```bash
cd lead-extractor/backend
.venv\Scripts\activate
uvicorn app.main:app --reload
```

Backend URL:

```text
http://127.0.0.1:8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

## Setup Frontend

```bash
cd lead-extractor/frontend
npm install
npm run dev
```

Frontend URL:

```text
http://127.0.0.1:5173
```

If your backend runs somewhere else, create `frontend/.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_APP_API_TOKEN=the_same_value_as_backend_APP_API_TOKEN
```

## Deploy for Personal Use

Free personal hosting:

- Frontend: Vercel
- Backend: Render Free web service
- Database: Neon Free Postgres
- Keep-alive: cron-job.org pinging `/health` every 5 minutes

This repo includes:

- `render.yaml` for the FastAPI backend
- `frontend/vercel.json` for the Vite frontend
- `backend/.env.example`
- `frontend/.env.example`

### Neon Postgres

Create a free Neon project and copy the pooled connection string. It should look like:

```text
postgresql://user:password@host/dbname?sslmode=require
```

Use that as `DATABASE_URL` on Render.

If you want to preserve your local SQLite data, run this migration from `backend` after setting `DATABASE_URL` locally:

```bash
python -c "from app.database import init_db; init_db()"
python scripts/migrate_sqlite_to_postgres.py
```

The migration copies rows from your local `apollo_leads.sqlite3` into Neon and preserves ids where possible.

### Render Backend

Create a Render web service from the repo using `render.yaml`. The backend service should use:

```text
Root directory: backend
Build command: pip install -r requirements.txt
Start command: python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set these Render environment variables:

```env
DATABASE_URL=your_neon_postgres_connection_string
UPLOAD_DIR=/tmp/uploads
EXPORT_DIR=/tmp/exports
APP_API_TOKEN=your_long_random_token
APOLLO_API_KEYS=...
APOLLO_ACCOUNT_EMAILS=...
ANTHROPIC_API_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://your-render-service.onrender.com/gmail/oauth/callback
GMAIL_SENDER_EMAIL=bhadja@usc.edu
SENDER_DISPLAY_NAMES=Sahaj Bhadja
SENDER_DAILY_LIMITS=400
TRACKING_BASE_URL=https://your-render-service.onrender.com
FRONTEND_ORIGIN=https://your-vercel-app.vercel.app
```

Render Free can sleep. Use cron-job.org to ping this endpoint every 5 minutes:

```text
https://your-render-service.onrender.com/health
```

Because scheduled messages live in Neon, Render restarts do not wipe campaign state. If Render sleeps anyway, messages may send late after it wakes up.

Avoid attachments on the free hosted setup unless you add object storage. Render Free local files under `/tmp` are ephemeral.

### Google OAuth Update

In Google Cloud, add the hosted redirect URI:

```text
https://your-render-service.onrender.com/gmail/oauth/callback
```

After deployment, reconnect Gmail by opening:

```text
https://your-render-service.onrender.com/gmail/oauth/start
```

### Vercel Frontend

Deploy the `frontend` folder to Vercel and set:

```env
VITE_API_BASE_URL=https://your-render-service.onrender.com
VITE_APP_API_TOKEN=the_same_value_as_backend_APP_API_TOKEN
```

After Vercel gives you the frontend URL, update Render `FRONTEND_ORIGIN` to that exact URL and redeploy/restart the backend.

## How Preview Works

Preview uses Apollo People API Search:

```text
POST https://api.apollo.io/api/v1/mixed_people/api_search
```

Apollo documents this endpoint as not consuming credits and not returning email addresses. The app uses company domain when provided, selected titles when any are checked, United States person location, verified email status, similar-title matching, and pagination until it reaches the max people value or Apollo returns no more results. If no titles are checked, the app searches all people at the company.

The preview table intentionally shows only:

- First Name
- Last Name
- Title
- Company
- LinkedIn

The Apollo person ID is returned to the frontend for later enrichment but is hidden from the table.

## How CSV Download Works

CSV download is the only step that reveals emails. The backend calls Apollo Bulk People Enrichment in batches of up to 10:

```text
POST https://api.apollo.io/api/v1/people/bulk_match
```

The CSV includes only rows where Apollo returns a verified email status.

CSV columns are exactly:

```text
First Name,Last Name,Email
```

Duplicates are not removed.

## Apollo Account Rotation

`GET /accounts` returns masked account status:

- `active`
- `empty`
- `rate_limited`
- `failed`

If an account appears empty, rate-limited, forbidden, or otherwise failed during preview or CSV generation, the backend marks it in SQLite and switches to the next usable account. The frontend displays messages such as:

```text
Apollo account X appears empty or limited. Switching to account Y.
```

You can also manually choose an account in the frontend account selector.

## API Endpoints

```text
GET  /health
GET  /accounts
POST /preview-people
POST /download-csv
POST /campaigns/from-preview
GET  /campaigns/{campaign_id}/audience-csv
```

## SQLite

The backend creates `backend/apollo_leads.sqlite3` automatically with:

- `apollo_accounts`
- `search_runs`
- `app_state`

The database tracks masked key status, last use, request counts, and search/export counts.

## Troubleshooting

Missing API keys:

- Confirm `.env` contains `APOLLO_API_KEYS=...`
- Restart the backend after changing `.env`

Invalid or limited Apollo key:

- Check the frontend status box and `GET /accounts`
- Add another key to `APOLLO_API_KEYS`

No preview results:

- Prefer a clean company domain like `openai.com`
- Try fewer or broader titles
- Confirm your Apollo key is a master API key with People API access

CSV has fewer rows than previewed:

- The app exports only verified emails returned by Apollo enrichment
- If 40 people are previewed but only 27 verified emails are found, the CSV contains 27 rows

Frontend cannot reach backend:

- Confirm FastAPI is running on `http://127.0.0.1:8000`
- If using a different backend URL, set `VITE_API_BASE_URL`
