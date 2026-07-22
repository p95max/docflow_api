"""Safe scheduling lifecycle for persisted calendar reminders.

Delivery transports are deliberately kept separate. Until an in-app or email
transport is configured, a claimed reminder is retried and never falsely marked
as sent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, object_session

from app.core.timezones import DEFAULT_USER_TIMEZONE, validate_iana_timezone
from app.models.calendar_event import CalendarEvent, CalendarEventStatus
from app.models.event_reminder import (
    EventReminder,
    EventReminderChannel,
    EventReminderStatus,
)
from app.services.notifications import create_notification
from app.services.audit import add_calendar_audit_log


class ReminderDeliveryUnavailable(RuntimeError):
    """Raised while no delivery implementation exists for a reminder channel."""


IN_APP_REMINDER_OFFSETS = frozenset({0, 60, 24 * 60})


def configure_in_app_reminder(
    *,
    db: Session,
    event: CalendarEvent,
    offset_minutes: int | None,
) -> EventReminder | None:
    """Compatibility helper for configuring one in-app reminder."""
    reminders = configure_in_app_reminders(
        db=db,
        event=event,
        offset_minutes={offset_minutes} if offset_minutes is not None else set(),
    )
    return reminders[0] if reminders else None


def configure_in_app_reminders(
    *,
    db: Session,
    event: CalendarEvent,
    offset_minutes: set[int],
) -> list[EventReminder]:
    """Keep the selected set of pending in-app reminders for an event."""
    if not offset_minutes.issubset(IN_APP_REMINDER_OFFSETS):
        raise ValueError("Unsupported reminder setting.")

    pending = list(
        db.scalars(
            select(EventReminder).where(
                EventReminder.event_id == event.id,
                EventReminder.channel == EventReminderChannel.in_app,
                EventReminder.status.in_([EventReminderStatus.pending, EventReminderStatus.sending]),
            )
        ).all()
    )
    for reminder in pending:
        if reminder.offset_minutes not in offset_minutes:
            reminder.status = EventReminderStatus.cancelled
            reminder.error_message = "Reminder setting was changed."

    reminders_by_offset = {reminder.offset_minutes: reminder for reminder in pending}
    selected: list[EventReminder] = []
    for offset in sorted(offset_minutes):
        reminder = reminders_by_offset.get(offset)
        if reminder is None:
            reminder = db.scalar(
                select(EventReminder).where(
                    EventReminder.event_id == event.id,
                    EventReminder.channel == EventReminderChannel.in_app,
                    EventReminder.offset_minutes == offset,
                )
            )
        if reminder is None:
            reminder = EventReminder(
                event_id=event.id,
                channel=EventReminderChannel.in_app,
                offset_minutes=offset,
                scheduled_for=event_start_at_utc(event=event) - timedelta(minutes=offset),
            )
            db.add(reminder)
        elif reminder.status == EventReminderStatus.cancelled and reminder.sent_at is None:
            reminder.scheduled_for = event_start_at_utc(event=event) - timedelta(minutes=offset)
            reminder.status = EventReminderStatus.pending
            reminder.attempts = 0
            reminder.last_attempt_at = None
            reminder.error_message = None
        selected.append(reminder)
    db.commit()
    for reminder in selected:
        db.refresh(reminder)
    return selected


class ReminderSchedulerMetrics(BaseModel):
    """Structured per-run counters suitable for logs and external metrics."""

    model_config = ConfigDict(extra="forbid")

    queued: int = Field(default=0, ge=0)
    sent: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    delayed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)


ReminderDeliverer = Callable[[EventReminder, CalendarEvent], None]


def process_due_reminders(
    *,
    db: Session,
    now: datetime | None = None,
    max_attempts: int,
    retry_base_seconds: int,
    batch_size: int,
    deliverer: ReminderDeliverer | None = None,
) -> ReminderSchedulerMetrics:
    """Claim due reminders once, then deliver or retry each claimed record.

    Due rows are selected with ``FOR UPDATE SKIP LOCKED`` so multiple Beat
    workers may run safely. A row becomes ``sending`` and its attempt is
    persisted before calling a transport; completed rows are never selected
    again.
    """
    current_time = _as_utc(now)
    metrics = ReminderSchedulerMetrics()
    metrics.cancelled = cancel_invalid_event_reminders(db=db)

    reminder_ids = _claim_due_reminders(
        db=db,
        now=current_time,
        batch_size=batch_size,
    )
    metrics.queued = len(reminder_ids)
    db.commit()

    active_deliverer = deliverer or deliver_reminder
    for reminder_id in reminder_ids:
        _deliver_claimed_reminder(
            db=db,
            reminder_id=reminder_id,
            now=current_time,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            deliverer=active_deliverer,
            metrics=metrics,
        )
    return metrics


def deliver_reminder(reminder: EventReminder, event: CalendarEvent) -> None:
    """Deliver supported channels in the scheduler transaction.

    The in-app record and the ``sent`` status are committed together. This
    prevents a retry from creating a second notification after a failed commit.
    Email remains deliberately unavailable until a mail transport is configured.
    """
    if reminder.channel == EventReminderChannel.in_app:
        db = object_session(reminder)
        if db is None:
            raise ReminderDeliveryUnavailable("Reminder is detached from its database session.")
        create_notification(
            db=db,
            owner_id=event.owner_id,
            event_id=event.id,
            title=f"Reminder: {event.title}",
            body=_reminder_notification_body(event),
        )
        return
    raise ReminderDeliveryUnavailable(
        f"{reminder.channel.value} reminder delivery is not configured yet."
    )


def cancel_event_reminders(
    *,
    db: Session,
    event: CalendarEvent,
    reason: str,
) -> int:
    """Cancel pending/in-flight reminders as part of an event state transition."""
    reminders = list(
        db.scalars(
            select(EventReminder)
            .where(
                EventReminder.event_id == event.id,
                EventReminder.status.in_(
                    [EventReminderStatus.pending, EventReminderStatus.sending]
                ),
            )
            .with_for_update(of=EventReminder)
        )
    )
    for reminder in reminders:
        reminder.status = EventReminderStatus.cancelled
        reminder.error_message = reason
    return len(reminders)


def reschedule_event_reminders(*, db: Session, event: CalendarEvent) -> int:
    """Move unsent reminders when an event's date, time or timezone changes."""
    reminders = list(
        db.scalars(
            select(EventReminder)
            .where(
                EventReminder.event_id == event.id,
                EventReminder.status.in_(
                    [EventReminderStatus.pending, EventReminderStatus.sending]
                ),
            )
            .with_for_update(of=EventReminder)
        )
    )
    for reminder in reminders:
        reminder.scheduled_for = reminder_scheduled_for(event=event, reminder=reminder)
        reminder.status = EventReminderStatus.pending
        reminder.attempts = 0
        reminder.last_attempt_at = None
        reminder.error_message = None
    return len(reminders)


