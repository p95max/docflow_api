import gzip
import hashlib
import io
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.schemas.recovery_backup import RecoveryBackupPayloadV2, RecoveryDocumentV2
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
        payload = RecoveryBackupPayloadV2.model_validate_json(
            _decode_backup_json(
                content=encrypted_content,
                recovery_key=recovery_key,
                allow_legacy=allow_legacy,
            )
        )
    except ValidationError as exc:
        raise RuntimeError(
            "Recovery backup does not match the supported version 2 schema."
        ) from exc

    if len(payload.records.documents) > settings.backup_restore_max_documents:
        raise RuntimeError("Recovery backup contains too many document records.")

    restored: list[Document] = []
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
                continue
            document = _document_from_record(owner_id=owner_id, record=record)
            db.add(document)
            restored.append(document)

        db.commit()
    except RuntimeError:
        db.rollback()
        raise
    except (SQLAlchemyError, TypeError, ValueError, ArithmeticError) as exc:
        db.rollback()
        raise RuntimeError(
            "Recovery backup could not be stored because its document data is invalid."
        ) from exc
    for document in restored:
        if document.raw_text:
            enqueue_document_index_job(db=db, document=document)
    return RestoreResult(restored_documents=len(restored), skipped_documents=skipped)


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
