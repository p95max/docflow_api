from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User


def test_document_list_api_returns_filtered_paginated_active_documents(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    test_user: User,
) -> None:
    visible = Document(
        owner_id=test_user.id,
        original_filename="action-invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        document_type="invoice",
        raw_text="Payment needs review",
        ai_extracted_data={"requires_action": True},
        amount=Decimal("125.00"),
        document_date=date(2026, 7, 1),
        deadline=date(2026, 7, 20),
    )
    deleted = Document(
        owner_id=test_user.id,
        original_filename="deleted-invoice.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        document_type="invoice",
        deleted_at=datetime.now(UTC),
    )
    db_session.add_all([visible, deleted])
    db_session.commit()

    response = client.get(
        "/api/v1/documents",
        params={
            "query": "review",
            "document_type": "invoice",
            "requires_action": "true",
            "amount_min": "100",
            "page": "1",
            "page_size": "10",
            "sort_by": "amount",
            "sort_direction": "asc",
        },
        headers=auth_headers,
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["total"] == 1
    assert response.json()["page"] == 1
    assert response.json()["total_pages"] == 1
    assert response.json()["items"][0]["id"] == visible.id


def test_documents_page_renders_search_controls(
    client: TestClient,
    test_user: User,
) -> None:
    login_response = client.post(
        "/login",
        data={"email": test_user.email, "password": "strong-password"},
        follow_redirects=False,
    )
    assert login_response.status_code == status.HTTP_303_SEE_OTHER

    response = client.get("/documents?query=invoice&sort_by=amount")

    assert response.status_code == status.HTTP_200_OK
    assert 'name="query"' in response.text
    assert 'name="requires_action"' in response.text
    assert 'name="sort_by"' in response.text
