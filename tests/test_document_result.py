import hashlib
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.document import (
    Document,
    DocumentStatus,
    ExtractionStatus,
    ProcessingMode,
)
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingOperationType,
)
from app.models.user import User
from app.services.storage import save_document_file


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def test_get_document_result_returns_extraction_and_preview(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, job = _create_document_result(
        db=db_session,
        user=test_user,
    )

    response = client.get(
        f"/api/v1/documents/{document.id}/result",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["id"] == document.id
    assert payload["status"] == "completed"
    assert payload["document_type"] == "invoice"
    assert payload["summary"] == "Invoice for software services."
    assert payload["amount"] == 950.0
    assert payload["currency"] == "USD"
    assert payload["deadline"] == "2016-12-26"
    assert payload["sender"] == "YesLogic Pty. Ltd."
    assert payload["confidence_score"] == 0.95
    assert payload["extraction_status"] == "draft"
    assert payload["can_confirm"] is True

    assert payload["can_correct"] is True
    assert payload["can_reprocess"] is False
    assert payload["processing_error"] is None

    assert payload["latest_job"]["id"] == job.id
    assert payload["latest_job"]["status"] == "completed"

    assert payload["file_preview_url"] is not None
    assert payload["file_preview_expires_at"] is not None
    assert payload["file_download_url"] is not None
    assert payload["file_download_expires_at"] is not None

    preview_url = urlsplit(payload["file_preview_url"])

    preview_response = client.get(
        f"{preview_url.path}?{preview_url.query}",
    )

    assert preview_response.status_code == status.HTTP_200_OK
    assert preview_response.content == PDF_BYTES
    assert preview_response.headers["content-type"].startswith(
        "application/pdf"
    )
    assert preview_response.headers["content-disposition"].startswith(
        "inline;"
    )

    download_url = urlsplit(payload["file_download_url"])
    download_response = client.get(
        f"{download_url.path}?{download_url.query}",
    )

    assert download_response.status_code == status.HTTP_200_OK
    assert download_response.content == PDF_BYTES
    assert download_response.headers["content-disposition"].startswith(
        "attachment;"
    )


def test_get_download_url_returns_signed_attachment_url(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    response = client.get(
        f"/api/v1/documents/{document.id}/download-url",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["expires_at"] is not None

    download_url = urlsplit(payload["url"])
    download_response = client.get(
        f"{download_url.path}?{download_url.query}",
    )

    assert download_response.status_code == status.HTTP_200_OK
    assert download_response.headers["content-disposition"].startswith(
        "attachment;"
    )


def test_download_rejects_preview_token(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    result_response = client.get(
        f"/api/v1/documents/{document.id}/result",
        headers=auth_headers,
    )
    preview_url = urlsplit(result_response.json()["file_preview_url"])

    response = client.get(
        f"/api/v1/documents/{document.id}/download?{preview_url.query}",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {
        "detail": "Invalid or expired document download token.",
    }


def test_manual_correction_updates_effective_fields(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    original_ai_data = dict(document.ai_extracted_data or {})

    response = client.patch(
        f"/api/v1/documents/{document.id}/result",
        headers=auth_headers,
        json={
            "summary": "Corrected invoice summary.",
            "amount": "975.50",
            "currency": "eur",
            "deadline": "2017-01-15",
            "sender": "Corrected Sender GmbH",
            "confidence_score": 0.8,
        },
    )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["summary"] == "Corrected invoice summary."
    assert payload["amount"] == 975.5
    assert payload["currency"] == "EUR"
    assert payload["deadline"] == "2017-01-15"
    assert payload["sender"] == "Corrected Sender GmbH"
    assert payload["confidence_score"] == 0.8
    assert payload["manually_corrected_at"] is not None
    assert payload["extraction_status"] == "corrected"

    db_session.refresh(document)

    assert document.summary == "Corrected invoice summary."
    assert document.amount == Decimal("975.50")
    assert document.currency == "EUR"
    assert document.deadline == date(2017, 1, 15)
    assert document.sender == "Corrected Sender GmbH"
    assert document.confidence_score == 0.8
    assert document.extraction_status == ExtractionStatus.corrected

    assert document.manual_corrections == {
        "summary": "Corrected invoice summary.",
        "amount": "975.50",
        "currency": "EUR",
        "deadline": "2017-01-15",
        "sender": "Corrected Sender GmbH",
        "confidence_score": 0.8,
    }

    # Original AI response remains available for audit.
    assert document.ai_extracted_data == original_ai_data


def test_manual_correction_creates_audit_log_for_each_changed_field(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    response = client.patch(
        f"/api/v1/documents/{document.id}/extraction",
        headers=auth_headers,
        json={
            "amount": "1000.25",
            "document_date": "2016-11-26",
            "document_type": "receipt",
            "sender": "Corrected Vendor Ltd.",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["document_date"] == "2016-11-26"

    audit_logs = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.document_id == document.id)
            .order_by(AuditLog.id)
        ).all()
    )

    assert [log.field_name for log in audit_logs] == [
        "document_type",
        "amount",
        "document_date",
        "sender",
    ]
    assert all(
        log.action == "extraction_field_corrected"
        for log in audit_logs
    )
    assert audit_logs[1].old_value == "950.00"
    assert audit_logs[1].new_value == "1000.25"
    assert audit_logs[2].old_value is None
    assert audit_logs[2].new_value == "2016-11-26"
    assert all(log.user_id == test_user.id for log in audit_logs)


def test_confirm_extraction_updates_status_and_creates_audit_log(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    response = client.post(
        f"/api/v1/documents/{document.id}/confirm",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["extraction_status"] == "confirmed"
    assert payload["extraction_confirmed_at"] is not None
    assert payload["can_confirm"] is False

    db_session.refresh(document)
    assert document.extraction_status == ExtractionStatus.confirmed
    assert document.extraction_confirmed_at is not None

    audit_log = db_session.scalar(
        select(AuditLog).where(
            AuditLog.document_id == document.id,
            AuditLog.action == "extraction_confirmed",
        )
    )
    assert audit_log is not None
    assert audit_log.field_name == "extraction_status"
    assert audit_log.old_value == "draft"
    assert audit_log.new_value == "confirmed"


def test_confirm_extraction_rejects_repeated_confirmation(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    first_response = client.post(
        f"/api/v1/documents/{document.id}/confirm",
        headers=auth_headers,
    )
    second_response = client.post(
        f"/api/v1/documents/{document.id}/confirm",
        headers=auth_headers,
    )

    assert first_response.status_code == status.HTTP_200_OK
    assert second_response.status_code == status.HTTP_409_CONFLICT
    assert second_response.json() == {
        "detail": "Extraction is already confirmed.",
    }


def test_failed_document_result_exposes_error_and_reprocess_control(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, job = _create_document_result(
        db=db_session,
        user=test_user,
        document_status=DocumentStatus.failed,
        job_status=ProcessingJobStatus.failed,
        error_message="OpenAI processing failed.",
    )

    response = client.get(
        f"/api/v1/documents/{document.id}/result",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert payload["status"] == "failed"
    assert payload["processing_error"] == "OpenAI processing failed."
    assert payload["latest_job"]["id"] == job.id
    assert payload["latest_job"]["status"] == "failed"
    assert payload["can_correct"] is False
    assert payload["can_reprocess"] is True


def test_manual_correction_rejects_non_completed_document(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
        document_status=DocumentStatus.failed,
        job_status=ProcessingJobStatus.failed,
        error_message="Processing failed.",
    )

    response = client.patch(
        f"/api/v1/documents/{document.id}/result",
        headers=auth_headers,
        json={"summary": "Manual summary"},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "Only completed documents can be corrected.",
    }


def test_preview_rejects_invalid_token(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    document, _ = _create_document_result(
        db=db_session,
        user=test_user,
    )

    response = client.get(
        f"/api/v1/documents/{document.id}/preview",
        params={"token": "invalid-token"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def _create_document_result(
    *,
    db: Session,
    user: User,
    document_status: DocumentStatus = DocumentStatus.completed,
    job_status: ProcessingJobStatus = ProcessingJobStatus.completed,
    error_message: str | None = None,
) -> tuple[Document, ProcessingJob]:
    checksum = hashlib.sha256(
        PDF_BYTES
        + str(user.id).encode()
        + document_status.value.encode()
        + job_status.value.encode()
    ).hexdigest()

    document = Document(
        owner_id=user.id,
        original_filename="invoice.pdf",
        status=document_status,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        file_size_bytes=len(PDF_BYTES),
        checksum_sha256=checksum,
        raw_text="Invoice number 161126. Total USD 950.00.",
        document_type="invoice",
        ai_extracted_data={
            "document_type": "invoice",
            "summary": "Invoice for software services.",
            "total_amount": 950.0,
            "currency": "USD",
        },
        ai_extraction_model="gpt-4o-mini",
        summary="Invoice for software services.",
        amount=Decimal("950.00"),
        currency="USD",
        deadline=date(2016, 12, 26),
        sender="YesLogic Pty. Ltd.",
        confidence_score=0.95,
    )

    db.add(document)
    db.flush()

    document.storage_key = (
        f"users/{user.id}/documents/{document.id}/original.pdf"
    )

    save_document_file(
        content=PDF_BYTES,
        storage_key=document.storage_key,
    )

    job = ProcessingJob(
        document_id=document.id,
        operation_type=ProcessingOperationType.text_extraction,
        status=job_status,
        attempts=1,
        max_retries=3,
        error_message=error_message,
    )

    db.add(job)
    db.commit()
    db.refresh(document)
    db.refresh(job)

    return document, job
