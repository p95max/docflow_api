from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


TOKEN_PREFIX = "docsflow:gdrive:v1:"


def validate_google_drive_token_encryption() -> None:
    _active_fernet()
    _decryption_fernets()


def encrypt_google_drive_refresh_token(refresh_token: str) -> str:
    if not refresh_token:
        raise RuntimeError("Google Drive refresh token must not be empty.")
    encrypted = _active_fernet().encrypt(refresh_token.encode("utf-8")).decode("utf-8")
    return TOKEN_PREFIX + encrypted


def decrypt_google_drive_refresh_token(stored_value: str) -> str:
    if not stored_value.startswith(TOKEN_PREFIX):
        # Legacy rows are accepted only so the startup rotation can encrypt them.
        return stored_value

    encrypted = stored_value.removeprefix(TOKEN_PREFIX).encode("utf-8")
    for fernet in _decryption_fernets():
        try:
            return fernet.decrypt(encrypted).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            continue
    raise RuntimeError(
        "Google Drive refresh token cannot be decrypted with the configured keys."
    )


def google_drive_refresh_token_is_encrypted(stored_value: str) -> bool:
    return stored_value.startswith(TOKEN_PREFIX)


def _active_fernet() -> Fernet:
    key = (settings.google_drive_token_encryption_key or "").strip()
    if not key:
        raise RuntimeError(
            "GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY is required to store Google Drive tokens."
        )
    return _fernet_from_key(key, "GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY")


def _decryption_fernets() -> list[Fernet]:
    active = _active_fernet()
    previous_keys = [
        value.strip()
        for value in settings.google_drive_token_previous_encryption_keys.split(",")
        if value.strip()
    ]
    return [
        active,
        *[
            _fernet_from_key(key, "GOOGLE_DRIVE_TOKEN_PREVIOUS_ENCRYPTION_KEYS")
            for key in previous_keys
        ],
    ]


def _fernet_from_key(key: str, setting_name: str) -> Fernet:
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"{setting_name} must contain valid Fernet keys.") from exc
