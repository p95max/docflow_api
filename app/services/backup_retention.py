"""Retention of completed recovery archives without deleting records blindly."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.backup_job import BackupJob, BackupJobStatus


def prune_completed_backups(
    *,
    db: Session,
    owner_id: int,
    keep: int,
    delete_remote_file: Callable[[str], None],
) -> tuple[list[int], list[str]]:
    """Keep the newest completed archives and remove older remote/local pairs.

    A database record is deleted only after its Drive archive is deleted (or
    when it has no Drive file at all), so a transient Drive failure never loses
    the only reference to a recovery file.
    """
    completed_jobs = list(
        db.scalars(
            select(BackupJob)
            .where(
                BackupJob.owner_id == owner_id,
                BackupJob.status == BackupJobStatus.completed,
            )
            .order_by(BackupJob.created_at.desc(), BackupJob.id.desc())
        ).all()
    )
    stale_jobs = completed_jobs[keep:]
    deleted_ids: list[int] = []
    errors: list[str] = []

    for stale_job in stale_jobs:
        if stale_job.drive_file_id:
            try:
                delete_remote_file(stale_job.drive_file_id)
            except Exception as exc:
                errors.append(f"Backup {stale_job.id}: {exc}")
                continue

        deleted_ids.append(stale_job.id)
        db.delete(stale_job)

    return deleted_ids, errors
