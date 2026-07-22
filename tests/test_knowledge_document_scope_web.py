from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.web as web
from app.db.session import get_db
from app.main import app
from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.models.knowledge_conversation import KnowledgeConversation
from app.models.user import User
from app.services.security import create_access_token
from app.web import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME


def test_conversation_document_selector_limits_the_submitted_scope(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    document = Document(
        owner_id=test_user.id,
        original_filename="selected-invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Invoice content",
    )
    conversation = KnowledgeConversation(owner_id=test_user.id, title="Invoices")
    db_session.add_all([document, conversation])
    db_session.flush()
    db_session.add(
        DocumentIndexJob(
            document_id=document.id,
            status=DocumentIndexJobStatus.completed,
        )
    )
    db_session.commit()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        web,
        "answer_conversation_question",
        lambda **kwargs: captured.update(kwargs),
    )

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            csrf_token = "knowledge-document-scope-csrf-token"
            client.cookies.set(CSRF_COOKIE_NAME, csrf_token)
            client.headers["X-CSRF-Token"] = csrf_token
            client.cookies.set(
                SESSION_COOKIE_NAME,
                create_access_token(subject=str(test_user.id)),
            )
            page = client.get(f"/knowledge/conversations/{conversation.id}")
            response = client.post(
                f"/knowledge/conversations/{conversation.id}/messages",
                data={
                    "csrf_token": csrf_token,
                    "question": "What amount is on this invoice?",
                    "document_id": str(document.id),
                },
                follow_redirects=False,
            )
    finally:
        app.dependency_overrides.clear()

    assert page.status_code == status.HTTP_200_OK
    assert 'name="document_id"' in page.text
    assert "All indexed documents" in page.text
    assert "selected-invoice.pdf" in page.text
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert captured["document_id"] == document.id
