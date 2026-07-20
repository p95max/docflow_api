from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select

from app.api.v1.dependencies import CurrentUser, DbSession
from app.core.timezones import validate_iana_timezone
from app.models.audit_log import AuditLog
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.document import Document
from app.schemas.calendar_event import (
    CalendarEventComplete,
    CalendarEventConfirm,
    CalendarEventCreate,
    CalendarEventListRead,
    CalendarEventRead,
    CalendarEventUpdate,
    CalendarRangeQuery,
)


router = APIRouter()


@router.get("/events", response_model=CalendarEventListRead)
def list_calendar_events(
    db: DbSession,
    current_user: CurrentUser,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    status_filter: CalendarEventStatus | None = Query(default=None, alias="status"),
    event_type: CalendarEventType | None = Query(default=None),
    document_id: int | None = Query(default=None, gt=0),
    source: CalendarEventSource | None = Query(default=None),
) -> CalendarEventListRead:
    try:
        query = CalendarRangeQuery(
            start=start,
            end=end,
            status=status_filter,
            event_type=event_type,
            document_id=document_id,
            source=source,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False),
        ) from None

    conditions = [
        CalendarEvent.owner_id == current_user.id,
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
        start_at = _local_day_start_as_utc(query.start, current_user.timezone)
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
        end_at = _local_day_start_as_utc(query.end + timedelta(days=1), current_user.timezone)
        conditions.append(
            or_(
                and_(CalendarEvent.all_day.is_(True), CalendarEvent.start_date <= query.end),
                and_(CalendarEvent.all_day.is_(False), CalendarEvent.start_at < end_at),
            )
        )

    events = list(
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
    return CalendarEventListRead(
        items=[CalendarEventRead.model_validate(event) for event in events],
        total=len(events),
    )


@router.post(
    "/events",
    response_model=CalendarEventRead,
    status_code=status.HTTP_201_CREATED,
)
def create_calendar_event(
    payload: CalendarEventCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> CalendarEventRead:
    _ensure_owned_document(
        db=db,
        owner_id=current_user.id,
        document_id=payload.document_id,
    )
    event = CalendarEvent(
        owner_id=current_user.id,
        source=CalendarEventSource.user,
        status=CalendarEventStatus.confirmed,
        **payload.model_dump(),
    )
    db.add(event)
    db.flush()
    _add_calendar_audit_log(
        db=db,
        event=event,
        action="calendar_event_created",
        new_value=_event_audit_state(event),
    )
    db.commit()
    db.refresh(event)
    return CalendarEventRead.model_validate(event)


@router.get("/events/{event_id}", response_model=CalendarEventRead)
def get_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> CalendarEventRead:
    return CalendarEventRead.model_validate(
        _get_owned_active_event(db=db, owner_id=current_user.id, event_id=event_id)
    )


@router.patch("/events/{event_id}", response_model=CalendarEventRead)
def update_calendar_event(
    event_id: int,
    payload: CalendarEventUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> CalendarEventRead:
    event = _get_owned_active_event(db=db, owner_id=current_user.id, event_id=event_id)
    event_values = _validate_merged_update(event=event, payload=payload)
    _ensure_owned_document(
        db=db,
        owner_id=current_user.id,
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
    if changes:
        event.sequence += 1
        if event.source == CalendarEventSource.ai:
            event.detached_from_source = True
        _add_calendar_audit_log(
            db=db,
            event=event,
            action="calendar_event_updated",
            new_value={"changes": changes},
        )
        db.commit()
        db.refresh(event)
    return CalendarEventRead.model_validate(event)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    event = _get_owned_active_event(db=db, owner_id=current_user.id, event_id=event_id)
    event.deleted_at = datetime.now(UTC)
    event.sequence += 1
    _add_calendar_audit_log(
        db=db,
        event=event,
        action="calendar_event_deleted",
        old_value=_event_audit_state(event),
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/events/{event_id}/confirm", response_model=CalendarEventRead)
def confirm_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
    _payload: CalendarEventConfirm | None = None,
) -> CalendarEventRead:
    event = _get_owned_active_event(db=db, owner_id=current_user.id, event_id=event_id)
    if event.status == CalendarEventStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is cancelled.")
    if event.status == CalendarEventStatus.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is completed.")
    if event.status != CalendarEventStatus.confirmed:
        old_value = _event_audit_state(event)
        event.status = CalendarEventStatus.confirmed
        event.sequence += 1
        _add_calendar_audit_log(
            db=db,
            event=event,
            action="calendar_event_confirmed",
            old_value=old_value,
            new_value=_event_audit_state(event),
        )
        db.commit()
        db.refresh(event)
    return CalendarEventRead.model_validate(event)


@router.post("/events/{event_id}/complete", response_model=CalendarEventRead)
def complete_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
    _payload: CalendarEventComplete | None = None,
) -> CalendarEventRead:
    event = _get_owned_active_event(db=db, owner_id=current_user.id, event_id=event_id)
    if event.status == CalendarEventStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is cancelled.")
    if event.status != CalendarEventStatus.completed:
        old_value = _event_audit_state(event)
        event.status = CalendarEventStatus.completed
        event.completed_at = datetime.now(UTC)
        event.sequence += 1
        _add_calendar_audit_log(
            db=db,
            event=event,
            action="calendar_event_completed",
            old_value=old_value,
            new_value=_event_audit_state(event),
        )
        db.commit()
        db.refresh(event)
    return CalendarEventRead.model_validate(event)


@router.post("/events/{event_id}/cancel", response_model=CalendarEventRead)
def cancel_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> CalendarEventRead:
    event = _get_owned_active_event(db=db, owner_id=current_user.id, event_id=event_id)
    if event.status == CalendarEventStatus.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is completed.")
    if event.status != CalendarEventStatus.cancelled:
        old_value = _event_audit_state(event)
        event.status = CalendarEventStatus.cancelled
        event.sequence += 1
        _add_calendar_audit_log(
            db=db,
            event=event,
            action="calendar_event_cancelled",
            old_value=old_value,
            new_value=_event_audit_state(event),
        )
        db.commit()
        db.refresh(event)
    return CalendarEventRead.model_validate(event)


def _get_owned_active_event(*, db: DbSession, owner_id: int, event_id: int) -> CalendarEvent:
    event = db.get(CalendarEvent, event_id)
    if event is None or event.owner_id != owner_id or event.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar event not found.")
    return event


def _ensure_owned_document(*, db: DbSession, owner_id: int, document_id: int | None) -> None:
    if document_id is None:
        return
    document = db.get(Document, document_id)
    if (
        document is None
        or document.owner_id != owner_id
        or document.deleted_at is not None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")


def _validate_merged_update(
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
    values.update(payload.model_dump(exclude_unset=True))
    try:
        return CalendarEventCreate.model_validate(values).model_dump()
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False),
        ) from None


def _local_day_start_as_utc(value: date, timezone_name: str) -> datetime:
    timezone_key = validate_iana_timezone(timezone_name)
    return datetime.combine(value, time.min, tzinfo=ZoneInfo(timezone_key)).astimezone(UTC)


def _assume_stored_utc(value: datetime | None) -> datetime | None:
    """SQLite returns timezone-aware columns as naive values in local tests."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _add_calendar_audit_log(
    *,
    db: DbSession,
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
