"""Project validated temporal document candidates into AI calendar suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.document import Document
from app.schemas.ai_processing import DocumentAIExtraction, TemporalEventExtraction
from app.schemas.calendar_event import CalendarEventCreate
from app.services.calendar_event_validation import (
    TEMPORAL_VALID,
    TEMPORAL_WARNING,
    TemporalEventValidationResult,
    TemporalEventsValidationResult,
)
from app.services.calendar_events import reconcile_ai_event


@dataclass(frozen=True)
class CalendarProjectionResult:
    created: int = 0
    updated: int = 0
    removed: int = 0


def reconcile_document_calendar_events(
    *,
    db: Session,
    document: Document,
) -> CalendarProjectionResult:
    """Synchronize an AI document extraction with its mutable event suggestions.

    Only deterministic temporal validation outcomes are projected.  A user-confirmed
    event or an event manually edited in the calendar is deliberately outside the
    projection's ownership and is never changed or removed.
    """
    extraction, validation = _load_projection_inputs(document)
    if extraction is None or validation is None:
        return CalendarProjectionResult()

    desired_source_keys: set[str] = set()
    created = 0
    updated = 0
    validation_by_index = {item.event_index: item for item in validation.events}

    for index, candidate in enumerate(extraction.temporal_events):
        result = validation_by_index.get(index)
        if result is None or result.status not in {TEMPORAL_VALID, TEMPORAL_WARNING}:
            continue

        candidate, was_manually_corrected = _apply_manual_deadline_correction(
            document=document,
            candidate=candidate,
        )
        payload = _calendar_payload(candidate)
        if payload is None:
            # A timed candidate without an explicit IANA timezone cannot safely be
            # put on a user's calendar.  It remains in the document result instead.
            continue

        source_key = _source_key(document_id=document.id, candidate=candidate)
        desired_source_keys.add(source_key)
        existing_event = db.scalar(
            select(CalendarEvent).where(
                CalendarEvent.owner_id == document.owner_id,
                CalendarEvent.source == CalendarEventSource.ai,
                CalendarEvent.source_key == source_key,
                CalendarEvent.deleted_at.is_(None),
            )
        )
        existing_state = _projection_state(existing_event) if existing_event else None
        event = reconcile_ai_event(
            db=db,
            owner_id=document.owner_id,
            document_id=document.id,
            source_key=source_key,
            payload=payload,
            source_field=candidate.source_field,
            source_evidence=_source_evidence(
                candidate=candidate,
                validation=result,
                manually_corrected=was_manually_corrected,
            ),
            confidence_score=candidate.confidence_score,
            requires_review=result.status == TEMPORAL_WARNING,
        )
        if existing_event is None:
            created += 1
        elif existing_state != _projection_state(event):
            updated += 1

    removed = _remove_obsolete_suggestions(
        db=db,
        document=document,
        desired_source_keys=desired_source_keys,
    )
    return CalendarProjectionResult(created=created, updated=updated, removed=removed)


def _load_projection_inputs(
    document: Document,
) -> tuple[DocumentAIExtraction | None, TemporalEventsValidationResult | None]:
    try:
        extraction = DocumentAIExtraction.model_validate(document.ai_extracted_data)
        temporal_validation = (document.validation_candidates or {}).get(
            "temporal_validation"
        )
        validation = TemporalEventsValidationResult.model_validate(temporal_validation)
    except ValidationError:
        return None, None
    return extraction, validation


def _apply_manual_deadline_correction(
    *,
    document: Document,
    candidate: TemporalEventExtraction,
) -> tuple[TemporalEventExtraction, bool]:
    if (
        candidate.source_field not in {"due_date", "action_deadline"}
        or not candidate.all_day
        or document.deadline is None
        or "deadline" not in (document.manual_corrections or {})
    ):
        return candidate, False
    return candidate.model_copy(update={"date": document.deadline, "datetime": None}), True


def _calendar_payload(candidate: TemporalEventExtraction) -> CalendarEventCreate | None:
    try:
        return CalendarEventCreate(
            title=candidate.title,
            event_type=CalendarEventType(candidate.event_type),
            all_day=candidate.all_day,
            start_date=candidate.date if candidate.all_day else None,
            start_at=candidate.datetime if not candidate.all_day else None,
            timezone=candidate.timezone,
        )
    except ValidationError:
        return None


def _source_key(*, document_id: int, candidate: TemporalEventExtraction) -> str:
    if candidate.all_day:
        assert candidate.date is not None
        occurrence = candidate.date.isoformat()
    else:
        assert candidate.datetime is not None
        occurrence = _stable_datetime(candidate.datetime)
    return f"document:{document_id}:{candidate.event_type}:{occurrence}"


def _stable_datetime(value: datetime) -> str:
    if value.tzinfo is not None:
        return value.astimezone(UTC).isoformat()
    return value.isoformat()


def _source_evidence(
    *,
    candidate: TemporalEventExtraction,
    validation: TemporalEventValidationResult,
    manually_corrected: bool,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "quote": candidate.evidence.quote,
        "page_number": candidate.evidence.page_number,
        "original_phrase": candidate.original_phrase,
        "validation": validation.model_dump(mode="json"),
    }
    if manually_corrected:
        evidence["manual_correction"] = {"field": "deadline", "applied": True}
    return evidence


def _remove_obsolete_suggestions(
    *,
    db: Session,
    document: Document,
    desired_source_keys: set[str],
) -> int:
    obsolete_events = db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.owner_id == document.owner_id,
            CalendarEvent.document_id == document.id,
            CalendarEvent.source == CalendarEventSource.ai,
            CalendarEvent.status == CalendarEventStatus.suggested,
            CalendarEvent.detached_from_source.is_(False),
            CalendarEvent.deleted_at.is_(None),
            CalendarEvent.source_key.not_in(desired_source_keys),
        )
    ).all()
    if not obsolete_events:
        return 0

    removed_at = datetime.now(UTC)
    for event in obsolete_events:
        old_value = _projection_state(event)
        event.deleted_at = removed_at
        event.sequence += 1
        db.add(
            AuditLog(
                calendar_event_id=event.id,
                user_id=document.owner_id,
                action="calendar_event_projection_removed",
                old_value=old_value,
                new_value={"source_key": event.source_key, "reason": "obsolete"},
            )
        )
    db.commit()
    return len(obsolete_events)


def _projection_state(event: CalendarEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "title": event.title,
        "event_type": event.event_type.value,
        "start_date": event.start_date.isoformat() if event.start_date else None,
        "start_at": event.start_at.isoformat() if event.start_at else None,
        "timezone": event.timezone,
        "source_field": event.source_field,
        "source_evidence": event.source_evidence,
        "confidence_score": event.confidence_score,
        "requires_review": event.requires_review,
        "sequence": event.sequence,
    }
