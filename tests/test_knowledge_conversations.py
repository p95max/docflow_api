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
        snippet="Invoice total is 42 EUR and due on 2026-08-01.",
        score=0.91,
    )


def test_question_schema_limits_user_to_one_short_sentence() -> None:
    assert KnowledgeQuestionCreate(question="  Which invoices are due?  ").question == (
        "Which invoices are due?"
    )

    with pytest.raises(ValidationError):
        KnowledgeQuestionCreate(question="Which invoices are due? Which are overdue?")

    with pytest.raises(ValidationError):
        KnowledgeQuestionCreate(question="x" * 301)


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
                "answer": "The invoice is due on 2026-08-01.",
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
    assert assistant_message.content == "The invoice is due on 2026-08-01."
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
