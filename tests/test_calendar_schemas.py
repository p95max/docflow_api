from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.models.calendar_event import CalendarEventType
from app.schemas.calendar_event import (
    CalendarEventComplete,
    CalendarEventConfirm,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarRangeQuery,
)


def test_create_schema_accepts_date_only_event_without_conversion() -> None:
    payload = CalendarEventCreate(
        title="  Pay invoice  ",
        event_type=CalendarEventType.payment_due,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 2),
    )

    assert payload.title == "Pay invoice"
    assert payload.start_date == date(2026, 8, 1)
    assert payload.start_at is None


def test_create_schema_normalizes_timed_event_and_keeps_original_timezone() -> None:
    payload = CalendarEventCreate(
        title="Call supplier",
        event_type=CalendarEventType.appointment,
        all_day=False,
        start_at="2026-08-01T10:00:00+02:00",
        end_at="2026-08-01T11:00:00+02:00",
        timezone="Europe/Berlin",
    )

    assert payload.start_at == datetime(2026, 8, 1, 8, tzinfo=UTC)
    assert payload.end_at == datetime(2026, 8, 1, 9, tzinfo=UTC)
    assert payload.timezone == "Europe/Berlin"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "title": "Missing date",
            "event_type": "custom",
        },
        {
            "title": "Date with timestamp",
            "event_type": "custom",
            "start_date": "2026-08-01",
            "start_at": "2026-08-01T10:00:00+00:00",
        },
        {
            "title": "Timed without timezone",
            "event_type": "appointment",
            "all_day": False,
            "start_at": "2026-08-01T10:00:00+00:00",
        },
        {
            "title": "Naive timestamp",
            "event_type": "appointment",
            "all_day": False,
            "start_at": "2026-08-01T10:00:00",
            "timezone": "Europe/Berlin",
        },
    ],
)
def test_create_schema_rejects_invalid_event_variants(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CalendarEventCreate.model_validate(payload)


@pytest.mark.parametrize("field", ["owner_id", "source", "status", "source_evidence"])
def test_mutation_schemas_reject_server_controlled_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CalendarEventCreate.model_validate(
            {
                "title": "Pay invoice",
                "event_type": "payment_due",
                "start_date": "2026-08-01",
                field: 1 if field == "owner_id" else "ai",
            }
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CalendarEventUpdate.model_validate({field: "ai"})


def test_update_schema_validates_supplied_values_only() -> None:
    payload = CalendarEventUpdate(
        title="  Renamed event ",
        start_at="2026-08-01T10:00:00+02:00",
    )

    assert payload.title == "Renamed event"
    assert payload.start_at == datetime(2026, 8, 1, 8, tzinfo=UTC)


def test_update_schema_rejects_invalid_supplied_range() -> None:
    with pytest.raises(ValidationError, match="end_at must not be earlier"):
        CalendarEventUpdate(
            start_at="2026-08-01T10:00:00+02:00",
            end_at="2026-08-01T09:00:00+02:00",
        )


def test_range_and_lifecycle_schemas_are_strict() -> None:
    with pytest.raises(ValidationError, match="end must not be earlier"):
        CalendarRangeQuery(start=date(2026, 8, 2), end=date(2026, 8, 1))

    assert CalendarEventConfirm().model_dump() == {}
    assert CalendarEventComplete().model_dump() == {}
    with pytest.raises(ValidationError):
        CalendarEventComplete(unexpected=True)
