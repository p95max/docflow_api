import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.knowledge_message import KnowledgeMessageRole


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    document_ids: list[int] | None = Field(default=None, max_length=100)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Query must not be blank")
        return normalized


class SemanticSearchResult(BaseModel):
    chunk_id: int
    document_id: int
    filename: str
    page_from: int
    page_to: int
    snippet: str
    score: float


class SemanticSearchResponse(BaseModel):
    results: list[SemanticSearchResult]


class KnowledgeConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class KnowledgeQuestionCreate(BaseModel):
    question: str = Field(min_length=1, max_length=300)

    @field_validator("question")
    @classmethod
    def validate_single_sentence_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Question must not be blank")

        sentence_endings = re.findall(r"[.!?]+(?=\s|$)", normalized)
        if len(sentence_endings) > 1:
            raise ValueError("Question must contain no more than one sentence")
        return normalized


class KnowledgeMessageSourceRead(BaseModel):
    id: int
    chunk_id: int
    document_id: int | None
    filename: str
    page_from: int
    page_to: int
    snippet: str
    score: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeMessageRead(BaseModel):
    id: int
    role: KnowledgeMessageRole
    content: str
    created_at: datetime
    sources: list[KnowledgeMessageSourceRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class KnowledgeConversationRead(BaseModel):
    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeConversationDetail(KnowledgeConversationRead):
    messages: list[KnowledgeMessageRead] = Field(default_factory=list)


class KnowledgeAnswerResponse(BaseModel):
    conversation_id: int
    user_message: KnowledgeMessageRead
    assistant_message: KnowledgeMessageRead
