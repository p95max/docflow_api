from fastapi import APIRouter, HTTPException, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.backup_job import BackupJobStatus
from app.schemas.backup import BackupJobRead
from app.services.backup_jobs import (
    create_backup_job,
    delete_backup_job,
    enqueue_backup_job,
    get_backup_job,
    list_backup_jobs,
)
from app.services.google_drive import delete_gzip_backup
from app.services.google_drive_oauth import get_google_drive_connection
from app.services.backup_recovery import get_recovery_key

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
    try:
        get_recovery_key(user=current_user)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

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


@router.delete("/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(
    backup_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
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
    if job.status in (BackupJobStatus.pending, BackupJobStatus.running):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending or running backup cannot be deleted.",
        )

    if job.drive_file_id:
        connection = get_google_drive_connection(db=db, user_id=current_user.id)
        if connection is not None:
            try:
                delete_gzip_backup(
                    file_id=job.drive_file_id,
                    refresh_token=connection.refresh_token,
                )
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc

    delete_backup_job(db=db, backup_id=backup_id, owner_id=current_user.id)
