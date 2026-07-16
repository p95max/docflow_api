from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.document import DocumentStatus, ExtractionStatus, ProcessingMode


class RecoveryDocumentV2(BaseModel):
    id: int = Field(gt=0)
    owner_id: int = Field(gt=0)
    original_filename: str = Field(min_length=1, max_length=255)
    status: DocumentStatus
    processing_mode: ProcessingMode
    content_type: str | None = Field(default=None, max_length=100)
    file_size_bytes: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    storage_key: str | None = Field(default=None, max_length=500)
    raw_text: str | None = None
    document_type: str | None = Field(default=None, max_length=50)
    ai_extracted_data: dict[str, Any] | None = None
    summary: str | None = None
    user_note: str | None = Field(default=None, max_length=5000)
    amount: Decimal | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    deadline: date | None = None
    document_date: date | None = None
    sender: str | None = Field(default=None, max_length=255)
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    validation_status: str | None = Field(default=None, max_length=20)
    validation_errors: list[str] | None = None
    validation_warnings: list[str] | None = None
    validation_score: float | None = Field(default=None, ge=0, le=100)
    validation_evidence: dict[str, Any] | None = None
    validation_candidates: dict[str, Any] | None = None
    validation_flags: list[str] | None = None
    ocr_quality_score: float | None = Field(default=None, ge=0, le=100)
    ai_extraction_model: str | None = Field(default=None, max_length=100)
    manual_corrections: dict[str, Any] | None = None
    manually_corrected_at: datetime | None = None
    extraction_status: ExtractionStatus
    extraction_confirmed_at: datetime | None = None
    ai_extraction_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class RecoveryRecordsV2(BaseModel):
    users: list[dict[str, Any]]
    documents: list[RecoveryDocumentV2]
    processing_jobs: list[dict[str, Any]]
    openai_usage_logs: list[dict[str, Any]]
    audit_logs: list[dict[str, Any]]
    backup_jobs: list[dict[str, Any]]

    model_config = ConfigDict(extra="forbid")


class RecoveryBackupPayloadV2(BaseModel):
    schema_version: Literal[2]
    generated_at: datetime
    owner_id: int = Field(gt=0)
    record_counts: dict[str, int]
    records: RecoveryRecordsV2

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_record_counts(self) -> "RecoveryBackupPayloadV2":
        actual_counts = {
            name: len(value)
            for name, value in self.records.model_dump().items()
        }
        if self.record_counts != actual_counts:
            raise ValueError("record_counts does not match records")
        return self
