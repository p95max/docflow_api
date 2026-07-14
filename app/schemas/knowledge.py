from pydantic import BaseModel, Field, field_validator


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
