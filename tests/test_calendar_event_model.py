from datetime import date

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
