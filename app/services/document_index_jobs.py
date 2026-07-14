from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.core.config import settings


def prepare_document_index_job(
    *,
    db: Session,
    document: Document,
) -> DocumentIndexJob | None:
    """Create or reset a job for an indexable document."""
    if not settings.knowledge_enabled:
        return None

    if (
        document.status != DocumentStatus.completed
        or document.processing_mode != ProcessingMode.standard
        or document.deleted_at is not None
        or not document.raw_text
    ):
        return None

    job = db.scalar(
        select(DocumentIndexJob).where(
            DocumentIndexJob.document_id == document.id
        )
    )
    if job is None:
        job = DocumentIndexJob(document_id=document.id)
        db.add(job)
        db.flush()
        return job

    job.status = DocumentIndexJobStatus.pending
    job.celery_task_id = None
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    db.add(job)
    db.flush()
    return job


def enqueue_document_index_job(
    *,
    db: Session,
    document: Document,
) -> DocumentIndexJob | None:
    """Queue indexing without allowing enqueue failures to fail document processing."""
    job = prepare_document_index_job(db=db, document=document)
    if job is None:
        return None

    db.commit()
    db.refresh(job)

    try:
        from app.tasks.knowledge import index_document_task

        result = index_document_task.delay(job.id)
    except Exception as exc:
        db.rollback()
        current_job = db.get(DocumentIndexJob, job.id)
        if current_job is None:
            return None

        current_job.status = DocumentIndexJobStatus.failed
        current_job.error_message = f"Failed to enqueue indexing task: {exc}"[:2000]
        current_job.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(current_job)
        return current_job

    current_job = db.get(DocumentIndexJob, job.id)
    if current_job is not None:
        current_job.celery_task_id = result.id
        db.commit()
        db.refresh(current_job)

    return current_job
