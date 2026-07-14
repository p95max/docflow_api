from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.openai_client import create_openai_client


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int | None
    total_tokens: int | None


def create_embeddings(*, texts: list[str]) -> EmbeddingResult:
    """Create embeddings in batches without exposing OpenAI SDK objects."""
    if not texts:
        return EmbeddingResult(vectors=[], input_tokens=0, total_tokens=0)

    client = create_openai_client()
    vectors: list[list[float]] = []
    input_tokens = 0
    total_tokens = 0

    for batch in _batches(texts, settings.document_indexing_batch_size):
        response = client.embeddings.create(
            model=settings.openai_embedding_model,
            input=batch,
            dimensions=settings.openai_embedding_dimensions,
        )
        vectors.extend(
            [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
        )
        usage = getattr(response, "usage", None)
        input_tokens += _read_usage(usage, "prompt_tokens") or 0
        total_tokens += _read_usage(usage, "total_tokens") or 0

    if len(vectors) != len(texts):
        raise ValueError("Embedding response did not include every requested text.")

    return EmbeddingResult(
        vectors=vectors,
        input_tokens=input_tokens or None,
        total_tokens=total_tokens or None,
    )


def _batches(values: list[str], size: int) -> list[list[str]]:
    if size < 1:
        raise ValueError("DOCUMENT_INDEXING_BATCH_SIZE must be positive")
    return [values[index : index + size] for index in range(0, len(values), size)]


def _read_usage(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) else None
