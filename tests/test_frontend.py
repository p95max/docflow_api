from fastapi.testclient import TestClient


def test_frontend_pages_return_bootstrap_application_shell(
    client: TestClient,
) -> None:
    for path in (
        "/login",
        "/register",
        "/documents",
        "/documents/upload",
        "/documents/123",
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert "DocsFlow" in response.text
        assert "bootstrap" in response.text.lower()


def test_frontend_assets_are_served(client: TestClient) -> None:
    response = client.get("/assets/js/app.js")

    assert response.status_code == 200
    assert "renderDocuments" in response.text
    assert "data-file-size" in response.text


def test_api_routes_remain_available(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
