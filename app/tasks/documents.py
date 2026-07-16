from datetime import UTC, date, datetime
from decimal import Decimal

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.document import (
    Document,
    DocumentStatus,
    ExtractionStatus,
    ProcessingMode,
)
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.processing_job import ProcessingJob, ProcessingJobStatus
from app.services.ai_processing import StandardAIProcessingResult, run_standard_ai_processing
from app.services.document_index_jobs import enqueue_document_index_job
from app.services.extraction_validation import (
    ExtractionValidationResult,
    validate_ai_extraction,
)
from app.services.local_document_classification import classify_document_type
from app.services.rate_limits import enforce_openai_usage_quota
from app.services.text_extraction import (
    extract_text_from_document,
    extract_text_pages_from_document,
)
from app.worker import celery_app


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

            if document.processing_mode == ProcessingMode.confidential:
                local_document_type = classify_document_type(extracted_text)

                if local_document_type != "other":
                    document.document_type = local_document_type
            else:
                enforce_openai_usage_quota(db=db, owner_id=document.owner_id)
                ai_result = run_standard_ai_processing(
                    raw_text=extracted_text,
                    original_filename=document.original_filename,
                )
                source_pages = None
                if ai_result.extracted_data.evidence.model_dump(exclude_none=True):
                    source_pages = extract_text_pages_from_document(document)
                validation_result = validate_ai_extraction(
                    extraction=ai_result.extracted_data,
                    raw_text=extracted_text,
                    source_pages=source_pages,
                )
                _apply_standard_ai_processing_result(
                    db=db,
                    document=document,
                    ai_result=ai_result,
                    validation_result=validation_result,
                )

            document.status = DocumentStatus.completed

            job.status = ProcessingJobStatus.completed
            job.error_message = None
            job.finished_at = datetime.now(UTC)

            db.commit()
            enqueue_document_index_job(db=db, document=document)

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
    job.error_message = None

    _reset_document_processing_result(document)
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
    should_retry = task.request.retries < job.max_retries

    job_id = job.id
    document_id = document.id

    db.rollback()

    current_job = db.get(ProcessingJob, job_id)
    current_document = db.get(Document, document_id)

    if current_job is None:
        raise exc

    if current_document is None:
        _mark_job_failed(
            db=db,
            job=current_job,
            error_message="Document does not exist.",
        )
        raise exc

    if should_retry:
        current_job.status = ProcessingJobStatus.pending
        current_job.error_message = safe_error_message
        current_job.finished_at = None

        current_document.status = DocumentStatus.uploaded

        db.commit()

        raise task.retry(
            exc=exc,
            countdown=settings.document_processing_retry_delay_seconds,
        )

    current_job.status = ProcessingJobStatus.failed
    current_job.error_message = safe_error_message
    current_job.finished_at = datetime.now(UTC)

    current_document.status = DocumentStatus.failed

    db.commit()

    raise exc


def _apply_standard_ai_processing_result(
    *,
    db: Session,
    document: Document,
    ai_result: StandardAIProcessingResult,
    validation_result: ExtractionValidationResult,
) -> None:
    extracted_data = ai_result.extracted_data

    document.document_type = extracted_data.document_type
    document.ai_extracted_data = extracted_data.model_dump(mode="json")
    document.ai_extraction_model = ai_result.model
    document.ai_extraction_completed_at = datetime.now(UTC)

    document.summary = extracted_data.summary
    document.amount = _convert_amount(extracted_data.total_amount)
    document.currency = _normalize_currency(extracted_data.currency)
    document.document_date = _parse_iso_date(
        extracted_data.document_date,
        field_name="document date",
    )
    document.deadline = _extract_deadline(
        action_deadline=extracted_data.action_deadline,
        due_date=extracted_data.due_date,
    )
    document.sender = extracted_data.sender
    document.confidence_score = extracted_data.confidence_score
    document.validation_status = validation_result.status
    document.validation_errors = validation_result.errors or None
    document.validation_warnings = validation_result.warnings or None
    document.validation_score = validation_result.score
    document.validation_evidence = (
        {
            field_name: evidence.model_dump(mode="json")
            for field_name, evidence in validation_result.evidence.items()
        }
        or None
    )
    document.extraction_status = ExtractionStatus.draft
    document.extraction_confirmed_at = None
    document.manual_corrections = None
    document.manually_corrected_at = None

    db.add(
        OpenAIUsageLog(
            document_id=document.id,
            owner_id=document.owner_id,
            operation="document_ai_extraction",
            model=ai_result.model,
            response_id=ai_result.response_id,
            input_tokens=ai_result.usage.input_tokens,
            output_tokens=ai_result.usage.output_tokens,
            total_tokens=ai_result.usage.total_tokens,
        )
    )


def _reset_document_processing_result(document: Document) -> None:
    """Remove stale results before a new processing attempt."""
    document.raw_text = None

    document.document_type = None
    document.ai_extracted_data = None
    document.ai_extraction_model = None
    document.ai_extraction_completed_at = None

    document.summary = None
    document.amount = None
    document.currency = None
    document.deadline = None
    document.document_date = None
    document.sender = None
    document.confidence_score = None
    document.validation_status = None
    document.validation_errors = None
    document.validation_warnings = None
    document.validation_score = None
    document.validation_evidence = None

    document.extraction_status = ExtractionStatus.draft
    document.extraction_confirmed_at = None
    document.manual_corrections = None
    document.manually_corrected_at = None


def _convert_amount(value: float | None) -> Decimal | None:
    if value is None:
        return None

    return Decimal(str(value))


def _normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None

    normalized_value = value.strip().upper()

    if len(normalized_value) != 3 or not normalized_value.isalpha():
        raise ValueError(
            f"AI returned invalid ISO currency code: {value}"
        )

    return normalized_value


def _extract_deadline(
    *,
    action_deadline: str | None,
    due_date: str | None,
) -> date | None:
    value = action_deadline or due_date

    if value is None:
        return None

    return _parse_iso_date(value, field_name="deadline")


def _parse_iso_date(
    value: str | None,
    *,
    field_name: str,
) -> date | None:
    if value is None:
        return None

    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"AI returned invalid ISO {field_name}: {value}"
        ) from exc
