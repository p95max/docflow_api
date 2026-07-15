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

- [x] Display document with extraction results
- [x] File preview via storage URL / presigned URL
- [x] Show manual correction controls
- [x] Show error details if processing failed
- [x] Trigger manual reprocess

---

## MVP 1.1 — Manual Correction

After AI extraction, user can manually correct fields:

- [x] Correct amount
- [x] Correct date
- [x] Correct document type
- [x] Correct sender/vendor
- [x] Confirm extraction
- [x] `AuditLog` for all field changes
- [x] `extraction_status`: `draft` / `confirmed` / `corrected`

---

## MVP 1.2 — Presigned URLs

- [x] Presigned download URLs for preview/download

---

## Frontend — Bootstrap 5

**Goal:** provide a simple responsive web interface over the existing API.

- [x] Bootstrap 5 application shell and responsive navigation
- [x] Login and registration pages using the existing JWT API
- [x] Document list with status and document type
- [x] Upload page with confidential-mode switch
- [x] Document result page with preview and signed download URL
- [x] Manual correction and extraction confirmation controls
- [x] Static frontend assets served by FastAPI
- [x] Smoke tests for frontend pages and assets

---

## MVP 1.5 — Google Drive JSON Backup

**Goal:** implement a simple and reliable backup strategy.

### Features

- [x] JSON backup to Google Drive
  MVP backup includes DB records and file metadata.
  Original uploaded files are not included in JSON backup.
- [x] Store backups in a dedicated GDrive folder (`/docflow_backup`)
- [x] gzip compression
- [x] Backup metadata in DB
- [x] Manual backup trigger
- [x] Backup history
- [x] Statuses: `pending` / `running` / `completed` / `failed`
- [x] Exclude sensitive fields

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

- [x] Search by document name
- [x] Search by `raw_text`
- [x] Search by extracted fields
- [x] Filter by document type
- [x] Filter by status
- [x] Filter by document date
- [x] Filter by upload date
- [x] Filter by amount
- [x] Filter by deadline / due_date
- [x] Filter "requires action"
- [x] Pagination
- [x] Sorting
- [x] Soft delete

---

## MVP 3 — Knowledge Base / RAG

- [x] Q&A over uploaded documents
- [x] Search across document content
- [x] Source snippets
- [x] Limit context to user's own documents
- [x] Conversation history
- [x] Chunk `raw_text`
- [x] Generate embeddings
- [x] Store vectors in pgvector
- [x] Semantic search before Q&A

### Implementation Plan

#### MVP 3.1 — Document Indexing and Semantic Search

- [x] Replace the PostgreSQL Docker image with `pgvector/pgvector:pg16`
- [x] Add the `pgvector` Python dependency
- [x] Add Alembic migration with `CREATE EXTENSION IF NOT EXISTS vector`
- [x] Add `DocumentChunk` model:
  - `document_id`
  - `owner_id`
  - `chunk_index`
  - `content`
  - `page_from` / `page_to`
  - `token_count`
  - `content_sha256`
  - `embedding`
  - `embedding_model`
  - `created_at`
- [x] Add `DocumentIndexJob` with statuses:
  - `pending`
  - `running`
  - `completed`
  - `failed`
- [x] Preserve page numbers during PDF text extraction
- [x] Treat JPG and PNG documents as page 1
- [x] Split extracted text into chunks of approximately 500–800 tokens
- [x] Add approximately 100 tokens of overlap between adjacent chunks
- [x] Configure embeddings separately from the answer model:
  - `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`
  - `OPENAI_EMBEDDING_DIMENSIONS=1536`
- [x] Generate embeddings in a separate Celery task
- [x] Do not fail completed document processing when indexing fails
- [x] Make indexing idempotent using content hash and embedding model
- [x] Replace old chunks only after all new embeddings are ready
- [x] Add semantic search service using cosine distance
- [x] Start with exact pgvector search; add HNSW only after benchmarks
- [x] Add endpoint `POST /api/v1/knowledge/search`
- [x] Return document name, page number, snippet, and similarity score

#### MVP 3.2 — Conversations and Q&A

