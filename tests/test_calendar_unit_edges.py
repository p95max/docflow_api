from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.models.calendar_event import CalendarEventType
from app.schemas.ai_processing import TemporalEventExtraction
from app.schemas.calendar_event import CalendarEventCreate
from app.services.document_calendar_projection import _source_key
from app.web import _calendar_form_values, _parse_local_calendar_datetime


@pytest.mark.parametrize(
    ("local_value", "message"),
    [
        ("2026-03-29T02:30", "does not exist"),
        ("2026-10-25T02:30", "is ambiguous"),
    ],
)
def test_local_datetime_rejects_dst_gap_and_overlap(
    local_value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_local_calendar_datetime(
            local_value,
            timezone_name="Europe/Berlin",
            field_label="Start time",
        )


def test_date_only_event_rejects_reverse_range_and_invalid_timezone() -> None:
    with pytest.raises(ValidationError, match="end_date must not be earlier"):
        CalendarEventCreate(
            title="Pay invoice",
            event_type=CalendarEventType.payment_due,
            start_date=date(2026, 8, 2),
            end_date=date(2026, 8, 1),
        )

    with pytest.raises(ValidationError, match="valid IANA timezone"):
        CalendarEventCreate(
            title="Call supplier",
            event_type=CalendarEventType.appointment,
            all_day=False,
            start_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
            timezone="Berlin/Europe",
        )


def test_projection_source_key_is_stable_for_equivalent_timed_instants() -> None:
    candidate = TemporalEventExtraction(
        event_type="appointment",
        title="Call supplier",
        date=None,
        datetime=datetime.fromisoformat("2026-08-01T10:00:00+02:00"),
        all_day=False,
        timezone="Europe/Berlin",
        requires_action=True,
        confidence_score=0.9,
        original_phrase="01.08.2026, 10:00",
        source_field="appointment_date",
        evidence={"quote": "Call on 01.08.2026, 10:00", "page_number": 1},
    )

    assert _source_key(document_id=42, candidate=candidate) == (
        "document:42:appointment:2026-08-01T08:00:00+00:00"
    )


def test_new_calendar_form_uses_the_day_selected_in_the_month_view() -> None:
    values = _calendar_form_values(
        event=None,
        timezone_name="Europe/Berlin",
        start_date=date(2026, 8, 12),
    )

    assert values["all_day"] is True
    assert values["start_date"] == "2026-08-12"
