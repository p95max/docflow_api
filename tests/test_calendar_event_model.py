from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User


def test_calendar_event_is_separate_from_document_deadline(
    db_session: Session,
    test_user: User,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        deadline=date(2026, 8, 1),
    )
    db_session.add(document)
    db_session.flush()
    event = CalendarEvent(
        owner_id=test_user.id,
        document_id=document.id,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        source=CalendarEventSource.ai,
        start_date=date(2026, 8, 1),
        source_evidence={"field": "due_date", "page_number": 1},
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    assert event.public_id is not None
    assert event.status == CalendarEventStatus.suggested
    assert event.document is document
    assert document.calendar_events == [event]
    assert test_user.calendar_events == [event]


def test_calendar_event_rejects_invalid_time_ranges(
    db_session: Session,
    test_user: User,
) -> None:
    invalid_event = CalendarEvent(
        owner_id=test_user.id,
        title="Invalid date range",
        event_type=CalendarEventType.custom,
        start_date=date(2026, 8, 2),
        end_date=date(2026, 8, 1),
    )
    db_session.add(invalid_event)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    invalid_all_day_event = CalendarEvent(
        owner_id=test_user.id,
        title="All-day event with a timestamp",
        event_type=CalendarEventType.custom,
        start_date=date(2026, 8, 1),
        start_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
    )
    db_session.add(invalid_all_day_event)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    missing_start_datetime_event = CalendarEvent(
        owner_id=test_user.id,
        title="Timed event without a start",
        event_type=CalendarEventType.appointment,
        all_day=False,
    )
    db_session.add(missing_start_datetime_event)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    invalid_datetime_event = CalendarEvent(
        owner_id=test_user.id,
        title="Invalid datetime range",
        event_type=CalendarEventType.appointment,
        all_day=False,
        start_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
        end_at=datetime(2026, 8, 1, 11, tzinfo=UTC),
    )
    db_session.add(invalid_datetime_event)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_calendar_event_prevents_duplicate_active_ai_source_key(
    db_session: Session,
    test_user: User,
) -> None:
    first = CalendarEvent(
        owner_id=test_user.id,
        title="Invoice due",
        event_type=CalendarEventType.payment_due,
        source=CalendarEventSource.ai,
        source_key="document:42:due_date",
        start_date=date(2026, 8, 1),
    )
    duplicate = CalendarEvent(
        owner_id=test_user.id,
        title="Duplicate invoice due",
        event_type=CalendarEventType.payment_due,
        source=CalendarEventSource.ai,
        source_key="document:42:due_date",
        start_date=date(2026, 8, 1),
    )
    db_session.add(first)
    db_session.commit()
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.commit()
