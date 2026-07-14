import gzip

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

import app.services.backup_recovery as backup_recovery
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.services.backup_export import build_backup_archive
from app.services.backup_recovery import restore_recovery_backup
from app.services.users import create_user


def _backup_archive(*, db: Session, email: str) -> bytes:
    source_user = create_user(
        db=db,
        email=email,
        password="strong-password",
    )
    db.add(
        Document(
            owner_id=source_user.id,
            original_filename="restorable-invoice.pdf",
            status=DocumentStatus.completed,
            processing_mode=ProcessingMode.standard,
            content_type="application/pdf",
            checksum_sha256="c" * 64,
            raw_text="Restorable invoice text.",
            document_type="invoice",
        )
    )
    db.commit()
    return build_backup_archive(db=db, owner_id=source_user.id).content


def _disable_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backup_recovery,
        "enqueue_document_index_job",
        lambda **_: None,
    )


def test_restore_accepts_plain_json_from_previous_download_endpoint(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _backup_archive(
        db=db_session,
        email="plain-json-backup-source@example.com",
    )
    plain_json = gzip.decompress(archive)
    _disable_indexing(monkeypatch)

    result = restore_recovery_backup(
        db=db_session,
        owner_id=test_user.id,
        encrypted_content=plain_json,
        recovery_key=Fernet.generate_key().decode("utf-8"),
    )

    assert result.restored_documents == 1
    assert result.skipped_documents == 0
    restored = db_session.query(Document).filter_by(owner_id=test_user.id).one()
    assert restored.original_filename == "restorable-invoice.pdf"
    assert restored.raw_text == "Restorable invoice text."


def test_restore_accepts_legacy_unencrypted_gzip_archive(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _backup_archive(
        db=db_session,
        email="gzip-backup-source@example.com",
    )
    _disable_indexing(monkeypatch)

    result = restore_recovery_backup(
        db=db_session,
        owner_id=test_user.id,
        encrypted_content=archive,
        recovery_key=Fernet.generate_key().decode("utf-8"),
    )

    assert result.restored_documents == 1
    assert result.skipped_documents == 0


def test_restore_rejects_empty_backup_file(
    db_session: Session,
    test_user: User,
) -> None:
    with pytest.raises(RuntimeError, match="file is empty"):
        restore_recovery_backup(
            db=db_session,
            owner_id=test_user.id,
            encrypted_content=b"",
            recovery_key=Fernet.generate_key().decode("utf-8"),
        )


def test_restore_rejects_json_with_invalid_top_level_shape(
    db_session: Session,
    test_user: User,
) -> None:
    with pytest.raises(RuntimeError, match="not a supported recovery backup"):
        restore_recovery_backup(
            db=db_session,
            owner_id=test_user.id,
            encrypted_content=b"[]",
            recovery_key=Fernet.generate_key().decode("utf-8"),
        )
