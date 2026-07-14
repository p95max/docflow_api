import pytest
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.user import User
from app.services.semantic_search import _validate_document_ids
from app.services.users import create_user


def _document(owner: User) -> Document:
    return Document(
        owner_id=owner.id,
        original_filename="searchable.pdf",
        status=DocumentStatus.completed,
        processing_mode=ProcessingMode.standard,
        raw_text="Indexed text",
    )


def test_requested_documents_must_belong_to_current_user(
    db_session: Session,
    test_user: User,
) -> None:
    owned_document = _document(test_user)
    other_user = create_user(
        db=db_session,
        email="semantic-other@example.com",
        password="strong-password",
    )
    other_document = _document(other_user)
    db_session.add_all([owned_document, other_document])
    db_session.commit()

    assert _validate_document_ids(
        db=db_session,
        owner_id=test_user.id,
        document_ids=[owned_document.id],
    ) == [owned_document.id]

    with pytest.raises(LookupError):
        _validate_document_ids(
            db=db_session,
            owner_id=test_user.id,
            document_ids=[other_document.id],
        )
