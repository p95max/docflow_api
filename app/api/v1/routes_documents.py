from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.document import Document
from app.schemas.document import DocumentRead

router = APIRouter()


@router.get("", response_model=list[DocumentRead])
def list_my_documents(
    db: DbSession,
    current_user: CurrentUser,
) -> list[DocumentRead]:
    stmt = (
        select(Document)
        .where(Document.owner_id == current_user.id)
        .order_by(Document.created_at.desc())
    )

    return list(db.scalars(stmt).all())


@router.get("/{document_id}", response_model=DocumentRead)
def get_my_document(
    document_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> DocumentRead:
    document = db.get(Document, document_id)

    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return document