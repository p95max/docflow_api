import logging

from fastapi import APIRouter, HTTPException, Response, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.models.document import Document
from app.services.storage import delete_document_file


logger = logging.getLogger(__name__)
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
    """Delete an owned document and its locally stored file."""
    document = db.get(Document, document_id)

    if document is None or document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    storage_key = document.storage_key

    db.delete(document)
    db.commit()

    try:
        delete_document_file(storage_key)
    except OSError:
        logger.exception(
            "Document %s was deleted from the database, but its file could not be removed",
            document_id,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
