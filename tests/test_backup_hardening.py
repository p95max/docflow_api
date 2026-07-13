from types import TracebackType

import pytest
from sqlalchemy.orm import Session

import app.services.backup_jobs as backup_jobs_service
import app.tasks.backups as backup_tasks
from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.user import User
from app.services.backup_jobs import enqueue_backup_job
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


def test_enqueue_failure_is_persisted(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackupJob(
        owner_id=test_user.id,
        status=BackupJobStatus.pending,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    def fail_delay(backup_job_id: int):
        assert backup_job_id == job.id
        raise RuntimeError("Redis unavailable")

    monkeypatch.setattr(
        backup_jobs_service.run_backup_task,
        "delay",
        fail_delay,
    )

    with pytest.raises(RuntimeError, match="Redis unavailable"):
        enqueue_backup_job(db=db_session, job=job)

    db_session.refresh(job)
    assert job.status == BackupJobStatus.failed
    assert job.error_message == "Failed to enqueue backup task: Redis unavailable"
    assert job.finished_at is not None


def test_completed_backup_task_is_idempotent(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackupJob(
        owner_id=test_user.id,
        status=BackupJobStatus.completed,
        drive_file_id="existing-file",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    monkeypatch.setattr(
        backup_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )

    def unexpected_upload(*, filename: str, content: bytes):
        raise AssertionError("Completed job must not upload another backup")

    monkeypatch.setattr(
        backup_tasks,
        "upload_gzip_backup",
        unexpected_upload,
    )

    result = run_backup_task.apply(args=(job.id,), throw=True)

    assert result.successful()
    db_session.refresh(job)
    assert job.status == BackupJobStatus.completed
    assert job.drive_file_id == "existing-file"
