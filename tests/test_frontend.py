from fastapi.testclient import TestClient

from app.models.user import User


def test_login_page_is_server_rendered_without_javascript(
    client: TestClient,
) -> None:
    response = client.get(
        "/login",
        headers={"host": "localhost:8000"},
    )

    assert response.status_code == 200
    assert "DocsFlow" in response.text
    assert "bootstrap" in response.text.lower()
    assert '<form method="post" action="/login">' in response.text
    assert 'value="m@m.com"' in response.text
    assert 'value="12345678"' in response.text
    assert "<script" not in response.text


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
    assert javascript_response.status_code == 404


def test_api_routes_remain_available(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
