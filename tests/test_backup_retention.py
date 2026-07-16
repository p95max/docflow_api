from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.user import User
from app.services.backup_retention import prune_completed_backups


def test_backup_retention_deletes_oldest_completed_archives_only(
    db_session: Session,
    test_user: User,
) -> None:
    base_time = datetime.now(UTC) - timedelta(days=6)
    jobs = [
        BackupJob(
            owner_id=test_user.id,
            status=BackupJobStatus.completed,
            drive_file_id=f"drive-{index}",
            created_at=base_time + timedelta(days=index),
        )
        for index in range(6)
    ]
    failed_job = BackupJob(
        owner_id=test_user.id,
        status=BackupJobStatus.failed,
        drive_file_id="failed-drive-file",
        created_at=base_time,
    )
    db_session.add_all([*jobs, failed_job])
    db_session.commit()

    deleted_remote_files: list[str] = []
    deleted_ids, errors = prune_completed_backups(
        db=db_session,
        owner_id=test_user.id,
        keep=5,
        delete_remote_file=deleted_remote_files.append,
    )
    db_session.commit()

    assert errors == []
    assert deleted_ids == [jobs[0].id]
    assert deleted_remote_files == ["drive-0"]
    assert db_session.get(BackupJob, jobs[0].id) is None
    assert db_session.get(BackupJob, failed_job.id) is not None


def test_backup_retention_keeps_record_when_drive_deletion_fails(
    db_session: Session,
    test_user: User,
) -> None:
    base_time = datetime.now(UTC) - timedelta(days=6)
    jobs = [
        BackupJob(
            owner_id=test_user.id,
            status=BackupJobStatus.completed,
            drive_file_id=f"drive-{index}",
            created_at=base_time + timedelta(days=index),
        )
        for index in range(6)
    ]
    db_session.add_all(jobs)
    db_session.commit()

    def fail_delete(_: str) -> None:
        raise RuntimeError("Google Drive is unavailable")

    deleted_ids, errors = prune_completed_backups(
        db=db_session,
        owner_id=test_user.id,
        keep=5,
        delete_remote_file=fail_delete,
    )

    assert deleted_ids == []
    assert len(errors) == 1
    assert db_session.get(BackupJob, jobs[0].id) is not None
