import gzip
import logging
import secrets
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.backup_job import BackupJobStatus
from app.models.user import User
from app.services.backup_jobs import (
    create_backup_job,
    delete_backup_job,
    enqueue_backup_job,
    get_backup_job,
    list_backup_jobs,
)
from app.services.backup_recovery import (
    decrypt_recovery_archive,
    generate_recovery_key,
    get_recovery_key,
    restore_recovery_backup,
    save_recovery_key,
    validate_backup_master_key,
)
from app.services.google_drive import (
    delete_gzip_backup,
    download_gzip_backup,
    ensure_google_drive_backup_folder,
)
from app.services.google_drive_oauth import (
    build_google_drive_authorization_url,
    create_google_drive_oauth_state,
    delete_google_drive_connection,
    exchange_google_drive_authorization_code,
    get_google_drive_connection,
    google_drive_oauth_is_configured,
    missing_google_drive_oauth_settings,
    revoke_google_drive_refresh_token,
    save_google_drive_connection,
    validate_google_drive_oauth_state,
)
from app.web import _get_web_current_user, _redirect_to_login, _template_response

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)
GOOGLE_OAUTH_STATE_COOKIE = "docsflow_google_drive_oauth_state"


def _google_oauth_redirect_uri(request: Request) -> str:
    configured_uri = (settings.google_drive_redirect_uri or "").strip()
    if configured_uri:
        return configured_uri
    return str(request.url_for("google_drive_oauth_callback"))


