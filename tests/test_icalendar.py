from datetime import UTC, date, datetime

from app.models.calendar_event import CalendarEvent, CalendarEventStatus, CalendarEventType
from app.services.icalendar import escape_ical_text, fold_ical_line, serialize_event_calendar


def _event(**values: object) -> CalendarEvent:
    defaults: dict[str, object] = {
        "owner_id": 1,
        "title": "Pay, invoice; now",
        "description": "Line one\nLine two",
        "event_type": CalendarEventType.payment_due,
        "all_day": True,
        "start_date": date(2026, 8, 1),
        "ical_uid": "stable-event@docsflow",
        "sequence": 4,
        "created_at": datetime(2026, 7, 22, 8, 30, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 22, 9, 30, tzinfo=UTC),
    }
    defaults.update(values)
    return CalendarEvent(**defaults)


def test_all_day_ical_event_uses_date_values_and_stable_metadata() -> None:
    payload = serialize_event_calendar(_event(end_date=date(2026, 8, 3)))

    assert "UID:stable-event@docsflow\r\n" in payload
    assert "DTSTAMP:20260722T093000Z\r\n" in payload
    assert "SEQUENCE:4\r\n" in payload
    assert "DTSTART;VALUE=DATE:20260801\r\n" in payload
    assert "DTEND;VALUE=DATE:20260804\r\n" in payload
    assert "SUMMARY:Pay\\, invoice\\; now\r\n" in payload
    assert "DESCRIPTION:Line one\\nLine two\r\n" in payload


def test_timed_cancelled_event_uses_utc_and_cancelled_status() -> None:
    event = _event(
        all_day=False,
        start_date=None,
        start_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 1, 11, 30, tzinfo=UTC),
        status=CalendarEventStatus.cancelled,
    )
    payload = serialize_event_calendar(event)

    assert "DTSTART:20260801T100000Z\r\n" in payload
    assert "DTEND:20260801T113000Z\r\n" in payload
    assert "STATUS:CANCELLED\r\n" in payload


def test_ical_text_is_escaped_and_long_lines_are_folded_by_utf8_octets() -> None:
    assert escape_ical_text("one\\two;three,four\r\nfive") == "one\\\\two\\;three\\,four\\nfive"
    folded = fold_ical_line("SUMMARY:" + "€" * 40)
    physical_lines = folded.split("\r\n")

    assert len(physical_lines) > 1
    assert all(len(line.encode("utf-8")) <= 75 for line in physical_lines)
    assert all(line.startswith(" ") for line in physical_lines[1:])
