from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.timezones import normalize_datetime_to_utc, validate_iana_timezone
from app.models.calendar_event import (
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)


class _CalendarEventFields(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    event_type: CalendarEventType
    all_day: bool = True
    start_date: date | None = None
    end_date: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    document_id: int | None = Field(default=None, gt=0)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title must not be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_blank_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_iana(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iana_timezone(value)

    @field_validator("start_at", "end_at")
    @classmethod
    def datetime_must_be_aware_and_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        return normalize_datetime_to_utc(value)


class CalendarEventCreate(_CalendarEventFields):
    """User-controlled fields for a manually created calendar event."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_event_representation(self) -> CalendarEventCreate:
        if self.all_day:
            if self.start_date is None:
                raise ValueError("All-day events require start_date.")
            if self.start_at is not None or self.end_at is not None:
                raise ValueError("All-day events cannot include start_at or end_at.")
            if self.end_date is not None and self.end_date < self.start_date:
                raise ValueError("end_date must not be earlier than start_date.")
            return self

        if self.start_at is None:
            raise ValueError("Timed events require start_at.")
        if self.start_date is not None or self.end_date is not None:
            raise ValueError("Timed events cannot include start_date or end_date.")
        if self.timezone is None:
            raise ValueError("Timed events require an IANA timezone.")
        if self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("end_at must not be earlier than start_at.")
        return self


class CalendarEventUpdate(BaseModel):
    """User-controlled mutable fields; lifecycle changes use dedicated actions."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    event_type: CalendarEventType | None = None
    all_day: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    document_id: int | None = Field(default=None, gt=0)
    expected_sequence: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def update_title_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title must not be blank.")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_update_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("timezone")
    @classmethod
    def update_timezone_must_be_iana(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iana_timezone(value)

    @field_validator("start_at", "end_at")
    @classmethod
    def update_datetime_must_be_aware_and_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        return normalize_datetime_to_utc(value)

    @model_validator(mode="after")
    def validate_partial_event_representation(self) -> CalendarEventUpdate:
        fields = self.model_fields_set
        if "title" in fields and self.title is None:
            raise ValueError("title cannot be null.")

        if self.all_day is True:
            if self.start_at is not None or self.end_at is not None:
                raise ValueError("All-day events cannot include start_at or end_at.")
        elif self.all_day is False:
            if self.start_date is not None or self.end_date is not None:
                raise ValueError("Timed events cannot include start_date or end_date.")

        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must not be earlier than start_date.")
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at < self.start_at
        ):
            raise ValueError("end_at must not be earlier than start_at.")
        return self


class CalendarEventRead(BaseModel):
    id: int
    public_id: uuid.UUID
    document_id: int | None
    title: str
    description: str | None
    event_type: CalendarEventType
    status: CalendarEventStatus
    source: CalendarEventSource
    all_day: bool
    start_date: date | None
    end_date: date | None
    start_at: datetime | None
    end_at: datetime | None
    timezone: str | None
    source_field: str | None
    source_evidence: dict[str, Any]
    confidence_score: float | None
    requires_review: bool
    detached_from_source: bool
    completed_at: datetime | None
    ical_uid: str
    sequence: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CalendarEventListRead(BaseModel):
    items: list[CalendarEventRead]
    total: int


class CalendarRangeQuery(BaseModel):
    start: date | None = None
    end: date | None = None
    status: CalendarEventStatus | None = None
    event_type: CalendarEventType | None = None
    document_id: int | None = Field(default=None, gt=0)
    source: CalendarEventSource | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_range(self) -> CalendarRangeQuery:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must not be earlier than start.")
        return self


class CalendarEventConfirm(BaseModel):
    expected_sequence: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


class CalendarEventComplete(BaseModel):
    expected_sequence: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")
