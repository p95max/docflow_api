import gzip
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document, DocumentStatus
from app.models.calendar_event import CalendarEvent
from app.models.event_reminder import EventReminder, EventReminderStatus
from app.models.notification import Notification
from app.models.user import User
from app.schemas.recovery_backup import (
    RecoveryBackupPayloadV2,
    RecoveryBackupPayloadV3,
    RecoveryDocumentV2,
)
from app.services.document_index_jobs import enqueue_document_index_job
from app.services.reminder_scheduler import event_start_at_utc

@dataclass(frozen=True)
class RestoreResult:
    restored_documents: int
    skipped_documents: int
    schema_version: int = 2
    restored_calendar_events: int = 0
    restored_reminders: int = 0
    restored_notifications: int = 0


def validate_backup_master_key() -> None:
    _master_fernet()


def generate_recovery_key(*, db: Session, user: User) -> str:
    recovery_key = Fernet.generate_key().decode("utf-8")
    save_recovery_key(db=db, user=user, recovery_key=recovery_key)
    return recovery_key


def save_recovery_key(*, db: Session, user: User, recovery_key: str) -> None:
    _recovery_fernet(recovery_key)
    user.backup_recovery_key_encrypted = _master_fernet().encrypt(
        recovery_key.encode("utf-8")
    ).decode("utf-8")
    db.add(user)
    db.commit()


def get_recovery_key(*, user: User) -> str:
    encrypted_key = user.backup_recovery_key_encrypted
    if not encrypted_key:
        raise RuntimeError("Generate a Recovery Key before creating a backup.")
    try:
        return _master_fernet().decrypt(encrypted_key.encode("utf-8")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise RuntimeError("The server cannot unlock this user's Recovery Key.") from exc


def encrypt_recovery_archive(*, content: bytes, recovery_key: str) -> bytes:
    return _recovery_fernet(recovery_key).encrypt(content)


def decrypt_recovery_archive(*, content: bytes, recovery_key: str) -> bytes:
    try:
        return _recovery_fernet(recovery_key).decrypt(content)
    except InvalidToken as exc:
        raise RuntimeError("Recovery Key does not match this backup.") from exc


def recovery_key_identifier(recovery_key: str) -> str:
    _recovery_fernet(recovery_key)
    normalized = recovery_key.strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:16]


def restore_recovery_backup(
    *,
    db: Session,
    owner_id: int,
    encrypted_content: bytes,
    recovery_key: str,
    allow_legacy: bool = False,
) -> RestoreResult:
    """Validate and atomically restore an authenticated recovery archive."""
    if len(encrypted_content) > settings.backup_restore_max_file_size_bytes:
        raise RuntimeError("Recovery backup file exceeds the allowed size.")
    try:
        payload = _validate_recovery_payload(
            _decode_backup_json(
                content=encrypted_content,
                recovery_key=recovery_key,
                allow_legacy=allow_legacy,
            )
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Recovery backup does not match the supported version 2 schema or current version 3 schema."
        ) from exc

    if len(payload.records.documents) > settings.backup_restore_max_documents:
        raise RuntimeError("Recovery backup contains too many document records.")

    restored: list[Document] = []
    document_map: dict[int, Document] = {}
    event_map: dict[int, CalendarEvent] = {}
    restored_events = 0
    restored_reminders = 0
    restored_notifications = 0
    skipped = 0
    try:
        for record in payload.records.documents:
            raw_text = record.raw_text
            if raw_text is not None and (
                len(raw_text) > settings.backup_restore_max_raw_text_chars
            ):
                raise RuntimeError(
                    "Recovery backup contains document text that is too large."
                )
            checksum = record.checksum_sha256
            if checksum and db.scalar(
                select(Document.id).where(
                    Document.owner_id == owner_id,
                    Document.checksum_sha256 == checksum,
                    Document.deleted_at.is_(None),
                )
            ):
                skipped += 1
                document_map[record.id] = db.scalar(
                    select(Document).where(
                        Document.owner_id == owner_id,
                        Document.checksum_sha256 == checksum,
                        Document.deleted_at.is_(None),
                    )
                )
                continue
            document = _document_from_record(owner_id=owner_id, record=record)
            db.add(document)
            db.flush()
            restored.append(document)
            document_map[record.id] = document

        if isinstance(payload, RecoveryBackupPayloadV3):
            event_map, restored_events = _restore_calendar_events(
                db=db,
                owner_id=owner_id,
                records=payload.records.calendar_events,
                document_map=document_map,
            )
            restored_reminders = _restore_event_reminders(
                db=db,
                records=payload.records.event_reminders,
                event_map=event_map,
            )
            restored_notifications = _restore_notifications(
                db=db,
                owner_id=owner_id,
                records=payload.records.notifications,
                event_map=event_map,
            )

        db.commit()
    except RuntimeError:
        db.rollback()
        raise
    except (SQLAlchemyError, TypeError, ValueError, ArithmeticError) as exc:
        db.rollback()
        raise RuntimeError(
            "Recovery backup could not be stored because its data is invalid."
        ) from exc
    for document in restored:
        if document.raw_text:
            enqueue_document_index_job(db=db, document=document)
    return RestoreResult(
        restored_documents=len(restored),
        skipped_documents=skipped,
        schema_version=payload.schema_version,
        restored_calendar_events=restored_events,
        restored_reminders=restored_reminders,
        restored_notifications=restored_notifications,
    )


