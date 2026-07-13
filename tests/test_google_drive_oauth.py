from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web_backups as web_backups
from app.models.google_drive_connection import GoogleDriveConnection
from app.models.user import User
from app.services.google_drive_oauth import (
    GOOGLE_DRIVE_FILE_SCOPE,
    GoogleOAuthTokenResult,
    get_google_drive_connection,
)


def _login(client: TestClient, user: User) -> None:
    response = client.post(
        "/login",
        data={"email": user.email, "password": "strong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_connect_route_starts_offline_google_oauth_flow(
    client: TestClient,
    test_user: User,
) -> None:
    _login(client, test_user)

    response = client.get("/backups/google/connect", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "accounts.google.com"
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["scope"] == [GOOGLE_DRIVE_FILE_SCOPE]
    assert query["redirect_uri"] == ["http://testserver/backups/google/callback"]
    assert query["state"][0]
    assert client.cookies.get(web_backups.GOOGLE_OAUTH_STATE_COOKIE)


def test_google_oauth_callback_persists_refresh_token(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(client, test_user)
    connect_response = client.get(
        "/backups/google/connect",
        follow_redirects=False,
    )
    state = parse_qs(urlparse(connect_response.headers["location"]).query)["state"][0]

    def fake_exchange(*, code: str, redirect_uri: str) -> GoogleOAuthTokenResult:
        assert code == "authorization-code"
        assert redirect_uri == "http://testserver/backups/google/callback"
        return GoogleOAuthTokenResult(
            refresh_token="oauth-refresh-token",
            scope=GOOGLE_DRIVE_FILE_SCOPE,
        )

    monkeypatch.setattr(
        web_backups,
        "exchange_google_drive_authorization_code",
        fake_exchange,
    )

    response = client.get(
        "/backups/google/callback",
        params={"code": "authorization-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/backups?google_connected=1"
    connection = get_google_drive_connection(db=db_session, user_id=test_user.id)
    assert connection is not None
    assert connection.refresh_token == "oauth-refresh-token"
    assert connection.scope == GOOGLE_DRIVE_FILE_SCOPE


def test_google_oauth_callback_rejects_invalid_state(
    client: TestClient,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(client, test_user)
    client.get("/backups/google/connect", follow_redirects=False)

    def unexpected_exchange(*, code: str, redirect_uri: str) -> GoogleOAuthTokenResult:
        raise AssertionError("Invalid state must be rejected before token exchange")

    monkeypatch.setattr(
        web_backups,
        "exchange_google_drive_authorization_code",
        unexpected_exchange,
    )

    response = client.get(
        "/backups/google/callback",
        params={"code": "authorization-code", "state": "invalid-state"},
    )

    assert response.status_code == 400
    assert "OAuth" in response.text or "token" in response.text


def test_disconnect_removes_google_drive_connection(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login(client, test_user)
    connection = GoogleDriveConnection(
        user_id=test_user.id,
        refresh_token="refresh-token-to-revoke",
        scope=GOOGLE_DRIVE_FILE_SCOPE,
    )
    db_session.add(connection)
    db_session.commit()

    revoked: list[str] = []
    monkeypatch.setattr(
        web_backups,
        "revoke_google_drive_refresh_token",
        revoked.append,
    )

    response = client.post(
        "/backups/google/disconnect",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/backups?google_disconnected=1"
    assert revoked == ["refresh-token-to-revoke"]
    assert get_google_drive_connection(db=db_session, user_id=test_user.id) is None