def reminder_scheduled_for(*, event: CalendarEvent, reminder: EventReminder) -> datetime:
    """Calculate a reminder instant from the event using its user's timezone."""
    return event_start_at_utc(event=event) - timedelta(minutes=reminder.offset_minutes)


def event_start_at_utc(*, event: CalendarEvent) -> datetime:
    """Return the event start instant, resolving date-only events in user time."""
    if not event.all_day:
        if event.start_at is None:
            raise ValueError("Timed calendar event has no start_at.")
        return _as_utc(event.start_at)

    if event.start_date is None:
        raise ValueError("All-day calendar event has no start_date.")
    timezone_name = event.timezone or _event_owner_timezone(event)
    timezone = ZoneInfo(validate_iana_timezone(timezone_name))
    return datetime.combine(event.start_date, time.min, tzinfo=timezone).astimezone(UTC)


def cancel_invalid_event_reminders(*, db: Session) -> int:
    """Cancel unsent rows tied to deleted, cancelled or completed events."""
    reminders = list(
        db.scalars(
            select(EventReminder)
            .join(CalendarEvent)
            .where(
                EventReminder.status.in_(
                    [EventReminderStatus.pending, EventReminderStatus.sending]
                ),
                or_(
                    CalendarEvent.deleted_at.is_not(None),
                    CalendarEvent.status.in_(
                        [CalendarEventStatus.cancelled, CalendarEventStatus.completed]
                    ),
                ),
            )
            .with_for_update(of=EventReminder, skip_locked=True)
        )
    )
    for reminder in reminders:
        reminder.status = EventReminderStatus.cancelled
        reminder.error_message = "The related calendar event is no longer active."
    return len(reminders)


