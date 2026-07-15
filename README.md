# DocsFlow API

DocsFlow API is a FastAPI-based backend service for uploading, storing and processing documents.

Current MVP scope includes user authentication, document upload, local file storage, asynchronous processing with Celery, processing job tracking, and local text extraction for supported document types.

## Tech Stack

| Category | Technologies |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI |
| Database | PostgreSQL, SQLAlchemy 2, Alembic |
| Queue | Celery, Redis |
| PDF Extraction | PyMuPDF |
| OCR | Tesseract |
| Testing | Pytest |
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

Users can correct the amount, document date, document type, sender/vendor and
other existing extraction fields. Every effective field change is stored as a
separate immutable `AuditLog` row with the old and new value.

### Signed File URLs

Document result responses include short-lived, signed URLs for inline preview
and attachment download. A preview token cannot be used to download a file,
and a download token cannot be used for preview. Tokens expire after
`DOCUMENT_PREVIEW_TOKEN_EXPIRE_MINUTES` (10 minutes by default).

### Web Interface

DocsFlow includes a lightweight Bootstrap 5 interface served by FastAPI. No
separate frontend server or JavaScript build step is required.

| Page | Purpose |
|---|---|
| `/login` | Authenticate with email and password |
| `/register` | Create an account |
| `/documents` | List the current user's documents |
| `/documents/upload` | Upload a document and select confidential mode |
| `/documents/{id}` | Preview, download, correct, and confirm extraction |
| `/knowledge` | Ask questions about indexed documents and check indexing status |
| `/backups` | Create encrypted Google Drive recovery backups and restore document data |

The browser receives the short-lived access token in an HTTP-only cookie.
Bootstrap is loaded from its CDN; production
deployments may vendor the Bootstrap files under `app/frontend/assets/` if a
network-independent interface is required.

### Knowledge Base / RAG

The Knowledge Base indexes completed `standard` documents into page-aware
chunks and answers questions only from the current user's retrieved chunks.
Each answer retains a snapshot of its source filename, page, snippet, and
similarity score. `confidential` documents are never indexed or sent to OpenAI.

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

Backups on `/backups` are recovery archives: they contain document metadata,
extracted text, and the structured extraction result, but not original PDF,
JPG, or PNG files. Each archive is encrypted with a per-user **Recovery Key**
before it is uploaded to Google Drive. The key is displayed once after it is
generated; save it in a password manager or another secure location.

For the first backup, use this sequence:

1. Generate and securely save the Recovery Key.
2. Connect the Google Drive account that will store the archive.
3. Create the backup.

Google Drive access does not replace the Recovery Key. DocsFlow cannot show or
recover a lost Recovery Key. Anyone who obtains both the encrypted archive and
its Recovery Key can read the backed-up document data, so keep them separately
and securely. Recovery archives preserve extracted text and document data, but
not the original PDF, JPG, or PNG files; deleting an archive from Google Drive
removes that recovery copy.

DocsFlow stores only an encrypted copy of that key. Set a separate application
master key before generating Recovery Keys or restoring backups:

```powershell
python -m poetry run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated value in `BACKUP_MASTER_KEY` in the deployment secret store
or `.env`. Keep this value stable and private: changing it prevents DocsFlow
from creating or downloading backups for existing accounts. A user can still
restore an existing archive anywhere with the saved Recovery Key.

To recover after a database loss, recreate or sign in to the account, open
`/backups`, select the encrypted `.json.gz.enc` archive, and enter its
Recovery Key. Documents are restored without their original files; completed
standard documents are automatically queued for Knowledge Base indexing.

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
    audit_log.py
    processing_job.py
  schemas/
  services/
    uploads.py
    storage.py
    processing_jobs.py
    text_extraction.py
  tasks/
    documents.py
  worker.py
  main.py
  web.py
  frontend/
    index.html
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

If the database is empty, all migrations are applied before the FastAPI server starts. If the database is already up to date, Alembic exits without changes.

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

```

> Reprocessing is allowed only for documents with status `failed`.

---

## Check Extracted Text

The public document response does not expose `raw_text`.

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
docker compose run --rm \
  -e TEST_POSTGRESQL_URL=postgresql+psycopg://docsflow:docsflow@db:5432/docsflow \
  api pytest -m postgres -q
