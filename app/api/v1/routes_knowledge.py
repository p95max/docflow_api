from fastapi import APIRouter, HTTPException, status

from app.api.v1.dependencies import CurrentUser, DbSession
from app.core.config import settings
from app.schemas.knowledge import (
    KnowledgeAnswerResponse,
    KnowledgeConversationCreate,
    KnowledgeConversationDetail,
    KnowledgeConversationRead,
    KnowledgeMessageRead,
    KnowledgeQuestionCreate,
    SemanticSearchRequest,
    SemanticSearchResponse,
)
from app.services.knowledge_conversations import (
    answer_conversation_question,
    create_conversation,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
)
from app.services.rate_limits import (
    enforce_knowledge_question_rate_limit,
    enforce_semantic_search_rate_limit,
)
from app.services.semantic_search import search_document_chunks


router = APIRouter()


def _require_knowledge_enabled() -> None:
    if not settings.knowledge_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge Base is disabled.",
        )


@router.post("/search", response_model=SemanticSearchResponse)
def semantic_search(
    payload: SemanticSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SemanticSearchResponse:
    _require_knowledge_enabled()
    enforce_semantic_search_rate_limit(user_id=current_user.id)
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


@router.post(
    "/conversations",
    response_model=KnowledgeConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_conversation(
    payload: KnowledgeConversationCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> KnowledgeConversationRead:
    _require_knowledge_enabled()
    return create_conversation(
        db=db,
        owner_id=current_user.id,
        title=payload.title,
    )


@router.get("/conversations", response_model=list[KnowledgeConversationRead])
def list_knowledge_conversations(
    db: DbSession,
    current_user: CurrentUser,
) -> list[KnowledgeConversationRead]:
    _require_knowledge_enabled()
    return list_conversations(db=db, owner_id=current_user.id)


@router.get(
    "/conversations/{conversation_id}",
    response_model=KnowledgeConversationDetail,
)
def get_knowledge_conversation(
    conversation_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> KnowledgeConversationDetail:
    _require_knowledge_enabled()
    try:
        return get_owned_conversation(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_knowledge_conversation(
    conversation_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    _require_knowledge_enabled()
    try:
        delete_conversation(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=KnowledgeAnswerResponse,
)
def ask_knowledge_question(
    conversation_id: int,
    payload: KnowledgeQuestionCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> KnowledgeAnswerResponse:
    _require_knowledge_enabled()
    enforce_knowledge_question_rate_limit(user_id=current_user.id)
    try:
        user_message, assistant_message = answer_conversation_question(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
            question=payload.question,
            document_id=payload.document_id,
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

    return KnowledgeAnswerResponse(
        conversation_id=conversation_id,
        user_message=KnowledgeMessageRead.model_validate(user_message),
        assistant_message=KnowledgeMessageRead.model_validate(assistant_message),
    )
