from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.timezones import validate_iana_timezone
from app.models.audit_log import AuditLog
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
)
from app.models.document import Document
from app.schemas.calendar_event import (
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarRangeQuery,
)
from app.services.reminder_scheduler import (
    cancel_event_reminders,
    reschedule_event_reminders,
)


class CalendarEventNotFoundError(LookupError):
    pass


class CalendarDocumentNotFoundError(LookupError):
    pass


class CalendarEventConflictError(ValueError):
    pass


class CalendarEventValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("Calendar event validation failed.")


def list_events(
    *,
    db: Session,
    owner_id: int,
    user_timezone: str,
    query: CalendarRangeQuery,
) -> list[CalendarEvent]:
    conditions = [
        CalendarEvent.owner_id == owner_id,
        CalendarEvent.deleted_at.is_(None),
    ]
    if query.status is not None:
        conditions.append(CalendarEvent.status == query.status)
    if query.event_type is not None:
        conditions.append(CalendarEvent.event_type == query.event_type)
    if query.document_id is not None:
        conditions.append(CalendarEvent.document_id == query.document_id)
    if query.source is not None:
        conditions.append(CalendarEvent.source == query.source)
    if query.start is not None:
        start_at = _local_day_start_as_utc(query.start, user_timezone)
        conditions.append(
            or_(
                and_(
                    CalendarEvent.all_day.is_(True),
                    func.coalesce(CalendarEvent.end_date, CalendarEvent.start_date)
                    >= query.start,
                ),
                and_(
                    CalendarEvent.all_day.is_(False),
                    func.coalesce(CalendarEvent.end_at, CalendarEvent.start_at) >= start_at,
                ),
            )
        )
    if query.end is not None:
        end_at = _local_day_start_as_utc(query.end + timedelta(days=1), user_timezone)
        conditions.append(
            or_(
                and_(CalendarEvent.all_day.is_(True), CalendarEvent.start_date <= query.end),
                and_(CalendarEvent.all_day.is_(False), CalendarEvent.start_at < end_at),
            )
        )
    return list(
        db.scalars(
            select(CalendarEvent)
            .where(*conditions)
            .order_by(
                CalendarEvent.start_date.asc(),
                CalendarEvent.start_at.asc(),
                CalendarEvent.id.asc(),
            )
        ).all()
    )


def create_user_event(
    *,
    db: Session,
    owner_id: int,
    payload: CalendarEventCreate,
) -> CalendarEvent:
    _ensure_owned_document(db=db, owner_id=owner_id, document_id=payload.document_id)
    event = CalendarEvent(
        owner_id=owner_id,
        source=CalendarEventSource.user,
        status=CalendarEventStatus.confirmed,
        **payload.model_dump(),
    )
    db.add(event)
    db.flush()
    _add_audit_log(
        db=db,
        event=event,
        action="calendar_event_created",
        new_value=_event_audit_state(event),
    )
    db.commit()
    db.refresh(event)
    return event


def get_event(*, db: Session, owner_id: int, event_id: int) -> CalendarEvent:
    event = db.get(CalendarEvent, event_id)
    if event is None or event.owner_id != owner_id or event.deleted_at is not None:
        raise CalendarEventNotFoundError("Calendar event not found.")
    return event


def update_event(
    *,
    db: Session,
    owner_id: int,
    event_id: int,
    payload: CalendarEventUpdate,
) -> CalendarEvent:
    event = get_event(db=db, owner_id=owner_id, event_id=event_id)
    _ensure_sequence(event=event, expected_sequence=payload.expected_sequence)
    event_values = _validated_update_values(event=event, payload=payload)
    _ensure_owned_document(
        db=db,
        owner_id=owner_id,
        document_id=event_values["document_id"],
    )

    changes: dict[str, dict[str, object | None]] = {}
    for field_name, value in event_values.items():
        if getattr(event, field_name) != value:
            changes[field_name] = {
                "old": _serialize_audit_value(getattr(event, field_name)),
                "new": _serialize_audit_value(value),
            }
            setattr(event, field_name, value)
    if not changes:
        return event

    event.sequence += 1
    if event.source == CalendarEventSource.ai:
        event.detached_from_source = True
    if set(changes).intersection({"all_day", "start_date", "start_at", "timezone"}):
        reschedule_event_reminders(db=db, event=event)
    _add_audit_log(
        db=db,
        event=event,
        action="calendar_event_updated",
        new_value={"changes": changes},
    )
    db.commit()
    db.refresh(event)
    return event


