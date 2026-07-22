from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.backup_job import BackupJob
from app.models.calendar_event import CalendarEvent
from app.models.document import Document
from app.models.event_reminder import EventReminder
from app.models.notification import Notification
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.processing_job import ProcessingJob
from app.models.user import User

BACKUP_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class BackupArchive:
    content: bytes
    checksum_sha256: str
    record_counts: dict[str, int]


def build_backup_archive(
    *,
    db: Session,
    owner_id: int,
) -> BackupArchive:
    payload = build_backup_payload(db=db, owner_id=owner_id)
    json_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    ).encode("utf-8")
    compressed = gzip.compress(json_bytes, compresslevel=9, mtime=0)

    return BackupArchive(
        content=compressed,
        checksum_sha256=hashlib.sha256(compressed).hexdigest(),
        record_counts=payload["record_counts"],
    )


def build_backup_payload(
    *,
    db: Session,
    owner_id: int,
) -> dict[str, Any]:
    user = db.get(User, owner_id)
    if user is None:
        raise RuntimeError("Backup owner does not exist")

    documents = list(
        db.scalars(
            select(Document)
            .where(
                Document.owner_id == owner_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.id)
        ).all()
    )
    document_ids = [document.id for document in documents]

    processing_jobs = _load_by_document_ids(
        db=db,
        model=ProcessingJob,
        document_ids=document_ids,
    )
    usage_logs = _load_by_document_ids(
        db=db,
        model=OpenAIUsageLog,
        document_ids=document_ids,
    )
    audit_logs = _load_by_document_ids(
        db=db,
        model=AuditLog,
        document_ids=document_ids,
    )
    calendar_events = list(
        db.scalars(
            select(CalendarEvent)
            .where(
                CalendarEvent.owner_id == owner_id,
                CalendarEvent.deleted_at.is_(None),
            )
            .order_by(CalendarEvent.id)
        ).all()
    )
    event_ids = [event.id for event in calendar_events]
    event_reminders = _load_by_event_ids(
        db=db,
        model=EventReminder,
        event_ids=event_ids,
    )
    notifications = list(
        db.scalars(
            select(Notification)
            .where(Notification.owner_id == owner_id)
            .order_by(Notification.id)
        ).all()
    )
    backup_jobs = list(
        db.scalars(
            select(BackupJob)
            .where(BackupJob.owner_id == owner_id)
            .order_by(BackupJob.id)
        ).all()
    )

    records = {
        "users": [_serialize_user(user)],
        "documents": [_serialize_document(document) for document in documents],
        "processing_jobs": [
            _serialize_processing_job(job) for job in processing_jobs
        ],
        "openai_usage_logs": [_serialize_usage_log(log) for log in usage_logs],
        "audit_logs": [_serialize_audit_log(log) for log in audit_logs],
        "backup_jobs": [_serialize_backup_job(job) for job in backup_jobs],
        "calendar_events": [_serialize_calendar_event(event) for event in calendar_events],
        "event_reminders": [_serialize_event_reminder(reminder) for reminder in event_reminders],
        "notifications": [_serialize_notification(notification) for notification in notifications],
    }
    record_counts = {name: len(items) for name, items in records.items()}

    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC),
        "owner_id": owner_id,
        "record_counts": record_counts,
        "records": records,
    }


def _load_by_document_ids(
    *,
    db: Session,
    model: type[Any],
    document_ids: list[int],
) -> list[Any]:
    if not document_ids:
        return []

    return list(
        db.scalars(
            select(model)
            .where(model.document_id.in_(document_ids))
            .order_by(model.id)
        ).all()
    )


def _load_by_event_ids(
    *,
    db: Session,
    model: type[Any],
    event_ids: list[int],
) -> list[Any]:
    if not event_ids:
        return []
    return list(
        db.scalars(select(model).where(model.event_id.in_(event_ids)).order_by(model.id)).all()
    )


def _serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


