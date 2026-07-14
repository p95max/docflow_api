from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import app.services.knowledge_conversations as knowledge_conversations
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.user import User
from app.services.knowledge_conversations import (
    answer_conversation_question,
    create_conversation,
)
from app.services.structured_knowledge_queries import answer_structured_question


def _invoice(
    *,
    db: Session,
    owner: User,
    filename: str,
    deadline: date,
    indexed: bool = True,
) -> Document:
    document = Document(
        owner_id=owner.id,
        original_filename=filename,
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Invoice text",
        document_type="invoice",
        sender="Example Vendor",
        amount=Decimal("125.00"),
        currency="EUR",
        document_date=date(2026, 7, 1),
        deadline=deadline,
    )
    db.add(document)
    db.flush()

    if indexed:
        db.add(
            DocumentIndexJob(
                document_id=document.id,
                status=DocumentIndexJobStatus.completed,
            )
        )
        db.add(
            DocumentChunk(
                document_id=document.id,
                owner_id=owner.id,
                chunk_index=0,
                content=f"{filename} is due on {deadline.isoformat()}.",
                page_from=1,
                page_to=1,
                token_count=8,
                content_sha256=("a" * 63) + str(document.id % 10),
                embedding=None,
                embedding_model=None,
            )
        )

    db.commit()
    db.refresh(document)
    return document


def test_current_month_invoice_query_checks_all_indexed_documents(
    db_session: Session,
    test_user: User,
) -> None:
    due_invoice = _invoice(
        db=db_session,
        owner=test_user,
        filename="due-july.pdf",
        deadline=date(2026, 7, 31),
    )
    _invoice(
        db=db_session,
        owner=test_user,
        filename="not-indexed.pdf",
        deadline=date(2026, 7, 20),
        indexed=False,
    )
    _invoice(
        db=db_session,
        owner=test_user,
        filename="due-august.pdf",
        deadline=date(2026, 8, 1),
    )

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="Which invoices are due this month?",
        current_date=date(2026, 7, 14),
    )

    assert result is not None
    assert result.answer.startswith("1 indexed invoice is due in July 2026")
    assert "due-july.pdf" in result.answer
    assert "not-indexed.pdf" not in result.answer
    assert "due-august.pdf" not in result.answer
    assert len(result.sources) == 1
    assert result.sources[0].document_id == due_invoice.id


def test_current_month_invoice_query_returns_verified_empty_result(
    db_session: Session,
    test_user: User,
) -> None:
    _invoice(
        db=db_session,
        owner=test_user,
        filename="old-invoice.pdf",
        deadline=date(2016, 12, 26),
    )

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="Which invoices are due in the current month?",
        current_date=date(2026, 7, 14),
    )

    assert result is not None
    assert result.answer == "No indexed invoices are due in July 2026."
    assert result.sources == []


def test_current_month_invoice_question_skips_embeddings_and_openai(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _invoice(
        db=db_session,
        owner=test_user,
        filename="due-july.pdf",
        deadline=date.today().replace(day=28),
    )
    conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title="Invoices",
    )

    monkeypatch.setattr(
        knowledge_conversations,
        "search_document_chunks",
        lambda **_: pytest.fail("semantic retrieval must not run"),
    )
    monkeypatch.setattr(
        knowledge_conversations,
        "create_openai_client",
        lambda: pytest.fail("OpenAI must not run"),
    )

    _, assistant_message = answer_conversation_question(
        db=db_session,
        owner_id=test_user.id,
        conversation_id=conversation.id,
        question="Which invoices are due this month?",
    )

    assert "indexed invoice" in assistant_message.content
    assert len(assistant_message.sources) == 1


def test_document_count_question_returns_active_and_indexed_totals(
    db_session: Session,
    test_user: User,
) -> None:
    _invoice(
        db=db_session,
        owner=test_user,
        filename="indexed.pdf",
        deadline=date(2026, 7, 31),
    )
    db_session.add(
        Document(
            owner_id=test_user.id,
            original_filename="confidential.pdf",
            status=DocumentStatus.completed,
            processing_mode=ProcessingMode.confidential,
            raw_text="Local only",
        )
    )
    deleted_document = Document(
        owner_id=test_user.id,
        original_filename="deleted.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Deleted",
        deleted_at=datetime.now(UTC),
    )
    db_session.add(deleted_document)
    db_session.commit()

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="How many docs are in my account?",
    )

    assert result is not None
    assert result.answer == (
        "You have 2 active documents in your account; "
        "1 document is indexed in the Knowledge Base."
    )
    assert result.sources == []


def test_indexed_document_count_question_returns_knowledge_base_total(
    db_session: Session,
    test_user: User,
) -> None:
    _invoice(
        db=db_session,
        owner=test_user,
        filename="indexed.pdf",
        deadline=date(2026, 7, 31),
    )
    _invoice(
        db=db_session,
        owner=test_user,
        filename="not-indexed.pdf",
        deadline=date(2026, 8, 1),
        indexed=False,
    )

    result = answer_structured_question(
        db=db_session,
        owner_id=test_user.id,
        question="How many indexed documents do I have?",
    )

    assert result is not None
    assert result.answer == "You have 1 indexed document in the Knowledge Base."
    assert result.sources == []


def test_document_count_question_skips_embeddings_and_openai(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _invoice(
        db=db_session,
        owner=test_user,
        filename="indexed.pdf",
        deadline=date(2026, 7, 31),
    )
    conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title="Documents",
    )

    monkeypatch.setattr(
        knowledge_conversations,
        "search_document_chunks",
        lambda **_: pytest.fail("semantic retrieval must not run"),
    )
    monkeypatch.setattr(
        knowledge_conversations,
        "create_openai_client",
        lambda: pytest.fail("OpenAI must not run"),
    )

    _, assistant_message = answer_conversation_question(
        db=db_session,
        owner_id=test_user.id,
        conversation_id=conversation.id,
        question="How many docs in my account?",
    )

    assert assistant_message.content == (
        "You have 1 active document in your account; "
        "1 document is indexed in the Knowledge Base."
    )
    assert assistant_message.sources == []