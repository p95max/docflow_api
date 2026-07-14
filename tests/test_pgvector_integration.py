from types import SimpleNamespace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.services.knowledge_conversations as knowledge_conversations
import app.services.semantic_search as semantic_search
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.services.embeddings import EmbeddingResult
from app.services.knowledge_conversations import (
    answer_conversation_question,
    create_conversation,
)
from app.services.semantic_search import search_document_chunks
from app.services.users import create_user
from tests.pgvector_support import pgvector_session


pytestmark = pytest.mark.postgres


class _FakeResponses:
    def __init__(self, chunk_id: int) -> None:
        self.chunk_id = chunk_id

    def parse(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            id="resp_pgvector_test",
            model="gpt-5.6-terra",
            output_parsed={
                "answer": "The indexed invoice is due on 2026-08-01.",
                "source_chunk_ids": [self.chunk_id],
            },
            usage=SimpleNamespace(
                input_tokens=20,
                output_tokens=10,
                total_tokens=30,
            ),
        )


class _FakeClient:
    def __init__(self, chunk_id: int) -> None:
        self.responses = _FakeResponses(chunk_id)


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second, *([0.0] * 1534)]


def _document(owner: User, filename: str) -> Document:
    return Document(
        owner_id=owner.id,
        original_filename=filename,
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Indexed text",
    )


def _chunk(
    *,
    document: Document,
    owner: User,
    content: str,
    embedding: list[float],
) -> DocumentChunk:
    return DocumentChunk(
        document_id=document.id,
        owner_id=owner.id,
        chunk_index=0,
        content=content,
        page_from=1,
        page_to=1,
        token_count=10,
        content_sha256=f"{document.id:064x}",
        embedding=embedding,
        embedding_model="text-embedding-3-small",
    )


def _patch_query_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        semantic_search,
        "create_embeddings",
        lambda **_: EmbeddingResult(
            vectors=[_vector(1.0)],
            input_tokens=1,
            total_tokens=1,
        ),
    )


def test_pgvector_ranking_filters_other_users_and_soft_deleted_documents(
    pgvector_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(
        db=pgvector_session,
        email="pgvector-owner@example.com",
        password="strong-password",
    )
    other_user = create_user(
        db=pgvector_session,
        email="pgvector-other@example.com",
        password="strong-password",
    )
    best_document = _document(owner, "best.pdf")
    second_document = _document(owner, "second.pdf")
    other_document = _document(other_user, "other.pdf")
    deleted_document = _document(owner, "deleted.pdf")
    pgvector_session.add_all(
        [best_document, second_document, other_document, deleted_document]
    )
    pgvector_session.flush()
    deleted_document.deleted_at = datetime.now(UTC)
    pgvector_session.add_all(
        [
            _chunk(
                document=best_document,
                owner=owner,
                content="Best matching invoice is due on 2026-08-01.",
                embedding=_vector(1.0),
            ),
            _chunk(
                document=second_document,
                owner=owner,
                content="Second matching invoice is due on 2026-09-01.",
                embedding=_vector(0.8, 0.2),
            ),
            _chunk(
                document=other_document,
                owner=other_user,
                content="Other user's private invoice.",
                embedding=_vector(1.0),
            ),
            _chunk(
                document=deleted_document,
                owner=owner,
                content="Soft-deleted invoice.",
                embedding=_vector(1.0),
            ),
        ]
    )
    pgvector_session.commit()
    _patch_query_embedding(monkeypatch)

    results = search_document_chunks(
        db=pgvector_session,
        owner_id=owner.id,
        query="Which invoice is due first?",
        document_ids=None,
        limit=10,
    )

    assert [result.document_id for result in results] == [
        best_document.id,
        second_document.id,
    ]
    assert results[0].score > results[1].score
    assert all(result.document_id != other_document.id for result in results)
    assert all(result.document_id != deleted_document.id for result in results)


def test_pgvector_q_and_a_only_persists_owned_retrieved_sources(
    pgvector_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(
        db=pgvector_session,
        email="rag-owner@example.com",
        password="strong-password",
    )
    other_user = create_user(
        db=pgvector_session,
        email="rag-other@example.com",
        password="strong-password",
    )
    own_document = _document(owner, "owned.pdf")
    other_document = _document(other_user, "other.pdf")
    pgvector_session.add_all([own_document, other_document])
    pgvector_session.flush()
    own_chunk = _chunk(
        document=own_document,
        owner=owner,
        content="The invoice is due on 2026-08-01.",
        embedding=_vector(1.0),
    )
    pgvector_session.add_all(
        [
            own_chunk,
            _chunk(
                document=other_document,
                owner=other_user,
                content="The other user's invoice is due earlier.",
                embedding=_vector(1.0),
            ),
        ]
    )
    pgvector_session.commit()
    _patch_query_embedding(monkeypatch)
    monkeypatch.setattr(
        knowledge_conversations,
        "create_openai_client",
        lambda: _FakeClient(own_chunk.id),
    )
    conversation = create_conversation(
        db=pgvector_session,
        owner_id=owner.id,
        title="Invoices",
    )

    _, answer = answer_conversation_question(
        db=pgvector_session,
        owner_id=owner.id,
        conversation_id=conversation.id,
        question="When is the invoice due?",
    )

    assert [source.chunk_id for source in answer.sources] == [own_chunk.id]
    assert [source.document_id for source in answer.sources] == [own_document.id]


def test_pgvector_extension_and_document_chunk_indexes_exist(
    pgvector_session: Session,
) -> None:
    extension_name = pgvector_session.scalar(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )
    index_names = set(
        pgvector_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND tablename = 'document_chunks'"
            )
        ).all()
    )

    assert extension_name == "vector"
    assert "ix_document_chunks_owner_document" in index_names
