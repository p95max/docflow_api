import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.timezones import normalize_datetime_to_utc
from app.db.base import Base


class CalendarEventType(str, enum.Enum):
    payment_due = "payment_due"
    response_deadline = "response_deadline"
    action_deadline = "action_deadline"
    appointment = "appointment"
    contract_start = "contract_start"
    contract_end = "contract_end"
    cancellation_deadline = "cancellation_deadline"
    renewal = "renewal"
    custom = "custom"


class CalendarEventStatus(str, enum.Enum):
    suggested = "suggested"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"


class CalendarEventSource(str, enum.Enum):
    ai = "ai"
    user = "user"
    system = "system"
    external = "external"


class CalendarEvent(Base):
    """A calendar record linked to a document when that source still exists."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint(
            "(all_day = true AND start_date IS NOT NULL AND start_at IS NULL) "
            "OR (all_day = false AND start_at IS NOT NULL)",
            name="ck_calendar_events_time_representation",
        ),
        CheckConstraint(
            "end_date IS NULL OR (start_date IS NOT NULL AND end_date >= start_date)",
            name="ck_calendar_events_date_range",
        ),
        CheckConstraint(
            "end_at IS NULL OR (start_at IS NOT NULL AND end_at >= start_at)",
            name="ck_calendar_events_datetime_range",
        ),
        Index("ix_calendar_events_owner_start_date", "owner_id", "start_date"),
        Index("ix_calendar_events_owner_start_at", "owner_id", "start_at"),
        Index("ix_calendar_events_document_id", "document_id"),
        Index("ix_calendar_events_owner_status", "owner_id", "status"),
        Index("ix_calendar_events_owner_deleted_at", "owner_id", "deleted_at"),
        Index(
            "uq_calendar_events_owner_ai_source_key",
            "owner_id",
            "source_key",
            unique=True,
            postgresql_where=text("source = 'ai' AND source_key IS NOT NULL AND deleted_at IS NULL"),
            sqlite_where=text("source = 'ai' AND source_key IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        default=uuid.uuid4,
        unique=True,
        index=True,
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[CalendarEventType] = mapped_column(
        Enum(CalendarEventType, name="calendar_event_type"),
        nullable=False,
    )
    status: Mapped[CalendarEventStatus] = mapped_column(
        Enum(CalendarEventStatus, name="calendar_event_status"),
        default=CalendarEventStatus.suggested,
        server_default=CalendarEventStatus.suggested.value,
        nullable=False,
    )
    source: Mapped[CalendarEventSource] = mapped_column(
        Enum(CalendarEventSource, name="calendar_event_source"),
        default=CalendarEventSource.user,
        server_default=CalendarEventSource.user.value,
        nullable=False,
    )
    all_day: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_field: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    requires_review: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    detached_from_source: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ical_uid: Mapped[str] = mapped_column(
        String(255),
        default=lambda: f"{uuid.uuid4()}@docsflow",
        unique=True,
        index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner = relationship("User", back_populates="calendar_events")
    document = relationship("Document", back_populates="calendar_events")
    audit_logs = relationship("AuditLog", back_populates="calendar_event")

    @validates("start_at", "end_at")
    def normalize_event_timestamp(self, _key: str, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return normalize_datetime_to_utc(value)
