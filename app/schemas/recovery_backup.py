from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.document import DocumentStatus, ExtractionStatus, ProcessingMode
from app.models.calendar_event import CalendarEventSource, CalendarEventStatus, CalendarEventType
from app.models.event_reminder import EventReminderChannel, EventReminderStatus


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
    fallback_extraction: dict[str, Any] | None = None
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


class RecoveryCalendarEventV3(BaseModel):
    id: int = Field(gt=0)
    document_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    event_type: CalendarEventType
    status: CalendarEventStatus
    source: CalendarEventSource
    all_day: bool
    start_date: date | None = None
    end_date: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    source_field: str | None = Field(default=None, max_length=100)
    source_key: str | None = Field(default=None, max_length=255)
    source_evidence: dict[str, Any] = Field(default_factory=dict)
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    requires_review: bool
    detached_from_source: bool
    completed_at: datetime | None = None
    ical_uid: str = Field(min_length=1, max_length=255)
    sequence: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_time_representation(self) -> "RecoveryCalendarEventV3":
        if self.all_day:
            if self.start_date is None or self.start_at is not None or self.end_at is not None:
                raise ValueError("all-day events require start_date and must not contain datetimes")
            if self.end_date is not None and self.end_date < self.start_date:
                raise ValueError("end_date must not be before start_date")
            return self

        if self.start_at is None or self.start_date is not None or self.end_date is not None:
            raise ValueError("timed events require start_at and must not contain date-only fields")
        if not self.timezone:
            raise ValueError("timed events require a timezone")
        if self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("end_at must not be before start_at")
        return self


class RecoveryEventReminderV3(BaseModel):
    id: int = Field(gt=0)
    event_id: int = Field(gt=0)
    channel: EventReminderChannel
    offset_minutes: int = Field(ge=0)
    scheduled_for: datetime
    status: EventReminderStatus
    last_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    attempts: int = Field(ge=0)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(extra="forbid")


class RecoveryNotificationV3(BaseModel):
    id: int = Field(gt=0)
    event_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=255)
    body: str
    read_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(extra="forbid")


class RecoveryRecordsV3(BaseModel):
    users: list[dict[str, Any]]
    documents: list[RecoveryDocumentV2]
    processing_jobs: list[dict[str, Any]]
    openai_usage_logs: list[dict[str, Any]]
    audit_logs: list[dict[str, Any]]
    backup_jobs: list[dict[str, Any]]
    calendar_events: list[RecoveryCalendarEventV3]
    event_reminders: list[RecoveryEventReminderV3]
    notifications: list[RecoveryNotificationV3]

    model_config = ConfigDict(extra="forbid")


class RecoveryBackupPayloadV3(BaseModel):
    schema_version: Literal[3]
    generated_at: datetime
    owner_id: int = Field(gt=0)
    record_counts: dict[str, int]
    records: RecoveryRecordsV3

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_record_counts(self) -> "RecoveryBackupPayloadV3":
        actual_counts = {
            name: len(value)
            for name, value in self.records.model_dump().items()
        }
        if self.record_counts != actual_counts:
            raise ValueError("record_counts does not match records")
        return self