def _serialize_document(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "owner_id": document.owner_id,
        "original_filename": document.original_filename,
        "status": document.status,
        "processing_mode": document.processing_mode,
        "content_type": document.content_type,
        "file_size_bytes": document.file_size_bytes,
        "checksum_sha256": document.checksum_sha256,
        "storage_key": document.storage_key,
        "raw_text": document.raw_text,
        "document_type": document.document_type,
        "ai_extracted_data": document.ai_extracted_data,
        "summary": document.summary,
        "user_note": document.user_note,
        "amount": document.amount,
        "currency": document.currency,
        "deadline": document.deadline,
        "document_date": document.document_date,
        "sender": document.sender,
        "confidence_score": document.confidence_score,
        "validation_status": document.validation_status,
        "validation_errors": document.validation_errors,
        "validation_warnings": document.validation_warnings,
        "validation_score": document.validation_score,
        "validation_evidence": document.validation_evidence,
        "validation_candidates": document.validation_candidates,
        "validation_flags": document.validation_flags,
        "ocr_quality_score": document.ocr_quality_score,
        "fallback_extraction": document.fallback_extraction,
        "ai_extraction_model": document.ai_extraction_model,
        "manual_corrections": document.manual_corrections,
        "manually_corrected_at": document.manually_corrected_at,
        "extraction_status": document.extraction_status,
        "extraction_confirmed_at": document.extraction_confirmed_at,
        "ai_extraction_completed_at": document.ai_extraction_completed_at,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def _serialize_processing_job(job: ProcessingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "document_id": job.document_id,
        "operation_type": job.operation_type,
        "status": job.status,
        "attempts": job.attempts,
        "max_retries": job.max_retries,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _serialize_usage_log(log: OpenAIUsageLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "document_id": log.document_id,
        "operation": log.operation,
        "model": log.model,
        "response_id": log.response_id,
        "input_tokens": log.input_tokens,
        "output_tokens": log.output_tokens,
        "total_tokens": log.total_tokens,
        "created_at": log.created_at,
    }


def _serialize_audit_log(log: AuditLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "document_id": log.document_id,
        "calendar_event_id": log.calendar_event_id,
        "user_id": log.user_id,
        "action": log.action,
        "field_name": log.field_name,
        "old_value": log.old_value,
        "new_value": log.new_value,
        "created_at": log.created_at,
    }


def _serialize_calendar_event(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "document_id": event.document_id,
        "title": event.title,
        "description": event.description,
        "event_type": event.event_type,
        "status": event.status,
        "source": event.source,
        "all_day": event.all_day,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "start_at": event.start_at,
        "end_at": event.end_at,
        "timezone": event.timezone,
        "source_field": event.source_field,
        "source_key": event.source_key,
        "source_evidence": event.source_evidence,
        "confidence_score": event.confidence_score,
        "requires_review": event.requires_review,
        "detached_from_source": event.detached_from_source,
        "completed_at": event.completed_at,
        "ical_uid": event.ical_uid,
        "sequence": event.sequence,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _serialize_event_reminder(reminder: EventReminder) -> dict[str, Any]:
    return {
        "id": reminder.id,
        "event_id": reminder.event_id,
        "channel": reminder.channel,
        "offset_minutes": reminder.offset_minutes,
        "scheduled_for": reminder.scheduled_for,
        "status": reminder.status,
        "last_attempt_at": reminder.last_attempt_at,
        "sent_at": reminder.sent_at,
        "attempts": reminder.attempts,
        "error_message": reminder.error_message,
        "created_at": reminder.created_at,
        "updated_at": reminder.updated_at,
    }


def _serialize_notification(notification: Notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "event_id": notification.event_id,
        "title": notification.title,
        "body": notification.body,
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }


def _serialize_backup_job(job: BackupJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "owner_id": job.owner_id,
        "status": job.status,
        "drive_folder_id": job.drive_folder_id,
        "drive_file_id": job.drive_file_id,
        "drive_file_name": job.drive_file_name,
        "content_type": job.content_type,
        "compressed_size_bytes": job.compressed_size_bytes,
        "checksum_sha256": job.checksum_sha256,
        "recovery_key_id": job.recovery_key_id,
        "record_counts": job.record_counts,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
