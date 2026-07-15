from sqlalchemy import Text
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from app.services.google_drive_token_encryption import (
    decrypt_google_drive_refresh_token,
    encrypt_google_drive_refresh_token,
)


class EncryptedGoogleDriveRefreshToken(TypeDecorator[str]):
    """Persist Google refresh tokens encrypted while exposing plaintext to services."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return encrypt_google_drive_refresh_token(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return decrypt_google_drive_refresh_token(value)
