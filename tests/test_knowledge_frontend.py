from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web as web
from app.core.config import settings
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.knowledge_conversation import KnowledgeConversation
from app.models.user import User


def _login(client: TestClient, user: User) -> None:
    response = client.post(
        "/login",
        data={"email": user.email, "password": "strong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_knowledge_page_is_server_rendered_and_lists_indexing_status(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    db_session.add_all(
        [
            Document(
                owner_id=test_user.id,
                original_filename="indexed.pdf",
                status=DocumentStatus.completed,
                processing_mode=ProcessingMode.standard,
                raw_text="Indexed document text",
            ),
            Document(
                owner_id=test_user.id,
                original_filename="private.pdf",
                status=DocumentStatus.completed,
                processing_mode=ProcessingMode.confidential,
                raw_text="Local-only text",
            ),
        ]
    )
    db_session.commit()
    _login(client, test_user)

    response = client.get("/knowledge")

    assert response.status_code == 200
    assert "Knowledge Base" in response.text
    assert "Document indexing" in response.text
    assert "Unavailable: confidential document" in response.text
    assert 'action="/knowledge/documents/' in response.text
    assert "<script" not in response.text


def test_knowledge_conversation_page_has_short_question_form(
    client: TestClient,
    test_user: User,
    db_session: Session,
) -> None:
    conversation = KnowledgeConversation(
        owner_id=test_user.id,
        title="Invoices",
    )
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    _login(client, test_user)

    response = client.get(f"/knowledge/conversations/{conversation.id}")

    assert response.status_code == 200
    assert 'maxlength="300"' in response.text
    assert "Which invoices are due this month?" in response.text
    assert "Ask a concise question" in response.text
    assert "<script" not in response.text


def test_knowledge_can_be_disabled_without_enqueuing_embeddings(
    client: TestClient,
    test_user: User,
    db_session: Session,
    monkeypatch,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="ready.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Ready for indexing",
    )
    db_session.add(document)
    db_session.commit()
    _login(client, test_user)
    monkeypatch.setattr(settings, "knowledge_enabled", False)

    documents_response = client.get("/documents")
    knowledge_response = client.get("/knowledge")
    reindex_response = client.post(
        f"/knowledge/documents/{document.id}/reindex",
        follow_redirects=False,
    )

    assert 'aria-disabled="true"' in documents_response.text
    assert 'href="/knowledge"' not in documents_response.text
    assert knowledge_response.status_code == 404
    assert reindex_response.status_code == 404
