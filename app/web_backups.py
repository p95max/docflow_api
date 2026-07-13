import logging
import secrets

import jwt
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.services.backup_jobs import (
    create_backup_job,
    enqueue_backup_job,
    list_backup_jobs,
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
        created=request.query_params.get("created") == "1",
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
        save_google_drive_connection(
            db=db,
            user_id=current_user.id,
            refresh_token=token_result.refresh_token,
            scope=token_result.scope,
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
