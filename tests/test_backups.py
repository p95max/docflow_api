import gzip
import json
from types import TracebackType

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.api.v1.routes_backups as routes_backups
import app.tasks.backups as backup_tasks
from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.knowledge_conversation import KnowledgeConversation
from app.models.knowledge_message import KnowledgeMessage, KnowledgeMessageRole
from app.models.user import User
from app.services.backup_export import build_backup_archive
from app.services.google_drive import DriveUploadResult
from app.services.users import create_user
from app.tasks.backups import run_backup_task


class SessionLocalOverride:
    def __init__(self, db_session: Session) -> None:
        self.db_session = db_session

    def __enter__(self) -> Session:
        return self.db_session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


def _connect_drive(db: Session, user: User) -> GoogleDriveConnection:
    connection = GoogleDriveConnection(
        user_id=user.id,
        refresh_token="stored-refresh-token",
        scope="https://www.googleapis.com/auth/drive.file",
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def test_backup_archive_excludes_sensitive_credentials_and_document_text(
    db_session: Session,
    test_user: User,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        file_size_bytes=1234,
        checksum_sha256="a" * 64,
        storage_key="1/invoice.pdf",
        raw_text="Sensitive invoice text that must not leave the database.",
        ai_extracted_data={"document_type": "invoice", "amount": 49.99},
    )
    db_session.add(document)
    db_session.flush()
    conversation = KnowledgeConversation(
        owner_id=test_user.id,
        title="Sensitive knowledge conversation",
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        KnowledgeMessage(
            conversation_id=conversation.id,
            role=KnowledgeMessageRole.user,
            content="Sensitive conversation content that must not leave the database.",
        )
    )
    db_session.commit()

    archive = build_backup_archive(db=db_session, owner_id=test_user.id)
    payload = json.loads(gzip.decompress(archive.content))
    serialized = json.dumps(payload)

    assert payload["schema_version"] == 1
    assert payload["records"]["users"][0]["email"] == test_user.email
    assert payload["records"]["documents"][0]["storage_key"] == "1/invoice.pdf"
    document_payload = payload["records"]["documents"][0]

    assert "raw_text" not in document_payload
    assert "password_hash" not in serialized
    assert "refresh_token" not in serialized
    assert test_user.password_hash not in serialized
    assert "Sensitive invoice text that must not leave the database." not in serialized
    assert "knowledge_conversations" not in payload["records"]
    assert "Sensitive conversation content that must not leave the database." not in serialized
    assert archive.checksum_sha256
    assert archive.record_counts["documents"] == 1


def test_run_backup_endpoint_requires_google_drive_connection(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post("/api/v1/backups/run", headers=auth_headers)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "Google Drive is not connected" in response.json()["detail"]


def test_run_backup_endpoint_creates_pending_job(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect_drive(db_session, test_user)

    def fake_enqueue_backup_job(*, db: Session, job: BackupJob) -> BackupJob:
        job.celery_task_id = "fake-backup-task-id"
        db.commit()
        db.refresh(job)
        return job

    monkeypatch.setattr(
        routes_backups,
        "enqueue_backup_job",
        fake_enqueue_backup_job,
    )

    response = client.post("/api/v1/backups/run", headers=auth_headers)

    assert response.status_code == status.HTTP_202_ACCEPTED
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["celery_task_id"] == "fake-backup-task-id"

    job = db_session.get(BackupJob, payload["id"])
    assert job is not None
    assert job.status == BackupJobStatus.pending


def test_backup_history_is_isolated_per_user(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    other_user = create_user(
        db=db_session,
        email="other@example.com",
        password="strong-password",
    )
    own_job = BackupJob(owner_id=test_user.id)
    other_job = BackupJob(owner_id=other_user.id)
    db_session.add_all([own_job, other_job])
    db_session.commit()

    response = client.get("/api/v1/backups", headers=auth_headers)

    assert response.status_code == status.HTTP_200_OK
    assert [item["id"] for item in response.json()] == [own_job.id]

    response = client.get(
        f"/api/v1/backups/{other_job.id}",
        headers=auth_headers,
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_run_backup_task_uploads_gzip_and_completes_job(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect_drive(db_session, test_user)
    job = BackupJob(owner_id=test_user.id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    monkeypatch.setattr(
        backup_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )

    def fake_upload_gzip_backup(
        *,
        filename: str,
        content: bytes,
        refresh_token: str,
    ) -> DriveUploadResult:
        assert refresh_token == "stored-refresh-token"
        assert filename.endswith(".json.gz")
        payload = json.loads(gzip.decompress(content))
        assert payload["owner_id"] == test_user.id
        return DriveUploadResult(
            folder_id="folder-1",
            file_id="file-1",
            file_name=filename,
            web_view_link="https://drive.google.com/file/d/file-1/view",
        )

    monkeypatch.setattr(
        backup_tasks,
        "upload_gzip_backup",
        fake_upload_gzip_backup,
    )

    result = run_backup_task.apply(args=(job.id,), throw=True)

    assert result.successful()
    db_session.refresh(job)
    assert job.status == BackupJobStatus.completed
    assert job.drive_folder_id == "folder-1"
    assert job.drive_file_id == "file-1"
    assert job.drive_file_name is not None
    assert job.compressed_size_bytes is not None
    assert job.compressed_size_bytes > 0
    assert job.checksum_sha256 is not None
    assert job.record_counts is not None
    assert job.record_counts["users"] == 1
    assert job.finished_at is not None


def test_run_backup_task_marks_job_failed_without_connection(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackupJob(owner_id=test_user.id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    monkeypatch.setattr(
        backup_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )

    result = run_backup_task.apply(args=(job.id,), throw=False)

    assert result.failed()
    db_session.refresh(job)
    assert job.status == BackupJobStatus.failed
    assert "Google Drive is not connected" in (job.error_message or "")
    assert job.finished_at is not None


def test_run_backup_task_marks_job_failed_when_drive_upload_fails(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connect_drive(db_session, test_user)
    job = BackupJob(owner_id=test_user.id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    monkeypatch.setattr(
        backup_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )

    def fail_upload(
        *,
        filename: str,
        content: bytes,
        refresh_token: str,
    ) -> None:
        raise RuntimeError("Drive unavailable")

    monkeypatch.setattr(backup_tasks, "upload_gzip_backup", fail_upload)

    result = run_backup_task.apply(args=(job.id,), throw=False)

    assert result.failed()
    db_session.refresh(job)
    assert job.status == BackupJobStatus.failed
    assert job.error_message == "Drive unavailable"
    assert job.finished_at is not None
