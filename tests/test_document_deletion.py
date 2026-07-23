from pathlib import Path
from datetime import date

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.routes_document_deletion import delete_document
from app.models.audit_log import AuditLog
from app.models.calendar_event import (
    CalendarEvent,
    CalendarEventSource,
    CalendarEventStatus,
    CalendarEventType,
)
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.processing_job import ProcessingJob
from app.models.user import User
from app.services.security import create_access_token
from app.services.storage import save_document_file
from app.services.users import create_user


def _create_stored_document(
    db: Session,
    owner: User,
    *,
    filename: str = "invoice.pdf",
) -> tuple[Document, Path, ProcessingJob, OpenAIUsageLog, AuditLog]:
    document = Document(
        owner_id=owner.id,
        original_filename=filename,
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        file_size_bytes=14,
        checksum_sha256=f"{owner.id:064x}",
    )
    db.add(document)
    db.flush()

    document.storage_key = (
        f"users/{owner.id}/documents/{document.id}/original.pdf"
    )

    processing_job = ProcessingJob(document_id=document.id)
    usage_log = OpenAIUsageLog(
        document_id=document.id,
        model="gpt-4o-mini",
    )
    audit_log = AuditLog(
        document_id=document.id,
        user_id=owner.id,
        action="test_action",
    )

    db.add_all([processing_job, usage_log, audit_log])
    db.commit()
    db.refresh(document)
    db.refresh(processing_job)
    db.refresh(usage_log)
    db.refresh(audit_log)

    file_path = save_document_file(
        content=b"stored content",
        storage_key=document.storage_key,
    )

    return document, file_path, processing_job, usage_log, audit_log


def test_delete_document_soft_deletes_database_record_and_keeps_file(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    document, file_path, processing_job, usage_log, audit_log = (
        _create_stored_document(db_session, test_user)
    )

    response = client.delete(
        f"/api/v1/documents/{document.id}",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""

    db_session.expire_all()

    deleted_document = db_session.get(Document, document.id)
    assert deleted_document is not None
    assert deleted_document.deleted_at is not None
    assert db_session.get(ProcessingJob, processing_job.id) is not None
    assert db_session.get(OpenAIUsageLog, usage_log.id) is not None
    assert db_session.get(AuditLog, audit_log.id) is not None
    assert file_path.exists()


def test_delete_document_preserves_linked_event_without_broken_document_link(
    db_session: Session,
    test_user: User,
) -> None:
    document, _, *_ = _create_stored_document(db_session, test_user)
    event = CalendarEvent(
        owner_id=test_user.id,
        document_id=document.id,
        title="Pay invoice",
        event_type=CalendarEventType.payment_due,
        source=CalendarEventSource.ai,
        status=CalendarEventStatus.confirmed,
        all_day=True,
        start_date=date(2026, 8, 1),
        source_evidence={},
    )
    db_session.add(event)
    db_session.commit()

    response = delete_document(
        document_id=document.id,
        db=db_session,
        current_user=test_user,
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    db_session.refresh(event)
    assert event.deleted_at is None
    assert event.document_id is None
    assert event.detached_from_source is True


def test_delete_document_hides_other_users_documents(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    document, file_path, *_ = _create_stored_document(
        db_session,
        test_user,
    )
    other_user = create_user(
        db=db_session,
        email="other@example.com",
        password="strong-password",
    )
    other_headers = {
        "Authorization": (
            f"Bearer {create_access_token(subject=str(other_user.id))}"
        ),
    }

    response = client.delete(
        f"/api/v1/documents/{document.id}",
        headers=other_headers,
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Document not found"}

    db_session.expire_all()

    assert db_session.get(Document, document.id) is not None
    assert file_path.exists()


def test_delete_document_requires_authentication(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    document, file_path, *_ = _create_stored_document(
        db_session,
        test_user,
    )

    response = client.delete(
        f"/api/v1/documents/{document.id}",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED

    db_session.expire_all()

    assert db_session.get(Document, document.id) is not None
    assert file_path.exists()
