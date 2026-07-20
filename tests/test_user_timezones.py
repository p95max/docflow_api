from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

import app.api.v1.routes_users as routes_users
from app.core.timezones import DEFAULT_USER_TIMEZONE, normalize_datetime_to_utc
from app.models.calendar_event import CalendarEvent, CalendarEventType
from app.models.user import User
from app.schemas.user import UserTimezoneUpdate
from app.services.users import update_user_timezone
from app.web import _format_user_datetime


def test_user_timezone_defaults_to_berlin_and_can_be_updated(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.timezone == DEFAULT_USER_TIMEZONE

    updated = update_user_timezone(
        db=db_session,
        user=test_user,
        timezone=" America/New_York ",
    )

    assert updated.timezone == "America/New_York"
    assert routes_users.update_me_timezone(
        payload=UserTimezoneUpdate(timezone="Asia/Tokyo"),
        current_user=test_user,
        db=db_session,
    ).timezone == "Asia/Tokyo"


@pytest.mark.parametrize("value", ["", "Not/AZone", "Europe/Berlin/Extra"])
def test_user_timezone_rejects_non_iana_identifiers(value: str) -> None:
    with pytest.raises(ValidationError):
        UserTimezoneUpdate(timezone=value)


def test_datetime_normalization_handles_dst_transitions() -> None:
    berlin = ZoneInfo("Europe/Berlin")

    assert normalize_datetime_to_utc(
        datetime(2026, 3, 29, 1, 30, tzinfo=berlin)
    ) == datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
    assert normalize_datetime_to_utc(
        datetime(2026, 3, 29, 3, 30, tzinfo=berlin)
    ) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)

    first_occurrence = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0)
    second_occurrence = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1)
    assert normalize_datetime_to_utc(first_occurrence) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )
    assert normalize_datetime_to_utc(second_occurrence) == datetime(
        2026, 10, 25, 1, 30, tzinfo=UTC
    )

    with pytest.raises(ValueError, match="must include a timezone"):
        normalize_datetime_to_utc(datetime(2026, 7, 20, 12))


def test_date_only_events_remain_dates_and_keep_event_timezone(
    db_session: Session,
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Invoice due",
        event_type=CalendarEventType.payment_due,
        start_date=date(2026, 10, 25),
        timezone="Europe/Berlin",
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    assert event.start_date == date(2026, 10, 25)
    assert event.start_at is None
    assert event.timezone == "Europe/Berlin"
    assert _format_user_datetime(
        datetime(2026, 7, 20, 12, tzinfo=UTC), "America/New_York"
    ) == "08:00 20-07-2026"


def test_datetime_events_are_normalized_to_utc(
    test_user: User,
) -> None:
    event = CalendarEvent(
        owner_id=test_user.id,
        title="Call supplier",
        event_type=CalendarEventType.appointment,
        all_day=False,
        start_at=datetime(2026, 7, 20, 10, tzinfo=ZoneInfo("Europe/Berlin")),
        timezone="Europe/Berlin",
    )

    assert event.start_at == datetime(2026, 7, 20, 8, tzinfo=UTC)
    assert event.timezone == "Europe/Berlin"
