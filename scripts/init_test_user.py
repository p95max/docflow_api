from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.security import hash_password
from app.services.users import get_user_by_email


def ensure_test_user(
    *,
    db: Session,
    email: str,
    password: str,
) -> str:
    normalized_email = email.strip().lower()
    user = get_user_by_email(db, normalized_email)

    if user is None:
        from app.models.user import User

        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(user)
        db.commit()
        return "created"

    user.password_hash = hash_password(password)
    user.is_active = True
    db.add(user)
    db.commit()
    return "updated"


def main() -> None:
    if settings.app_env != "local":
        print("Skipping test user initialization outside local environment.")
        return

    with SessionLocal() as db:
        result = ensure_test_user(
            db=db,
            email=settings.test_user_email,
            password=settings.test_user_password,
        )

    print(f"Test user {settings.test_user_email} {result}.")


if __name__ == "__main__":
    main()
