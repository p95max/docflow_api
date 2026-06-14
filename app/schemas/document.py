from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, ProcessingMode
from app.schemas.ai_processing import DocumentType
from app.schemas.processing_job import ProcessingJobRead


class DocumentRead(BaseModel):
    id: int
    original_filename: str
    status: DocumentStatus
    processing_mode: ProcessingMode
    content_type: str | None
    file_size_bytes: int | None
    checksum_sha256: str | None

    document_type: str | None
    ai_extracted_data: dict[str, Any] | None
    ai_extraction_model: str | None
    ai_extraction_completed_at: datetime | None

    summary: str | None
    amount: float | None
    currency: str | None
    deadline: date | None
    sender: str | None
    confidence_score: float | None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentCorrection(BaseModel):
    document_type: DocumentType | None = None

    summary: str | None = Field(
        default=None,
        max_length=5000,
    )

    amount: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=14,
        decimal_places=2,
    )

    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
    )

    deadline: date | None = None

    sender: str | None = Field(
        default=None,
        max_length=255,
    )

    confidence_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )


class DocumentResultRead(DocumentRead):
    raw_text: str | None

    file_preview_url: str | None
    file_preview_expires_at: datetime | None

    manual_corrections: dict[str, Any] | None
    manually_corrected_at: datetime | None

    latest_job: ProcessingJobRead | None
    processing_error: str | None

    can_correct: bool
    can_reprocess: bool