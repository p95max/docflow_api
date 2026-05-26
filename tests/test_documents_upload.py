import hashlib
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1 import routes_documents
from app.core.config import settings
from app.models import ProcessingJob
from app.models.document import Document
from app.services.uploads import _upload_rate_limit_state

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PDF_BYTES_SECOND = b"%PDF-1.4\n2 0 obj\n<<>>\nendobj\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32

@pytest.fixture(autouse=True)
def disable_processing_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_enqueue_processing_job(
        db: Session,
        job: ProcessingJob,
    ) -> ProcessingJob:
        job.celery_task_id = "fake-upload-test-task-id"
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    monkeypatch.setattr(
        routes_documents,
        "enqueue_processing_job",
        fake_enqueue_processing_job,
    )

@pytest.mark.parametrize(
    ("filename", "content", "content_type", "expected_extension"),
    [
        ("invoice.pdf", PDF_BYTES, "application/pdf", "pdf"),
        ("scan.png", PNG_BYTES, "image/png", "png"),
        ("photo.jpg", JPG_BYTES, "image/jpeg", "jpg"),
    ],
)
def test_upload_accepts_supported_file_types(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    filename: str,
    content: bytes,
    content_type: str,
    expected_extension: str,
) -> None:
    response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                filename,
                content,
                content_type,
            ),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED

    payload = response.json()
    expected_checksum = hashlib.sha256(content).hexdigest()

    assert payload["original_filename"] == filename
    assert payload["status"] == "uploaded"
    assert payload["processing_mode"] == "standard"
    assert payload["content_type"] == content_type
    assert payload["file_size_bytes"] == len(content)
    assert payload["checksum_sha256"] == expected_checksum

    document = db_session.get(Document, payload["id"])

    assert document is not None
    assert document.storage_key == (
        f"users/{document.owner_id}/documents/{document.id}/original.{expected_extension}"
    )

    stored_file_path = Path(settings.local_storage_path) / document.storage_key

    assert stored_file_path.exists()
    assert stored_file_path.read_bytes() == content


def test_upload_confidential_mode_sets_processing_mode(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "true"},
        files={
            "file": (
                "confidential.pdf",
                PDF_BYTES,
                "application/pdf",
            ),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED

    payload = response.json()

    assert payload["status"] == "uploaded"
    assert payload["processing_mode"] == "confidential"


def test_upload_rejects_duplicate_document_for_same_user(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    first_response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "invoice.pdf",
                PDF_BYTES,
                "application/pdf",
            ),
        },
    )

    assert first_response.status_code == status.HTTP_201_CREATED

    first_document_id = first_response.json()["id"]

    second_response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "same-content-different-name.pdf",
                PDF_BYTES,
                "application/pdf",
            ),
        },
    )

    assert second_response.status_code == status.HTTP_409_CONFLICT
    assert second_response.json() == {
        "detail": {
            "message": "Duplicate document upload detected.",
            "document_id": first_document_id,
        },
    }

    documents_count = db_session.scalar(
        select(func.count()).select_from(Document),
    )

    assert documents_count == 1


def test_upload_rejects_unsupported_mime_type(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "notes.txt",
                b"plain text",
                "text/plain",
            ),
        },
    )

    assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    assert response.json() == {
        "detail": "Unsupported file type. Only PDF, JPG and PNG files are allowed.",
    }


def test_upload_rejects_file_with_mismatched_signature(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "fake.pdf",
                b"this is not a real pdf",
                "application/pdf",
            ),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "File content does not match declared MIME type.",
    }


def test_upload_rejects_empty_file(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "empty.pdf",
                b"",
                "application/pdf",
            ),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Uploaded file is empty.",
    }


def test_upload_rejects_file_above_size_limit(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "upload_max_file_size_mb", 0)

    response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "too-large.pdf",
                PDF_BYTES,
                "application/pdf",
            ),
        },
    )

    assert response.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert response.json() == {
        "detail": "Uploaded file is too large. Maximum allowed size is 0 MB.",
    }


def test_upload_requires_authentication(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/documents/upload",
        data={"confidential": "false"},
        files={
            "file": (
                "invoice.pdf",
                PDF_BYTES,
                "application/pdf",
            ),
        },
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_upload_rate_limit_returns_429(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "upload_rate_limit_requests", 1)
    monkeypatch.setattr(settings, "upload_rate_limit_window_seconds", 60)
    _upload_rate_limit_state.clear()

    first_response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "first.pdf",
                PDF_BYTES,
                "application/pdf",
            ),
        },
    )

    assert first_response.status_code == status.HTTP_201_CREATED

    second_response = client.post(
        "/api/v1/documents/upload",
        headers=auth_headers,
        data={"confidential": "false"},
        files={
            "file": (
                "second.pdf",
                PDF_BYTES_SECOND,
                "application/pdf",
            ),
        },
    )

    assert second_response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert second_response.json() == {
        "detail": "Upload rate limit exceeded. Please try again later.",
    }