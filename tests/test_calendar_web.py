from datetime import UTC, date, datetime, tzinfo

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import web
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.web import CSRF_COOKIE_NAME


def _login(client: TestClient, user: User) -> None:
    response = client.post(
        "/login",
        data={"email": user.email, "password": "strong-password"},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER


def test_calendar_page_renders_month_agenda_filters_and_event_detail_link(
    client: TestClient,
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
    db_session.flush()
    event = CalendarEvent(
        owner_id=test_user.id,
        document_id=document.id,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        status=CalendarEventStatus.suggested,
        source=CalendarEventSource.ai,
        start_date=date(2026, 8, 4),
        source_key="document:1:payment_due:2026-08-04",
        source_evidence={"quote": "Payment due 2026-08-04", "page_number": 1},
    )
    db_session.add(event)
    db_session.commit()
    _login(client, test_user)

    response = client.get("/calendar?month=2026-08&view=month")

    assert response.status_code == status.HTTP_200_OK
    assert "Calendar" in response.text
    assert 'href="/calendar"' in response.text
    assert 'name="event_type"' in response.text
    assert 'name="status"' in response.text
    assert 'name="source"' in response.text
    assert 'name="document_id"' in response.text
    assert "Pay invoice" in response.text
    assert f'href="/calendar/events/{event.id}"' in response.text
    assert "drag" not in response.text.lower()

    agenda = client.get("/calendar?month=2026-08&view=agenda&source=ai")
    assert agenda.status_code == status.HTTP_200_OK
    assert "Pay invoice" in agenda.text


def test_calendar_month_renders_each_overlapping_day_of_multiday_event(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Conference",
        event_type=CalendarEventType.custom,
        start_date=date(2026, 7, 31),
        end_date=date(2026, 8, 2),
        source_evidence={},
    )
    db_session.add(event)
    db_session.commit()
    _login(client, test_user)

    response = client.get("/calendar?month=2026-08&view=month")

    assert response.status_code == status.HTTP_200_OK
    assert response.text.count(">Conference</span>") == 2


def test_calendar_today_uses_the_user_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            value = datetime(2026, 8, 1, 0, 30, tzinfo=UTC)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(web, "datetime", FrozenDatetime)

    assert web._calendar_today("Europe/Berlin") == date(2026, 8, 1)
    assert web._calendar_today("America/Los_Angeles") == date(2026, 7, 31)


def test_calendar_detail_confirms_and_dismisses_suggestion(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Review contract",
        event_type=CalendarEventType.action_deadline,
        status=CalendarEventStatus.suggested,
        source=CalendarEventSource.ai,
        start_date=date(2026, 8, 6),
        source_key="document:1:action_deadline:2026-08-06",
        source_evidence={},
    )
    db_session.add(event)
    db_session.commit()
    _login(client, test_user)

    detail = client.get(f"/calendar/events/{event.id}")
    assert detail.status_code == status.HTTP_200_OK
    assert "Confirm suggestion" in detail.text
    assert "Dismiss suggestion" in detail.text

    confirmed = client.post(
        f"/calendar/events/{event.id}/confirm",
        data={"sequence": str(event.sequence)},
        follow_redirects=False,
    )
    assert confirmed.status_code == status.HTTP_303_SEE_OTHER
    db_session.refresh(event)
    assert event.status == CalendarEventStatus.confirmed

    dismissed = CalendarEvent(
        owner_id=test_user.id,
        title="Old suggestion",
        event_type=CalendarEventType.action_deadline,
        status=CalendarEventStatus.suggested,
        source=CalendarEventSource.ai,
        start_date=date(2026, 8, 7),
        source_key="document:2:action_deadline:2026-08-07",
        source_evidence={},
    )
    db_session.add(dismissed)
    db_session.commit()
    response = client.post(
        f"/calendar/events/{dismissed.id}/dismiss",
        data={"sequence": str(dismissed.sequence)},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    db_session.refresh(dismissed)
    assert dismissed.status == CalendarEventStatus.cancelled


def test_calendar_detail_deletes_event_after_browser_confirmation(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Remove this event",
        event_type=CalendarEventType.custom,
        start_date=date(2026, 8, 8),
        source_evidence={},
    )
    db_session.add(event)
    db_session.commit()
    _login(client, test_user)

    detail = client.get(f"/calendar/events/{event.id}")
    assert detail.status_code == status.HTTP_200_OK
    assert f'action="/calendar/events/{event.id}/delete"' in detail.text
    assert "Delete this calendar event? This cannot be undone." in detail.text

    response = client.post(
        f"/calendar/events/{event.id}/delete",
        data={"sequence": str(event.sequence)},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/calendar?deleted=1"
    db_session.refresh(event)
    assert event.deleted_at is not None


def test_document_page_lists_related_events_and_creates_deadline_event(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="notice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        deadline=date(2026, 8, 15),
    )
    db_session.add(document)
    db_session.flush()
    related = CalendarEvent(
        owner_id=test_user.id,
        document_id=document.id,
        title="Existing deadline",
        event_type=CalendarEventType.action_deadline,
        start_date=date(2026, 8, 15),
        source_evidence={},
    )
    db_session.add(related)
    db_session.commit()
    _login(client, test_user)

    page = client.get(f"/documents/{document.id}")
    assert page.status_code == status.HTTP_200_OK
    assert "Related calendar events" in page.text
    assert "Existing deadline" in page.text
    assert "Create event from deadline" in page.text
    assert "Add to calendar" in page.text

    created = client.post(
        f"/documents/{document.id}/calendar/from-deadline",
        follow_redirects=False,
    )
    assert created.status_code == status.HTTP_303_SEE_OTHER
    event = db_session.scalar(
        select(CalendarEvent).where(
            CalendarEvent.owner_id == test_user.id,
            CalendarEvent.title == "Deadline: notice.pdf",
        )
    )
    assert event is not None
    assert event.document_id == document.id
    assert event.start_date == document.deadline
    assert event.status == CalendarEventStatus.confirmed


def test_calendar_event_form_creates_timed_event_and_rejects_bad_range(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="appointment.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
    )
    db_session.add(document)
    db_session.commit()
    _login(client, test_user)

    form = client.get(f"/calendar/new?document_id={document.id}")
    assert form.status_code == status.HTTP_200_OK
    assert 'name="description"' in form.text
    assert 'name="event_type"' in form.text
    assert 'name="all_day"' in form.text
    assert 'name="start_date"' in form.text
    assert 'name="start_time"' in form.text
    assert 'name="timezone_name"' in form.text
    assert "Reminder delivery is planned for MVP 8" in form.text
    csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token
    assert f'name="csrf_token" value="{csrf_token}"' in form.text

    invalid = client.post(
        "/calendar/events",
        data={
            "title": "Bad range",
            "event_type": "appointment",
            "all_day": "true",
            "start_date": "2026-08-10",
            "end_date": "2026-08-09",
        },
    )
    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "end_date must not be earlier than start_date" in invalid.text

    client.headers["X-CSRF-Token"] = "stale-token"
    created = client.post(
        "/calendar/events",
        data={
            "csrf_token": csrf_token,
            "title": "Contract call",
            "description": "Discuss renewal terms.",
            "event_type": "appointment",
            "start_time": "2026-08-10T09:30",
            "end_time": "2026-08-10T10:15",
            "timezone_name": "Europe/Berlin",
            "document_id": str(document.id),
        },
        follow_redirects=False,
    )
    assert created.status_code == status.HTTP_303_SEE_OTHER
    event = db_session.scalar(
        select(CalendarEvent).where(CalendarEvent.title == "Contract call")
    )
    assert event is not None
    assert event.all_day is False
    assert event.timezone == "Europe/Berlin"
    assert event.document_id == document.id
    assert event.start_at is not None
    assert event.start_at.hour == 7


@pytest.mark.parametrize(
    ("start_time", "message"),
    [
        (
            "2026-03-29T02:30",
            "does not exist in Europe/Berlin because of a daylight-saving transition",
        ),
        (
            "2026-10-25T02:30",
            "is ambiguous in Europe/Berlin because of a daylight-saving transition",
        ),
    ],
)
def test_calendar_event_form_rejects_invalid_dst_local_times(
    start_time: str,
    message: str,
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    _login(client, test_user)
    form = client.get("/calendar/new")
    csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
    assert form.status_code == status.HTTP_200_OK
    assert csrf_token

    response = client.post(
        "/calendar/events",
        data={
            "csrf_token": csrf_token,
            "title": "DST appointment",
            "event_type": "appointment",
            "start_time": start_time,
            "timezone_name": "Europe/Berlin",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert message in response.text
    assert db_session.scalar(
        select(CalendarEvent).where(CalendarEvent.title == "DST appointment")
    ) is None


def test_editing_ai_event_displays_evidence_and_detaches_from_projection(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="AI payment reminder",
        event_type=CalendarEventType.payment_due,
        status=CalendarEventStatus.suggested,
        source=CalendarEventSource.ai,
        start_date=date(2026, 8, 10),
        source_key="document:1:payment_due:2026-08-10",
        source_evidence={"quote": "Pay by 10 August", "page_number": 2},
    )
    db_session.add(event)
    db_session.commit()
    _login(client, test_user)

    form = client.get(f"/calendar/events/{event.id}/edit")
    assert form.status_code == status.HTTP_200_OK
    assert "saving changes detaches this event" in form.text
    assert "AI evidence (read-only)" in form.text
    assert "Pay by 10 August" in form.text

    updated = client.post(
        f"/calendar/events/{event.id}/edit",
        data={
            "sequence": str(event.sequence),
            "title": "Pay after review",
            "description": "",
            "event_type": "payment_due",
            "all_day": "true",
            "start_date": "2026-08-11",
            "end_date": "",
            "timezone_name": "Europe/Berlin",
            "document_id": "",
        },
        follow_redirects=False,
    )
    assert updated.status_code == status.HTTP_303_SEE_OTHER
    db_session.refresh(event)
    assert event.title == "Pay after review"
    assert event.start_date == date(2026, 8, 11)
    assert event.detached_from_source is True