```

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

Main settings are configured through `.env`.

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `local` | Environment name |
| `APP_DEBUG` | `true` | Debug mode |
| `APP_SECRET_KEY` | — | Change in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT expiry |
| `DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL connection |
| `CORS_ORIGINS` | `["http://localhost:8000"]` | Allowed origins |
| `UPLOAD_MAX_FILE_SIZE_MB` | `10` | Max upload size |
| `UPLOAD_RATE_LIMIT_REQUESTS` | `10` | Rate limit count |
| `UPLOAD_RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window |
| `LOCAL_STORAGE_PATH` | `storage` | Local file storage path |
| `LOCAL_OCR_LANGUAGES` | `eng+deu` | Tesseract languages |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` | Celery backend |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks synchronously |
| `DOCUMENT_PROCESSING_SOFT_TIME_LIMIT_SECONDS` | `60` | Soft task limit |
| `DOCUMENT_PROCESSING_HARD_TIME_LIMIT_SECONDS` | `90` | Hard task limit |
| `DOCUMENT_PROCESSING_MAX_RETRIES` | `3` | Max retry attempts |
| `DOCUMENT_PROCESSING_RETRY_DELAY_SECONDS` | `10` | Delay between retries |
| `BACKUP_MASTER_KEY` | â€” | Valid Fernet key used to protect stored per-user Recovery Keys |
| `GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY` | — | Active Fernet key used to encrypt Google Drive refresh tokens at rest |
| `GOOGLE_DRIVE_TOKEN_PREVIOUS_ENCRYPTION_KEYS` | — | Comma-separated old Fernet keys used temporarily during key rotation |
| `BACKUP_RESTORE_MAX_FILE_SIZE_MB` | `25` | Maximum uploaded recovery archive size |
| `BACKUP_RESTORE_MAX_DECOMPRESSED_SIZE_MB` | `100` | Maximum JSON size after gzip decompression |
| `BACKUP_RESTORE_MAX_DOCUMENTS` | `2000` | Maximum documents accepted from one restore |
| `BACKUP_RESTORE_MAX_RAW_TEXT_CHARS` | `2000000` | Maximum extracted text length per restored document |

---

## AI Extraction for Standard Documents

DocsFlow supports AI-based structured extraction for documents uploaded in `standard` mode.

Processing flow for standard documents:

```text
upload
→ local text extraction
→ AI document classification
→ AI structured JSON extraction
→ Pydantic validation
→ save extracted JSON
→ save OpenAI usage log
→ mark document as completed
```

> AI processing is executed only after local text extraction has completed successfully.

### Supported AI Processing Scope

- Document type classification
- Structured data extraction to JSON
- Pydantic validation of the AI response
- OpenAI token usage logging

**Storage fields:**

| Field | Table |
|---|---|
| Extracted JSON | `documents.ai_extracted_data` |
| Document type | `documents.document_type` |
| Model used | `documents.ai_extraction_model` |
| Completion timestamp | `documents.ai_extraction_completed_at` |
| Token usage | `openai_usage_logs` |

### Processing Modes

AI extraction is only available for documents uploaded with `confidential=false`.

Documents uploaded with `confidential=true` are processed locally only and never sent to OpenAI. This is an intentional security boundary.

### Required Environment Variables

```bash
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_REQUEST_TIMEOUT_SECONDS=45
OPENAI_MAX_INPUT_CHARS=12000
```

> If `OPENAI_API_KEY` is missing, standard document processing will fail during the AI extraction step. Confidential documents do not require an OpenAI API key.

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

- `raw_text` is stored internally but not exposed through the public API
- Uploaded files are stored on the local filesystem
- Upload rate limiting is in-memory and not shared between multiple API instances
- AI extraction is available only for `standard` mode — `confidential` documents are never sent to OpenAI
- AI extraction depends on successful local text extraction
- Extracted JSON schema is generic and will be refined in later MVP steps
- Knowledge Base retrieval is available only for indexed `standard` documents
- Set `KNOWLEDGE_ENABLED=false` to disable Knowledge Base access and embedding work
- standard-mode AI extraction requires `OPENAI_API_KEY`

## Contacts

Author: Maksym Petrykin

Email: [m.petrykin@gmx.de](mailto:m.petrykin@gmx.de)

Telegram: [@max_p95](https://t.me/max_p95)