def _validate_recovery_payload(content: bytes) -> RecoveryBackupPayloadV2 | RecoveryBackupPayloadV3:
    decoded = json.loads(content)
    if not isinstance(decoded, dict):
        raise ValueError("Recovery payload must be an object.")
    if decoded.get("schema_version") == 3:
        return RecoveryBackupPayloadV3.model_validate(decoded)
    if decoded.get("schema_version") == 2:
        return RecoveryBackupPayloadV2.model_validate(decoded)
    raise ValueError("Unsupported recovery schema version.")


def _restore_calendar_events(
    *,
    db: Session,
    owner_id: int,
    records: list[object],
    document_map: dict[int, Document],
) -> tuple[dict[int, CalendarEvent], int]:
    restored = 0
    event_map: dict[int, CalendarEvent] = {}
    for record in records:
        restored_ical_uid = _restored_ical_uid(owner_id=owner_id, source_ical_uid=record.ical_uid)
        existing = db.scalar(
            select(CalendarEvent).where(
                CalendarEvent.owner_id == owner_id,
                CalendarEvent.ical_uid.in_([record.ical_uid, restored_ical_uid]),
            )
        )
        if existing is not None:
            event_map[record.id] = existing
            continue
        event = CalendarEvent(
            owner_id=owner_id,
            document_id=(document_map.get(record.document_id).id if record.document_id in document_map else None),
            title=record.title,
            description=record.description,
            event_type=record.event_type,
            status=record.status,
            source=record.source,
            all_day=record.all_day,
            start_date=record.start_date,
            end_date=record.end_date,
            start_at=record.start_at,
            end_at=record.end_at,
            timezone=record.timezone,
            source_field=record.source_field,
            source_key=record.source_key,
            source_evidence=record.source_evidence,
            confidence_score=record.confidence_score,
            requires_review=record.requires_review,
            detached_from_source=record.detached_from_source,
            completed_at=record.completed_at,
            ical_uid=restored_ical_uid,
            sequence=record.sequence,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        db.add(event)
        db.flush()
        event_map[record.id] = event
        restored += 1
    return event_map, restored


def _restored_ical_uid(*, owner_id: int, source_ical_uid: str) -> str:
    """Avoid the global UID collision with the source account while remaining idempotent."""
    value = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"docsflow-recovery:{owner_id}:{source_ical_uid}",
    )
    return f"{value}@docsflow"


