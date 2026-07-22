[![Tests](https://github.com/p95max/docflow_api/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/p95max/docflow_api/actions/workflows/tests.yml)
[![Coverage](https://codecov.io/gh/p95max/docflow_api/branch/master/graph/badge.svg)](https://codecov.io/gh/p95max/docflow_api)


# DocsFlow

DocsFlow is a server-rendered FastAPI application for personal document
management. It uploads and processes documents asynchronously, extracts
structured fields, supports a per-user Knowledge Base, and creates encrypted
Google Drive recovery backups. It also turns validated document deadlines into
reviewable calendar suggestions and lets users maintain their own events.
FastAPI serves both the JSON API and the Bootstrap interface; there is no
separate frontend service or build step.

## Tech Stack

| Category | Technologies |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI |
| Database | PostgreSQL, SQLAlchemy 2, Alembic |
| Queue | Celery, Redis |
| PDF Extraction | PyMuPDF |
| OCR | Tesseract |
| Search / RAG | pgvector, OpenAI embeddings and responses |
| Testing | Pytest, optional Playwright |
| Infrastructure | Docker Compose v2 |

---

## Current Features

### Authentication

- User registration with email and password
- Password hashing with bcrypt
- JWT access token authentication
- Current user endpoint

### Document Upload

**Supported file types:**

- PDF: `application/pdf`
- JPEG: `image/jpeg`
- PNG: `image/png`

**Upload validation includes:**

- MIME type validation
- File signature validation
- Empty file rejection
- Maximum file size limit
- Per-user upload rate limiting
- Duplicate document detection by SHA-256 checksum
- Soft deletion: a deleted upload may be uploaded again
- Per-document optional personal note (up to 5,000 characters)

### Document Library

The Documents page supports pagination, sorting, a collapsible filter panel,
and search across filename and extracted text. Filters include document type,
status, upload/document/deadline dates, amount, and action-required state.
Documents and all associated API responses are isolated by owner.

### Processing Modes

Each uploaded document has one processing mode:

| Mode | Description |
|---|---|
| `standard` | Default processing |
| `confidential` | Guarantees no external AI or third-party API is used |

Standard mode performs local text extraction and then uses OpenAI for structured
extraction. Confidential mode remains fully local and never calls external AI.

### Asynchronous Processing

After upload, the API creates a `ProcessingJob` and sends it to Celery.

**Processing job statuses:** `pending` / `running` / `completed` / `failed`

**Document statuses:** `uploaded` / `processing` / `completed` / `failed`

Processing includes:

- Retry support
- Retry delay
- Soft and hard task time limits
- Error message persistence
- Manual reprocess endpoint for failed documents

### Local Text Extraction

| File type | Method |
|---|---|
| PDF | Text extracted from PDF text layer via PyMuPDF |
| JPG / PNG | Text extracted locally via Tesseract OCR |

Extracted text is stored in `documents.raw_text`.

Scanned PDF pages without a text layer use the local Tesseract OCR fallback.

### Manual Extraction Review

Completed standard documents have a review lifecycle:

| Status | Meaning |
|---|---|
| `draft` | AI extraction is ready for review |
| `corrected` | A user changed one or more extracted fields |
| `confirmed` | A user confirmed the current extraction |

Users can correct the amount, document date, document type, sender/vendor,
summary, currency, deadline and confidence score. Every effective field change
is stored as a separate immutable `AuditLog` row with the old and new value.
Personal notes are optional, private to the account, audited, and included in
recovery backups.

### Calendar

The calendar is a separate domain from `Document.deadline`: a document may have
multiple related events, and users may create events without a document.

- Month and agenda views with filters for event type, status, source, and document
- User-created all-day and timed events, including optional end dates/times and document links
- AI suggestions projected from validated temporal document data; `needs_review`
  candidates are not added automatically
- Confirmation and dismissal of AI suggestions; editing an AI event detaches it
  from later document reprocessing
- Owner-scoped JSON API for list, create, read, update, delete, confirm,
  complete, and cancel operations
- IANA user timezones; timed events are stored in UTC while date-only events
  remain dates. The web form rejects nonexistent and ambiguous DST local times.
- Calendar audit logs and optimistic locking through an event sequence number.
  Reminder creation and successful in-app delivery are also logged. Audit
  values redact token-like secrets and are bounded to 4 KB per old/new value.

Celery Beat runs the reminder scheduler every five minutes. It claims due
reminders safely, records retries with exponential backoff, and cancels unsent
reminders when an event changes to cancelled, completed, or deleted. Due
in-app reminders create an owner-scoped notification atomically with the
delivery status; the navbar shows the unread count and `/notifications` lets a
user mark one or all notifications as read. Email delivery and reminder
settings in the event form are not implemented yet.

Calendar events can also be downloaded as standard `.ics` files from their
detail page. The same format works with Google Calendar, Apple Calendar, and
Outlook without connecting DocsFlow to any calendar provider. In Settings, a
user can create a private subscription URL, regenerate it (which revokes the
old URL), or revoke it. Only an HMAC hash of this opaque feed token is stored;
the access JWT is never placed in a subscription URL.

Calendar writes are owner-scoped, CSRF-protected in the HTML interface, and
rate-limited per account (60 writes per minute by default). Event titles and
descriptions are normalized as plain text, length-bounded, and rendered with
Jinja auto-escaping; they are never treated as HTML.

### Signed File URLs

Document result responses include short-lived, signed URLs for inline preview
and attachment download. A preview token cannot be used to download a file,
and a download token cannot be used for preview. Tokens expire after
`DOCUMENT_PREVIEW_TOKEN_EXPIRE_MINUTES` (10 minutes by default).

### Web Interface

DocsFlow includes a custom server-rendered document workspace served by
FastAPI. Bootstrap provides the responsive grid and accessible base controls,
while DocsFlow's own CSS defines the navigation, forms, tables, cards and chat
interface. No separate frontend server or JavaScript build step is required.

| Page | Purpose |
|---|---|
| `/login` | Authenticate with email and password |
| `/register` | Create an account |
| `/settings` | Set the account timezone used for date and time display |
| `/documents` | List the current user's documents |
| `/documents/upload` | Upload a document and select confidential mode |
| `/documents/{id}` | Preview/download a file, edit extracted fields, add a note, confirm or delete |
| `/calendar` | Browse month or agenda views, filter events, and open event details |
| `/calendar/new` | Create an all-day or timed calendar event |
| `/knowledge` | Create conversations and ask concise questions about indexed documents |
| `/backups` | Create encrypted Google Drive recovery backups and restore document data |

The browser receives the short-lived access token in an HTTP-only cookie.
Bootstrap is pinned to version 5.3.8 and protected with the official SHA-384
Subresource Integrity hash. Deployments may still vendor it under
`app/frontend/assets/` if a network-independent interface is required.

Server-rendered forms retain normal HTML validation and add a small progressive
enhancement for invalid-field highlighting, loading labels, and double-submit
protection. The application remains usable when JavaScript is unavailable.

### AI usage indicator

For signed-in users, the navigation bar shows the currently most-used AI model
in the last 24 hours (or the configured Ask Documents model when there is no
usage) and the recorded token total against `OPENAI_DAILY_TOKEN_QUOTA`.
Selecting it opens an AI usage dialog with remaining tokens, a visual limit
indicator, recorded operations, per-model token usage, and the configured
models for extraction, Ask Documents and embeddings.

The display is an account-only view of persisted `openai_usage_logs` in the
rolling 24-hour window. The Redis-backed request quota is enforced separately,
so failed or blocked attempts may not appear as recorded model usage.

### Knowledge Base / RAG

The Knowledge Base indexes completed `standard` documents into page-aware
paragraph-aware chunks and answers questions only from the current user's
retrieved chunks. Questions are limited to one sentence and 300 characters.
Each answer retains a snapshot of its source filename, page, snippet, and
similarity score. `confidential` documents are never indexed or sent to OpenAI.
The planned local-only embedding and LLM architecture required before enabling
confidential RAG is documented in
[local confidential RAG design](docs/local-confidential-rag-design.md).

Set the following values in `.env`:

```bash
OPENAI_API_KEY=your-openai-api-key
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_RAG_MODEL=gpt-5.6-terra
OPENAI_RAG_REASONING_EFFORT=high
KNOWLEDGE_ENABLED=true
```

Set `KNOWLEDGE_ENABLED=false` to disable the complete Knowledge Base: its
navigation item becomes inactive, web/API access is rejected, new documents
are not queued for embeddings, and queued indexing work exits without calling
OpenAI. Existing conversations and indexes remain stored.

Manual reindexing is available in `/knowledge` for completed standard
documents. Apply the database migration before enabling the feature:

```powershell
python -m poetry run alembic upgrade head
```

### Recovery backups

Backups on `/backups` are encrypted recovery archives. Each archive is
encrypted with a per-user **Recovery Key** before upload to Google Drive. The
key is displayed once after it is generated; save it in a password manager or
another secure location.

Schema v3 archives save:

- document metadata, extracted text, structured AI results, and personal notes;
- calendar events and their links to documents;
- event reminders and in-app notifications.

They never save original PDF, JPG, or PNG files, document previews, OAuth
credentials, or Google Drive refresh tokens. A restored document therefore has
its extracted content and fields, but no preview or original-file download.

For the first backup, use this sequence:

1. Generate and securely save the Recovery Key.
2. Connect the Google Drive account that will store the archive.
3. Create the backup.

DocsFlow also creates an automatic recovery archive once a week, by default on
Sunday at 03:00 Berlin time. It keeps the five newest completed manual archives
and the five newest completed automatic archives separately. When a sixth
archive of either type completes, the oldest archive of that same type is
deleted from DocsFlow and Google Drive.

Google Drive access does not replace the Recovery Key. The UI shows a newly
generated key only once, while DocsFlow keeps a copy encrypted with
`BACKUP_MASTER_KEY` so background backups and JSON downloads can use it. If the
database or server master key is lost, only the user's separately saved Recovery
Key can restore the archive. Anyone who obtains both the encrypted archive and
its Recovery Key can read the backed-up document data, so keep them separately
and securely. A compromise of both the database and `BACKUP_MASTER_KEY` can also
expose the stored Recovery Key. Recovery archives preserve extracted text and
document data, but not the original PDF, JPG, or PNG files.

The current archive format is schema version 3 and contains record counts for
each exported collection. Restore checks that schema strictly, reconnects
calendar events to their restored documents, and also restores user-created
events. Pending reminders are recalculated from the restored event time; a
reminder whose scheduled time is already past is cancelled instead of being
delivered late. Version 2 recovery archives remain supported. OAuth credentials
and Google Drive refresh tokens are never included in recovery archives.

DocsFlow stores only an encrypted copy of that key. Set a separate application
master key before generating Recovery Keys or restoring backups:

```powershell
python -m poetry run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated value in `BACKUP_MASTER_KEY` in the deployment secret store
or `.env`. Keep this value stable and private: changing it prevents DocsFlow
from creating or downloading backups for existing accounts. A user can still
restore an existing archive anywhere with the saved Recovery Key.

Every new backup records a non-secret Recovery Key ID. After rotating the key,
`Download JSON` continues to work for backups made with the active key. For an
older backup, use `Encrypted file` to download the original authenticated
archive, then restore it with the saved old key. The encrypted download is not
decrypted or re-encrypted by DocsFlow.

To recover after a database loss, recreate or sign in to the account, open
`/backups`, select the encrypted `.json.gz.enc` archive, and enter its
Recovery Key. Documents are restored without their original files, so preview
and original-file download are unavailable; the UI labels these records as
backup data only. Completed standard documents with extracted text are
automatically queued for Knowledge Base indexing.
Normal restore accepts only authenticated Fernet archives. To migrate an old
unencrypted JSON or JSON.gz export, temporarily set
`BACKUP_ALLOW_LEGACY_RESTORE=true`, explicitly select migration mode in the
restore form, import only a trusted file, and disable the setting again.

### Google Drive OAuth token encryption

Google Drive refresh tokens are encrypted before they are stored in the
database. Generate a separate Fernet key for this purpose:

```powershell
python -m poetry run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store it as `GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY` in the deployment secret store
or `.env`. This server-side key is different from both the user-facing Recovery
Key and `BACKUP_MASTER_KEY`. If Google Drive connections already exist, the API
startup intentionally fails until this key is configured; startup then encrypts
legacy plaintext tokens automatically.

To rotate the key without disconnecting users:

1. Move the current key to `GOOGLE_DRIVE_TOKEN_PREVIOUS_ENCRYPTION_KEYS`.
2. Put the newly generated key in `GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY`.
3. Restart the API. Startup decrypts tokens with either key and re-encrypts them
   with the new active key.
4. After startup and a Google Drive backup have succeeded, remove the old key
   from `GOOGLE_DRIVE_TOKEN_PREVIOUS_ENCRYPTION_KEYS` and restart again.

Multiple previous keys may be supplied as a comma-separated list during a
staged rotation. Keep all token-encryption keys in the secret store, never in
source control.

Google OAuth uses the restricted `drive.file` scope. On connection DocsFlow
creates or reuses `/docflow_backup` (configurable with
`GOOGLE_DRIVE_FOLDER_NAME`) in the connected Google Drive root, then stores
only the archives created by DocsFlow in that folder.

---

## Project Structure

```text
app/
  api/
    v1/
      routes_auth.py
      routes_documents.py
      routes_users.py
  core/
    config.py
  db/
    session.py
    base.py
  models/
    user.py
    document.py
    calendar_event.py
    event_reminder.py
    notification.py
    audit_log.py
    document_chunk.py
    backup_job.py
    knowledge_conversation.py
    processing_job.py
  schemas/
  services/
    calendar_events.py
    calendar_event_validation.py
    document_calendar_projection.py
    uploads.py
    storage.py
    processing_jobs.py
    text_extraction.py
    backup_recovery.py
    semantic_search.py
  tasks/
    documents.py
  worker.py
  main.py
  web.py
  web_backups.py
  frontend/
    templates/
    assets/

alembic/
tests/
docker-compose.yml
Dockerfile
```

---

## Local Setup

Create a local environment file:

```bash
cp .env.example .env
```

Before the first start, replace `APP_SECRET_KEY` with a random value of at least
32 characters and set `POSTGRES_PASSWORD`; the password embedded in
`DATABASE_URL` must match it. For example, generate an application secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the generated value to `APP_SECRET_KEY` in `.env`. Placeholder values such
as `change-me-in-production` are intentionally rejected, and the startup script
stops before attempting database migrations when the configuration is invalid.

For an existing PostgreSQL volume, setting a new Compose environment variable
does not change the password already stored in PostgreSQL. Start once with the
current password, change the `docsflow` role password, then update both
`POSTGRES_PASSWORD` and `DATABASE_URL`. PostgreSQL and Redis are reachable only
inside the Compose network; use `docker compose exec` for administrative access.

Start the stack:

```bash
docker compose up --build
```

| Resource | URL |
|---|---|
| API | http://localhost:8000 |
| Interactive docs | http://localhost:8000/docs |

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Database Migrations

### Automatic Migrations on Startup

The API container runs database migrations automatically on startup.

On every API start, the entrypoint script executes:

```bash
alembic upgrade head
```

If the database is empty, all migrations are applied before the FastAPI server
starts. If the database is already up to date, Alembic exits without changes.
The entrypoint validates the environment first, retries a temporarily
unavailable database up to ten times, rotates stored Google Drive tokens with
the active encryption key, and optionally initializes the local test user.
The Celery worker waits for the API health check, so it cannot start against an
older schema. In local development (including Codespaces), the entrypoint also
watches `alembic/versions`: saving or adding a migration applies it within a
few seconds. Production still applies migrations only during container startup.

The migration startup script is located at:

```
scripts/start-api.sh
```

The API service uses this script as its startup command:

```yaml
command: sh scripts/start-api.sh
```

> Only the API container runs migrations. The Celery worker does not run migrations to avoid concurrent migration execution.

### Manual Commands

```bash
# Check current migration
docker compose exec api alembic current

# Inspect tables
docker compose exec db psql -U docsflow -d docsflow -c "\dt"
```

For a clean local start:

```bash
docker compose down -v
docker compose up --build
```

After startup, expected tables:

```
users
documents
processing_jobs
openai_usage_logs
audit_logs
document_chunks
document_index_jobs
knowledge_conversations
knowledge_messages
knowledge_message_sources
calendar_events
event_reminders
notifications
google_drive_connections
backup_jobs
alembic_version
```

---

## Authentication Flow

**Register a user:**

```bash
curl -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "strong-password"
  }'
```

**Login and store the access token:**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com" \
  -d "password=strong-password" | jq -r ".access_token")
```

**Check current user:**

```bash
curl http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer $TOKEN"

# Set the IANA timezone for the account and web date/time display
curl -X PATCH http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"timezone": "Europe/Berlin"}'
```

---

## Upload a Document

**Standard mode:**

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/document.pdf;type=application/pdf" \
  -F "confidential=false"
```

**Confidential mode:**

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/document.pdf;type=application/pdf" \
  -F "confidential=true"
```

**Example response:**

```json
{
  "id": 1,
  "original_filename": "document.pdf",
  "status": "uploaded",
  "processing_mode": "standard",
  "content_type": "application/pdf",
  "file_size_bytes": 24943,
  "checksum_sha256": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

---

## Documents API

```bash
# List documents owned by the current user
curl http://localhost:8000/api/v1/documents \
  -H "Authorization: Bearer $TOKEN"

# Search, filter and paginate documents
curl "http://localhost:8000/api/v1/documents?query=invoice&document_type=invoice&requires_action=true&page=1&page_size=25&sort_by=deadline" \
  -H "Authorization: Bearer $TOKEN"

# Get one document
curl http://localhost:8000/api/v1/documents/1 \
  -H "Authorization: Bearer $TOKEN"

# Get the processing result, extracted text and signed file URLs
curl http://localhost:8000/api/v1/documents/1/result \
  -H "Authorization: Bearer $TOKEN"

# Create a signed download URL for one document
curl http://localhost:8000/api/v1/documents/1/download-url \
  -H "Authorization: Bearer $TOKEN"

# List processing jobs for a document
curl http://localhost:8000/api/v1/documents/1/jobs \
  -H "Authorization: Bearer $TOKEN"

# Reprocess a failed document
curl -X POST http://localhost:8000/api/v1/documents/1/reprocess \
  -H "Authorization: Bearer $TOKEN"

# Correct extracted fields
curl -X PATCH http://localhost:8000/api/v1/documents/1/extraction \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 49.99,
    "document_date": "2026-05-20",
    "document_type": "invoice",
    "sender": "Vodafone GmbH"
  }'

# Confirm the current extraction
curl -X POST http://localhost:8000/api/v1/documents/1/confirm \
  -H "Authorization: Bearer $TOKEN"

# Save or clear the optional personal note (use null to clear)
curl -X PATCH http://localhost:8000/api/v1/documents/1/note \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_note": "Call the supplier next week."}'

# Soft-delete a document; its original file is retained on local storage
curl -X DELETE http://localhost:8000/api/v1/documents/1 \
  -H "Authorization: Bearer $TOKEN"

```

> Reprocessing is allowed only for documents with status `failed`.

---

## Calendar API

Calendar endpoints are owner-scoped and require the same Bearer token as the
document API. The list endpoint accepts `start`, `end`, `status`, `event_type`,
`source`, and `document_id` filters.

```bash
# List events that overlap August 2026 in the user's timezone
curl "http://localhost:8000/api/v1/calendar/events?start=2026-08-01&end=2026-08-31" \
  -H "Authorization: Bearer $TOKEN"

# Create an all-day user event
curl -X POST http://localhost:8000/api/v1/calendar/events \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Pay invoice",
    "event_type": "payment_due",
    "all_day": true,
    "start_date": "2026-08-15",
    "document_id": 1
  }'

# Create a timed event. Datetimes must include an offset and timezone is IANA.
curl -X POST http://localhost:8000/api/v1/calendar/events \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Call supplier",
    "event_type": "appointment",
    "all_day": false,
    "start_at": "2026-08-15T09:30:00+02:00",
    "end_at": "2026-08-15T10:00:00+02:00",
    "timezone": "Europe/Berlin"
  }'

# Update an event with optimistic locking
curl -X PATCH http://localhost:8000/api/v1/calendar/events/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Pay corrected invoice","expected_sequence":0}'

# Act on an event
curl -X POST http://localhost:8000/api/v1/calendar/events/1/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"expected_sequence":0}'
curl -X POST http://localhost:8000/api/v1/calendar/events/1/complete \
  -H "Authorization: Bearer $TOKEN"
curl -X POST "http://localhost:8000/api/v1/calendar/events/1/cancel?expected_sequence=1" \
  -H "Authorization: Bearer $TOKEN"
```

Event lifecycle statuses are `suggested`, `confirmed`, `completed`, and
`cancelled`. User-created events start as `confirmed`; AI projections always
start as `suggested`.

---

## Check Extracted Text

The list and single-document endpoints do not expose `raw_text`. The owned
`/api/v1/documents/{id}/result` response and the document detail page do expose
it after processing, so keep API credentials and backups secure.

For local development, inspect extracted text directly in PostgreSQL:

```bash
# Preview raw text
docker compose exec db psql -U docsflow -d docsflow \
  -P pager=off \
  -c "SELECT id, status, processing_mode, left(raw_text, 700) AS raw_text_preview FROM documents ORDER BY id DESC LIMIT 5;"

# Check text length
docker compose exec db psql -U docsflow -d docsflow \
  -P pager=off \
  -c "SELECT id, original_filename, status, processing_mode, length(raw_text) AS raw_text_length FROM documents ORDER BY id DESC LIMIT 5;"
```

---

## Testing

```bash
# Run the full test suite
docker compose run --rm api pytest

# Run specific test groups
docker compose run --rm api pytest tests/test_documents_upload.py
docker compose run --rm api pytest tests/test_processing_jobs.py
docker compose run --rm api pytest tests/test_text_extraction.py

# Run real PostgreSQL + pgvector integration tests
docker compose run --rm api sh -c \
  'TEST_POSTGRESQL_URL="$DATABASE_URL" pytest -m postgres -q'
```

### Browser end-to-end tests

The optional Playwright smoke test registers a user and signs in through a real
Chromium browser. Start DocsFlow first, then install the optional dependency and
browser once:

```bash
python -m poetry install --with e2e
python -m poetry run playwright install chromium
E2E_BASE_URL=http://localhost:8000 python -m poetry run pytest -m e2e -q
```

In PowerShell, set the URL for the command this way:

```powershell
$env:E2E_BASE_URL = "http://localhost:8000"
python -m poetry run pytest -m e2e -q
```

For Codespaces, set `E2E_BASE_URL` to the forwarded port URL when the browser
cannot reach `localhost:8000` directly.

### Windows (local Poetry environment)

FastAPI does not use `manage.py`; run pytest through Poetry:

```powershell
python -m poetry run pytest -q

# Example: backup tests only
python -m poetry run pytest tests/test_backups.py -q
```

PostgreSQL/pgvector integration tests are skipped locally unless
`TEST_POSTGRESQL_URL` points to a disposable PostgreSQL database with the
`vector` extension. The GitHub Actions workflow runs them in Docker Compose.

---

## Useful Development Commands

```bash
# View API logs
docker compose logs -f api

# View Celery worker logs
docker compose logs -f celery_worker

# Open a database shell
docker compose exec db psql -U docsflow -d docsflow

# Stop the stack
docker compose down

# Stop the stack and remove volumes
docker compose down -v
```

---

## Environment Variables

All settings are configured through `.env`; [`.env.example`](.env.example)
contains the complete local-development template. Do not commit a real `.env`
file.

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `local` | Environment name |
| `APP_DEBUG` | `true` | Debug mode |
| `APP_SECRET_KEY` | — | Random secret of at least 32 characters; placeholders are rejected outside local/test |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT expiry |
| `REMEMBER_ME_TOKEN_EXPIRE_DAYS` | `30` | Lifetime of the explicit “Keep me signed in” browser session; 1–90 days. |
| `POSTGRES_PASSWORD` | — | Required PostgreSQL password used by Docker Compose |
| `DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL connection |
| `CORS_ORIGINS` | `["http://localhost:8000"]` | Allowed origins |
| `UPLOAD_MAX_FILE_SIZE_MB` | `10` | Max upload size |
| `UPLOAD_RATE_LIMIT_REQUESTS` | `10` | Rate limit count |
| `UPLOAD_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window |
| `RATE_LIMIT_ENABLED` | `true` | Enable fail-closed shared limits and OpenAI quotas |
| `RATE_LIMIT_REDIS_URL` | `redis://redis:6379/2` | Dedicated Redis database for shared counters |
| `LOGIN_RATE_LIMIT_REQUESTS` | `10` | Per-account login attempts per window; the IP guard is 10x broader |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `300` | Login limit window |
| `REGISTRATION_RATE_LIMIT_REQUESTS` | `5` | Per-principal registration attempts per window |
| `REGISTRATION_RATE_LIMIT_WINDOW_SECONDS` | `3600` | Registration limit window |
| `SEMANTIC_SEARCH_RATE_LIMIT_REQUESTS` | `30` | Semantic searches per user/window |
| `SEMANTIC_SEARCH_RATE_LIMIT_WINDOW_SECONDS` | `60` | Semantic search window |
| `KNOWLEDGE_QUESTION_RATE_LIMIT_REQUESTS` | `10` | Document-chat questions per user/window |
| `KNOWLEDGE_QUESTION_RATE_LIMIT_WINDOW_SECONDS` | `60` | Document-chat question window |
| `OPENAI_DAILY_REQUEST_QUOTA` | `200` | Maximum OpenAI-backed operations per user/24 hours |
| `OPENAI_DAILY_TOKEN_QUOTA` | `500000` | Maximum recorded OpenAI tokens per user/24 hours |
| `LOCAL_STORAGE_PATH` | `storage` | Local file storage path |
| `LOCAL_OCR_LANGUAGES` | `eng+deu` | Tesseract languages |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` | Celery backend |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks synchronously |
| `DOCUMENT_PROCESSING_SOFT_TIME_LIMIT_SECONDS` | `60` | Soft task limit |
| `DOCUMENT_PROCESSING_HARD_TIME_LIMIT_SECONDS` | `90` | Hard task limit |
| `DOCUMENT_PROCESSING_MAX_RETRIES` | `3` | Max retry attempts |
| `DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS` | `10` | Delay between retries |
| `BACKUP_MASTER_KEY` | Not set | Valid Fernet key used to protect stored per-user Recovery Keys |
| `BACKUP_MAX_RETAINED` | `5` | Maximum completed recovery archives retained per user **for each type** (manual and automatic); the oldest archive of the same type is removed after a successful new backup. |
| `AUTOMATIC_BACKUPS_ENABLED` | `true` | Enables the weekly automatic Google Drive recovery backup scheduler. |
| `AUTOMATIC_BACKUP_WEEKDAY` | `sun` | Day of week for automatic backups (`mon` … `sun`). |
| `AUTOMATIC_BACKUP_HOUR` | `3` | Hour in Berlin time (0–23) when the automatic backup runs. |
| `GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY` | — | Active Fernet key used to encrypt Google Drive refresh tokens at rest |
| `GOOGLE_DRIVE_TOKEN_PREVIOUS_ENCRYPTION_KEYS` | — | Comma-separated old Fernet keys used temporarily during key rotation |
| `BACKUP_RESTORE_MAX_FILE_SIZE_MB` | `25` | Maximum uploaded recovery archive size |
| `BACKUP_RESTORE_MAX_DECOMPRESSED_SIZE_MB` | `100` | Maximum JSON size after gzip decompression |
| `BACKUP_RESTORE_MAX_DOCUMENTS` | `2000` | Maximum documents accepted from one restore |
| `BACKUP_RESTORE_MAX_RAW_TEXT_CHARS` | `2000000` | Maximum extracted text length per restored document |
| `BACKUP_ALLOW_LEGACY_RESTORE` | `false` | Temporarily expose explicit migration mode for trusted unencrypted legacy exports |

### Additional current settings

| Variable | Default | Description |
|---|---|---|
| `INIT_TEST_USER` | `false` | Creates/updates the local test account; valid only with `APP_ENV=local`. |
| `TEST_USER_EMAIL` / `TEST_USER_PASSWORD` | `m@m.com` / `12345678` | Credentials for the optional local test account. |
| `DOCUMENT_PREVIEW_TOKEN_EXPIRE_MINUTES` | `10` | Lifetime of signed preview and download URLs. |
| `BACKUP_SOFT_TIME_LIMIT_SECONDS` / `BACKUP_HARD_TIME_LIMIT_SECONDS` | `120` / `180` | Backup task limits. |
| `GOOGLE_DRIVE_CLIENT_ID` / `GOOGLE_DRIVE_CLIENT_SECRET` | Not set | OAuth client credentials for user-authorized Google Drive backups. |
| `GOOGLE_DRIVE_REDIRECT_URI` | `http://localhost:8000/backups/google/callback` | Callback URI registered in Google Cloud. |
| `GOOGLE_DRIVE_FOLDER_NAME` / `GOOGLE_DRIVE_TIMEOUT_SECONDS` | `docflow_backup` / `60` | Backup folder in Drive root and Google HTTP timeout. |
| `GOOGLE_OAUTH_STATE_EXPIRE_MINUTES` | `10` | OAuth state lifetime. |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | Not set | Deprecated and ignored; each user connects Drive through OAuth. |
| `OPENAI_API_KEY` | Not set | Required for standard AI extraction and enabled Knowledge Base operations. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for standard document extraction. |
| `OPENAI_VALIDATION_FALLBACK_MODEL` | Not set | Optional stronger model for one separate retry of a `needs_review` extraction. |
| `OPENAI_REQUEST_TIMEOUT_SECONDS` / `OPENAI_MAX_INPUT_CHARS` | `45` / `12000` | Standard extraction request limits. |
| `OPENAI_EMBEDDING_MODEL` / `OPENAI_EMBEDDING_DIMENSIONS` | `text-embedding-3-small` / `1536` | Knowledge Base embedding model and vector size. |
| `OPENAI_RAG_MODEL` / `OPENAI_RAG_REASONING_EFFORT` | `gpt-5.6-terra` / `high` | Knowledge Base answer model and reasoning effort. |
| `OPENAI_RAG_MAX_CONTEXT_CHARS` / `OPENAI_RAG_MAX_OUTPUT_TOKENS` | `24000` / `800` | RAG context and answer limits. |
| `KNOWLEDGE_ENABLED` | `true` | Enables indexing plus Knowledge Base UI and API. |
| `KNOWLEDGE_RETRIEVAL_LIMIT` / `KNOWLEDGE_MIN_SIMILARITY` | `8` / `0.25` | Maximum retrieved chunks and minimum similarity. |
| `KNOWLEDGE_HISTORY_MESSAGE_LIMIT` / `KNOWLEDGE_HISTORY_TOKEN_BUDGET` | `8` / `1600` | Conversation-history limits. |
| `DOCUMENT_INDEXING_SOFT_TIME_LIMIT_SECONDS` / `DOCUMENT_INDEXING_HARD_TIME_LIMIT_SECONDS` / `DOCUMENT_INDEXING_BATCH_SIZE` | `120` / `180` / `64` | Indexing task limits and embedding batch size. |
| `DOCUMENT_CHUNK_SIZE_TOKENS` / `DOCUMENT_CHUNK_OVERLAP_TOKENS` | `700` / `100` | Paragraph-aware chunk size and overlap. |

---

### Shared abuse limits and OpenAI cost guard

Login, registration, uploads, semantic search, and document-chat questions use
atomic Redis counters shared by all API processes. Redis failure returns `503`
for protected operations instead of silently bypassing the limits. Login and
registration use both a client-IP key and a normalized account/email key.

Before every OpenAI-backed operation, DocsFlow also checks a per-user 24-hour
operation counter and the rolling token usage persisted in
`openai_usage_logs`. The token quota provides a configurable spend ceiling; it
is a safety control, not an accounting or billing report. Set
`RATE_LIMIT_ENABLED=false` only in isolated tests.

The navigation AI usage indicator exposes the same persisted token usage to the
signed-in account, grouped by model, for a quick in-product view of consumption.

Outside local development, set `APP_ENV=production`, `APP_DEBUG=false`, and
`INIT_TEST_USER=false`. Production startup uses `WEB_CONCURRENCY` workers and
never enables Uvicorn reload.

---

## AI Extraction for Standard Documents

DocsFlow supports AI-based structured extraction for documents uploaded in `standard` mode.

Processing flow for standard documents:

```text
upload
-> local text extraction
-> AI structured JSON extraction (including document type)
-> schema and deterministic validation (grounding + logic)
-> save extracted JSON and OpenAI usage log
-> mark document as completed
-> project validated temporal candidates into suggested calendar events
```

> AI processing is executed only after local text extraction has completed successfully.

### Supported AI Processing Scope

- Document type classification
- Structured data extraction to JSON
- Strict Pydantic validation of the AI response
- Deterministic validation against extracted text and cross-field rules
- OpenAI token usage logging

Validation stores a score, status, errors and warnings with the document. A
`needs_review` result is kept for the user to correct or confirm; it is not
silently discarded. The implementation roadmap is in
[AI extraction validation checklist](docs/ai-extraction-validation-checklist.md).

For amount, currency, sender and dates, the AI also returns a short source
quote and one-based page number. DocsFlow checks that quote against locally
extracted page text before storing it as verified evidence.

The validation layer also stores labelled amount/date candidates, flags only
competing totals or deadlines, and records a deterministic OCR-quality score.
Those signals affect the score and can move an extraction to `needs_review`.

Validated temporal candidates are projected to the calendar only when they are
`valid` or `warning`. Warnings create suggestions marked for review; candidates
that need review are kept in the document result but do not create an event.
Projection uses a stable source key, is idempotent during reprocessing, and
never overwrites a confirmed or manually edited calendar event.

**Storage fields:**

| Field | Table |
|---|---|
| Extracted JSON | `documents.ai_extracted_data` |
| Document type | `documents.document_type` |
| Model used | `documents.ai_extraction_model` |
| Completion timestamp | `documents.ai_extraction_completed_at` |
| Validation result | `documents.validation_*` |
| Token usage | `openai_usage_logs` |

### Processing Modes

AI extraction is only available for documents uploaded with `confidential=false`.

Documents uploaded with `confidential=true` are processed locally only and never sent to OpenAI. This is an intentional security boundary.

### Required Environment Variables

```bash
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_VALIDATION_FALLBACK_MODEL=
OPENAI_REQUEST_TIMEOUT_SECONDS=45
OPENAI_MAX_INPUT_CHARS=12000
```

> If `OPENAI_API_KEY` is missing, standard document processing will fail during the AI extraction step. Confidential documents do not require an OpenAI API key.

`OPENAI_VALIDATION_FALLBACK_MODEL` is deliberately empty by default. When set
(for example to `gpt-4o`), DocsFlow performs at most one additional AI pass only
for a result marked `needs_review`; it never overwrites the original AI result.

### Example: Upload a Standard Document

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/document.pdf;type=application/pdf" \
  -F "confidential=false"
```

After processing, the document response includes AI extraction fields:

```json
{
  "id": 1,
  "status": "completed",
  "processing_mode": "standard",
  "document_type": "invoice",
  "ai_extracted_data": {
    "document_type": "invoice",
    "summary": "Invoice for services.",
    "sender": "Example Company",
    "recipient": "Customer Name",
    "document_date": "2026-01-15",
    "due_date": "2026-02-15",
    "total_amount": 950.0,
    "currency": "EUR",
    "invoice_number": "INV-001",
    "reference_number": null,
    "requires_action": true,
    "action_deadline": "2026-02-15",
    "confidence_score": 0.95,
    "notes": null
  },
  "ai_extraction_model": "gpt-4o-mini",
  "ai_extraction_completed_at": "2026-06-03T17:47:42.746233Z"
}
```

### Check OpenAI Usage Logs

```bash
docker compose exec db psql -U docsflow -d docsflow \
  -P pager=off \
  -c "SELECT document_id, operation, model, input_tokens, output_tokens, total_tokens FROM openai_usage_logs ORDER BY id DESC LIMIT 5;"
```

Example result:

```
 document_id |       operation        |    model    | input_tokens | output_tokens | total_tokens
-------------+------------------------+-------------+--------------+---------------+--------------
           1 | document_ai_extraction | gpt-4o-mini |          844 |           100 |          944
```

### Check Extracted AI Data

```bash
docker compose exec db psql -U docsflow -d docsflow \
  -P pager=off \
  -c "SELECT id, document_type, ai_extracted_data FROM documents ORDER BY id DESC LIMIT 1;"
```

---

## Current Limitations

- `raw_text` is available only to the document owner through the result endpoint; it is not included in list or single-document responses
- Uploaded files are stored on the local filesystem
- Recovery backups restore extracted content and metadata, but never the original PDF, JPG, or PNG file
- Abuse limits require Redis; protected operations fail closed while Redis is unavailable
- AI extraction is available only for `standard` mode; `confidential` documents are never sent to OpenAI
- AI extraction depends on successful local text extraction
- Extracted JSON schema is generic and will be refined in later MVP steps
- Knowledge Base retrieval is available only for indexed `standard` documents
- Set `KNOWLEDGE_ENABLED=false` to disable Knowledge Base access and embedding work
- Confidential RAG is intentionally not enabled until the documented local-only embedding and LLM design is implemented
- standard-mode AI extraction requires `OPENAI_API_KEY`
- Calendar reminders are delivered in-app; email transport and the UI for
  creating reminder settings are not implemented yet
- External calendar synchronization is not implemented; calendar exports use
  provider-neutral `.ics` downloads and private feed URLs instead

## Contacts

Author: Maksym Petrykin

Email: [m.petrykin@gmx.de](mailto:m.petrykin@gmx.de)

Telegram: [@max_p95](https://t.me/max_p95)
