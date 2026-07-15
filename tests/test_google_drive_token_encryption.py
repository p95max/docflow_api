from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.user import User
from app.services.google_drive_token_encryption import TOKEN_PREFIX
from scripts.rotate_google_drive_tokens import reencrypt_google_drive_tokens


def _stored_token(db: Session, connection_id: int) -> str:
    return str(
        db.execute(
            text(
                "SELECT refresh_token FROM google_drive_connections "
                "WHERE id = :connection_id"
            ),
            {"connection_id": connection_id},
        ).scalar_one()
    )


def test_google_drive_refresh_token_is_encrypted_at_rest(
    db_session: Session,
    test_user: User,
) -> None:
    connection = GoogleDriveConnection(
        user_id=test_user.id,
        refresh_token="plain-oauth-refresh-token",
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)

    stored_token = _stored_token(db_session, connection.id)
    assert stored_token.startswith(TOKEN_PREFIX)
    assert "plain-oauth-refresh-token" not in stored_token
    assert connection.refresh_token == "plain-oauth-refresh-token"


def test_startup_rotation_encrypts_legacy_plaintext_token(
    db_session: Session,
    test_user: User,
) -> None:
    db_session.execute(
        text(
            "INSERT INTO google_drive_connections "
            "(user_id, refresh_token, created_at, updated_at) "
            "VALUES (:user_id, :refresh_token, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "user_id": test_user.id,
            "refresh_token": "legacy-plaintext-refresh-token",
        },
    )
    db_session.commit()
    connection_id = int(
        db_session.execute(
            text(
                "SELECT id FROM google_drive_connections WHERE user_id = :user_id"
            ),
            {"user_id": test_user.id},
        ).scalar_one()
    )

    assert _stored_token(db_session, connection_id) == "legacy-plaintext-refresh-token"
    assert reencrypt_google_drive_tokens(db=db_session) == 1

    stored_token = _stored_token(db_session, connection_id)
    assert stored_token.startswith(TOKEN_PREFIX)
    assert "legacy-plaintext-refresh-token" not in stored_token

    db_session.expire_all()
    migrated = db_session.get(GoogleDriveConnection, connection_id)
    assert migrated is not None
    assert migrated.refresh_token == "legacy-plaintext-refresh-token"


def test_token_rotation_uses_new_key_and_accepts_previous_key(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    old_key = Fernet.generate_key().decode("utf-8")
    new_key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setattr(settings, "google_drive_token_encryption_key", old_key)
    connection = GoogleDriveConnection(
        user_id=test_user.id,
        refresh_token="token-to-rotate",
    )
    db_session.add(connection)
    db_session.commit()
    old_ciphertext = _stored_token(db_session, connection.id)

    db_session.expire_all()
    monkeypatch.setattr(settings, "google_drive_token_encryption_key", new_key)
    monkeypatch.setattr(
        settings,
        "google_drive_token_previous_encryption_keys",
        old_key,
    )

    assert reencrypt_google_drive_tokens(db=db_session) == 1
    new_ciphertext = _stored_token(db_session, connection.id)
    assert new_ciphertext.startswith(TOKEN_PREFIX)
    assert new_ciphertext != old_ciphertext

    db_session.expire_all()
    rotated = db_session.get(GoogleDriveConnection, connection.id)
    assert rotated is not None
    assert rotated.refresh_token == "token-to-rotate"
