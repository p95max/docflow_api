from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.calendar_event import CalendarEvent, CalendarEventStatus, CalendarEventType
from app.models.event_reminder import EventReminder, EventReminderChannel
from app.models.user import User


def test_audit_values_redact_secrets_and_bound_json_size(db_session: Session, test_user: User) -> None:
    audit_log = AuditLog(
        user_id=test_user.id,
        action="settings_changed",
        old_value={"refresh_token": "never-store-this", "password": "also-secret"},
        new_value={str(index): "x" * 1_000 for index in range(10)},
    )
    db_session.add(audit_log)
    db_session.commit()
    db_session.refresh(audit_log)

    assert audit_log.old_value == {"refresh_token": "[redacted]", "password": "[redacted]"}
    assert audit_log.new_value["_truncated"] is True
    assert len(str(audit_log.new_value).encode("utf-8")) <= 4_300


def test_reminder_creation_is_audited_for_its_event_owner(db_session: Session, test_user: User) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        status=CalendarEventStatus.confirmed,
        start_date=date(2026, 8, 1),
        source_evidence={},
    )
    reminder = EventReminder(
        event=event,
        channel=EventReminderChannel.in_app,
        offset_minutes=60,
        scheduled_for=datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
    )
    db_session.add(reminder)
    db_session.commit()

    audit_log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "calendar_reminder_created")
    )
    assert audit_log is not None
    assert audit_log.calendar_event_id == event.id
    assert audit_log.user_id == test_user.id
    assert audit_log.new_value == {
        "channel": "in_app",
        "offset_minutes": 60,
        "scheduled_for": "2026-08-01T08:00:00+00:00",
        "status": "pending",
    }
