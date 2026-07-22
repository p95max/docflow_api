from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import ValidationError

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.calendar_event import (
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.schemas.calendar_event import (
    CalendarEventComplete,
    CalendarEventConfirm,
    CalendarEventCreate,
    CalendarEventListRead,
    CalendarEventRead,
    CalendarEventUpdate,
    CalendarRangeQuery,
)
from app.services import calendar_events
from app.services.calendar_feeds import get_user_for_calendar_feed_token
from app.services.icalendar import ICAL_CONTENT_TYPE, serialize_event_calendar, serialize_feed_calendar


router = APIRouter()


@router.get("/feed.ics", response_class=Response, response_model=None)
def download_calendar_feed(
    db: DbSession,
    token: str = Query(..., min_length=20, max_length=512),
) -> Response:
    """Private subscription endpoint authenticated only by an opaque feed token."""
    owner = get_user_for_calendar_feed_token(db=db, token=token)
    if owner is None:
        # Do not reveal whether a feed token once existed.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar feed not found.")
    events = calendar_events.list_events(
        db=db,
        owner_id=owner.id,
        user_timezone=owner.timezone,
        query=CalendarRangeQuery(),
    )
    return _calendar_download_response(
        content=serialize_feed_calendar(events),
        filename="docsflow-calendar.ics",
    )


@router.get("/events/{event_id}.ics", response_class=Response, response_model=None)
def download_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    try:
        event = calendar_events.get_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    return _calendar_download_response(
        content=serialize_event_calendar(event),
        filename=f"docsflow-event-{event.id}.ics",
    )


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
        raise _validation_error(exc.errors(include_url=False)) from None

    events = calendar_events.list_events(
        db=db,
        owner_id=current_user.id,
        user_timezone=current_user.timezone,
        query=query,
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
    try:
        event = calendar_events.create_user_event(
            db=db,
            owner_id=current_user.id,
            payload=payload,
        )
    except calendar_events.CalendarDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    return CalendarEventRead.model_validate(event)


@router.get("/events/{event_id}", response_model=CalendarEventRead)
def get_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> CalendarEventRead:
    try:
        event = calendar_events.get_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    return CalendarEventRead.model_validate(event)


@router.patch("/events/{event_id}", response_model=CalendarEventRead)
def update_calendar_event(
    event_id: int,
    payload: CalendarEventUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> CalendarEventRead:
    try:
        event = calendar_events.update_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            payload=payload,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except calendar_events.CalendarDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except calendar_events.CalendarEventValidationError as exc:
        raise _validation_error(exc.errors) from None
    except calendar_events.CalendarEventConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return CalendarEventRead.model_validate(event)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
    expected_sequence: Annotated[int | None, Query(ge=0)] = None,
) -> Response:
    try:
        calendar_events.delete_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=expected_sequence,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except calendar_events.CalendarEventConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/events/{event_id}/confirm", response_model=CalendarEventRead)
def confirm_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
    payload: CalendarEventConfirm | None = None,
) -> CalendarEventRead:
    try:
        event = calendar_events.confirm_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=payload.expected_sequence if payload else None,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except calendar_events.CalendarEventConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return CalendarEventRead.model_validate(event)


@router.post("/events/{event_id}/complete", response_model=CalendarEventRead)
def complete_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
    payload: CalendarEventComplete | None = None,
) -> CalendarEventRead:
    try:
        event = calendar_events.complete_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=payload.expected_sequence if payload else None,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except calendar_events.CalendarEventConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return CalendarEventRead.model_validate(event)


@router.post("/events/{event_id}/cancel", response_model=CalendarEventRead)
def cancel_calendar_event(
    event_id: int,
    db: DbSession,
    current_user: CurrentUser,
    expected_sequence: Annotated[int | None, Query(ge=0)] = None,
) -> CalendarEventRead:
    try:
        event = calendar_events.cancel_event(
            db=db,
            owner_id=current_user.id,
            event_id=event_id,
            expected_sequence=expected_sequence,
        )
    except calendar_events.CalendarEventNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except calendar_events.CalendarEventConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return CalendarEventRead.model_validate(event)


def _validation_error(errors: list[dict[object, object]]) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=errors,
    )


def _calendar_download_response(*, content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=ICAL_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
