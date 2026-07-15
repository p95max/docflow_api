from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.session import SessionLocal
from app.models.google_drive_connection import GoogleDriveConnection
from app.services.google_drive_token_encryption import (
    validate_google_drive_token_encryption,
)


def reencrypt_google_drive_tokens(*, db: Session) -> int:
    connection_count = db.scalar(
        select(func.count()).select_from(GoogleDriveConnection)
    ) or 0
    if connection_count == 0:
        return 0

    validate_google_drive_token_encryption()
    connections = list(db.scalars(select(GoogleDriveConnection)).all())
    for connection in connections:
        plaintext_token = connection.refresh_token
        connection.refresh_token = plaintext_token
        flag_modified(connection, "refresh_token")
    db.commit()
    return len(connections)


def main() -> None:
    with SessionLocal() as db:
        updated = reencrypt_google_drive_tokens(db=db)
    print(f"Google Drive refresh tokens encrypted with the active key: {updated}.")


if __name__ == "__main__":
    main()
