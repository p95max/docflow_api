import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.api.v1.routes_users as routes_users
from app.schemas.user import UserCreate


def test_registration_race_returns_conflict(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes_users, "get_user_by_email", lambda *_: None)

    def raise_unique_conflict(**_: object) -> None:
        raise IntegrityError("INSERT users", {}, RuntimeError("duplicate email"))

    monkeypatch.setattr(routes_users, "create_user", raise_unique_conflict)

    with pytest.raises(HTTPException) as exc_info:
        routes_users.register(
            request=Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/users/register",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                }
            ),
            payload=UserCreate(
                email="race@example.com",
                password="strong-password",
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Email already registered"
