from datetime import UTC, datetime

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.openai_usage_log import OpenAIUsageLog
from app.services.document_chunking import build_document_chunk_drafts
from app.services.embeddings import create_embeddings
from app.services.rate_limits import enforce_openai_usage_quota
from app.services.text_extraction import (
    ExtractedTextPage,
    extract_text_pages_from_document,
)
from app.worker import celery_app


@celery_app.task(
    bind=True,
    name="knowledge.index_document",
    soft_time_limit=settings.document_indexing_soft_time_limit_seconds,
    time_limit=settings.document_indexing_hard_time_limit_seconds,
    max_retries=settings.document_processing_max_retries,
)
def index_document_task(self, index_job_id: int) -> None:
    """Create and persist embeddings for one completed standard document."""
    with SessionLocal() as db:
        job = db.get(DocumentIndexJob, index_job_id)
        if job is None or job.status == DocumentIndexJobStatus.completed:
            return

        if not settings.knowledge_enabled:
            job.status = DocumentIndexJobStatus.failed
            job.error_message = "Knowledge Base is disabled."
            job.finished_at = datetime.now(UTC)
            db.commit()
            return

        document = db.get(Document, job.document_id)
        if not _is_indexable(document):
            _mark_job_failed(
                db=db,
                job=job,
                error_message="Document is not eligible for external indexing.",
            )
            return

        _mark_job_running(db=db, job=job, attempts=self.request.retries + 1)

        try:
            pages = _indexing_pages(document)
            drafts = build_document_chunk_drafts(pages=pages)
            if not drafts:
                raise ValueError("Document does not contain indexable text.")

            if _existing_chunks_match(db=db, document=document, drafts=drafts):
                job.status = DocumentIndexJobStatus.completed
                job.error_message = None
                job.finished_at = datetime.now(UTC)
                db.commit()
                return

            enforce_openai_usage_quota(db=db, owner_id=document.owner_id)
            embedding_result = create_embeddings(
                texts=[draft.content for draft in drafts]
            )
            if len(embedding_result.vectors) != len(drafts):
                raise ValueError("Embedding count does not match chunk count.")

            db.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            db.add_all(
                [
                    DocumentChunk(
                        document_id=document.id,
                        owner_id=document.owner_id,
                        chunk_index=draft.chunk_index,
                        content=draft.content,
                        page_from=draft.page_from,
                        page_to=draft.page_to,
                        token_count=draft.token_count,
                        content_sha256=draft.content_sha256,
                        embedding=embedding,
                        embedding_model=settings.openai_embedding_model,
                    )
                    for draft, embedding in zip(
                        drafts,
                        embedding_result.vectors,
                        strict=True,
                    )
                ]
            )
            db.add(
                OpenAIUsageLog(
                    document_id=document.id,
                    owner_id=document.owner_id,
                    operation="document_embedding",
                    model=settings.openai_embedding_model,
                    input_tokens=embedding_result.input_tokens,
                    total_tokens=embedding_result.total_tokens,
                )
            )
            job.status = DocumentIndexJobStatus.completed
            job.error_message = None
            job.finished_at = datetime.now(UTC)
            db.commit()

        except SoftTimeLimitExceeded as exc:
            _handle_indexing_failure(task=self, db=db, job_id=index_job_id, exc=exc)
        except Exception as exc:
            _handle_indexing_failure(task=self, db=db, job_id=index_job_id, exc=exc)


def _is_indexable(document: Document | None) -> bool:
    return bool(
        document
        and document.status == DocumentStatus.completed
        and document.processing_mode == ProcessingMode.standard
        and document.deleted_at is None
        and document.raw_text
    )


def _indexing_pages(document: Document) -> list[ExtractedTextPage]:
    """Preserve source pages, falling back to restored raw text without a file."""
    try:
        return extract_text_pages_from_document(document)
    except ValueError as exc:
        if (
            not document.storage_key
            and document.raw_text
            and str(exc) == "Document has no storage key."
        ):
            return [ExtractedTextPage(page_number=1, text=document.raw_text)]
        raise


def _existing_chunks_match(*, db, document: Document, drafts) -> bool:
    chunks = list(
        db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    if len(chunks) != len(drafts):
        return False

    return all(
        chunk.content_sha256 == draft.content_sha256
        and chunk.page_from == draft.page_from
        and chunk.page_to == draft.page_to
        and chunk.embedding_model == settings.openai_embedding_model
        and chunk.embedding is not None
        for chunk, draft in zip(chunks, drafts, strict=True)
    )


def _mark_job_running(
    *,
    db,
    job: DocumentIndexJob,
    attempts: int,
) -> None:
    job.status = DocumentIndexJobStatus.running
    job.attempts = attempts
    job.started_at = job.started_at or datetime.now(UTC)
    job.finished_at = None
    job.error_message = None
    db.commit()


def _mark_job_failed(
    *,
    db,
    job: DocumentIndexJob,
    error_message: str,
) -> None:
    job.status = DocumentIndexJobStatus.failed
    job.error_message = error_message[:2000]
    job.finished_at = datetime.now(UTC)
    db.commit()


def _handle_indexing_failure(*, task, db, job_id: int, exc: Exception) -> None:
    db.rollback()
    job = db.get(DocumentIndexJob, job_id)
    if job is None:
        raise exc

    error_message = str(exc)[:2000]
    if task.request.retries < task.max_retries:
        job.status = DocumentIndexJobStatus.pending
        job.error_message = error_message
        job.finished_at = None
        db.commit()
        raise task.retry(
            exc=exc,
            countdown=settings.document_processing_retry_delay_seconds,
        )

    _mark_job_failed(db=db, job=job, error_message=error_message)
    raise exc
