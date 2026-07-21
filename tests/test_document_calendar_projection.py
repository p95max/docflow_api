from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventStatus,
)
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.services.document_calendar_projection import (
    reconcile_document_calendar_events,
)


def _extraction(*, title: str = "Pay invoice", event_date: str = "2026-08-01") -> dict[str, object]:
    return {
        "document_type": "invoice",
        "summary": "Invoice requiring payment.",
        "sender": "Example GmbH",
        "recipient": None,
        "document_date": "2026-07-01",
        "due_date": event_date,
        "total_amount": 100.0,
        "currency": "EUR",
        "invoice_number": "INV-123",
        "reference_number": None,
        "requires_action": True,
        "action_deadline": None,
        "confidence_score": 0.95,
        "notes": None,
        "temporal_events": [
            {
                "event_type": "payment_due",
                "title": title,
                "date": event_date,
                "datetime": None,
                "all_day": True,
                "timezone": None,
                "requires_action": True,
                "confidence_score": 0.96,
                "original_phrase": f"due on {event_date}",
                "source_field": "due_date",
                "reference_date": None,
                "evidence": {"quote": f"Payment due {event_date}", "page_number": 1},
            }
        ],
    }


def _validation(*, status: str = "valid", event_date: str = "2026-08-01") -> dict[str, object]:
    return {
        "temporal_validation": {
            "events": [
                {
                    "event_index": 0,
                    "status": status,
                    "date_kind": "concrete",
                    "date_role": "event_date",
                    "resolved_date": event_date,
                    "evidence_page_number": 1,
                    "is_projectable": status in {"valid", "warning"},
                }
            ]
        }
    }


def _document(*, owner_id: int) -> Document:
    return Document(
        owner_id=owner_id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        ai_extracted_data=_extraction(),
        validation_candidates=_validation(),
    )


def test_projection_is_idempotent_updates_suggestions_and_records_audits(
    db_session: Session,
    test_user: User,
) -> None:
    document = _document(owner_id=test_user.id)
    db_session.add(document)
    db_session.commit()

    first = reconcile_document_calendar_events(db=db_session, document=document)
    event = db_session.scalar(select(CalendarEvent))

    assert first.created == 1
    assert event is not None
    assert event.status == CalendarEventStatus.suggested
    assert event.source_key == f"document:{document.id}:payment_due:2026-08-01"

    repeated = reconcile_document_calendar_events(db=db_session, document=document)
    assert repeated.created == repeated.updated == repeated.removed == 0
    assert len(db_session.scalars(select(CalendarEvent)).all()) == 1

    document.ai_extracted_data = _extraction(title="Pay corrected invoice")
    updated = reconcile_document_calendar_events(db=db_session, document=document)
    db_session.refresh(event)

    assert updated.updated == 1
    assert event.title == "Pay corrected invoice"
    actions = db_session.scalars(
        select(AuditLog.action).where(AuditLog.calendar_event_id == event.id)
    ).all()
    assert actions == ["calendar_event_projected", "calendar_event_reconciled"]


def test_projection_reconciles_manual_deadline_and_preserves_confirmed_events(
    db_session: Session,
    test_user: User,
) -> None:
    document = _document(owner_id=test_user.id)
    db_session.add(document)
    db_session.commit()
    reconcile_document_calendar_events(db=db_session, document=document)

    original = db_session.scalar(select(CalendarEvent))
    assert original is not None
    document.deadline = date(2026, 8, 4)
    document.manual_corrections = {"deadline": "2026-08-04"}
    corrected = reconcile_document_calendar_events(db=db_session, document=document)

    assert corrected.created == 1
    assert corrected.removed == 1
    assert original.deleted_at is not None
    corrected_event = db_session.scalar(
        select(CalendarEvent).where(CalendarEvent.deleted_at.is_(None))
    )
    assert corrected_event is not None
    assert corrected_event.start_date == date(2026, 8, 4)
    assert corrected_event.source_evidence["manual_correction"]["applied"] is True

    corrected_event.status = CalendarEventStatus.confirmed
    db_session.commit()
    document.ai_extracted_data = _extraction(event_date="2026-08-10")
    document.validation_candidates = _validation(event_date="2026-08-10")
    reconcile_document_calendar_events(db=db_session, document=document)
    db_session.refresh(corrected_event)

    assert corrected_event.deleted_at is None
    assert corrected_event.status == CalendarEventStatus.confirmed


def test_projection_marks_warning_suggestions_and_skips_needs_review(
    db_session: Session,
    test_user: User,
) -> None:
    document = _document(owner_id=test_user.id)
    document.validation_candidates = _validation(status="warning")
    db_session.add(document)
    db_session.commit()

    warning_result = reconcile_document_calendar_events(db=db_session, document=document)
    event = db_session.scalar(select(CalendarEvent))

    assert warning_result.created == 1
    assert event is not None
    assert event.requires_review is True

    document.validation_candidates = _validation(status="needs_review")
    review_result = reconcile_document_calendar_events(db=db_session, document=document)
    db_session.refresh(event)

    assert review_result.created == 0
    assert review_result.removed == 1
    assert event.deleted_at is not None
