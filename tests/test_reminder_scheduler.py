from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.tasks.reminders as reminder_tasks
from app.models.calendar_event import CalendarEvent, CalendarEventStatus, CalendarEventType
from app.models.audit_log import AuditLog
from app.models.event_reminder import (
    EventReminder,
    EventReminderChannel,
    EventReminderStatus,
)
from app.models.notification import Notification
from app.models.user import User
from app.schemas.calendar_event import CalendarEventUpdate
from app.services.calendar_events import cancel_event, delete_event, update_event
from app.services.reminder_scheduler import (
    ReminderSchedulerMetrics,
    configure_in_app_reminder,
    event_start_at_utc,
    process_due_reminders,
)
from app.worker import celery_app


def _event(*, owner: User, start_date: date = date(2026, 7, 20)) -> CalendarEvent:
    return CalendarEvent(
        owner=owner,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        status=CalendarEventStatus.confirmed,
        start_date=start_date,
        source_evidence={},
    )


def _reminder(*, event: CalendarEvent, scheduled_for: datetime) -> EventReminder:
    return EventReminder(
        event=event,
        channel=EventReminderChannel.in_app,
        offset_minutes=60,
        scheduled_for=scheduled_for,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_reminder_setting_creates_updates_and_removes_one_in_app_reminder(
    db_session: Session,
    test_user: User,
) -> None:
    event = _event(owner=test_user, start_date=date(2026, 8, 1))
    db_session.add(event)
    db_session.commit()

    first = configure_in_app_reminder(db=db_session, event=event, offset_minutes=60)
    repeated = configure_in_app_reminder(db=db_session, event=event, offset_minutes=60)
    updated = configure_in_app_reminder(db=db_session, event=event, offset_minutes=24 * 60)
    removed = configure_in_app_reminder(db=db_session, event=event, offset_minutes=None)

    assert first is not None
    assert repeated is not None and repeated.id == first.id
    assert updated is not None and updated.offset_minutes == 24 * 60
    assert removed is None
    reminders = list(db_session.scalars(select(EventReminder).where(EventReminder.event_id == event.id)))
    assert [reminder.status for reminder in reminders] == [
        EventReminderStatus.cancelled,
        EventReminderStatus.cancelled,
    ]


def test_scheduler_sends_due_reminder_once(
    db_session: Session,
    test_user: User,
) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    reminder = _reminder(event=_event(owner=test_user), scheduled_for=now - timedelta(1))
    db_session.add(reminder)
    db_session.commit()
    delivered: list[int] = []

    first = process_due_reminders(
        db=db_session,
        now=now,
        max_attempts=3,
        retry_base_seconds=60,
        batch_size=10,
        deliverer=lambda candidate, _event: delivered.append(candidate.id),
    )
    db_session.refresh(reminder)
    second = process_due_reminders(
        db=db_session,
        now=now + timedelta(minutes=5),
        max_attempts=3,
        retry_base_seconds=60,
        batch_size=10,
        deliverer=lambda candidate, _event: delivered.append(candidate.id),
    )

    assert first.model_dump() == {
        "queued": 1,
        "sent": 1,
        "failed": 0,
        "delayed": 0,
        "cancelled": 0,
    }
    assert reminder.status == EventReminderStatus.sent
    assert reminder.attempts == 1
    assert delivered == [reminder.id]
    assert second.queued == 0
    assert delivered == [reminder.id]


def test_scheduler_retries_with_backoff_then_marks_failed(
    db_session: Session,
    test_user: User,
) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    reminder = _reminder(event=_event(owner=test_user), scheduled_for=now)
    db_session.add(reminder)
    db_session.commit()

    def failing_delivery(*_args: object) -> None:
        raise RuntimeError("Temporary delivery failure")

    delayed = process_due_reminders(
        db=db_session,
        now=now,
        max_attempts=2,
        retry_base_seconds=60,
        batch_size=10,
        deliverer=failing_delivery,
    )
    db_session.refresh(reminder)
    assert delayed.queued == 1
    assert delayed.delayed == 1
    assert reminder.status == EventReminderStatus.pending
    assert reminder.attempts == 1
    assert _as_utc(reminder.scheduled_for) == now + timedelta(seconds=60)

    failed = process_due_reminders(
        db=db_session,
        now=now + timedelta(seconds=60),
        max_attempts=2,
        retry_base_seconds=60,
        batch_size=10,
        deliverer=failing_delivery,
    )
    db_session.refresh(reminder)

    assert failed.failed == 1
    assert reminder.status == EventReminderStatus.failed
    assert reminder.attempts == 2
    assert reminder.error_message == "Temporary delivery failure"


def test_scheduler_never_marks_unconfigured_delivery_as_sent(
    db_session: Session,
    test_user: User,
) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    reminder = _reminder(event=_event(owner=test_user), scheduled_for=now)
    reminder.channel = EventReminderChannel.email
    db_session.add(reminder)
    db_session.commit()

    metrics = process_due_reminders(
        db=db_session,
        now=now,
        max_attempts=3,
        retry_base_seconds=60,
        batch_size=10,
    )
    db_session.refresh(reminder)

    assert metrics.sent == 0
    assert metrics.delayed == 1
    assert reminder.status == EventReminderStatus.pending
    assert "not configured" in (reminder.error_message or "")


def test_scheduler_creates_one_in_app_notification_for_a_due_reminder(
    db_session: Session,
    test_user: User,
) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    event = _event(owner=test_user)
    reminder = _reminder(event=event, scheduled_for=now)
    db_session.add(reminder)
    db_session.commit()

    metrics = process_due_reminders(
        db=db_session,
        now=now,
        max_attempts=3,
        retry_base_seconds=60,
        batch_size=10,
    )
    process_due_reminders(
        db=db_session,
        now=now + timedelta(minutes=5),
        max_attempts=3,
        retry_base_seconds=60,
        batch_size=10,
    )

    notifications = list(db_session.query(Notification).all())
    assert metrics.sent == 1
    assert reminder.status == EventReminderStatus.sent
    assert len(notifications) == 1
    assert notifications[0].owner_id == test_user.id
    assert notifications[0].event_id == event.id
    assert notifications[0].title == "Reminder: Pay invoice"
    audit_actions = list(
        db_session.scalars(
            select(AuditLog.action)
            .where(AuditLog.calendar_event_id == event.id)
            .order_by(AuditLog.id)
        )
    )
    assert audit_actions == ["calendar_reminder_created", "calendar_reminder_sent"]


def test_cancel_and_delete_event_cancel_unsent_reminders(
    db_session: Session,
    test_user: User,
) -> None:
    first_event = _event(owner=test_user)
    first_reminder = _reminder(
        event=first_event,
        scheduled_for=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )
    second_event = _event(owner=test_user)
    second_reminder = _reminder(
        event=second_event,
        scheduled_for=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )
    db_session.add_all([first_reminder, second_reminder])
    db_session.commit()

    cancel_event(db=db_session, owner_id=test_user.id, event_id=first_event.id)
    delete_event(db=db_session, owner_id=test_user.id, event_id=second_event.id)
    db_session.refresh(first_reminder)
    db_session.refresh(second_reminder)

    assert first_reminder.status == EventReminderStatus.cancelled
    assert second_reminder.status == EventReminderStatus.cancelled
    assert "cancelled" in (first_reminder.error_message or "")
    assert "deleted" in (second_reminder.error_message or "")


def test_event_date_change_reschedules_pending_reminder_in_user_timezone(
    db_session: Session,
    test_user: User,
) -> None:
    test_user.timezone = "Europe/Berlin"
    event = _event(owner=test_user, start_date=date(2026, 12, 20))
    reminder = _reminder(
        event=event,
        scheduled_for=datetime(2026, 12, 19, 22, 0, tzinfo=UTC),
    )
    db_session.add(reminder)
    db_session.commit()

    update_event(
        db=db_session,
        owner_id=test_user.id,
        event_id=event.id,
        payload=CalendarEventUpdate(start_date=date(2026, 12, 25)),
    )
    db_session.refresh(reminder)

    assert reminder.status == EventReminderStatus.pending
    assert reminder.attempts == 0
    assert _as_utc(reminder.scheduled_for) == datetime(2026, 12, 24, 22, tzinfo=UTC)
    assert event_start_at_utc(event=event) == datetime(2026, 12, 24, 23, tzinfo=UTC)


def test_beat_runs_due_reminder_scheduler_every_five_minutes() -> None:
    schedule = celery_app.conf.beat_schedule["calendar-due-reminders"]

    assert schedule["task"] == "calendar.schedule_due_reminders"
    assert schedule["schedule"]._orig_minute == "*/5"


def test_celery_task_returns_scheduler_metrics(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SessionContext:
        def __enter__(self) -> Session:
            return db_session

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(reminder_tasks, "SessionLocal", lambda: SessionContext())
    monkeypatch.setattr(
        reminder_tasks,
        "process_due_reminders",
        lambda **_kwargs: ReminderSchedulerMetrics(queued=2, delayed=2),
    )

    result = reminder_tasks.schedule_due_reminders.apply(throw=True)

    assert result.get() == {
        "queued": 2,
        "sent": 0,
        "failed": 0,
        "delayed": 2,
        "cancelled": 0,
    }
