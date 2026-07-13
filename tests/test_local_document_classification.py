import hashlib
from pathlib import Path
from types import TracebackType

import pytest
from sqlalchemy.orm import Session

import app.tasks.documents as document_tasks
from app.core.config import settings
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.processing_job import ProcessingJobStatus
from app.models.user import User
from app.services.local_document_classification import classify_document_type
from app.services.processing_jobs import create_processing_job
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


def test_classify_document_type_detects_invoice() -> None:
    raw_text = """
    Invoice Number: #20130304
    Subtotal $36.00
    GST (10%) $3.60
    Total $39.60
    """

    assert classify_document_type(raw_text) == "invoice"


def test_classify_document_type_detects_german_letter() -> None:
    raw_text = """
    Sehr geehrte Damen und Herren,
    Betreff: Ihre Anfrage
    Mit freundlichen Grüßen
    """

    assert classify_document_type(raw_text) == "letter"


def test_classify_document_type_returns_other_for_unknown_or_tied_text() -> None:
    assert classify_document_type("Unstructured notes without known markers") == "other"
    assert classify_document_type("Receipt contract") == "other"


def test_confidential_processing_saves_local_document_type(
    db_session: Session,
    test_user: User,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path / "storage"))
    monkeypatch.setattr(
        document_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )

    document = Document(
        owner_id=test_user.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.uploaded,
        processing_mode=ProcessingMode.confidential,
        content_type="application/pdf",
        file_size_bytes=len(PDF_BYTES),
        checksum_sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
    )
    db_session.add(document)
    db_session.flush()

    document.storage_key = (
        f"users/{test_user.id}/documents/{document.id}/original.pdf"
    )
    save_document_file(
        content=PDF_BYTES,
        storage_key=document.storage_key,
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
    assert document.document_type == "invoice"
    assert document.ai_extracted_data is None
    assert document.ai_extraction_model is None
    assert job.status == ProcessingJobStatus.completed
