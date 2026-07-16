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
from app.models.document import Document
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.processing_job import ProcessingJob
from app.models.user import User

BACKUP_SCHEMA_VERSION = 2


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
        "user_id": log.user_id,
        "action": log.action,
        "field_name": log.field_name,
        "old_value": log.old_value,
        "new_value": log.new_value,
        "created_at": log.created_at,
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
