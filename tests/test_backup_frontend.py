import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web_backups as web_backups
from app.models.backup_job import BackupJob
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


def test_backup_page_renders_and_creates_job(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(client, test_user)

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

    create_response = client.post("/backups/run", follow_redirects=False)
    assert create_response.status_code == 303
    assert create_response.headers["location"] == "/backups?created=1"

    history_response = client.get("/backups?created=1")
    assert history_response.status_code == 200
    assert "Backup job created" in history_response.text
    assert "pending" in history_response.text
