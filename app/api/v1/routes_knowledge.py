from fastapi import APIRouter, HTTPException, status

from app.api.v1.dependencies import CurrentUser, DbSession
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
    get_owned_conversation,
    list_conversations,
)
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
    try:
        user_message, assistant_message = answer_conversation_question(
            db=db,
            owner_id=current_user.id,
            conversation_id=conversation_id,
            question=payload.question,
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
