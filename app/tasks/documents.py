from datetime import UTC, datetime
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.processing_job import ProcessingJob, ProcessingJobStatus
from app.worker import celery_app
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.services.text_extraction import extract_text_from_document


@celery_app.task(
    bind=True,
    name="documents.process_document",
    soft_time_limit=settings.document_processing_soft_time_limit_seconds,
    time_limit=settings.document_processing_hard_time_limit_seconds,
    max_retries=settings.document_processing_max_retries,
)
def process_document_task(self, job_id: int) -> None:
    """Run asynchronous document processing for a single ProcessingJob."""
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)

        if job is None:
            return

        document = db.get(Document, job.document_id)

        if document is None:
            _mark_job_failed(
                db=db,
                job=job,
                error_message="Document does not exist.",
            )
            return

        try:
            _mark_job_running(
                db=db,
                job=job,
                document=document,
                attempts=self.request.retries + 1,
            )

            extracted_text = extract_text_from_document(document=document)

            document.raw_text = extracted_text
            document.status = _get_success_status(document=document)

            job.status = ProcessingJobStatus.completed
            job.error_message = None
            job.finished_at = datetime.now(UTC)

            db.commit()

        except SoftTimeLimitExceeded as exc:
            _handle_processing_failure(
                task=self,
                db=db,
                job=job,
                document=document,
                exc=exc,
                error_message="Document processing soft time limit exceeded.",
            )

        except Exception as exc:
            _handle_processing_failure(
                task=self,
                db=db,
                job=job,
                document=document,
                exc=exc,
                error_message=str(exc),
            )


def _mark_job_running(
    db: Session,
    job: ProcessingJob,
    document: Document,
    attempts: int,
) -> None:
    now = datetime.now(UTC)

    job.status = ProcessingJobStatus.running
    job.attempts = attempts
    job.started_at = job.started_at or now
    job.finished_at = None

    document.status = DocumentStatus.processing

    db.commit()


def _mark_job_failed(
    db: Session,
    job: ProcessingJob,
    error_message: str,
) -> None:
    job.status = ProcessingJobStatus.failed
    job.error_message = error_message[:2000]
    job.finished_at = datetime.now(UTC)

    db.commit()


def _handle_processing_failure(
    task,
    db: Session,
    job: ProcessingJob,
    document: Document,
    exc: Exception,
    error_message: str,
) -> None:
    safe_error_message = error_message[:2000]

    if task.request.retries < job.max_retries:
        job.status = ProcessingJobStatus.pending
        job.error_message = safe_error_message
        document.status = DocumentStatus.uploaded

        db.commit()

        raise task.retry(
            exc=exc,
            countdown=settings.document_processing_retry_delay_seconds,
        )

    job.status = ProcessingJobStatus.failed
    job.error_message = safe_error_message
    job.finished_at = datetime.now(UTC)

    document.status = DocumentStatus.failed

    db.commit()

    raise exc


def _extract_text_placeholder(document: Document) -> str:
    if not document.storage_key:
        raise ValueError("Document has no storage key.")

    file_path = Path(settings.local_storage_path) / document.storage_key

    if not file_path.exists():
        raise FileNotFoundError(f"Stored file does not exist: {document.storage_key}")

    return (
        "Document processing completed.\n"
        f"Original filename: {document.original_filename}\n"
        f"Content type: {document.content_type}\n"
        f"File size bytes: {document.file_size_bytes}\n"
        f"Storage key: {document.storage_key}\n"
    )

def _get_success_status(document: Document) -> DocumentStatus:
    if document.processing_mode == ProcessingMode.confidential:
        return DocumentStatus.completed

    # MVP 1 step 6 will continue standard-mode documents with AI extraction.
    # At step 5 there are intentionally no external API calls here.
    return DocumentStatus.completed