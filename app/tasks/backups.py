import hashlib
from datetime import UTC, datetime

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.backup_job import BackupJob, BackupJobStatus
from app.models.user import User
from app.services.backup_export import build_backup_archive
from app.services.backup_recovery import (
    encrypt_recovery_archive,
    get_recovery_key,
    recovery_key_identifier,
)
from app.services.google_drive import upload_gzip_backup
from app.services.google_drive_oauth import get_google_drive_connection
from app.worker import celery_app


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
