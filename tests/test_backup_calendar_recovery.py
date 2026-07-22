import gzip
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import app.services.backup_recovery as backup_recovery
from app.models.calendar_event import CalendarEvent, CalendarEventSource, CalendarEventStatus, CalendarEventType
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.event_reminder import EventReminder, EventReminderChannel, EventReminderStatus
from app.models.notification import Notification
from app.models.user import User
from app.services.backup_export import build_backup_archive
from app.services.backup_recovery import encrypt_recovery_archive, restore_recovery_backup
from app.services.reminder_scheduler import event_start_at_utc
from app.services.users import create_user


def _event(*, owner: User, title: str, start_date: date, document_id: int | None = None) -> CalendarEvent:
    return CalendarEvent(
        owner_id=owner.id,
        document_id=document_id,
        title=title,
        description="Plain restored description",
        event_type=CalendarEventType.payment_due,
        status=CalendarEventStatus.confirmed,
        source=CalendarEventSource.user,
        all_day=True,
        start_date=start_date,
        source_evidence={},
    )


def test_v3_backup_restores_calendar_records_without_overdue_reminders(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = create_user(db=db_session, email="calendar-backup-source@example.com", password="strong-password")
    source_document = Document(
        owner_id=source.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        checksum_sha256="d" * 64,
        raw_text="Invoice text",
    )
    db_session.add(source_document)
    db_session.flush()
    future_day = (datetime.now(UTC) + timedelta(days=10)).date()
    past_day = (datetime.now(UTC) - timedelta(days=10)).date()
    linked_event = _event(owner=source, title="Linked invoice", start_date=future_day, document_id=source_document.id)
    standalone_event = _event(owner=source, title="Personal deadline", start_date=future_day)
    overdue_event = _event(owner=source, title="Old deadline", start_date=past_day)
    db_session.add_all([linked_event, standalone_event, overdue_event])
    db_session.flush()
    db_session.add_all(
        [
            EventReminder(
                event_id=linked_event.id,
                channel=EventReminderChannel.in_app,
                recipient_email="alerts@example.com",
                provider_message_id="provider-message-123",
                offset_minutes=60,
                scheduled_for=datetime.now(UTC) + timedelta(days=9),
                status=EventReminderStatus.pending,
            ),
            EventReminder(
                event_id=overdue_event.id,
                channel=EventReminderChannel.in_app,
                offset_minutes=60,
                scheduled_for=datetime.now(UTC) - timedelta(days=11),
                status=EventReminderStatus.pending,
            ),
        ]
    )
    db_session.add_all(
        [
            Notification(
                owner_id=source.id,
                event_id=linked_event.id,
                title="Reminder: Linked invoice",
                body="Due soon",
            ),
            Notification(owner_id=source.id, title="Account update", body="No event"),
        ]
    )
    db_session.commit()

    archive = build_backup_archive(db=db_session, owner_id=source.id)
    payload = json.loads(gzip.decompress(archive.content))
    assert payload["schema_version"] == 3
    assert payload["record_counts"]["calendar_events"] == 3
    assert payload["record_counts"]["event_reminders"] == 2
    assert payload["record_counts"]["notifications"] == 2
    assert "refresh_token" not in json.dumps(payload)
    assert payload["records"]["event_reminders"][0]["recipient_email"] == "alerts@example.com"
    assert payload["records"]["event_reminders"][0]["provider_message_id"] == "provider-message-123"

    monkeypatch.setattr(backup_recovery, "enqueue_document_index_job", lambda **_: None)
    recovery_key = Fernet.generate_key().decode("utf-8")
    result = restore_recovery_backup(
        db=db_session,
        owner_id=test_user.id,
        encrypted_content=encrypt_recovery_archive(content=archive.content, recovery_key=recovery_key),
        recovery_key=recovery_key,
    )

    assert result.restored_documents == 1
    assert result.schema_version == 3
    assert result.restored_calendar_events == 3
    assert result.restored_reminders == 2
    assert result.restored_notifications == 2
    restored_document = db_session.query(Document).filter_by(owner_id=test_user.id).one()
    restored_events = {event.title: event for event in db_session.query(CalendarEvent).filter_by(owner_id=test_user.id)}
    assert restored_events["Linked invoice"].document_id == restored_document.id
    assert restored_events["Personal deadline"].document_id is None
    restored_reminders = {
        reminder.event.title: reminder
        for reminder in db_session.query(EventReminder)
        .join(CalendarEvent)
        .filter(CalendarEvent.owner_id == test_user.id)
    }
    assert restored_reminders["Linked invoice"].status == EventReminderStatus.pending
    assert restored_reminders["Linked invoice"].recipient_email == "alerts@example.com"
    assert restored_reminders["Linked invoice"].provider_message_id == "provider-message-123"
    expected_schedule = event_start_at_utc(event=restored_events["Linked invoice"]) - timedelta(minutes=60)
    actual_schedule = restored_reminders["Linked invoice"].scheduled_for
    if actual_schedule.tzinfo is None:  # SQLite returns datetimes without timezone information.
        actual_schedule = actual_schedule.replace(tzinfo=UTC)
    assert actual_schedule == expected_schedule
    assert restored_reminders["Old deadline"].status == EventReminderStatus.cancelled
    assert "overdue" in (restored_reminders["Old deadline"].error_message or "").lower()
    restored_notifications = db_session.query(Notification).filter_by(owner_id=test_user.id).all()
    assert {item.title for item in restored_notifications} == {"Reminder: Linked invoice", "Account update"}
    assert next(item for item in restored_notifications if item.title.startswith("Reminder")).event_id == restored_events["Linked invoice"].id

    repeated = restore_recovery_backup(
        db=db_session,
        owner_id=test_user.id,
        encrypted_content=encrypt_recovery_archive(content=archive.content, recovery_key=recovery_key),
        recovery_key=recovery_key,
    )
    assert repeated.restored_documents == 0
    assert repeated.restored_calendar_events == 0
    assert repeated.restored_reminders == 0
    assert repeated.restored_notifications == 0

    deleted_event = restored_events["Personal deadline"]
    deleted_event.deleted_at = datetime.now(UTC)
    db_session.commit()
    reactivated = restore_recovery_backup(
        db=db_session,
        owner_id=test_user.id,
        encrypted_content=encrypt_recovery_archive(content=archive.content, recovery_key=recovery_key),
        recovery_key=recovery_key,
    )
    db_session.refresh(deleted_event)
    assert reactivated.restored_calendar_events == 1
    assert deleted_event.deleted_at is None


def test_v3_restore_schema_rejects_unknown_calendar_fields(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = create_user(db=db_session, email="calendar-schema-source@example.com", password="strong-password")
    event = _event(owner=source, title="Strict event", start_date=date(2027, 1, 1))
    db_session.add(event)
    db_session.commit()
    payload = json.loads(gzip.decompress(build_backup_archive(db=db_session, owner_id=source.id).content))
    payload["records"]["calendar_events"][0]["unexpected_secret"] = "do-not-accept"
    recovery_key = Fernet.generate_key().decode("utf-8")
    encrypted = encrypt_recovery_archive(
        content=gzip.compress(json.dumps(payload).encode("utf-8")),
        recovery_key=recovery_key,
    )

    with pytest.raises(RuntimeError, match="supported version 2 schema"):
        restore_recovery_backup(
            db=db_session,
            owner_id=test_user.id,
            encrypted_content=encrypted,
            recovery_key=recovery_key,
        )
