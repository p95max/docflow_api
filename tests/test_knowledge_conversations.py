from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.services.knowledge_conversations as knowledge_conversations
from app.models.knowledge_message import KnowledgeMessage, KnowledgeMessageRole
from app.models.openai_usage_log import OpenAIUsageLog
from app.models.user import User
from app.schemas.knowledge import KnowledgeQuestionCreate, SemanticSearchResult
from app.services.knowledge_conversations import (
    UNVERIFIABLE_ANSWER_MESSAGE,
    answer_conversation_question,
    create_conversation,
    get_owned_conversation,
)
from app.services.users import create_user


class _FakeResponses:
    def __init__(self, answer: object) -> None:
        self.answer = answer

    def parse(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            id="resp_knowledge_test",
            model="gpt-5.6-terra",
            output_parsed=self.answer,
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=40,
                total_tokens=160,
            ),
        )


class _FakeClient:
    def __init__(self, answer: object) -> None:
        self.responses = _FakeResponses(answer)


def _search_result() -> SemanticSearchResult:
    return SemanticSearchResult(
        chunk_id=42,
        document_id=7,
        filename="invoice.pdf",
        page_from=2,
        page_to=2,
        snippet="Invoice total is 950 USD and payment is due 30 days after issue.",
        score=0.91,
        document_type="invoice",
        sender="YesLogic Pty. Ltd.",
        summary="Invoice for Prince Upgrades & Support.",
        amount=Decimal("950.00"),
        currency="USD",
        document_date=date(2016, 11, 26),
        deadline=date(2016, 12, 26),
    )


def test_question_schema_enforces_one_sentence() -> None:
    assert KnowledgeQuestionCreate(
        question="  What is due to YesLogic Pty. Ltd.?  "
    ).question == "What is due to YesLogic Pty. Ltd.?"

    with pytest.raises(ValidationError):
        KnowledgeQuestionCreate(question="x" * 301)
    with pytest.raises(ValidationError, match="one sentence"):
        KnowledgeQuestionCreate(question="Which invoices are due? Include overdue items.")


def test_rag_input_includes_current_date_and_structured_metadata() -> None:
    rag_input = knowledge_conversations._build_rag_input(
        question="When is invoice 161126 due?",
        history=[],
        sources=[_search_result()],
        current_date=date(2026, 7, 14),
    )

    assert "Current UTC date:\n2026-07-14" in rag_input
    assert "Structured metadata:" in rag_input
    assert "document_type: invoice" in rag_input
    assert "sender: YesLogic Pty. Ltd." in rag_input
    assert "amount: 950.00" in rag_input
    assert "currency: USD" in rag_input
    assert "document_date: 2016-11-26" in rag_input
    assert "deadline: 2016-12-26" in rag_input
    assert "Document text:" in rag_input


def test_rag_answer_persists_verified_sources_and_usage(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title="Invoices",
    )
    monkeypatch.setattr(
        knowledge_conversations,
        "search_document_chunks",
        lambda **_: [_search_result()],
    )
    monkeypatch.setattr(
        knowledge_conversations,
        "create_openai_client",
        lambda: _FakeClient(
            {
                "answer": "The invoice is due on 2016-12-26.",
                "source_chunk_ids": [42],
            }
        ),
    )

    user_message, assistant_message = answer_conversation_question(
        db=db_session,
        owner_id=test_user.id,
        conversation_id=conversation.id,
        question="When is the invoice due?",
    )

    assert user_message.role == KnowledgeMessageRole.user
    assert assistant_message.role == KnowledgeMessageRole.assistant
    assert assistant_message.content == "The invoice is due on 2016-12-26."
    assert len(assistant_message.sources) == 1
    source = assistant_message.sources[0]
    assert source.chunk_id == 42
    assert source.filename == "invoice.pdf"
    assert source.page_from == 2
    assert source.snippet == _search_result().snippet

    usage_log = db_session.scalar(
        select(OpenAIUsageLog).where(
            OpenAIUsageLog.message_id == assistant_message.id,
        )
    )
    assert usage_log is not None
    assert usage_log.document_id is None
    assert usage_log.owner_id == test_user.id
    assert usage_log.operation == "knowledge_answer"
    assert usage_log.total_tokens == 160


def test_invented_source_id_is_not_saved_or_returned(
    db_session: Session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title=None,
    )
    monkeypatch.setattr(
        knowledge_conversations,
        "search_document_chunks",
        lambda **_: [_search_result()],
    )
    monkeypatch.setattr(
        knowledge_conversations,
        "create_openai_client",
        lambda: _FakeClient(
            {
                "answer": "Unsupported answer.",
                "source_chunk_ids": [999],
            }
        ),
    )

    _, assistant_message = answer_conversation_question(
        db=db_session,
        owner_id=test_user.id,
        conversation_id=conversation.id,
        question="What is the invoice total?",
    )

    assert assistant_message.content == UNVERIFIABLE_ANSWER_MESSAGE
    assert assistant_message.sources == []


def test_conversations_are_isolated_by_owner(
    db_session: Session,
    test_user: User,
) -> None:
    conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title=None,
    )
    other_user = create_user(
        db=db_session,
        email="knowledge-other@example.com",
        password="strong-password",
    )

    with pytest.raises(LookupError):
        get_owned_conversation(
            db=db_session,
            owner_id=other_user.id,
            conversation_id=conversation.id,
        )


def test_history_is_limited_to_the_current_conversation(
    db_session: Session,
    test_user: User,
) -> None:
    conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title="First",
    )
    other_conversation = create_conversation(
        db=db_session,
        owner_id=test_user.id,
        title="Second",
    )
    current_message = KnowledgeMessage(
        conversation_id=conversation.id,
        role=KnowledgeMessageRole.user,
        content="Current question",
    )
    db_session.add_all(
        [
            KnowledgeMessage(
                conversation_id=conversation.id,
                role=KnowledgeMessageRole.user,
                content="First conversation message",
            ),
            KnowledgeMessage(
                conversation_id=other_conversation.id,
                role=KnowledgeMessageRole.user,
                content="Must never enter this history",
            ),
            current_message,
        ]
    )
    db_session.commit()

    history = knowledge_conversations._load_history(
        db=db_session,
        conversation_id=conversation.id,
        before_message_id=current_message.id,
    )

    assert [message.content for message in history] == ["First conversation message"]
