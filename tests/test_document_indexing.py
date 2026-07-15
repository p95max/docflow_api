from types import TracebackType

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.tasks.knowledge as knowledge_tasks
import app.services.document_chunking as document_chunking
from app.core.config import settings
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.user import User
from app.services.document_chunking import build_document_chunk_drafts
from app.services.document_index_jobs import prepare_document_index_job
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


def test_chunking_keeps_complete_paragraphs_when_they_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        document_chunking,
        "_tokenize",
        lambda text: text.split(),
    )
    paragraphs = [
        "Alpha paragraph has three words.",
        "Beta paragraph has three words.",
        "Gamma paragraph has three words.",
    ]

    chunks = build_document_chunk_drafts(
        pages=[ExtractedTextPage(page_number=1, text="\n\n".join(paragraphs))],
        chunk_size_tokens=8,
        overlap_tokens=0,
    )

    assert [chunk.content for chunk in chunks] == paragraphs


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


def test_disabled_knowledge_does_not_create_an_index_job(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    document = _completed_document(test_user)
    db_session.add(document)
    db_session.commit()
    monkeypatch.setattr(settings, "knowledge_enabled", False)

    job = prepare_document_index_job(db=db_session, document=document)

    assert job is None
    assert db_session.scalar(
        select(DocumentIndexJob).where(DocumentIndexJob.document_id == document.id)
    ) is None


def test_failed_embedding_schedules_a_retry(
    db_session: Session,
    test_user: User,
) -> None:
    document = _completed_document(test_user)
    db_session.add(document)
    db_session.flush()
    job = DocumentIndexJob(document_id=document.id)
    db_session.add(job)
    db_session.commit()

    class RetryRequested(Exception):
        pass

    class FakeTask:
        max_retries = 3
        request = type("Request", (), {"retries": 0})()

        def retry(self, *, exc: Exception, countdown: int) -> None:
            assert str(exc) == "embedding service unavailable"
            assert countdown == settings.document_processing_retry_delay_seconds
            raise RetryRequested()

    with pytest.raises(RetryRequested):
        knowledge_tasks._handle_indexing_failure(
            task=FakeTask(),
            db=db_session,
            job_id=job.id,
            exc=RuntimeError("embedding service unavailable"),
        )

    db_session.refresh(job)
    assert job.status == DocumentIndexJobStatus.pending
    assert job.error_message == "embedding service unavailable"
    assert job.finished_at is None


def test_failed_embedding_marks_job_failed_after_last_retry(
    db_session: Session,
    test_user: User,
) -> None:
    document = _completed_document(test_user)
    db_session.add(document)
    db_session.flush()
    job = DocumentIndexJob(document_id=document.id)
    db_session.add(job)
    db_session.commit()

    class FakeTask:
        max_retries = 3
        request = type("Request", (), {"retries": 3})()

        def retry(self, **_: object) -> None:
            raise AssertionError("retry must not be scheduled after max retries")

    with pytest.raises(RuntimeError, match="embedding service unavailable"):
        knowledge_tasks._handle_indexing_failure(
            task=FakeTask(),
            db=db_session,
            job_id=job.id,
            exc=RuntimeError("embedding service unavailable"),
        )

    db_session.refresh(job)
    assert job.status == DocumentIndexJobStatus.failed
    assert job.error_message == "embedding service unavailable"
    assert job.finished_at is not None


def test_failed_embedding_schedules_a_retry(
    db_session: Session,
    test_user: User,
) -> None:
    document = _completed_document(test_user)
    db_session.add(document)
    db_session.flush()
    job = DocumentIndexJob(document_id=document.id)
    db_session.add(job)
    db_session.commit()

    class RetryRequested(Exception):
        pass

    class FakeTask:
        max_retries = 3
        request = type("Request", (), {"retries": 0})()

        def retry(self, *, exc: Exception, countdown: int) -> None:
            assert str(exc) == "embedding service unavailable"
            assert countdown == settings.document_processing_retry_delay_seconds
            raise RetryRequested()

    with pytest.raises(RetryRequested):
        knowledge_tasks._handle_indexing_failure(
            task=FakeTask(),
            db=db_session,
            job_id=job.id,
            exc=RuntimeError("embedding service unavailable"),
        )

    db_session.refresh(job)
    assert job.status == DocumentIndexJobStatus.pending
    assert job.error_message == "embedding service unavailable"
    assert job.finished_at is None


def test_failed_embedding_marks_job_failed_after_last_retry(
    db_session: Session,
    test_user: User,
) -> None:
    document = _completed_document(test_user)
    db_session.add(document)
    db_session.flush()
    job = DocumentIndexJob(document_id=document.id)
    db_session.add(job)
    db_session.commit()

    class FakeTask:
        max_retries = 3
        request = type("Request", (), {"retries": 3})()

        def retry(self, **_: object) -> None:
            raise AssertionError("retry must not be scheduled after max retries")

    with pytest.raises(RuntimeError, match="embedding service unavailable"):
        knowledge_tasks._handle_indexing_failure(
            task=FakeTask(),
            db=db_session,
            job_id=job.id,
            exc=RuntimeError("embedding service unavailable"),
        )

    db_session.refresh(job)
    assert job.status == DocumentIndexJobStatus.failed
    assert job.error_message == "embedding service unavailable"
    assert job.finished_at is not None
