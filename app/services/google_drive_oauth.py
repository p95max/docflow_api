from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.google_drive_connection import GoogleDriveConnection
from app.services.google_drive_token_encryption import (
    validate_google_drive_token_encryption,
)

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_DRIVE_OAUTH_STATE_PURPOSE = "google_drive_oauth"


@dataclass(frozen=True)
class GoogleOAuthTokenResult:
    refresh_token: str | None
    scope: str | None


def missing_google_drive_oauth_settings() -> list[str]:
    required_settings = {
        "GOOGLE_DRIVE_CLIENT_ID": settings.google_drive_client_id,
        "GOOGLE_DRIVE_CLIENT_SECRET": settings.google_drive_client_secret,
    }
    return [name for name, value in required_settings.items() if not value]


def google_drive_oauth_is_configured() -> bool:
    return not missing_google_drive_oauth_settings()


def create_google_drive_oauth_state(
    *,
    user_id: int,
    nonce: str,
) -> str:
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.google_oauth_state_expire_minutes,
    )
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": GOOGLE_DRIVE_OAUTH_STATE_PURPOSE,
            "nonce": nonce,
            "exp": expires_at,
        },
        settings.app_secret_key,
        algorithm="HS256",
    )


def validate_google_drive_oauth_state(
    *,
    state: str,
    expected_user_id: int,
    expected_nonce: str,
) -> None:
    payload = jwt.decode(
        state,
        settings.app_secret_key,
        algorithms=["HS256"],
    )
    if payload.get("purpose") != GOOGLE_DRIVE_OAUTH_STATE_PURPOSE:
        raise jwt.InvalidTokenError("Invalid Google OAuth state purpose.")
    if payload.get("sub") != str(expected_user_id):
        raise jwt.InvalidTokenError("Google OAuth state belongs to another user.")
    if payload.get("nonce") != expected_nonce:
        raise jwt.InvalidTokenError("Google OAuth state nonce does not match.")


def build_google_drive_authorization_url(
    *,
    redirect_uri: str,
    state: str,
    login_hint: str | None = None,
) -> str:
    missing = missing_google_drive_oauth_settings()
    if missing:
        raise RuntimeError(
            "Google Drive OAuth is not configured. Missing: " + ", ".join(missing)
        )

    params = {
        "client_id": settings.google_drive_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_DRIVE_FILE_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent select_account",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint

    return f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"


def exchange_google_drive_authorization_code(
    *,
    code: str,
    redirect_uri: str,
) -> GoogleOAuthTokenResult:
    missing = missing_google_drive_oauth_settings()
    if missing:
        raise RuntimeError(
            "Google Drive OAuth is not configured. Missing: " + ", ".join(missing)
        )

    try:
        with httpx.Client(timeout=settings.google_drive_timeout_seconds) as client:
            response = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_drive_client_id,
                    "client_secret": settings.google_drive_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not contact the Google OAuth service.") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.is_error:
        detail = payload.get("error_description") or payload.get("error")
        raise RuntimeError(
            f"Google OAuth token exchange failed: {detail or response.status_code}"
        )

    refresh_token = payload.get("refresh_token")
    scope = payload.get("scope")
    return GoogleOAuthTokenResult(
        refresh_token=str(refresh_token) if refresh_token else None,
        scope=str(scope) if scope else None,
    )


def get_google_drive_connection(
    *,
    db: Session,
    user_id: int,
) -> GoogleDriveConnection | None:
    stmt = select(GoogleDriveConnection).where(
        GoogleDriveConnection.user_id == user_id,
    )
    connection = db.scalar(stmt)
    if connection is not None:
        # Fail closed for legacy plaintext rows when the application is started
        # without the server-side encryption key.
        validate_google_drive_token_encryption()
    return connection


def save_google_drive_connection(
    *,
    db: Session,
    user_id: int,
    refresh_token: str | None,
    scope: str | None,
) -> GoogleDriveConnection:
    validate_google_drive_token_encryption()
    connection = get_google_drive_connection(db=db, user_id=user_id)

    if connection is None:
        if not refresh_token:
            raise RuntimeError(
                "Google did not return a refresh token. Reconnect and grant consent."
            )
        connection = GoogleDriveConnection(
            user_id=user_id,
            refresh_token=refresh_token,
            scope=scope,
        )
        db.add(connection)
    else:
        if refresh_token:
            connection.refresh_token = refresh_token
        connection.scope = scope or connection.scope

    db.commit()
    db.refresh(connection)
    return connection


def delete_google_drive_connection(
    *,
    db: Session,
    connection: GoogleDriveConnection,
) -> None:
    db.delete(connection)
    db.commit()


def revoke_google_drive_refresh_token(refresh_token: str) -> None:
    with httpx.Client(timeout=settings.google_drive_timeout_seconds) as client:
        response = client.post(
            GOOGLE_REVOKE_URL,
            params={"token": refresh_token},
        )
    if response.status_code not in {200, 400}:
        response.raise_for_status()
