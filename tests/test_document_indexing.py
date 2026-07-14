from types import TracebackType

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.tasks.knowledge as knowledge_tasks
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.user import User
from app.services.document_chunking import build_document_chunk_drafts
from app.services.embeddings import EmbeddingResult
from app.services.text_extraction import ExtractedTextPage


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


def _completed_document(user: User) -> Document:
    return Document(
        owner_id=user.id,
        original_filename="indexed.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        content_type="application/pdf",
        raw_text="Indexed document text.",
    )


def test_chunking_preserves_page_numbers_and_overlap() -> None:
    pages = [
        ExtractedTextPage(
            page_number=1,
            text=" ".join(f"alpha-{index}" for index in range(80)),
        ),
        ExtractedTextPage(
            page_number=2,
            text=" ".join(f"beta-{index}" for index in range(80)),
        ),
    ]

    chunks = build_document_chunk_drafts(
        pages=pages,
        chunk_size_tokens=30,
        overlap_tokens=5,
    )

    assert len(chunks) > 2
    assert {chunk.page_from for chunk in chunks} == {1, 2}
    assert all(chunk.page_from == chunk.page_to for chunk in chunks)
    assert all(chunk.token_count <= 30 for chunk in chunks)
    assert chunks[0].content_sha256 != chunks[1].content_sha256


def test_index_task_persists_page_aware_chunks_and_usage_log(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    document = _completed_document(test_user)
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
        lambda _: [ExtractedTextPage(page_number=3, text="alpha " * 100)],
    )
    monkeypatch.setattr(
        knowledge_tasks,
        "create_embeddings",
        lambda *, texts: EmbeddingResult(
            vectors=[[0.1, 0.2] for _ in texts],
            input_tokens=42,
            total_tokens=42,
        ),
    )

    result = knowledge_tasks.index_document_task.apply(args=(job.id,), throw=True)

    db_session.refresh(job)
    chunks = list(
        db_session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    usage_log = db_session.scalar(
        select(OpenAIUsageLog).where(
            OpenAIUsageLog.document_id == document.id,
            OpenAIUsageLog.operation == "document_embedding",
        )
    )

    assert result.successful()
    assert job.status == DocumentIndexJobStatus.completed
    assert chunks
    assert all(chunk.owner_id == test_user.id for chunk in chunks)
    assert all(chunk.page_from == 3 for chunk in chunks)
    assert all(chunk.embedding_model for chunk in chunks)
    assert usage_log is not None
    assert usage_log.input_tokens == 42


def test_index_task_does_not_index_confidential_document(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    document = _completed_document(test_user)
    document.processing_mode = ProcessingMode.confidential
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
        "create_embeddings",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not embed")),
    )

    result = knowledge_tasks.index_document_task.apply(args=(job.id,), throw=True)

    db_session.refresh(job)
    assert result.successful()
    assert job.status == DocumentIndexJobStatus.failed
    assert "not eligible" in (job.error_message or "")


def test_index_task_skips_embedding_when_existing_index_matches(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    document = _completed_document(test_user)
    db_session.add(document)
    db_session.flush()
    job = DocumentIndexJob(document_id=document.id)
    db_session.add(job)
    db_session.commit()

    pages = [ExtractedTextPage(page_number=1, text="stable content " * 80)]
    drafts = build_document_chunk_drafts(pages=pages)
    db_session.add_all(
        [
            DocumentChunk(
                document_id=document.id,
                owner_id=test_user.id,
                chunk_index=draft.chunk_index,
                content=draft.content,
                page_from=draft.page_from,
                page_to=draft.page_to,
                token_count=draft.token_count,
                content_sha256=draft.content_sha256,
                embedding=[0.1, 0.2],
                embedding_model="text-embedding-3-small",
            )
            for draft in drafts
        ]
    )
    db_session.commit()

    monkeypatch.setattr(
        knowledge_tasks,
        "SessionLocal",
        lambda: SessionLocalOverride(db_session),
    )
    monkeypatch.setattr(
        knowledge_tasks,
        "extract_text_pages_from_document",
        lambda _: pages,
    )
    monkeypatch.setattr(
        knowledge_tasks,
        "create_embeddings",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not embed")),
    )

    result = knowledge_tasks.index_document_task.apply(args=(job.id,), throw=True)

    db_session.refresh(job)
    assert result.successful()
    assert job.status == DocumentIndexJobStatus.completed
