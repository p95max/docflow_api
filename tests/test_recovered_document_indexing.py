from types import TracebackType

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.tasks.knowledge as knowledge_tasks
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.user import User
from app.services.embeddings import EmbeddingResult


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


def test_index_task_uses_raw_text_for_restored_document_without_storage_key(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="restored-invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        storage_key=None,
        raw_text="Recovered invoice text available only from the backup archive.",
        document_type="invoice",
    )
    db_session.add(document)
    db_session.flush()
    job = DocumentIndexJob(document_id=document.id)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(
        knowledge_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )
    monkeypatch.setattr(
        knowledge_tasks,
        "extract_text_pages_from_document",
        lambda _: pytest.fail("restored documents must not require a stored file"),
    )
    monkeypatch.setattr(
        knowledge_tasks,
        "create_embeddings",
        lambda *, texts: EmbeddingResult(
            vectors=[[0.1, 0.2] for _ in texts],
            input_tokens=12,
            total_tokens=12,
        ),
    )

    result = knowledge_tasks.index_document_task.apply(args=(job.id,), throw=True)

    db_session.refresh(job)
    chunks = list(
        db_session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
    )

    assert result.successful()
    assert job.status == DocumentIndexJobStatus.completed
    assert job.error_message is None
    assert len(chunks) == 1
    assert chunks[0].page_from == 1
    assert chunks[0].page_to == 1
    assert "Recovered invoice text" in chunks[0].content
