from sqlalchemy.orm import Session

from app.services.users import authenticate_user, get_user_by_email
from scripts.init_test_user import ensure_test_user


def test_init_test_user_creates_and_keeps_expected_credentials(
    db_session: Session,
) -> None:
    result = ensure_test_user(
        db=db_session,
        email="m@m.com",
        password="12345678",
    )

    assert result == "created"
    user = get_user_by_email(db_session, "m@m.com")
    assert user is not None
    assert user.is_active is True
    assert authenticate_user(db_session, "m@m.com", "12345678") is not None

    second_result = ensure_test_user(
        db=db_session,
        email="m@m.com",
        password="12345678",
    )

    assert second_result == "updated"
    assert authenticate_user(db_session, "m@m.com", "12345678") is not None
