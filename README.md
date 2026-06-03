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

> In the current MVP, both modes use local processing only.

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

> **Limitation:** scanned PDFs without a text layer are not OCR-processed yet. OCR fallback for scanned PDF pages should be implemented as a separate improvement.

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

# Get one document
curl http://localhost:8000/api/v1/documents/1 \
  -H "Authorization: Bearer $TOKEN"

# List processing jobs for a document
curl http://localhost:8000/api/v1/documents/1/jobs \
  -H "Authorization: Bearer $TOKEN"

# Reprocess a failed document
curl -X POST http://localhost:8000/api/v1/documents/1/reprocess \
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
```

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
- Scanned PDF OCR fallback is not implemented yet
- Uploaded files are stored on the local filesystem
- Upload rate limiting is in-memory and not shared between multiple API instances
- AI extraction is available only for `standard` mode — `confidential` documents are never sent to OpenAI
- AI extraction depends on successful local text extraction
- Scanned PDFs without a text layer require OCR fallback before AI extraction can work well
- Extracted JSON schema is generic and will be refined in later MVP steps
- Semantic search and document Q&A are planned for later MVP stages
- standard-mode AI extraction requires `OPENAI_API_KEY`
- scanned PDF files without a text layer may produce empty `raw_text` until OCR fallback is implemented

## Contacts

Author: Maksym Petrykin

Email: [m.petrykin@gmx.de](mailto:m.petrykin@gmx.de)

Telegram: [@max_p95](https://t.me/max_p95)