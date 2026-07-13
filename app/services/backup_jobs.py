from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.backup_job import BackupJob, BackupJobStatus
from app.tasks.backups import run_backup_task


def create_backup_job(*, db: Session, owner_id: int) -> BackupJob:
    job = BackupJob(
        owner_id=owner_id,
        status=BackupJobStatus.pending,
    )
    db.add(job)
    db.flush()
    return job


def enqueue_backup_job(*, db: Session, job: BackupJob) -> BackupJob:
    async_result = run_backup_task.delay(job.id)
    job.celery_task_id = async_result.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_backup_jobs(*, db: Session, owner_id: int) -> list[BackupJob]:
    stmt = (
        select(BackupJob)
        .where(BackupJob.owner_id == owner_id)
        .order_by(BackupJob.created_at.desc(), BackupJob.id.desc())
    )
    return list(db.scalars(stmt).all())


def get_backup_job(
    *,
    db: Session,
    backup_id: int,
    owner_id: int,
) -> BackupJob | None:
    stmt = select(BackupJob).where(
        BackupJob.id == backup_id,
        BackupJob.owner_id == owner_id,
    )
    return db.scalar(stmt)