- [x] Add `KnowledgeConversation` model owned by a user
- [x] Add `KnowledgeMessage` model with `user` / `assistant` roles
- [x] Add `KnowledgeMessageSource` model
- [x] Store a snapshot of filename, page, snippet, and score for each source
- [x] Extend AI usage logging for embeddings and multi-document Q&A
- [x] Add a shared OpenAI client factory for extraction, embeddings, and Q&A
- [x] Implement the RAG flow:
  1. [x] Validate conversation ownership
  2. [x] Embed the user question
  3. [x] Retrieve the nearest owned active document chunks
  4. [x] Build a size-limited context
  5. [x] Generate an answer grounded only in the retrieved context
  6. [x] Validate returned source references against retrieved chunks
  7. [x] Persist the question, answer, usage, and sources
- [x] Return a clear "not found in documents" answer when context is insufficient
- [x] Treat document content as untrusted input in the RAG prompt
- [x] Limit conversation history by message count and token budget
- [x] Limit a user question to 300 characters
- [x] Enforce the 300-character question limit in the API schema, not only in the UI
- [x] Add endpoints:
  - `POST /api/v1/knowledge/conversations`
  - `GET /api/v1/knowledge/conversations`
  - `GET /api/v1/knowledge/conversations/{conversation_id}`
  - `POST /api/v1/knowledge/conversations/{conversation_id}/messages`

#### MVP 3.3 — Bootstrap UI and Operations

- [x] Add server-rendered `/knowledge` page without JavaScript
- [x] Add conversation list and conversation detail pages
- [x] Add question form and render source cards under each answer
- [x] Set question textarea `maxlength="300"`
- [x] Explain that questions must be one simple sentence about uploaded documents
- [x] Show an example: "Which invoices are due this month?"
- [x] Link sources to the owned document and show page numbers
- [x] Display document indexing status and errors
- [x] Add a manual reindex action
- [x] Add `KNOWLEDGE_ENABLED` feature flag: disable UI/API access and new OpenAI embedding work
- [x] Exclude deleted documents from retrieval without deleting conversation history
- [x] Exclude chunks and conversation content from Google Drive JSON backup
- [x] Document RAG configuration and indexing commands in README

#### Security and Confidential Mode

- [x] Always filter retrieval by `owner_id`
- [x] Always exclude documents with `deleted_at IS NOT NULL`
- [x] Validate requested document IDs belong to the current user
- [x] Do not send `confidential` documents to external embeddings or Q&A APIs
- [x] Show that confidential documents are unavailable in Knowledge Base

#### Testing Focus

- [x] Test deterministic page-aware chunking and overlap
- [x] Test failed embedding retries
- [x] Test idempotent reindexing
- [x] Test that confidential documents never trigger external AI calls
- [x] Test that source IDs cannot be invented by the answer model
- [x] Test conversation ownership and history isolation
- [x] Keep SQLite unit tests for pure services and models
- [x] Add PostgreSQL integration tests for vector ranking, ownership, soft deletion, extension, and indexes

---

## Tech Stack

| Component      | Technology                         |
|----------------|------------------------------------|
| Backend        | FastAPI, SQLAlchemy 2, Pydantic v2 |
| Database       | PostgreSQL, Alembic, Redis         |
| Queue          | Celery                             |
| Storage        | Local filesystem                   |
| AI             | OpenAI API                         |
| Integrations   | Google Drive API                   |
| Frontend       | Bootstrap 5 + vanilla JavaScript   |
| Testing        | Pytest                             |
| Infrastructure | Docker Compose                     |

---

## Core Models

- User
- Document
- ProcessingJob
- OpenAIUsageLog
- BackupJob
- AuditLog
- GoogleDriveConnection
- DocumentChunk
- DocumentIndexJob
- KnowledgeConversation / KnowledgeMessage / KnowledgeMessageSource

---

## Document Endpoints

