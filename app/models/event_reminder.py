import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text, event, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.core.audit import sanitize_audit_value


class EventReminderChannel(str, enum.Enum):
    in_app = "in_app"
    email = "email"


class EventReminderStatus(str, enum.Enum):
    pending = "pending"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class EventReminder(Base):
    """One future delivery for a calendar event and a chosen reminder offset."""

    __tablename__ = "event_reminders"
    __table_args__ = (
        CheckConstraint("offset_minutes >= 0", name="ck_event_reminders_offset_nonnegative"),
        CheckConstraint("attempts >= 0", name="ck_event_reminders_attempts_nonnegative"),
        Index(
            "uq_event_reminders_event_channel_offset",
            "event_id",
            "channel",
            "offset_minutes",
            unique=True,
        ),
        Index(
            "ix_event_reminders_status_scheduled_for",
            "status",
            "scheduled_for",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[EventReminderChannel] = mapped_column(
        Enum(EventReminderChannel, name="event_reminder_channel"),
        nullable=False,
    )
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[EventReminderStatus] = mapped_column(
        Enum(EventReminderStatus, name="event_reminder_status"),
        default=EventReminderStatus.pending,
        server_default=EventReminderStatus.pending.value,
        nullable=False,
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    event = relationship("CalendarEvent", back_populates="reminders")


@event.listens_for(EventReminder, "after_insert")
def record_created_reminder_audit(_mapper: object, connection: object, target: EventReminder) -> None:
    """Audit every persisted reminder, including reminders created outside web routes."""
    from app.models.audit_log import AuditLog
    from app.models.calendar_event import CalendarEvent

    owner_id = connection.execute(
        select(CalendarEvent.owner_id).where(CalendarEvent.id == target.event_id)
    ).scalar_one()
    connection.execute(
        AuditLog.__table__.insert().values(
            calendar_event_id=target.event_id,
            user_id=owner_id,
            action="calendar_reminder_created",
            new_value=sanitize_audit_value(
                {
                    "channel": target.channel.value,
                    "offset_minutes": target.offset_minutes,
                    "scheduled_for": target.scheduled_for,
                    "status": target.status.value,
                }
            ),
        )
    )
