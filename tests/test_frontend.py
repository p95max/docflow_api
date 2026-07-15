import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.user import User
from app.web import CSRF_COOKIE_NAME


def test_login_page_is_server_rendered_without_javascript(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "init_test_user", True)
    response = client.get(
        "/login",
        headers={"host": "localhost:8000"},
    )

    assert response.status_code == 200
    assert "DocsFlow" in response.text
    assert "bootstrap" in response.text.lower()
    assert "sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB" in response.text
    assert 'crossorigin="anonymous"' in response.text
    assert 'data-bs-theme="dark"' in response.text
    assert '<form method="post" action="/login">' in response.text
    assert 'value="m@m.com"' in response.text
    assert 'value="12345678"' in response.text
    assert "<script" not in response.text


def test_codespaces_login_does_not_prefill_test_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "init_test_user", True)

    response = client.get(
        "/login",
        headers={"host": "workspace-123.app.github.dev"},
    )

    assert response.status_code == 200
    assert 'value="m@m.com"' not in response.text
    assert 'value="12345678"' not in response.text


def test_responses_include_baseline_security_headers(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_login_form_contains_matching_csrf_token(client: TestClient) -> None:
    response = client.get("/login")
    csrf_token = client.cookies.get(CSRF_COOKIE_NAME)

    assert csrf_token
    assert f'name="csrf_token" value="{csrf_token}"' in response.text


def test_html_post_rejects_missing_csrf_token(client: TestClient) -> None:
    del client.headers["X-CSRF-Token"]
    client.cookies.delete(CSRF_COOKIE_NAME)

    response = client.post(
        "/login",
        data={"email": "user@example.com", "password": "strong-password"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid CSRF token."}


def test_protected_html_page_redirects_to_login(
    client: TestClient,
) -> None:
    response = client.get(
        "/documents",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_html_login_uses_http_only_cookie_and_renders_documents(
    client: TestClient,
    test_user: User,
) -> None:
    login_response = client.post(
        "/login",
        data={
            "email": test_user.email,
            "password": "strong-password",
        },
        follow_redirects=False,
    )

    assert login_response.status_code == 303
    assert login_response.headers["location"] == "/documents"
    assert "httponly" in login_response.headers["set-cookie"].lower()

    documents_response = client.get("/documents")

    assert documents_response.status_code == 200
    assert "Your documents" in documents_response.text
    assert test_user.email in documents_response.text
    assert "<script" not in documents_response.text


def test_frontend_css_is_served_and_javascript_bundle_is_removed(
    client: TestClient,
) -> None:
    css_response = client.get("/assets/css/app.css")
    javascript_response = client.get("/assets/js/app.js")

    assert css_response.status_code == 200
    assert ".preview-frame" in css_response.text
    assert ':root[data-bs-theme="dark"]' in css_response.text
    assert javascript_response.status_code == 404


def test_api_routes_remain_available(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
