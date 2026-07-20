from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_db
from app.api.v1.routes_calendar import (
    cancel_calendar_event,
    complete_calendar_event,
    confirm_calendar_event,
    create_calendar_event,
    delete_calendar_event,
    get_calendar_event,
    list_calendar_events,
    update_calendar_event,
)
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.audit_log import AuditLog
from app.models.user import User
from app.main import app
from app.schemas.calendar_event import CalendarEventCreate, CalendarEventUpdate
from app.services.security import create_access_token
from app.services.users import create_user


def _create_payload(title: str = "Pay invoice") -> CalendarEventCreate:
    return CalendarEventCreate(
        title=title,
        event_type=CalendarEventType.payment_due,
        start_date=date(2026, 8, 1),
    )


def test_calendar_crud_is_owner_scoped_and_soft_deletes(
    db_session: Session,
    test_user: User,
) -> None:
    created = create_calendar_event(
        payload=_create_payload(),
        db=db_session,
        current_user=test_user,
    )
    assert created.status == CalendarEventStatus.confirmed
    assert created.source == CalendarEventSource.user
    assert db_session.scalar(
        select(AuditLog.action).where(AuditLog.calendar_event_id == created.id)
    ) == "calendar_event_created"

    loaded = get_calendar_event(
        event_id=created.id,
        db=db_session,
        current_user=test_user,
    )
    assert loaded.title == "Pay invoice"

    updated = update_calendar_event(
        event_id=created.id,
        payload=CalendarEventUpdate(title="Pay corrected invoice"),
        db=db_session,
        current_user=test_user,
    )
    assert updated.title == "Pay corrected invoice"
    assert updated.sequence == 1

    other_user = create_user(
        db=db_session,
        email="other-calendar@example.com",
        password="strong-password",
    )
    with pytest.raises(HTTPException) as exc_info:
        get_calendar_event(
            event_id=created.id,
            db=db_session,
            current_user=other_user,
        )
    assert exc_info.value.status_code == 404

    response = delete_calendar_event(
        event_id=created.id,
        db=db_session,
        current_user=test_user,
    )
    assert response.status_code == 204
    with pytest.raises(HTTPException) as exc_info:
        get_calendar_event(
            event_id=created.id,
            db=db_session,
            current_user=test_user,
    )
    assert exc_info.value.status_code == 404
    audit_actions = list(
        db_session.scalars(
            select(AuditLog.action)
            .where(AuditLog.calendar_event_id == created.id)
            .order_by(AuditLog.id)
        ).all()
    )
    assert audit_actions == [
        "calendar_event_created",
        "calendar_event_updated",
        "calendar_event_deleted",
    ]


def test_calendar_list_filters_date_range_and_source(
    db_session: Session,
    test_user: User,
) -> None:
    create_calendar_event(
        payload=_create_payload("August invoice"),
        db=db_session,
        current_user=test_user,
    )
    september_event = CalendarEvent(
        owner_id=test_user.id,
        title="September reminder",
        event_type=CalendarEventType.custom,
        source=CalendarEventSource.ai,
        start_date=date(2026, 9, 1),
    )
    db_session.add(september_event)
    db_session.commit()

    listed = list_calendar_events(
        db=db_session,
        current_user=test_user,
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
        status_filter=None,
        event_type=None,
        document_id=None,
        source=None,
    )
    assert [event.title for event in listed.items] == ["August invoice"]

    ai_events = list_calendar_events(
        db=db_session,
        current_user=test_user,
        start=None,
        end=None,
        status_filter=None,
        event_type=None,
        document_id=None,
        source=CalendarEventSource.ai,
    )
    assert [event.title for event in ai_events.items] == ["September reminder"]


def test_calendar_actions_are_idempotent_and_manual_edit_detaches_ai_event(
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="AI suggestion",
        event_type=CalendarEventType.payment_due,
        source=CalendarEventSource.ai,
        start_date=date(2026, 8, 1),
    )
    db_session.add(event)
    db_session.commit()

    updated = update_calendar_event(
        event_id=event.id,
        payload=CalendarEventUpdate(title="Confirmed manually"),
        db=db_session,
        current_user=test_user,
    )
    assert updated.detached_from_source is True

    confirmed = confirm_calendar_event(
        event_id=event.id,
        db=db_session,
        current_user=test_user,
    )
    assert confirmed.status == CalendarEventStatus.confirmed
    assert confirm_calendar_event(
        event_id=event.id,
        db=db_session,
        current_user=test_user,
    ).sequence == confirmed.sequence

    completed = complete_calendar_event(
        event_id=event.id,
        db=db_session,
        current_user=test_user,
    )
    assert completed.status == CalendarEventStatus.completed
    assert completed.completed_at is not None
    assert "calendar_event_confirmed" in list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.calendar_event_id == event.id)
        ).all()
    )

    with pytest.raises(HTTPException) as exc_info:
        cancel_calendar_event(
            event_id=event.id,
            db=db_session,
            current_user=test_user,
        )
    assert exc_info.value.status_code == 409


def test_calendar_list_interprets_timed_events_in_user_timezone(
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Late-night call",
        event_type=CalendarEventType.appointment,
        all_day=False,
        start_at=datetime(2026, 8, 1, 0, 30, tzinfo=UTC),
        timezone="Europe/Berlin",
    )
    db_session.add(event)
    db_session.commit()

    listed = list_calendar_events(
        db=db_session,
        current_user=test_user,
        start=date(2026, 8, 1),
        end=date(2026, 8, 1),
        status_filter=None,
        event_type=None,
        document_id=None,
        source=None,
    )
    assert [item.id for item in listed.items] == [event.id]

    updated = update_calendar_event(
        event_id=event.id,
        payload=CalendarEventUpdate(title="Updated late-night call"),
        db=db_session,
        current_user=test_user,
    )
    assert updated.title == "Updated late-night call"


def test_calendar_routes_accept_authenticated_http_requests(
    db_session: Session,
    test_user: User,
) -> None:
    def override_get_db() -> Session:
        return db_session

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(subject=str(test_user.id))
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            created = client.post(
                "/api/v1/calendar/events",
                headers=headers,
                json={
                    "title": "HTTP event",
                    "event_type": "payment_due",
                    "start_date": "2026-08-01",
                },
            )
            assert created.status_code == 201
            event_id = created.json()["id"]

            listed = client.get(
                "/api/v1/calendar/events",
                headers=headers,
                params={"start": "2026-08-01", "end": "2026-08-01"},
            )
            assert listed.status_code == 200
            assert listed.json()["total"] == 1

            confirmed = client.post(
                f"/api/v1/calendar/events/{event_id}/confirm",
                headers=headers,
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed"
    finally:
        app.dependency_overrides.clear()
