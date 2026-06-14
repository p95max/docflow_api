from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings


JWT_ALGORITHM = "HS256"
DOCUMENT_PREVIEW_TOKEN_PURPOSE = "document_preview"


def hash_password(password: str) -> str:
    """Hash a plain password using bcrypt."""
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True when a plain password matches the stored bcrypt hash."""
    return bcrypt.checkpw(
        password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


def create_access_token(subject: str) -> str:
    """Create a short-lived JWT access token."""
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.access_token_expire_minutes,
    )

    payload = {
        "sub": subject,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        settings.app_secret_key,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token."""
    return jwt.decode(
        token,
        settings.app_secret_key,
        algorithms=[JWT_ALGORITHM],
    )


def create_document_preview_token(
    *,
    document_id: int,
    owner_id: int,
) -> tuple[str, datetime]:
    """Create a short-lived token granting access to one document file."""
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.document_preview_token_expire_minutes,
    )

    payload = {
        "sub": str(owner_id),
        "document_id": document_id,
        "purpose": DOCUMENT_PREVIEW_TOKEN_PURPOSE,
        "exp": expires_at,
    }

    token = jwt.encode(
        payload,
        settings.app_secret_key,
        algorithm=JWT_ALGORITHM,
    )

    return token, expires_at


def decode_document_preview_token(token: str) -> dict:
    """Decode and validate a document preview token."""
    payload = jwt.decode(
        token,
        settings.app_secret_key,
        algorithms=[JWT_ALGORITHM],
    )

    if payload.get("purpose") != DOCUMENT_PREVIEW_TOKEN_PURPOSE:
        raise jwt.InvalidTokenError("Invalid preview token purpose.")

    return payload