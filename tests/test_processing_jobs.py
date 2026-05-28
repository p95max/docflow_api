import hashlib
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.v1.routes_documents as routes_documents
import app.services.processing_jobs as processing_jobs_service
import app.tasks.documents as document_tasks
from app.core.config import settings
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.processing_job import (
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingOperationType,
)
from app.models.user import User
from app.services.processing_jobs import (
    create_processing_job,
    enqueue_processing_job,
    list_processing_jobs_for_document,
)
from app.services.storage import save_document_file
from app.tasks.documents import process_document_task


PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 72 >>
stream
BT
/F1 24 Tf
72 720 Td
(Invoice number 12345) Tj
0 -30 Td
(Amount 99.95 EUR) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000241 00000 n 
0000000311 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
433
%%EOF
"""


class SessionLocalOverride:
    def __init__(self, db_session: Session) -> None:
        self.db_session = db_session

    def __enter__(self) -> Session:
        return self.db_session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


def test_create_processing_job_creates_pending_job(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
    )

    job = create_processing_job(
        db=db_session,
        document=document,
    )

    db_session.commit()
    db_session.refresh(job)

    assert job.document_id == document.id
    assert job.operation_type == ProcessingOperationType.text_extraction
    assert job.status == ProcessingJobStatus.pending
    assert job.attempts == 0
    assert job.max_retries == settings.document_processing_max_retries
    assert job.error_message is None
    assert job.celery_task_id is None


def test_enqueue_processing_job_stores_celery_task_id(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
    )
    job = create_processing_job(
        db=db_session,
        document=document,
    )
    db_session.commit()
    db_session.refresh(job)

    class FakeAsyncResult:
        id = "fake-celery-task-id"

    def fake_delay(job_id: int) -> FakeAsyncResult:
        assert job_id == job.id
        return FakeAsyncResult()

    monkeypatch.setattr(
        processing_jobs_service.process_document_task,
        "delay",
        fake_delay,
    )

    updated_job = enqueue_processing_job(
        db=db_session,
        job=job,
    )

    assert updated_job.celery_task_id == "fake-celery-task-id"


def test_process_document_task_completes_document_and_job(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))
    _patch_task_session(
        monkeypatch=monkeypatch,
        db_session=db_session,
    )

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
    )
    job = create_processing_job(
        db=db_session,
        document=document,
    )
    db_session.commit()
    db_session.refresh(job)

    result = process_document_task.apply(
        args=(job.id,),
        throw=True,
    )

    db_session.refresh(document)
    db_session.refresh(job)

    assert result.successful()
    assert document.status == DocumentStatus.completed
    assert document.raw_text is not None
    assert "Invoice number 12345" in document.raw_text
    assert "Amount 99.95 EUR" in document.raw_text

    assert job.status == ProcessingJobStatus.completed
    assert job.attempts == 1
    assert job.error_message is None
    assert job.started_at is not None
    assert job.finished_at is not None


def test_process_document_task_marks_failed_when_file_is_missing(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))
    _patch_task_session(
        monkeypatch=monkeypatch,
        db_session=db_session,
    )

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
    )

    stored_file_path = Path(settings.local_storage_path) / document.storage_key
    stored_file_path.unlink()

    job = create_processing_job(
        db=db_session,
        document=document,
    )
    job.max_retries = 0

    db_session.commit()
    db_session.refresh(job)

    result = process_document_task.apply(
        args=(job.id,),
        throw=False,
    )

    db_session.refresh(document)
    db_session.refresh(job)

    assert result.failed()
    assert document.status == DocumentStatus.failed
    assert job.status == ProcessingJobStatus.failed
    assert job.attempts == 1
    assert job.error_message is not None
    assert "Stored file does not exist" in job.error_message
    assert job.finished_at is not None


def test_list_processing_jobs_for_document_returns_document_jobs(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
    )

    first_job = create_processing_job(
        db=db_session,
        document=document,
    )
    second_job = create_processing_job(
        db=db_session,
        document=document,
    )

    db_session.commit()

    jobs = list_processing_jobs_for_document(
        db=db_session,
        document_id=document.id,
    )

    assert {job.id for job in jobs} == {first_job.id, second_job.id}


def test_reprocess_failed_document_creates_new_job(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
        status=DocumentStatus.failed,
    )

    failed_job = ProcessingJob(
        document_id=document.id,
        operation_type=ProcessingOperationType.text_extraction,
        status=ProcessingJobStatus.failed,
        attempts=1,
        max_retries=3,
        error_message="manual failure",
    )

    db_session.add(failed_job)
    db_session.commit()

    def fake_enqueue_processing_job(
        db: Session,
        job: ProcessingJob,
    ) -> ProcessingJob:
        job.celery_task_id = "fake-reprocess-task-id"
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    monkeypatch.setattr(
        routes_documents,
        "enqueue_processing_job",
        fake_enqueue_processing_job,
    )

    response = client.post(
        f"/api/v1/documents/{document.id}/reprocess",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_202_ACCEPTED

    payload = response.json()

    assert payload["document_id"] == document.id
    assert payload["operation_type"] == "text_extraction"
    assert payload["status"] == "pending"
    assert payload["celery_task_id"] == "fake-reprocess-task-id"
    assert payload["attempts"] == 0
    assert payload["error_message"] is None

    db_session.refresh(document)

    assert document.status == DocumentStatus.uploaded

    jobs = db_session.scalars(
        select(ProcessingJob).where(ProcessingJob.document_id == document.id),
    ).all()

    assert len(jobs) == 2


def test_reprocess_rejects_non_failed_document(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
        status=DocumentStatus.completed,
    )

    response = client.post(
        f"/api/v1/documents/{document.id}/reprocess",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "Only failed documents can be reprocessed.",
    }


def test_list_document_jobs_endpoint_returns_jobs(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
    )

    job = create_processing_job(
        db=db_session,
        document=document,
    )

    db_session.commit()
    db_session.refresh(job)

    response = client.get(
        f"/api/v1/documents/{document.id}/jobs",
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK

    payload = response.json()

    assert len(payload) == 1
    assert payload[0]["id"] == job.id
    assert payload[0]["document_id"] == document.id
    assert payload[0]["operation_type"] == "text_extraction"
    assert payload[0]["status"] == "pending"


def test_process_document_task_has_time_limits_and_retries() -> None:
    assert (
        process_document_task.soft_time_limit
        == settings.document_processing_soft_time_limit_seconds
    )
    assert (
        process_document_task.time_limit
        == settings.document_processing_hard_time_limit_seconds
    )
    assert process_document_task.max_retries == settings.document_processing_max_retries


def _patch_task_session(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    monkeypatch.setattr(
        document_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )


def _create_document_with_file(
    db: Session,
    user: User,
    content: bytes,
    status: DocumentStatus = DocumentStatus.uploaded,
    processing_mode: ProcessingMode = ProcessingMode.standard,
) -> Document:
    checksum = hashlib.sha256(content + str(status.value).encode()).hexdigest()

    document = Document(
        owner_id=user.id,
        original_filename="test-document.pdf",
        status=status,
        processing_mode=processing_mode,
        content_type="application/pdf",
        file_size_bytes=len(content),
        checksum_sha256=checksum,
    )

    db.add(document)
    db.flush()

    document.storage_key = f"users/{user.id}/documents/{document.id}/original.pdf"

    save_document_file(
        content=content,
        storage_key=document.storage_key,
    )

    db.commit()
    db.refresh(document)

    return document


def test_process_document_task_completes_confidential_document_locally(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))
    _patch_task_session(
        monkeypatch=monkeypatch,
        db_session=db_session,
    )

    document = _create_document_with_file(
        db=db_session,
        user=test_user,
        content=PDF_BYTES,
        processing_mode=ProcessingMode.confidential,
    )
    job = create_processing_job(
        db=db_session,
        document=document,
    )
    db_session.commit()
    db_session.refresh(job)

    result = process_document_task.apply(
        args=(job.id,),
        throw=True,
    )

    db_session.refresh(document)
    db_session.refresh(job)

    assert result.successful()
    assert document.processing_mode == ProcessingMode.confidential
    assert document.status == DocumentStatus.completed
    assert document.raw_text is not None
    assert "Invoice number 12345" in document.raw_text
    assert job.status == ProcessingJobStatus.completed