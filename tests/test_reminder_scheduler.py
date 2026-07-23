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
    configure_email_reminders,
    configure_in_app_reminders,
    event_start_at_utc,
    process_due_reminders,
)
from app.services.email_reminders import FakeEmailReminderDeliverer
from app.core.config import settings
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


def test_reminder_settings_create_update_and_remove_multiple_in_app_reminders(
    db_session: Session,
    test_user: User,
) -> None:
    event = _event(owner=test_user, start_date=date(2026, 8, 1))
    db_session.add(event)
    db_session.commit()

    first = configure_in_app_reminders(
        db=db_session,
        event=event,
        offset_minutes={0, 60, 24 * 60},
    )
    repeated = configure_in_app_reminders(
        db=db_session,
        event=event,
        offset_minutes={0, 60, 24 * 60},
    )
    updated = configure_in_app_reminders(db=db_session, event=event, offset_minutes={24 * 60})
    removed = configure_in_app_reminders(db=db_session, event=event, offset_minutes=set())

    assert [reminder.offset_minutes for reminder in first] == [0, 60, 24 * 60]
    assert [reminder.id for reminder in repeated] == [reminder.id for reminder in first]
    assert [reminder.offset_minutes for reminder in updated] == [24 * 60]
    assert removed == []
    reminders = list(db_session.scalars(select(EventReminder).where(EventReminder.event_id == event.id)))
    assert [reminder.status for reminder in reminders] == [
        EventReminderStatus.cancelled,
        EventReminderStatus.cancelled,
        EventReminderStatus.cancelled,
    ]


def test_email_reminders_are_created_only_for_future_selected_offsets(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "email_reminders_enabled", True)
    monkeypatch.setattr(settings, "email_reminders_from_address", "reminders@example.com")
    monkeypatch.setattr(settings, "email_reminders_smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "public_app_base_url", "https://docsflow.example.com")
    event = _event(owner=test_user, start_date=date(2026, 8, 2))
    db_session.add(event)
    db_session.commit()

    in_app = configure_in_app_reminders(db=db_session, event=event, offset_minutes={60, 1440})
    email = configure_email_reminders(
        db=db_session,
        event=event,
        offset_minutes={60, 1440},
        recipient_email=test_user.email,
        now=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
    )
    past_event = _event(owner=test_user, start_date=date(2026, 7, 20))
    db_session.add(past_event)
    db_session.commit()
    skipped = configure_email_reminders(
        db=db_session,
        event=past_event,
        offset_minutes={0, 60, 1440},
        recipient_email=test_user.email,
        now=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
    )

    assert [item.channel for item in in_app] == [EventReminderChannel.in_app] * 2
    assert [item.offset_minutes for item in email] == [60, 1440]
    assert all(item.recipient_email == test_user.email for item in email)
    assert skipped == []
    assert list(
        db_session.scalars(
            select(EventReminder).where(
                EventReminder.event_id == past_event.id,
                EventReminder.channel == EventReminderChannel.email,
            )
        )
    ) == []


def test_email_delivery_records_provider_acceptance_without_affecting_in_app(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    event = _event(owner=test_user, start_date=date(2026, 7, 21))
    email = EventReminder(
        event=event,
        channel=EventReminderChannel.email,
        recipient_email=test_user.email,
        offset_minutes=60,
        scheduled_for=now,
    )
    in_app = _reminder(event=event, scheduled_for=now)
    db_session.add_all([email, in_app])
    db_session.commit()
    monkeypatch.setattr(settings, "public_app_base_url", "https://docsflow.example.com")
    fake = FakeEmailReminderDeliverer()
    monkeypatch.setattr(
        "app.services.reminder_scheduler.get_email_reminder_deliverer",
        lambda: fake,
    )

    metrics = process_due_reminders(
        db=db_session,
        now=now,
        max_attempts=3,
        retry_base_seconds=60,
        batch_size=10,
    )

    db_session.refresh(email)
    db_session.refresh(in_app)
    assert metrics.sent == 2
    assert email.status == EventReminderStatus.sent
    assert email.provider_message_id == f"fake-event-reminder-{email.id}"
    assert in_app.status == EventReminderStatus.sent
    assert len(fake.delivered) == 1
    assert "https://docsflow.example.com/calendar/events/" in fake.delivered[0].text_body


def test_event_update_reschedules_and_cancellation_stops_email_reminders(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "email_reminders_enabled", True)
    monkeypatch.setattr(settings, "email_reminders_from_address", "reminders@example.com")
    monkeypatch.setattr(settings, "email_reminders_smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "public_app_base_url", "https://docsflow.example.com")
    event = _event(owner=test_user, start_date=date(2040, 8, 2))
    db_session.add(event)
    db_session.commit()
    email = configure_email_reminders(
        db=db_session,
        event=event,
        offset_minutes={60},
        recipient_email=test_user.email,
        now=datetime(2039, 1, 1, tzinfo=UTC),
    )[0]

    update_event(
        db=db_session,
        owner_id=test_user.id,
        event_id=event.id,
        payload=CalendarEventUpdate(start_date=date(2040, 8, 4)),
    )
    db_session.refresh(email)
    assert _as_utc(email.scheduled_for) == datetime(2040, 8, 3, 21, tzinfo=UTC)

    cancel_event(db=db_session, owner_id=test_user.id, event_id=event.id)
    db_session.refresh(email)
    assert email.status == EventReminderStatus.cancelled


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
    assert "disabled or incomplete" in (reminder.error_message or "")


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
    heartbeat_calls: list[bool] = []
    monkeypatch.setattr(
        reminder_tasks,
        "record_beat_heartbeat",
        lambda: heartbeat_calls.append(True),
    )

    result = reminder_tasks.schedule_due_reminders.apply(throw=True)

    assert result.get() == {
        "queued": 2,
        "sent": 0,
        "failed": 0,
        "delayed": 2,
        "cancelled": 0,
    }
    assert heartbeat_calls == [True]
