from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.calendar_event import CalendarEvent, CalendarEventType
from app.models.event_reminder import (
    EventReminder,
    EventReminderChannel,
    EventReminderStatus,
)
from app.models.user import User


def _event(*, owner_id: int) -> CalendarEvent:
    return CalendarEvent(
        owner_id=owner_id,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        start_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
        source_evidence={},
    )


def test_event_reminder_has_expected_defaults_and_event_relationship(
    db_session: Session,
    test_user: User,
) -> None:
    event = _event(owner_id=test_user.id)
    reminder = EventReminder(
        event=event,
        channel=EventReminderChannel.email,
        recipient_email="alerts@example.com",
        provider_message_id="provider-message-123",
        offset_minutes=60,
        scheduled_for=datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
    )
    db_session.add(reminder)
    db_session.commit()

    assert reminder.status == EventReminderStatus.pending
    assert reminder.attempts == 0
    assert reminder.recipient_email == "alerts@example.com"
    assert reminder.provider_message_id == "provider-message-123"
    assert reminder.event is event
    assert event.reminders == [reminder]


def test_event_reminder_prevents_duplicate_delivery_definition(
    db_session: Session,
    test_user: User,
) -> None:
    event = _event(owner_id=test_user.id)
    db_session.add(event)
    db_session.flush()
    db_session.add_all(
        [
            EventReminder(
                event_id=event.id,
                channel=EventReminderChannel.in_app,
                offset_minutes=30,
                scheduled_for=datetime(2026, 8, 1, 8, 30, tzinfo=UTC),
            ),
            EventReminder(
                event_id=event.id,
                channel=EventReminderChannel.in_app,
                offset_minutes=30,
                scheduled_for=datetime(2026, 8, 1, 8, 30, tzinfo=UTC),
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
