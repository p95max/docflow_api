from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.security import hash_password, verify_password


def get_user_by_email(db: Session, email: str) -> User | None:
    stmt = select(User).where(User.email == email.lower())
    return db.scalar(stmt)


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def create_user(db: Session, email: str, password: str) -> User:
    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)

    if user is None:
        return None

    if not user.is_active:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user