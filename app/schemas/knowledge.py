import re
from datetime import date, datetime
from decimal import Decimal

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
    document_type: str | None = None
    sender: str | None = None
    summary: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    document_date: date | None = None
    deadline: date | None = None


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
    document_id: int | None = Field(default=None, ge=1)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Question must not be blank")
        without_common_abbreviations = re.sub(
            r"\b(?:Mr|Mrs|Ms|Dr|Prof|Pty|Ltd|Inc|Corp|Co)\.",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        sentence_endings = re.findall(
            r"[.!?]+(?=\s|$)",
            without_common_abbreviations,
        )
        if len(sentence_endings) > 1:
            raise ValueError("Ask one sentence at a time")
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
