import gzip

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web_backups as web_backups
from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.user import User


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
    assert "Google Drive backups" in response.text
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
    assert "Google Drive backups" in page_response.text
    assert '<form method="post" action="/backups/run">' in page_response.text
    assert "Disconnect Google Drive" in page_response.text

    create_response = client.post("/backups/run", follow_redirects=False)
    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/backups?created=1"

    history_response = client.get("/backups?created=1")
    assert history_response.status_code == 200
    assert "Backup job created" in history_response.text
    assert "pending" in history_response.text


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
