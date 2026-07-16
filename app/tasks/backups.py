import hashlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.user import User
from app.services.backup_export import build_backup_archive
from app.services.backup_recovery import (
    encrypt_recovery_archive,
    get_recovery_key,
    recovery_key_identifier,
)
from app.services.backup_retention import prune_completed_backups
from app.services.google_drive import delete_gzip_backup, upload_gzip_backup
from app.services.google_drive_oauth import get_google_drive_connection
from app.worker import celery_app


logger = logging.getLogger(__name__)
BERLIN_TIMEZONE = ZoneInfo("Europe/Berlin")


@celery_app.task(name="backups.schedule_automatic_backups")
def schedule_automatic_backups() -> int:
    """Queue at most one automatic backup per eligible account per Berlin week."""
    if not settings.automatic_backups_enabled:
        return 0
    with SessionLocal() as db:
        week_start = _current_berlin_week_start()
        owner_ids = list(
            db.scalars(
                select(User.id)
                .join(GoogleDriveConnection)
                .where(
                    User.is_active.is_(True),
                    User.backup_recovery_key_encrypted.is_not(None),
                )
            ).all()
        )
        queued = 0
        for owner_id in owner_ids:
            existing_job = db.scalar(
                select(BackupJob.id).where(
                    BackupJob.owner_id == owner_id,
                    BackupJob.is_automatic.is_(True),
                    BackupJob.created_at >= week_start,
                )
            )
            if existing_job is not None:
                continue

            from app.services.backup_jobs import create_backup_job, enqueue_backup_job

            job = create_backup_job(
                db=db,
                owner_id=owner_id,
                is_automatic=True,
            )
            db.commit()
            db.refresh(job)
            try:
                enqueue_backup_job(db=db, job=job)
                queued += 1
            except Exception:
                logger.exception("Could not queue automatic backup for user %s", owner_id)
        return queued


def _current_berlin_week_start() -> datetime:
    now = datetime.now(BERLIN_TIMEZONE)
    return (
        now - timedelta(days=now.weekday())
    ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


@celery_app.task(
    bind=True,
    name="backups.run_backup",
    soft_time_limit=settings.backup_soft_time_limit_seconds,
    time_limit=settings.backup_hard_time_limit_seconds,
)
def run_backup_task(self, backup_job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(BackupJob, backup_job_id)
        if job is None or job.status in {
            BackupJobStatus.running,
            BackupJobStatus.completed,
        }:
            return

        try:
            connection = get_google_drive_connection(
                db=db,
                user_id=job.owner_id,
            )
            if connection is None:
                raise RuntimeError(
                    "Google Drive is not connected. Connect it on the Backups page."
                )
            owner = db.get(User, job.owner_id)
            if owner is None:
                raise RuntimeError("Backup owner does not exist.")
            recovery_key = get_recovery_key(user=owner)

            job.status = BackupJobStatus.running
            job.started_at = job.started_at or datetime.now(UTC)
            job.finished_at = None
            job.error_message = None
            db.commit()

            archive = build_backup_archive(db=db, owner_id=job.owner_id)
            encrypted_content = encrypt_recovery_archive(
                content=archive.content,
                recovery_key=recovery_key,
            )
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            filename = f"docsflow-recovery-user-{job.owner_id}-{timestamp}.json.gz.enc"
            drive_result = upload_gzip_backup(
                filename=filename,
                content=encrypted_content,
                refresh_token=connection.refresh_token,
            )

            db.refresh(job)
            job.status = BackupJobStatus.completed
            job.drive_folder_id = drive_result.folder_id
            job.drive_file_id = drive_result.file_id
            job.drive_file_name = drive_result.file_name
            job.drive_web_view_link = drive_result.web_view_link
            job.content_type = "application/vnd.docsflow.recovery+fernet"
            job.compressed_size_bytes = len(encrypted_content)
            job.checksum_sha256 = hashlib.sha256(encrypted_content).hexdigest()
            job.recovery_key_id = recovery_key_identifier(recovery_key)
            job.record_counts = archive.record_counts
            job.finished_at = datetime.now(UTC)
            job.error_message = None
            db.flush()
            _, retention_errors = prune_completed_backups(
                db=db,
                owner_id=job.owner_id,
                keep=settings.backup_max_retained,
                is_automatic=job.is_automatic,
                delete_remote_file=lambda file_id: delete_gzip_backup(
                    file_id=file_id,
                    refresh_token=connection.refresh_token,
                ),
            )
            for retention_error in retention_errors:
                logger.warning("Recovery backup retention skipped: %s", retention_error)
            db.commit()

        except SoftTimeLimitExceeded:
            _mark_backup_failed(
                db=db,
                backup_job_id=backup_job_id,
                error_message="Backup task soft time limit exceeded.",
            )
            raise
        except Exception as exc:
            _mark_backup_failed(
                db=db,
                backup_job_id=backup_job_id,
                error_message=str(exc),
            )
            raise


def _mark_backup_failed(
    *,
    db: Session,
    backup_job_id: int,
    error_message: str,
) -> None:
    db.rollback()
    job = db.get(BackupJob, backup_job_id)
    if job is None:
        return

    job.status = BackupJobStatus.failed
    job.error_message = error_message[:2000]
    job.finished_at = datetime.now(UTC)
    db.commit()
