from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models.calendar_event import CalendarEventStatus, CalendarEventType
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.schemas.calendar_event import CalendarEventCreate, CalendarEventUpdate
from app.services.calendar_events import (
    CalendarEventConflictError,
    create_user_event,
    reconcile_ai_event,
    update_event,
)


def _event_payload(title: str = "Pay invoice") -> CalendarEventCreate:
    return CalendarEventCreate(
        title=title,
        event_type=CalendarEventType.payment_due,
        start_date=date(2026, 8, 1),
    )


def test_service_enforces_expected_sequence(
    db_session: Session,
    test_user: User,
) -> None:
    event = create_user_event(
        db=db_session,
        owner_id=test_user.id,
        payload=_event_payload(),
    )

    updated = update_event(
        db=db_session,
        owner_id=test_user.id,
        event_id=event.id,
        payload=CalendarEventUpdate(title="New title", expected_sequence=0),
    )
    assert updated.sequence == 1

    with pytest.raises(CalendarEventConflictError, match="changed"):
        update_event(
            db=db_session,
            owner_id=test_user.id,
            event_id=event.id,
            payload=CalendarEventUpdate(title="Stale update", expected_sequence=0),
        )


def test_ai_reconciliation_is_idempotent_and_preserves_manual_override(
    db_session: Session,
    test_user: User,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
    )
    db_session.add(document)
    db_session.commit()

    first = reconcile_ai_event(
        db=db_session,
        owner_id=test_user.id,
        document_id=document.id,
        source_key=f"document:{document.id}:payment_due:2026-08-01",
        payload=_event_payload(),
        source_field="due_date",
    )
    repeated = reconcile_ai_event(
        db=db_session,
        owner_id=test_user.id,
        document_id=document.id,
        source_key=f"document:{document.id}:payment_due:2026-08-01",
        payload=_event_payload(),
        source_field="due_date",
    )
    assert first.id == repeated.id
    assert repeated.status == CalendarEventStatus.suggested
    assert repeated.sequence == 0

    reconciled_change = reconcile_ai_event(
        db=db_session,
        owner_id=test_user.id,
        document_id=document.id,
        source_key=f"document:{document.id}:payment_due:2026-08-01",
        payload=_event_payload("Updated AI title"),
        source_field="due_date",
    )
    assert reconciled_change.title == "Updated AI title"
    assert reconciled_change.sequence == 1

    manually_updated = update_event(
        db=db_session,
        owner_id=test_user.id,
        event_id=reconciled_change.id,
        payload=CalendarEventUpdate(title="Pay after review"),
    )
    assert manually_updated.detached_from_source is True

    reconciled_after_edit = reconcile_ai_event(
        db=db_session,
        owner_id=test_user.id,
        document_id=document.id,
        source_key=f"document:{document.id}:payment_due:2026-08-01",
        payload=_event_payload("AI replacement title"),
        source_field="due_date",
    )
    assert reconciled_after_edit.title == "Pay after review"
