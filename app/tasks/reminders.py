"""Celery entrypoint for periodic calendar reminder scheduling."""

from __future__ import annotations

import logging

from app.core.config import settings
from app.db.session import SessionLocal
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
    payload = metrics.model_dump()
    logger.info("Calendar reminder scheduler metrics: %s", payload)
    return payload