def delete_event(
    *,
    db: Session,
    owner_id: int,
    event_id: int,
    expected_sequence: int | None = None,
) -> None:
    event = get_event(db=db, owner_id=owner_id, event_id=event_id)
    _ensure_sequence(event=event, expected_sequence=expected_sequence)
    event.deleted_at = datetime.now(UTC)
    event.sequence += 1
    cancel_event_reminders(
        db=db,
        event=event,
        reason="The calendar event was deleted.",
    )
    _add_audit_log(
        db=db,
        event=event,
        action="calendar_event_deleted",
        old_value=_event_audit_state(event),
    )
    db.commit()


def confirm_event(
    *,
    db: Session,
    owner_id: int,
    event_id: int,
    expected_sequence: int | None = None,
) -> CalendarEvent:
    event = get_event(db=db, owner_id=owner_id, event_id=event_id)
    _ensure_sequence(event=event, expected_sequence=expected_sequence)
    if event.status == CalendarEventStatus.cancelled:
        raise CalendarEventConflictError("Event is cancelled.")
    if event.status == CalendarEventStatus.completed:
        raise CalendarEventConflictError("Event is completed.")
    if event.status == CalendarEventStatus.confirmed:
        return event

    old_value = _event_audit_state(event)
    event.status = CalendarEventStatus.confirmed
    event.sequence += 1
    _add_audit_log(
        db=db,
        event=event,
        action="calendar_event_confirmed",
        old_value=old_value,
        new_value=_event_audit_state(event),
    )
    db.commit()
    db.refresh(event)
    return event


def complete_event(
    *,
    db: Session,
    owner_id: int,
    event_id: int,
    expected_sequence: int | None = None,
) -> CalendarEvent:
    event = get_event(db=db, owner_id=owner_id, event_id=event_id)
    _ensure_sequence(event=event, expected_sequence=expected_sequence)
    if event.status == CalendarEventStatus.cancelled:
        raise CalendarEventConflictError("Event is cancelled.")
    if event.status == CalendarEventStatus.completed:
        return event

    old_value = _event_audit_state(event)
    event.status = CalendarEventStatus.completed
    event.completed_at = datetime.now(UTC)
    event.sequence += 1
    cancel_event_reminders(
        db=db,
        event=event,
        reason="The calendar event was completed.",
    )
    _add_audit_log(
        db=db,
        event=event,
        action="calendar_event_completed",
        old_value=old_value,
        new_value=_event_audit_state(event),
    )
    db.commit()
    db.refresh(event)
    return event


def cancel_event(
    *,
    db: Session,
    owner_id: int,
    event_id: int,
    expected_sequence: int | None = None,
) -> CalendarEvent:
    event = get_event(db=db, owner_id=owner_id, event_id=event_id)
    _ensure_sequence(event=event, expected_sequence=expected_sequence)
    if event.status == CalendarEventStatus.completed:
        raise CalendarEventConflictError("Event is completed.")
    if event.status == CalendarEventStatus.cancelled:
        return event

    old_value = _event_audit_state(event)
    event.status = CalendarEventStatus.cancelled
    event.sequence += 1
    cancel_event_reminders(
        db=db,
        event=event,
        reason="The calendar event was cancelled.",
    )
    _add_audit_log(
        db=db,
        event=event,
        action="calendar_event_cancelled",
        old_value=old_value,
        new_value=_event_audit_state(event),
    )
    db.commit()
    db.refresh(event)
    return event


