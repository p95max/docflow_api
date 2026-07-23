from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import update

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.calendar_event import CalendarEvent
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
    # Soft deletion does not activate the database-level SET NULL rule. Keep
    # calendar history, but remove links that would lead the user to a deleted
    # document.
    db.execute(
        update(CalendarEvent)
        .where(
            CalendarEvent.owner_id == current_user.id,
            CalendarEvent.document_id == document.id,
            CalendarEvent.deleted_at.is_(None),
        )
        .values(document_id=None, detached_from_source=True)
    )
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
