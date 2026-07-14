from fastapi import APIRouter, HTTPException, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.schemas.knowledge import SemanticSearchRequest, SemanticSearchResponse
from app.services.semantic_search import search_document_chunks


router = APIRouter()


@router.post("/search", response_model=SemanticSearchResponse)
def semantic_search(
    payload: SemanticSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SemanticSearchResponse:
    try:
        results = search_document_chunks(
            db=db,
            owner_id=current_user.id,
            query=payload.query,
            document_ids=payload.document_ids,
            limit=payload.limit,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return SemanticSearchResponse(results=results)
