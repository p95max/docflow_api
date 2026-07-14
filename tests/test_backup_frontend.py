import gzip

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web_backups as web_backups
import app.services.backup_recovery as backup_recovery
from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.user import User
from app.services.backup_export import build_backup_archive
from app.services.backup_recovery import encrypt_recovery_archive, generate_recovery_key
from app.services.users import create_user


def _login(client: TestClient, user: User) -> None:
    response = client.post(
        "/login",
        data={
            "email": user.email,
            "password": "strong-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_backup_page_redirects_anonymous_user(client: TestClient) -> None:
    response = client.get("/backups", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_backup_page_prompts_user_to_connect_google_drive(
    client: TestClient,
    test_user: User,
) -> None:
    _login(client, test_user)

    response = client.get("/backups")

    assert response.status_code == 200
    assert "Google Drive recovery backups" in response.text
    assert "Connect Google Drive" in response.text
    assert '<form method="post" action="/backups/run">' not in response.text


def test_backup_page_renders_and_creates_job(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(client, test_user)
    db_session.add(
        GoogleDriveConnection(
            user_id=test_user.id,
            refresh_token="frontend-refresh-token",
            scope="https://www.googleapis.com/auth/drive.file",
        )
    )
    db_session.commit()
    generate_recovery_key(db=db_session, user=test_user)

    def fake_enqueue_backup_job(*, db: Session, job: BackupJob) -> BackupJob:
        job.celery_task_id = "frontend-backup-task-id"
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    monkeypatch.setattr(
        web_backups,
        "enqueue_backup_job",
        fake_enqueue_backup_job,
    )

    page_response = client.get("/backups")
    assert page_response.status_code == 200
    assert "Google Drive recovery backups" in page_response.text
    assert '<form method="post" action="/backups/run">' in page_response.text
    assert "Disconnect Google Drive" in page_response.text

    create_response = client.post("/backups/run", follow_redirects=False)
    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/backups?created=1"

    history_response = client.get("/backups?created=1")
    assert history_response.status_code == 200
    assert "Backup job created" in history_response.text
    assert "pending" in history_response.text


def test_recovery_key_is_shown_only_when_generated(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    _login(client, test_user)

    response = client.post("/backups/recovery-key")

    assert response.status_code == 200
    assert "Save your Recovery Key now" in response.text
    assert "Copy Recovery Key" in response.text
    assert "password manager as a secure note" in response.text
    db_session.refresh(test_user)
    assert test_user.backup_recovery_key_encrypted is not None

    page_response = client.get("/backups")
    assert "Save your Recovery Key now" not in page_response.text
    assert "A Recovery Key is configured" in page_response.text
    assert 'action="/backups/recovery-key/reset"' in page_response.text


def test_restore_recovery_backup_restores_text_and_queues_indexing(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_user = create_user(
        db=db_session,
        email="backup-source@example.com",
        password="strong-password",
    )
    source_document = Document(
        owner_id=source_user.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        checksum_sha256="b" * 64,
        raw_text="Invoice 161126 is due in 30 days.",
        document_type="invoice",
        ai_extracted_data={"invoice_number": "161126"},
    )
    db_session.add(source_document)
    db_session.commit()
    archive = build_backup_archive(db=db_session, owner_id=source_user.id)
    recovery_key = Fernet.generate_key().decode("utf-8")
    encrypted_archive = encrypt_recovery_archive(
        content=archive.content,
        recovery_key=recovery_key,
    )
    queued_document_ids: list[int] = []
    monkeypatch.setattr(
        backup_recovery,
        "enqueue_document_index_job",
        lambda *, db, document: queued_document_ids.append(document.id),
    )
    _login(client, test_user)

    response = client.post(
        "/backups/restore",
        data={"recovery_key": recovery_key},
        files={
            "backup_file": (
                "docsflow-recovery.json.gz.enc",
                encrypted_archive,
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    assert "Restored 1 document(s); skipped 0 duplicate(s)." in response.text
    restored = db_session.query(Document).filter_by(owner_id=test_user.id).one()
    assert restored.original_filename == "invoice.pdf"
    assert restored.storage_key is None
    assert restored.raw_text == "Invoice 161126 is due in 30 days."
    assert restored.ai_extracted_data == {"invoice_number": "161126"}
    assert queued_document_ids == [restored.id]
    db_session.refresh(test_user)
    assert test_user.backup_recovery_key_encrypted is not None


def test_failed_backup_can_be_deleted_from_history(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    job = BackupJob(owner_id=test_user.id, status=BackupJobStatus.failed)
    db_session.add(job)
    db_session.commit()
    _login(client, test_user)

    page_response = client.get("/backups")

    assert f'action="/backups/{job.id}/delete"' in page_response.text

    response = client.post(f"/backups/{job.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/backups?deleted=1"
    assert db_session.get(BackupJob, job.id) is None


def test_backup_record_can_be_deleted_when_google_drive_is_disconnected(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    job = BackupJob(
        owner_id=test_user.id,
        status=BackupJobStatus.completed,
        drive_file_id="drive-file-id",
    )
    db_session.add(job)
    db_session.commit()
    _login(client, test_user)

    response = client.post(f"/backups/{job.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/backups?deleted=1&drive_file_retained=1"
    assert db_session.get(BackupJob, job.id) is None


def test_backup_status_endpoint_returns_only_the_owner_job(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    job = BackupJob(owner_id=test_user.id, status=BackupJobStatus.running)
    db_session.add(job)
    db_session.commit()
    _login(client, test_user)

    response = client.get(f"/backups/{job.id}/status")

    assert response.status_code == 200
    assert response.json()["id"] == job.id
    assert response.json()["status"] == "running"
    assert response.headers["cache-control"] == "no-store"


def test_completed_backup_downloads_uncompressed_json(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackupJob(
        owner_id=test_user.id,
        status=BackupJobStatus.completed,
        drive_file_id="drive-file-id",
        drive_file_name="backup.json.gz",
    )
    connection = GoogleDriveConnection(
        user_id=test_user.id,
        refresh_token="frontend-refresh-token",
        scope="https://www.googleapis.com/auth/drive.file",
    )
    db_session.add_all([job, connection])
    db_session.commit()
    _login(client, test_user)
    monkeypatch.setattr(
        web_backups,
        "download_gzip_backup",
        lambda **_: gzip.compress(b'{"records": {}}'),
    )

    response = client.get(f"/backups/{job.id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.content == b'{"records": {}}'
