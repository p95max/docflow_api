"""Celery entrypoint for periodic calendar reminder scheduling."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.calendar_event import CalendarEvent, CalendarEventStatus
from app.models.event_reminder import EventReminder, EventReminderStatus
from app.services.calendar_observability import (
    log_calendar_event,
    record_beat_heartbeat,
)
from app.services.reminder_scheduler import process_due_reminders
from app.worker import celery_app


logger = logging.getLogger(__name__)


@celery_app.task(name="calendar.schedule_due_reminders")
def schedule_due_reminders() -> dict[str, int]:
    """Process one bounded batch of due reminders every five minutes."""
    with SessionLocal() as db:
        metrics = process_due_reminders(
            db=db,
            max_attempts=settings.reminder_max_attempts,
            retry_base_seconds=settings.reminder_retry_base_seconds,
            batch_size=settings.reminder_scheduler_batch_size,
        )
        backlog = _due_reminder_backlog(db=db)
    payload = metrics.model_dump()
    record_beat_heartbeat()
    log_calendar_event("calendar_reminder_scheduler_run", **payload, backlog=backlog)
    if backlog >= settings.reminder_backlog_alert_threshold:
        logger.warning(
            "calendar_observability=reminder_backlog_alert backlog=%s threshold=%s",
            backlog,
            settings.reminder_backlog_alert_threshold,
        )
    return payload


def _due_reminder_backlog(*, db: Session) -> int:
    """Count due active reminders after the bounded scheduler batch has run."""
    return int(
        db.scalar(
            select(func.count(EventReminder.id))
            .join(CalendarEvent)
            .where(
                EventReminder.status == EventReminderStatus.pending,
                EventReminder.scheduled_for <= datetime.now(UTC),
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.status == CalendarEventStatus.confirmed,
            )
        )
        or 0
    )
