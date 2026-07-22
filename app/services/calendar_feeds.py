"""Opaque private calendar feed tokens; plaintext tokens are never persisted."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User


def generate_calendar_feed_token(*, db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    user.calendar_feed_token_hash = _token_hash(token)
    db.commit()
    return token


def revoke_calendar_feed_token(*, db: Session, user: User) -> None:
    user.calendar_feed_token_hash = None
    db.commit()


def get_user_for_calendar_feed_token(*, db: Session, token: str) -> User | None:
    if not token or len(token) > 512:
        return None
    return db.scalar(
        select(User).where(
            User.calendar_feed_token_hash == _token_hash(token),
            User.is_active.is_(True),
        )
    )


def _token_hash(token: str) -> str:
    return hmac.new(
        settings.app_secret_key.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