def _render_backups_page(
    *,
    request: Request,
    db: Session,
    current_user: User,
    error: str | None = None,
    recovery_key_once: str | None = None,
    restore_result: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return _template_response(
        request=request,
        name="backups.html",
        current_user=current_user,
        backup_jobs=list_backup_jobs(db=db, owner_id=current_user.id),
        google_drive_connection=get_google_drive_connection(
            db=db,
            user_id=current_user.id,
        ),
        google_oauth_configured=google_drive_oauth_is_configured(),
        google_oauth_missing_settings=missing_google_drive_oauth_settings(),
        google_drive_folder_name=settings.google_drive_folder_name,
        recovery_key_configured=bool(current_user.backup_recovery_key_encrypted),
        recovery_key_once=recovery_key_once,
        restore_result=restore_result,
        created=request.query_params.get("created") == "1",
        deleted=request.query_params.get("deleted") == "1",
        drive_file_retained=request.query_params.get("drive_file_retained") == "1",
        google_connected=request.query_params.get("google_connected") == "1",
        google_disconnected=request.query_params.get("google_disconnected") == "1",
        error=error,
        status_code=status_code,
    )


@router.get(
    "/backups",
    response_class=HTMLResponse,
    response_model=None,
)
def backups_page(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    return _render_backups_page(
        request=request,
        db=db,
        current_user=current_user,
    )


@router.get(
    "/backups/google/connect",
    response_model=None,
)
def connect_google_drive(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    if not google_drive_oauth_is_configured():
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=(
                "Google OAuth client is not configured. Missing: "
                + ", ".join(missing_google_drive_oauth_settings())
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    nonce = secrets.token_urlsafe(32)
    state_token = create_google_drive_oauth_state(
        user_id=current_user.id,
        nonce=nonce,
    )
    authorization_url = build_google_drive_authorization_url(
        redirect_uri=_google_oauth_redirect_uri(request),
        state=state_token,
        login_hint=current_user.email,
    )

    response = RedirectResponse(
        url=authorization_url,
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        key=GOOGLE_OAUTH_STATE_COOKIE,
        value=nonce,
        max_age=settings.google_oauth_state_expire_minutes * 60,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/backups/google",
    )
    return response


@router.get(
    "/backups/google/callback",
    name="google_drive_oauth_callback",
    response_class=HTMLResponse,
    response_model=None,
)
def google_drive_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    oauth_nonce = request.cookies.get(GOOGLE_OAUTH_STATE_COOKIE)

    if error:
        response = _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=f"Google authorization was not completed: {error}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, path="/backups/google")
        return response

    if not code or not state or not oauth_nonce:
        response = _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Google OAuth callback is missing code, state, or state cookie.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, path="/backups/google")
        return response

    try:
        validate_google_drive_oauth_state(
            state=state,
            expected_user_id=current_user.id,
            expected_nonce=oauth_nonce,
        )
        token_result = exchange_google_drive_authorization_code(
            code=code,
            redirect_uri=_google_oauth_redirect_uri(request),
        )
        connection = save_google_drive_connection(
            db=db,
            user_id=current_user.id,
            refresh_token=token_result.refresh_token,
            scope=token_result.scope,
        )
        ensure_google_drive_backup_folder(
            refresh_token=connection.refresh_token,
        )
    except (jwt.PyJWTError, RuntimeError) as exc:
        response = _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
        response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, path="/backups/google")
        return response

    response = RedirectResponse(
        url="/backups?google_connected=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, path="/backups/google")
    return response


@router.post(
    "/backups/google/disconnect",
    response_model=None,
)
def disconnect_google_drive(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    connection = get_google_drive_connection(db=db, user_id=current_user.id)
    if connection is not None:
        try:
            revoke_google_drive_refresh_token(connection.refresh_token)
        except Exception:
            logger.exception("Could not revoke Google Drive refresh token")
        delete_google_drive_connection(db=db, connection=connection)

    return RedirectResponse(
        url="/backups?google_disconnected=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/backups/run",
    response_class=HTMLResponse,
    response_model=None,
)
def run_backup_submit(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    if get_google_drive_connection(db=db, user_id=current_user.id) is None:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Connect Google Drive before creating a backup.",
            status_code=status.HTTP_409_CONFLICT,
        )

    try:
        get_recovery_key(user=current_user)
    except RuntimeError as exc:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )

    job = create_backup_job(db=db, owner_id=current_user.id)
    db.commit()
    db.refresh(job)

    try:
        enqueue_backup_job(db=db, job=job)
    except Exception as exc:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=f"Backup could not be queued: {exc}",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return RedirectResponse(
        url="/backups?created=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/backups/recovery-key", response_class=HTMLResponse, response_model=None)
def generate_backup_recovery_key(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    if current_user.backup_recovery_key_encrypted:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="A Recovery Key has already been generated for this account.",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        recovery_key = generate_recovery_key(db=db, user=current_user)
    except RuntimeError as exc:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=str(exc),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return _render_backups_page(
        request=request,
        db=db,
        current_user=current_user,
        recovery_key_once=recovery_key,
    )


@router.post("/backups/restore", response_class=HTMLResponse, response_model=None)
def restore_backup_submit(
    request: Request,
    recovery_key: str = Form(...),
    backup_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()
    try:
        validate_backup_master_key()
        result = restore_recovery_backup(
            db=db,
            owner_id=current_user.id,
            encrypted_content=backup_file.file.read(),
            recovery_key=recovery_key,
        )
        if not current_user.backup_recovery_key_encrypted:
            save_recovery_key(
                db=db,
                user=current_user,
                recovery_key=recovery_key,
            )
    except RuntimeError as exc:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error=str(exc),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return _render_backups_page(
        request=request,
        db=db,
        current_user=current_user,
        restore_result=(
            f"Restored {result.restored_documents} document(s); "
            f"skipped {result.skipped_documents} duplicate(s)."
        ),
    )


@router.get(
    "/backups/{backup_id}/status",
    response_model=None,
)
def get_backup_status(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    job = get_backup_job(
        db=db,
        backup_id=backup_id,
        owner_id=current_user.id,
    )
    if job is None:
        return JSONResponse(
            {"detail": "Backup job not found."},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return JSONResponse(
        {
            "id": job.id,
            "status": job.status.value,
            "error_message": job.error_message,
            "drive_file_id": job.drive_file_id,
            "drive_file_name": job.drive_file_name,
            "drive_web_view_link": job.drive_web_view_link,
            "compressed_size_bytes": job.compressed_size_bytes,
            "checksum_sha256": job.checksum_sha256,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/backups/{backup_id}/delete",
    response_class=HTMLResponse,
    response_model=None,
)
def delete_backup_submit(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    job = get_backup_job(
        db=db,
        backup_id=backup_id,
        owner_id=current_user.id,
    )
    if job is None:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Backup job not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job.status in (BackupJobStatus.pending, BackupJobStatus.running):
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="A pending or running backup cannot be deleted.",
            status_code=status.HTTP_409_CONFLICT,
        )

    drive_file_retained = False
    if job.drive_file_id:
        connection = get_google_drive_connection(db=db, user_id=current_user.id)
        if connection is None:
            drive_file_retained = True
        else:
            try:
                delete_gzip_backup(
                    file_id=job.drive_file_id,
                    refresh_token=connection.refresh_token,
                )
            except RuntimeError as exc:
                return _render_backups_page(
                    request=request,
                    db=db,
                    current_user=current_user,
                    error=str(exc),
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

    delete_backup_job(db=db, backup_id=backup_id, owner_id=current_user.id)
    redirect_suffix = "&drive_file_retained=1" if drive_file_retained else ""
    return RedirectResponse(
        url=f"/backups?deleted=1{redirect_suffix}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/backups/{backup_id}/download",
    response_model=None,
)
def download_backup(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    current_user = _get_web_current_user(request, db)
    if current_user is None:
        return _redirect_to_login()

    job = get_backup_job(
        db=db,
        backup_id=backup_id,
        owner_id=current_user.id,
    )
    if job is None:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Backup job not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if job.status != BackupJobStatus.completed or not job.drive_file_id:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Only completed backups can be downloaded.",
            status_code=status.HTTP_409_CONFLICT,
        )

    connection = get_google_drive_connection(db=db, user_id=current_user.id)
    if connection is None:
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Connect Google Drive before downloading this backup file.",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        downloaded_content = download_gzip_backup(
            file_id=job.drive_file_id,
            refresh_token=connection.refresh_token,
        )
        if job.content_type == "application/vnd.docsflow.recovery+fernet":
            downloaded_content = decrypt_recovery_archive(
                content=downloaded_content,
                recovery_key=get_recovery_key(user=current_user),
            )
        content = gzip.decompress(downloaded_content)
    except (EOFError, OSError, RuntimeError):
        return _render_backups_page(
            request=request,
            db=db,
            current_user=current_user,
            error="Could not download a valid JSON backup file.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    filename = (job.drive_file_name or "docsflow-backup.json.gz").removesuffix(".gz")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                "attachment; filename=docsflow-backup.json; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )
