import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.services.security import (
    create_access_token,
    create_document_preview_token,
    decode_access_token,
)
from app.web import CSRF_COOKIE_NAME


def test_access_token_decoder_rejects_document_preview_token() -> None:
    preview_token, _ = create_document_preview_token(document_id=1, owner_id=1)

    with pytest.raises(jwt.InvalidTokenError, match="purpose"):
        decode_access_token(preview_token)


def test_access_token_decoder_accepts_access_token() -> None:
    payload = decode_access_token(create_access_token(subject="1"))

    assert payload["sub"] == "1"
    assert payload["purpose"] == "access"


def test_login_form_sets_and_renders_csrf_token() -> None:
    with TestClient(app) as client:
        response = client.get("/login")

        csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
        assert csrf_token
        assert f'name="csrf_token" value="{csrf_token}"' in response.text


def test_html_post_without_csrf_token_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"email": "user@example.com", "password": "strong-password"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid CSRF token."}


def test_html_form_with_matching_csrf_token_is_accepted(
    db_session: Session,
    test_user: User,
) -> None:
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            login_page = client.get("/login")
            csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
            assert csrf_token

            response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_token,
                    "email": test_user.email,
                    "password": "strong-password",
                },
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert login_page.status_code == 200
    assert response.status_code == 303
    assert response.headers["location"] == "/documents"
