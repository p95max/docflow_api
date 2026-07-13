from fastapi import APIRouter, HTTPException, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.schemas.backup import BackupJobRead
from app.services.backup_jobs import (
    create_backup_job,
    enqueue_backup_job,
    get_backup_job,
    list_backup_jobs,
)
from app.services.google_drive_oauth import get_google_drive_connection

router = APIRouter()


@router.post(
    "/run",
    response_model=BackupJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_backup(
    db: DbSession,
    current_user: CurrentUser,
) -> BackupJobRead:
    if get_google_drive_connection(db=db, user_id=current_user.id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google Drive is not connected. Connect it on the Backups page.",
        )

    job = create_backup_job(db=db, owner_id=current_user.id)
    db.commit()
    db.refresh(job)
    return enqueue_backup_job(db=db, job=job)


@router.get("", response_model=list[BackupJobRead])
def get_backup_history(
    db: DbSession,
    current_user: CurrentUser,
) -> list[BackupJobRead]:
    return list_backup_jobs(db=db, owner_id=current_user.id)


@router.get("/{backup_id}", response_model=BackupJobRead)
def get_backup(
    backup_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> BackupJobRead:
    job = get_backup_job(
        db=db,
        backup_id=backup_id,
        owner_id=current_user.id,
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found.",
        )
    return job
