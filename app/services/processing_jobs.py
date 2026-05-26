from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingOperationType,
)
from app.tasks.documents import process_document_task


def create_processing_job(
    db: Session,
    document: Document,
    operation_type: ProcessingOperationType = ProcessingOperationType.text_extraction,
) -> ProcessingJob:
    job = ProcessingJob(
        document_id=document.id,
        operation_type=operation_type,
        status=ProcessingJobStatus.pending,
        max_retries=settings.document_processing_max_retries,
    )

    db.add(job)
    db.flush()

    return job


def enqueue_processing_job(
    db: Session,
    job: ProcessingJob,
) -> ProcessingJob:
    async_result = process_document_task.delay(job.id)

    job.celery_task_id = async_result.id

    db.add(job)
    db.commit()
    db.refresh(job)

    return job


def list_processing_jobs_for_document(
    db: Session,
    document_id: int,
) -> list[ProcessingJob]:
    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.document_id == document_id)
        .order_by(ProcessingJob.created_at.desc())
    )

    return list(db.scalars(stmt).all())