def reconcile_ai_event(
    *,
    db: Session,
    owner_id: int,
    document_id: int,
    source_key: str,
    payload: CalendarEventCreate,
    source_field: str | None = None,
    source_evidence: dict[str, Any] | None = None,
    confidence_score: float | None = None,
    requires_review: bool = False,
) -> CalendarEvent:
    """Idempotently project one validated AI event without overwriting user changes."""
    _ensure_owned_document(db=db, owner_id=owner_id, document_id=document_id)
    event = db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.owner_id == owner_id,
            CalendarEvent.source == CalendarEventSource.ai,
            CalendarEvent.source_key == source_key,
            CalendarEvent.deleted_at.is_(None),
        )
    )
    if event is None:
        event = CalendarEvent(
            owner_id=owner_id,
            document_id=document_id,
            source=CalendarEventSource.ai,
            status=CalendarEventStatus.suggested,
            source_key=source_key,
            source_field=source_field,
            source_evidence=source_evidence or {},
            confidence_score=confidence_score,
            requires_review=requires_review,
            **payload.model_dump(exclude={"document_id"}),
        )
        db.add(event)
        db.flush()
        _add_audit_log(
            db=db,
            event=event,
            action="calendar_event_projected",
            new_value=_event_audit_state(event),
        )
        db.commit()
        db.refresh(event)
        return event

    if event.detached_from_source or event.status != CalendarEventStatus.suggested:
        return event

    values = payload.model_dump(exclude={"document_id"})
    changed = False
    for field_name, value in values.items():
        if getattr(event, field_name) != value:
            setattr(event, field_name, value)
            changed = True
    if event.document_id != document_id:
        event.document_id = document_id
        changed = True
    if event.source_field != source_field:
        event.source_field = source_field
        changed = True
    evidence = source_evidence or {}
    if event.source_evidence != evidence:
        event.source_evidence = evidence
        changed = True
    if event.confidence_score != confidence_score:
        event.confidence_score = confidence_score
        changed = True
    if event.requires_review != requires_review:
        event.requires_review = requires_review
        changed = True
    if changed:
        event.sequence += 1
        _add_audit_log(
            db=db,
            event=event,
            action="calendar_event_reconciled",
            new_value=_event_audit_state(event),
        )
        db.commit()
        db.refresh(event)
    return event


def _ensure_owned_document(*, db: Session, owner_id: int, document_id: int | None) -> None:
    if document_id is None:
        return
    document = db.get(Document, document_id)
    if (
        document is None
        or document.owner_id != owner_id
        or document.deleted_at is not None
    ):
        raise CalendarDocumentNotFoundError("Document not found.")


def _ensure_sequence(*, event: CalendarEvent, expected_sequence: int | None) -> None:
    if expected_sequence is not None and event.sequence != expected_sequence:
        raise CalendarEventConflictError(
            "Calendar event changed. Refresh it and try again."
        )


def _validated_update_values(
    *,
    event: CalendarEvent,
    payload: CalendarEventUpdate,
) -> dict[str, object]:
    values = {
        "title": event.title,
        "description": event.description,
        "event_type": event.event_type,
        "all_day": event.all_day,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "start_at": _assume_stored_utc(event.start_at),
        "end_at": _assume_stored_utc(event.end_at),
        "timezone": event.timezone,
        "document_id": event.document_id,
    }
    values.update(payload.model_dump(exclude={"expected_sequence"}, exclude_unset=True))
    try:
        return CalendarEventCreate.model_validate(values).model_dump()
    except ValidationError as exc:
        raise CalendarEventValidationError(exc.errors(include_url=False)) from None


def _local_day_start_as_utc(value: date, timezone_name: str) -> datetime:
    timezone_key = validate_iana_timezone(timezone_name)
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(timezone_key)).astimezone(UTC)


def _assume_stored_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _add_audit_log(
    *,
    db: Session,
    event: CalendarEvent,
    action: str,
    old_value: dict[str, object | None] | None = None,
    new_value: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            calendar_event_id=event.id,
            user_id=event.owner_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
        )
    )


def _event_audit_state(event: CalendarEvent) -> dict[str, object | None]:
    return {
        "status": event.status.value,
        "title": event.title,
        "document_id": event.document_id,
        "sequence": event.sequence,
    }


def _serialize_audit_value(value: Any) -> object | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
