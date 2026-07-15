import gzip
import io
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import (
    Document,
    DocumentStatus,
    ExtractionStatus,
    ProcessingMode,
)
from app.models.user import User
from app.services.document_index_jobs import enqueue_document_index_job


@dataclass(frozen=True)
class RestoreResult:
    restored_documents: int
    skipped_documents: int


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


def restore_recovery_backup(
    *,
    db: Session,
    owner_id: int,
    encrypted_content: bytes,
    recovery_key: str,
) -> RestoreResult:
    """Restore encrypted archives and previously downloaded JSON export formats."""
    if len(encrypted_content) > settings.backup_restore_max_file_size_bytes:
        raise RuntimeError("Recovery backup file exceeds the allowed size.")
    try:
        payload = json.loads(
            _decode_backup_json(
                content=encrypted_content,
                recovery_key=recovery_key,
            )
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Recovery backup is invalid or corrupted.") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("This file is not a supported recovery backup.")
    if payload.get("schema_version") != 2:
        raise RuntimeError("This file is not a supported recovery backup.")
    records = payload.get("records")
    if not isinstance(records, dict) or not isinstance(records.get("documents"), list):
        raise RuntimeError("Recovery backup does not contain document records.")
    if len(records["documents"]) > settings.backup_restore_max_documents:
        raise RuntimeError("Recovery backup contains too many document records.")

    restored: list[Document] = []
    skipped = 0
    try:
        for record in records["documents"]:
            if not isinstance(record, dict):
                continue
            raw_text = record.get("raw_text")
            if isinstance(raw_text, str) and (
                len(raw_text) > settings.backup_restore_max_raw_text_chars
            ):
                raise RuntimeError("Recovery backup contains document text that is too large.")
            checksum = _text_or_none(record.get("checksum_sha256"))
            if checksum and db.scalar(
                select(Document.id).where(
                    Document.owner_id == owner_id,
                    Document.checksum_sha256 == checksum,
                    Document.deleted_at.is_(None),
                )
            ):
                skipped += 1
                continue
            document = _document_from_record(owner_id=owner_id, record=record)
            db.add(document)
            restored.append(document)

        db.commit()
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise RuntimeError("Recovery backup contains invalid document data.") from exc
    for document in restored:
        if document.raw_text:
            enqueue_document_index_job(db=db, document=document)
    return RestoreResult(restored_documents=len(restored), skipped_documents=skipped)


def _decode_backup_json(*, content: bytes, recovery_key: str) -> bytes:
    if not content:
        raise RuntimeError("Recovery backup file is empty.")

    stripped = content.lstrip()
    if stripped.startswith((b"{", b"[")):
        # Compatibility with JSON files downloaded by older DocsFlow versions.
        return content
    if content.startswith(b"\x1f\x8b"):
        # Compatibility with unencrypted .json.gz backups.
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


def _document_from_record(*, owner_id: int, record: dict[str, Any]) -> Document:
    raw_text = _text_or_none(record.get("raw_text"))
    processing_mode = ProcessingMode(record.get("processing_mode", "standard"))
    return Document(
        owner_id=owner_id,
        original_filename=_text_or_none(record.get("original_filename")) or "recovered-document",
        status=DocumentStatus.completed if raw_text else DocumentStatus.failed,
        processing_mode=processing_mode,
        content_type=_text_or_none(record.get("content_type")),
        file_size_bytes=None,
        checksum_sha256=_text_or_none(record.get("checksum_sha256")),
        storage_key=None,
        raw_text=raw_text,
        document_type=_text_or_none(record.get("document_type")),
        ai_extracted_data=record.get("ai_extracted_data"),
        summary=_text_or_none(record.get("summary")),
        amount=Decimal(str(record["amount"])) if record.get("amount") is not None else None,
        currency=_text_or_none(record.get("currency")),
        deadline=_parse_date(record.get("deadline")),
        document_date=_parse_date(record.get("document_date")),
        sender=_text_or_none(record.get("sender")),
        confidence_score=record.get("confidence_score"),
        ai_extraction_model=_text_or_none(record.get("ai_extraction_model")),
        manual_corrections=record.get("manual_corrections"),
        extraction_status=ExtractionStatus(record.get("extraction_status", "draft")),
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


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    return date.fromisoformat(value)


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