```text
POST   /api/v1/documents/upload
GET    /api/v1/documents
GET    /api/v1/documents/{document_id}
GET    /api/v1/documents/{document_id}/result
DELETE /api/v1/documents/{document_id}
POST   /api/v1/documents/{document_id}/reprocess
PATCH  /api/v1/documents/{document_id}/extraction
POST   /api/v1/documents/{document_id}/confirm
GET    /api/v1/documents/{document_id}/download-url
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

- [x] Upload valid document
- [x] Reject invalid file type
- [x] Reject oversized file
- [x] Create ProcessingJob
- [x] Mark document as failed when extraction fails
- [x] Skip OpenAI calls in confidential mode
- [x] Save OpenAI usage log
- [x] Apply manual correction
- [x] Create Google Drive backup
- [x] Exclude sensitive fields from backup
- [x] Search by document type
- [x] Search by deadline / due date

---

## UI Pages

```
/login
/register
/documents
/documents/upload
/documents/{document_id}
/backups
/knowledge
```

---

## Backup Scope

**Included:**
- Database records
- File metadata
- Extraction results
- Extracted document `raw_text` inside the encrypted recovery archive
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

---

## Security and Hardening Audit

Items were reconciled with the code and test suite on 2026-07-15.

### Completed in the current hardening pass

- [x] JWT access-token purpose validation, preventing document and OAuth JWTs from
  authenticating API or web sessions.
- [x] Signed preview/download URLs are rejected after a document is soft-deleted.
- [x] Codespaces login no longer pre-fills local test credentials.
- [x] Baseline browser security headers and a Content Security Policy.
- [x] One-sentence validation for Knowledge questions.
- [x] Bounded recovery restore upload, gzip expansion, document count, and per-document
  extracted text size.
- [x] CSRF protection for all cookie-authenticated HTML POST forms.
- [x] Celery enqueue failures now persist failed processing job/document state.
- [x] Concurrent duplicate registration returns 409 instead of a database 500.
- [x] Pytest discovery is limited to `tests/`.
- [x] Google Drive refresh tokens are encrypted at rest, legacy plaintext tokens
  are migrated on startup, and active/previous-key rotation is documented.
- [x] Regression tests verify encrypted OAuth token storage, legacy-token migration,
  and re-encryption with a rotated key.

### Unfinished and Follow-up Work

### Security — high priority

- [ ] Add shared Redis-backed rate limits for login, registration, uploads,
  semantic search, and Q&A; add per-user OpenAI usage/cost quotas.
- [ ] Harden deployment defaults: require a non-placeholder 32+ byte
  `APP_SECRET_KEY`, remove `--reload` outside local development, and do not expose
  PostgreSQL/Redis with default credentials in a deployment configuration.
- [ ] Self-host Bootstrap or pin the CDN asset with integrity metadata.

### Backup and restore hardening

- [ ] Validate restore payloads with a versioned Pydantic schema and handle DB
  constraint/data errors as a clean rollback plus a user-facing 422 response.
- [ ] Put legacy plaintext JSON/gzip restore behind an explicit migration mode or
  add authenticity/integrity verification; normal restore should require an
  encrypted recovery archive.
- [ ] Track a recovery-key identifier per backup and support downloading the raw
  encrypted archive, so backups made before key rotation remain usable through
  DocsFlow with their old key.
- [ ] Decide and document the recovery-key trust model: the current server can
  decrypt the stored per-user key with `BACKUP_MASTER_KEY`, so the UI must not
  imply that DocsFlow is technically unable to recover it.

### Reliability and functional gaps

- [ ] Replace the process-local upload limiter with the shared limiter above so
  multiple API workers cannot bypass it.
- [ ] Keep paragraph boundaries where possible during document chunking.
- [ ] Add a separate local embedding and local LLM design before supporting
  confidential documents in RAG.
- [ ] Improve client-side form validation and loading states.
- [ ] Add browser end-to-end tests.
- [ ] Add presigned upload URLs if direct upload to object storage is introduced.

### Verification and missing regression tests

- [ ] Run the existing PostgreSQL/pgvector integration suite and confirm semantic
  ranking, owner isolation, soft-delete filtering, extension, and vector indexes.
- [ ] Add explicit cross-user tests for document detail/result and signed-file
  access.
- [ ] Add a negative test proving malformed AI structured output is rejected by
  Pydantic and does not persist partial extraction/usage data.
- [ ] Add tests for auth/Q&A rate limits.
