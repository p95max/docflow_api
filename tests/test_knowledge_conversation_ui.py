from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.knowledge_conversation import KnowledgeConversation
from app.models.knowledge_message import KnowledgeMessage, KnowledgeMessageRole
from app.models.knowledge_message_source import KnowledgeMessageSource
from app.models.user import User


def _login(client: TestClient, user: User) -> None:
    response = client.post(
        "/login",
        data={"email": user.email, "password": "strong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_conversation_shows_newest_messages_first_and_collapses_sources(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Invoice source text",
    )
    conversation = KnowledgeConversation(
        owner_id=test_user.id,
        title="Invoices",
    )
    db_session.add_all([document, conversation])
    db_session.flush()

    older_message = KnowledgeMessage(
        conversation_id=conversation.id,
        role=KnowledgeMessageRole.user,
        content="Older message",
    )
    newer_message = KnowledgeMessage(
        conversation_id=conversation.id,
        role=KnowledgeMessageRole.assistant,
        content="Newest message",
    )
    db_session.add_all([older_message, newer_message])
    db_session.flush()
    db_session.add(
        KnowledgeMessageSource(
            message_id=newer_message.id,
            chunk_id=123,
            document_id=document.id,
            filename=document.original_filename,
            page_from=1,
            page_to=1,
            snippet="A compact source excerpt.",
            score=1.0,
        )
    )
    db_session.commit()
    _login(client, test_user)

    response = client.get(f"/knowledge/conversations/{conversation.id}")

    assert response.status_code == 200
    assert response.text.index("Newest message") < response.text.index("Older message")
    assert "Sources (1)" in response.text
    assert "Show source excerpt" in response.text
    assert f'href="/documents/{document.id}"' in response.text
    assert "<details" in response.text
    assert "<details open" not in response.text
