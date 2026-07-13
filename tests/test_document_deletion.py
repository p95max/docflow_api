from pathlib import Path

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
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


def test_delete_document_removes_database_records_and_file(
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

    assert db_session.get(Document, document.id) is None
    assert db_session.get(ProcessingJob, processing_job.id) is None
    assert db_session.get(OpenAIUsageLog, usage_log.id) is None
    assert db_session.get(AuditLog, audit_log.id) is None
    assert not file_path.exists()


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