def _claim_due_reminders(*, db: Session, now: datetime, batch_size: int) -> list[int]:
    due_reminders = list(
        db.scalars(
            select(EventReminder)
            .join(CalendarEvent)
            .where(
                EventReminder.status == EventReminderStatus.pending,
                EventReminder.scheduled_for <= now,
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.status == CalendarEventStatus.confirmed,
            )
            .order_by(EventReminder.scheduled_for.asc(), EventReminder.id.asc())
            .limit(batch_size)
            .with_for_update(of=EventReminder, skip_locked=True)
        )
    )
    for reminder in due_reminders:
        reminder.status = EventReminderStatus.sending
        reminder.attempts += 1
        reminder.last_attempt_at = now
        reminder.error_message = None
    return [reminder.id for reminder in due_reminders]


def _deliver_claimed_reminder(
    *,
    db: Session,
    reminder_id: int,
    now: datetime,
    max_attempts: int,
    retry_base_seconds: int,
    deliverer: ReminderDeliverer,
    metrics: ReminderSchedulerMetrics,
) -> None:
    reminder = db.scalar(
        select(EventReminder)
        .where(EventReminder.id == reminder_id)
        .with_for_update(of=EventReminder, skip_locked=True)
    )
    if reminder is None or reminder.status != EventReminderStatus.sending:
        return

    event = reminder.event
    if _event_is_inactive(event):
        reminder.status = EventReminderStatus.cancelled
        reminder.error_message = "The related calendar event is no longer active."
        metrics.cancelled += 1
        db.commit()
        return

    try:
        deliverer(reminder, event)
    except Exception as exc:  # Delivery errors must be recorded, never retried blindly.
        _handle_delivery_failure(
            reminder=reminder,
            now=now,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            error_message=_safe_delivery_error(exc),
            metrics=metrics,
        )
    else:
        reminder.status = EventReminderStatus.sent
        reminder.sent_at = now
        reminder.error_message = None
        add_calendar_audit_log(
            db=db,
            user_id=event.owner_id,
            calendar_event_id=event.id,
            action="calendar_reminder_sent",
            new_value={
                "reminder_id": reminder.id,
                "channel": reminder.channel.value,
                "attempts": reminder.attempts,
                "sent_at": now,
            },
        )
        metrics.sent += 1
    db.commit()


def _handle_delivery_failure(
    *,
    reminder: EventReminder,
    now: datetime,
    max_attempts: int,
    retry_base_seconds: int,
    error_message: str,
    metrics: ReminderSchedulerMetrics,
) -> None:
    reminder.error_message = error_message
    if reminder.attempts >= max_attempts:
        reminder.status = EventReminderStatus.failed
        metrics.failed += 1
        return

    retry_delay = retry_base_seconds * (2 ** (reminder.attempts - 1))
    reminder.status = EventReminderStatus.pending
    reminder.scheduled_for = now + timedelta(seconds=retry_delay)
    metrics.delayed += 1


def _event_is_inactive(event: CalendarEvent) -> bool:
    return event.deleted_at is not None or event.status in {
        CalendarEventStatus.cancelled,
        CalendarEventStatus.completed,
    }


def _event_owner_timezone(event: CalendarEvent) -> str:
    owner = event.owner
    return owner.timezone if owner is not None else DEFAULT_USER_TIMEZONE


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_delivery_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:1000]


def _reminder_notification_body(event: CalendarEvent) -> str:
    if event.all_day and event.start_date is not None:
        return f"{event.title} is scheduled for {event.start_date.isoformat()}."
    if event.start_at is not None:
        return f"{event.title} starts at {event.start_at.isoformat()}."
    return f"{event.title} is due now."
