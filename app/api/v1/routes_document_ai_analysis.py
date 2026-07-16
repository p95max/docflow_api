from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.audit_log import AuditLog
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.schemas.processing_job import ProcessingJobRead
from app.services.processing_jobs import create_processing_job, enqueue_processing_job

router = APIRouter()


@router.post(
    "/{document_id}/analyze-with-ai",
    response_model=ProcessingJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def analyze_document_with_ai(
    document_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> ProcessingJobRead:
    document = db.get(Document, document_id)
    if (
        document is None
        or document.owner_id != current_user.id
        or document.deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.status in {DocumentStatus.uploaded, DocumentStatus.processing}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is already being processed.",
        )

    previous_mode = document.processing_mode
    document.processing_mode = ProcessingMode.standard
    document.status = DocumentStatus.uploaded

    processing_job = create_processing_job(db=db, document=document)
    db.add(
        AuditLog(
            document_id=document.id,
            user_id=current_user.id,
            action="document_ai_analysis_requested",
            field_name="processing_mode",
            old_value={"processing_mode": previous_mode.value},
            new_value={
                "processing_mode": ProcessingMode.standard.value,
                "requested_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    db.commit()
    db.refresh(processing_job)

    return enqueue_processing_job(db=db, job=processing_job)
