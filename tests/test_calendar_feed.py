from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.v1.routes_calendar import download_calendar_feed, download_calendar_event
from app.models.calendar_event import CalendarEvent, CalendarEventStatus, CalendarEventType
from app.models.user import User
from app.services.calendar_feeds import (
    generate_calendar_feed_token,
    get_user_for_calendar_feed_token,
    revoke_calendar_feed_token,
)


def _event(owner: User, **values: object) -> CalendarEvent:
    defaults: dict[str, object] = {
        "owner_id": owner.id,
        "title": "Pay invoice",
        "event_type": CalendarEventType.payment_due,
        "status": CalendarEventStatus.confirmed,
        "start_date": date(2026, 8, 1),
        "source_evidence": {},
    }
    defaults.update(values)
    return CalendarEvent(**defaults)


def test_private_feed_token_can_be_regenerated_and_revoked(db_session: Session, test_user: User) -> None:
    first = generate_calendar_feed_token(db=db_session, user=test_user)
    assert get_user_for_calendar_feed_token(db=db_session, token=first) == test_user
    assert test_user.calendar_feed_token_hash != first

    second = generate_calendar_feed_token(db=db_session, user=test_user)
    assert second != first
    assert get_user_for_calendar_feed_token(db=db_session, token=first) is None
    assert get_user_for_calendar_feed_token(db=db_session, token=second) == test_user

    revoke_calendar_feed_token(db=db_session, user=test_user)
    assert get_user_for_calendar_feed_token(db=db_session, token=second) is None


def test_private_feed_excludes_deleted_events_and_does_not_use_access_jwt(
    db_session: Session,
    test_user: User,
) -> None:
    visible = _event(test_user)
    deleted = _event(
        test_user,
        title="Deleted",
        deleted_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
    )
    db_session.add_all([visible, deleted])
    db_session.commit()
    token = generate_calendar_feed_token(db=db_session, user=test_user)

    response = download_calendar_feed(db=db_session, token=token)
    content = response.body.decode()
    assert response.headers["content-disposition"] == 'attachment; filename="docsflow-calendar.ics"'
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["cache-control"] == "private, no-store"
    assert "SUMMARY:Pay invoice" in content
    assert "SUMMARY:Deleted" not in content

    with pytest.raises(HTTPException) as exc_info:
        download_calendar_feed(db=db_session, token="x" * 43)
    assert exc_info.value.status_code == 404


def test_single_ical_download_is_owner_scoped(db_session: Session, test_user: User) -> None:
    event = _event(test_user)
    db_session.add(event)
    db_session.commit()
    response = download_calendar_event(event_id=event.id, db=db_session, current_user=test_user)
    assert "UID:" in response.body.decode()

    other = User(email="other-feed@example.com", password_hash="hash")
    db_session.add(other)
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        download_calendar_event(event_id=event.id, db=db_session, current_user=other)
    assert exc_info.value.status_code == 404
