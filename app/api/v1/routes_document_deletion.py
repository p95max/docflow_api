from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.document import Document
router = APIRouter()


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(
    document_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Soft-delete an owned document while retaining its audit trail."""
    document = db.get(Document, document_id)

    if (
        document is None
        or document.owner_id != current_user.id
        or document.deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    document.deleted_at = datetime.now(UTC)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
