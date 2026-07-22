"""Provider-neutral RFC 5545 calendar serialization."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.models.calendar_event import CalendarEvent, CalendarEventStatus


ICAL_CONTENT_TYPE = "text/calendar; charset=utf-8"


def serialize_event_calendar(event: CalendarEvent, *, calendar_name: str = "DocsFlow") -> str:
    return _serialize_calendar([event], calendar_name=calendar_name)


def serialize_feed_calendar(events: list[CalendarEvent], *, calendar_name: str = "DocsFlow") -> str:
    return _serialize_calendar(events, calendar_name=calendar_name)


def _serialize_calendar(events: list[CalendarEvent], *, calendar_name: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//DocsFlow//Calendar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{escape_ical_text(calendar_name)}",
    ]
    for event in events:
        lines.extend(_serialize_event(event))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ical_line(line) for line in lines) + "\r\n"


def _serialize_event(event: CalendarEvent) -> list[str]:
    lines = ["BEGIN:VEVENT"]
    lines.extend(
        [
            f"UID:{event.ical_uid}",
            f"DTSTAMP:{_format_utc(event.updated_at or event.created_at or datetime.now(UTC))}",
            f"SEQUENCE:{event.sequence}",
            f"SUMMARY:{escape_ical_text(event.title)}",
        ]
    )
    if event.description:
        lines.append(f"DESCRIPTION:{escape_ical_text(event.description)}")
    lines.extend(_serialize_event_time(event))
    status = _ical_status(event.status)
    if status:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return lines


def _serialize_event_time(event: CalendarEvent) -> list[str]:
    if event.all_day:
        if event.start_date is None:
            raise ValueError("All-day calendar event has no start_date.")
        end_date = (event.end_date or event.start_date) + timedelta(days=1)
        return [
            f"DTSTART;VALUE=DATE:{_format_date(event.start_date)}",
            f"DTEND;VALUE=DATE:{_format_date(end_date)}",
        ]
    if event.start_at is None:
        raise ValueError("Timed calendar event has no start_at.")
    lines = [f"DTSTART:{_format_utc(event.start_at)}"]
    if event.end_at is not None:
        lines.append(f"DTEND:{_format_utc(event.end_at)}")
    return lines


def escape_ical_text(value: str) -> str:
    """Escape RFC 5545 text values without treating user text as markup."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def fold_ical_line(line: str) -> str:
    """Fold a content line at 75 UTF-8 octets with RFC 5545 continuation."""
    chunks: list[str] = []
    current = ""
    current_bytes = 0
    for character in line:
        character_bytes = len(character.encode("utf-8"))
        if current and current_bytes + character_bytes > 75:
            chunks.append(current)
            current = " " + character
            current_bytes = 1 + character_bytes
        else:
            current += character
            current_bytes += character_bytes
    chunks.append(current)
    return "\r\n".join(chunks)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _ical_status(status: CalendarEventStatus) -> str | None:
    if status == CalendarEventStatus.cancelled:
        return "CANCELLED"
    if status == CalendarEventStatus.suggested:
        return "TENTATIVE"
    return "CONFIRMED"