def _restore_event_reminders(*, db: Session, records: list[object], event_map: dict[int, CalendarEvent]) -> int:
    restored = 0
    now = datetime.now(UTC)
    for record in records:
        event = event_map.get(record.event_id)
        if event is None:
            continue
        existing = db.scalar(
            select(EventReminder).where(
                EventReminder.event_id == event.id,
                EventReminder.channel == record.channel,
                EventReminder.offset_minutes == record.offset_minutes,
            )
        )
        if existing is not None:
            continue
        status = record.status
        scheduled_for = record.scheduled_for
        attempts = record.attempts
        error_message = record.error_message
        if status in {EventReminderStatus.pending, EventReminderStatus.sending}:
            scheduled_for = event_start_at_utc(event=event) - timedelta(minutes=record.offset_minutes)
            attempts = 0
            if scheduled_for <= now:
                status = EventReminderStatus.cancelled
                error_message = "Cancelled during recovery to prevent overdue delivery."
            else:
                status = EventReminderStatus.pending
                error_message = None
        db.add(
            EventReminder(
                event_id=event.id,
                channel=record.channel,
                offset_minutes=record.offset_minutes,
                scheduled_for=scheduled_for,
                status=status,
                last_attempt_at=record.last_attempt_at if status not in {EventReminderStatus.pending, EventReminderStatus.cancelled} else None,
                sent_at=record.sent_at,
                attempts=attempts,
                error_message=error_message,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        restored += 1
    return restored


def _restore_notifications(*, db: Session, owner_id: int, records: list[object], event_map: dict[int, CalendarEvent]) -> int:
    restored = 0
    for record in records:
        event = event_map.get(record.event_id) if record.event_id else None
        existing = db.scalar(
            select(Notification.id).where(
                Notification.owner_id == owner_id,
                Notification.title == record.title,
                Notification.body == record.body,
                Notification.created_at == record.created_at,
            )
        )
        if existing is not None:
            continue
        db.add(
            Notification(
                owner_id=owner_id,
                event_id=event.id if event else None,
                title=record.title,
                body=record.body,
                read_at=record.read_at,
                created_at=record.created_at,
            )
        )
        restored += 1
    return restored


def _decode_backup_json(
    *,
    content: bytes,
    recovery_key: str,
    allow_legacy: bool,
) -> bytes:
    if not content:
        raise RuntimeError("Recovery backup file is empty.")

    stripped = content.lstrip()
    if stripped.startswith((b"{", b"[")):
        if not allow_legacy:
            raise RuntimeError(
                "Unencrypted legacy restore is disabled. Enable migration mode explicitly."
            )
        return content
    if content.startswith(b"\x1f\x8b"):
        if not allow_legacy:
            raise RuntimeError(
                "Unencrypted legacy restore is disabled. Enable migration mode explicitly."
            )
        return _decompress_gzip_limited(content)

    decrypted_content = decrypt_recovery_archive(
        content=content,
        recovery_key=recovery_key,
    )
    return _decompress_gzip_limited(decrypted_content)


def _decompress_gzip_limited(content: bytes) -> bytes:
    max_size = settings.backup_restore_max_decompressed_size_bytes
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as archive:
            decompressed = archive.read(max_size + 1)
    except OSError as exc:
        raise RuntimeError("Recovery backup is invalid or corrupted.") from exc
    if len(decompressed) > max_size:
        raise RuntimeError("Recovery backup expands beyond the allowed size.")
    return decompressed


def _document_from_record(*, owner_id: int, record: RecoveryDocumentV2) -> Document:
    raw_text = record.raw_text or None
    return Document(
        owner_id=owner_id,
        original_filename=record.original_filename,
        status=DocumentStatus.completed if raw_text else DocumentStatus.failed,
        processing_mode=record.processing_mode,
        content_type=record.content_type,
        file_size_bytes=None,
        checksum_sha256=record.checksum_sha256,
        storage_key=None,
        raw_text=raw_text,
        document_type=record.document_type,
        ai_extracted_data=record.ai_extracted_data,
        summary=record.summary,
        user_note=record.user_note,
        amount=record.amount,
        currency=record.currency,
        deadline=record.deadline,
        document_date=record.document_date,
        sender=record.sender,
        confidence_score=record.confidence_score,
        validation_status=record.validation_status,
        validation_errors=record.validation_errors,
        validation_warnings=record.validation_warnings,
        validation_score=record.validation_score,
        validation_evidence=record.validation_evidence,
        validation_candidates=record.validation_candidates,
        validation_flags=record.validation_flags,
        ocr_quality_score=record.ocr_quality_score,
        fallback_extraction=record.fallback_extraction,
        ai_extraction_model=record.ai_extraction_model,
        manual_corrections=record.manual_corrections,
        extraction_status=record.extraction_status,
    )


def _master_fernet() -> Fernet:
    key = (settings.backup_master_key or "").strip()
    if not key:
        raise RuntimeError("BACKUP_MASTER_KEY is required for recovery backups.")
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as exc:
        raise RuntimeError("BACKUP_MASTER_KEY must be a valid Fernet key.") from exc


def _recovery_fernet(recovery_key: str) -> Fernet:
    try:
        return Fernet(recovery_key.strip().encode("utf-8"))
    except ValueError as exc:
        raise RuntimeError("Recovery Key must be a valid Fernet key.") from exc
