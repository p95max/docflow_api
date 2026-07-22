from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.v1.routes_calendar import (
    cancel_calendar_event,
    complete_calendar_event,
    confirm_calendar_event,
    create_calendar_event,
    delete_calendar_event,
    download_calendar_event,
    get_calendar_event,
    update_calendar_event,
)
from app.api.v1.routes_documents import get_my_document
from app.models.calendar_event import CalendarEvent, CalendarEventStatus, CalendarEventType
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.schemas.calendar_event import CalendarEventCreate, CalendarEventUpdate
from app.web import templates


def _event(owner: User, *, document_id: int | None = None) -> CalendarEvent:
    return CalendarEvent(
        owner_id=owner.id,
        document_id=document_id,
        title="Private event",
        description="<img src=x onerror=alert(1)>",
        event_type=CalendarEventType.custom,
        status=CalendarEventStatus.confirmed,
        all_day=True,
        start_date=date(2026, 8, 1),
        source_evidence={},
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
    )


def test_calendar_event_mutations_and_ics_are_idor_protected(
    db_session: Session,
    test_user: User,
) -> None:
    other = User(email="other-calendar-security@example.com", password_hash="hash")
    event = _event(test_user)
    db_session.add_all([other, event])
    db_session.commit()

    attempts = [
        lambda: get_calendar_event(event_id=event.id, db=db_session, current_user=other),
        lambda: update_calendar_event(
            event_id=event.id,
            payload=CalendarEventUpdate(title="Hijacked"),
            db=db_session,
            current_user=other,
        ),
        lambda: delete_calendar_event(event_id=event.id, db=db_session, current_user=other),
        lambda: confirm_calendar_event(event_id=event.id, db=db_session, current_user=other),
        lambda: complete_calendar_event(event_id=event.id, db=db_session, current_user=other),
        lambda: cancel_calendar_event(event_id=event.id, db=db_session, current_user=other),
        lambda: download_calendar_event(event_id=event.id, db=db_session, current_user=other),
    ]
    for attempt in attempts:
        with pytest.raises(HTTPException) as exc_info:
            attempt()
        assert exc_info.value.status_code == 404


def test_event_cannot_link_or_navigate_to_another_users_document(
    db_session: Session,
    test_user: User,
) -> None:
    other = User(email="other-document-security@example.com", password_hash="hash")
    document = Document(
        owner_id=test_user.id,
        original_filename="private.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
    )
    db_session.add_all([other, document])
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        create_calendar_event(
            payload=CalendarEventCreate(
                title="Link private document",
                event_type=CalendarEventType.custom,
                start_date="2026-08-01",
                document_id=document.id,
            ),
            db=db_session,
            current_user=other,
        )
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        get_my_document(document_id=document.id, db=db_session, current_user=other)
    assert exc_info.value.status_code == 404


def test_calendar_description_is_rendered_as_escaped_text() -> None:
    event = _event(User(id=1, email="render@example.com", password_hash="hash"))
    event.id = 4
    rendered = templates.get_template("calendar_event_detail.html").render(
        request=_request(),
        current_user=None,
        event=event,
        error=None,
        csrf_token="test-csrf-token",
    )

    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert "<img src=x onerror=alert(1)>" not in rendered
