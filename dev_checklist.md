# DocsFlow API

FastAPI-based backend service for document digitization, classification,
structured AI extraction, search and later semantic interaction with uploaded documents.

---

## MVP 0 — Base Setup & Simple Auth

- [x] Docker Compose setup
- [x] PostgreSQL
- [x] Alembic migrations
- [x] Simple email/password auth
- [x] JWT access token
- [x] Password hashing with bcrypt
- [x] Current user dependency
- [x] User-owned documents
- [x] Basic permission checks

---

## MVP 1 — Core Document Processing

**Goal:** implement document upload, storage, and automated structured data extraction.

### 1. Upload endpoint & basic validation

- [x] Upload endpoint via FastAPI
- [x] Accept PDF / JPG / PNG
- [x] File size / MIME validation
- [x] Rate limiting on upload endpoint
- [x] UI checkbox on upload for confidential mode

---

### 2. File storage

- [x] Save files to local storage
- [x] SHA-256 checksum
- [x] Duplicate detection

---

### 3. Document record

- [x] Create `Document` record in DB
- [x] Document statuses: `uploaded` / `processing` / `completed` / `failed`
- [x] `processing_mode` field on `Document`
- [x] Processing modes:
  - `standard`
  - `confidential`

---

### 4. Processing job

- [x] `ProcessingJob` for each processing operation
- [x] Celery task for asynchronous processing
- [x] Celery soft/hard time limits
- [x] Max retries
- [x] Failed status
- [x] `error_message`
- [x] Reprocess trigger on failure

---

### 5. Local text extraction

- [x] Extract text from file
- [x] Store `raw_text`
- [x] Confidential pipeline: local text extraction only
- [x] Block external API calls when `processing_mode = confidential`

---

### 6. AI processing for standard mode

- [x] Classify document type
- [x] AI extraction to JSON
- [x] Pydantic validation of AI response
- [x] OpenAI usage logging

---

### 7. Save extraction result

- [x] Store extracted data in PostgreSQL
- [x] Store summary / amount / deadline / sender / confidence score
- [x] Mark document status as `completed`
- [x] Mark document status as `failed` on any error

---

### 8. Document result view

- [ ] Display document with extraction results
- [ ] File preview via storage URL / presigned URL
- [ ] Show manual correction controls
- [ ] Show error details if processing failed
- [ ] Trigger manual reprocess

---

## MVP 1.1 — Manual Correction

After AI extraction, user can manually correct fields:

- [ ] Correct amount
- [ ] Correct date
- [ ] Correct document type
- [ ] Correct sender/vendor
- [ ] Confirm extraction
- [ ] `AuditLog` for all field changes
- [ ] `extraction_status`: `draft` / `confirmed` / `corrected`

---

## MVP 1.2 — Presigned URLs

- [ ] Presigned download URLs for preview/download
- [ ] Presigned upload URLs later, if direct upload to MinIO is required

---

## MVP 1.5 — Google Drive JSON Backup

**Goal:** implement a simple and reliable backup strategy.

### Features

- [ ] JSON backup to Google Drive
  MVP backup includes DB records and file metadata.
  Original uploaded files are not included in JSON backup.
- [ ] Store backups in a dedicated GDrive folder (`/docsflow_backups`)
- [ ] gzip compression
- [ ] Backup metadata in DB
- [ ] Manual backup trigger
- [ ] Backup history
- [ ] Statuses: `pending` / `running` / `completed` / `failed`
- [ ] Exclude sensitive fields

### Endpoints
```text
POST /backups/run
GET  /backups
GET  /backups/{backup_id}
```

---

## MVP 2 — Search & Filtering

**Goal:** implement full-featured document search with flexible filtering.

### Features

- [ ] Search by document name
- [ ] Search by `raw_text`
- [ ] Search by extracted fields
- [ ] Filter by document type
- [ ] Filter by status
- [ ] Filter by document date
- [ ] Filter by upload date
- [ ] Filter by amount
- [ ] Filter by deadline / due_date
- [ ] Filter "requires action"
- [ ] Pagination
- [ ] Sorting
- [ ] Soft delete

---

## MVP 3 — Knowledge Base / RAG

- [ ] Q&A over uploaded documents
- [ ] Search across document content
- [ ] Source snippets
- [ ] Limit context to user's own documents
- [ ] Conversation history
- [ ] Chunk `raw_text`
- [ ] Generate embeddings
- [ ] Store vectors in pgvector
- [ ] Semantic search before Q&A

---

## Tech Stack

| Component      | Technology                         |
|----------------|------------------------------------|
| Backend        | FastAPI, SQLAlchemy 2, Pydantic v2 |
| Database       | PostgreSQL, Alembic, Redis         |
| Queue          | Celery                             |
| Storage        | MinIO / local storage              |
| AI             | OpenAI API                         |
| Integrations   | Google Drive API                   |
| Frontend       | Bootstrap 5 + Jinja2               |
| Testing        | Pytest                             |
| Infrastructure | Docker Compose                     |

---

## Core Models

- User
- Document
- DocumentExtraction
- ProcessingJob
- OpenAIUsageLog
- BackupJob
- AuditLog

---

## Document Endpoints

```text
POST   /documents/upload
GET    /documents
GET    /documents/search
GET    /documents/{document_id}
DELETE /documents/{document_id}
POST   /documents/{document_id}/reprocess
PATCH  /documents/{document_id}/extraction
POST   /documents/{document_id}/confirm
```

---

## Base Extraction Fields

- document_type
- sender / vendor
- document_date
- due_date / deadline
- amount
- currency
- language
- summary
- requires_action
- action_summary
- confidence_score

---

## Document Extraction Schema

```json
{
  "document_type": "invoice",
  "sender": "Vodafone GmbH",
  "document_date": "2026-05-20",
  "due_date": "2026-06-03",
  "amount": 49.99,
  "currency": "EUR",
  "language": "de",
  "summary": "Monthly internet invoice.",
  "requires_action": true,
  "action_summary": "Payment required.",
  "confidence_score": 0.87
}
```

---

## Confidential Mode

```
processing_mode = "standard" | "confidential"
```

If `processing_mode = confidential`, the system must not call external AI APIs.
Only local text extraction is allowed. AI extraction is skipped and marked as unavailable.

---

## Testing Focus

- [ ] Upload valid document
- [ ] Reject invalid file type
- [ ] Reject oversized file
- [ ] Prevent access to another user's document
- [ ] Create ProcessingJob after upload
- [ ] Mark document as failed when extraction fails
- [ ] Skip OpenAI calls in confidential mode
- [ ] Validate AI JSON response with Pydantic
- [ ] Save OpenAI usage log
- [ ] Apply manual correction
- [ ] Create Google Drive backup
- [ ] Exclude sensitive fields from backup
- [ ] Search by document type
- [ ] Search by deadline / due date

---

## UI Pages

```
/login
/register
/documents
/documents/upload
/documents/{document_id}
/backups
/knowledge  (later)
```

---

## Backup Scope

**Included:**
- Database records
- File metadata
- Extraction results
- Processing history
- Backup metadata

**Excluded:**
- Original uploaded PDF / image files
- Password hashes
- OAuth tokens
- Refresh tokens
- API keys
- OpenAI credentials

---

## Supported Document Types

- `invoice`
- `receipt`
- `official_letter`
- `contract`
- `medical_referral`
- `car_document`
- `unknown`