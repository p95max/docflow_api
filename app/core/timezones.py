from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_USER_TIMEZONE = "Europe/Berlin"


def validate_iana_timezone(value: str) -> str:
    """Return a normalized IANA timezone identifier or raise ValueError."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("Timezone must be a valid IANA timezone identifier.")

    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Timezone must be a valid IANA timezone identifier.") from exc

    return normalized


def normalize_datetime_to_utc(value: datetime) -> datetime:
    """Normalize an aware event timestamp for UTC database storage."""
    if value.tzinfo is None:
        raise ValueError("Datetime events must include a timezone.")
    return value.astimezone(timezone.utc)
