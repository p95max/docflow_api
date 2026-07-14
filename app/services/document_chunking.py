import hashlib
import re
from dataclasses import dataclass

import tiktoken

from app.core.config import settings
from app.services.text_extraction import ExtractedTextPage


_TOKENIZER_UNAVAILABLE = False


@dataclass(frozen=True)
class DocumentChunkDraft:
    chunk_index: int
    content: str
    page_from: int
    page_to: int
    token_count: int
    content_sha256: str


def build_document_chunk_drafts(
    *,
    pages: list[ExtractedTextPage],
    chunk_size_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[DocumentChunkDraft]:
    """Split page-aware extracted text into stable, overlapping token chunks."""
    chunk_size = chunk_size_tokens or settings.document_chunk_size_tokens
    overlap = overlap_tokens if overlap_tokens is not None else settings.document_chunk_overlap_tokens

    if chunk_size < 1:
        raise ValueError("chunk_size_tokens must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap_tokens must be between 0 and chunk_size_tokens")

    drafts: list[DocumentChunkDraft] = []

    for page in pages:
        page_text = page.text.strip()
        if not page_text:
            continue

        for token_window in _chunk_page_tokens(
            _tokenize(page_text),
            chunk_size=chunk_size,
            overlap=overlap,
        ):
            content = "".join(token_window).strip()
            if not content:
                continue

            drafts.append(
                DocumentChunkDraft(
                    chunk_index=len(drafts),
                    content=content,
                    page_from=page.page_number,
                    page_to=page.page_number,
                    token_count=len(token_window),
                    content_sha256=hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                )
            )

    return drafts


def _get_encoder() -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(settings.openai_embedding_model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def _tokenize(text: str) -> list[str]:
    """Use tiktoken when available, with an offline-safe fallback."""
    global _TOKENIZER_UNAVAILABLE

    if _TOKENIZER_UNAVAILABLE:
        return re.findall(r"\S+\s*", text)

    try:
        encoder = _get_encoder()
        return [encoder.decode([token]) for token in encoder.encode(text)]
    except Exception:
        # Corporate networks can block tiktoken's first encoding download.
        # This keeps chunk boundaries stable enough for indexing until its cache
        # becomes available, while avoiding an external call from this service.
        _TOKENIZER_UNAVAILABLE = True
        return re.findall(r"\S+\s*", text)


def _chunk_page_tokens(
    tokens: list[str],
    *,
    chunk_size: int,
    overlap: int,
) -> list[list[str]]:
    windows: list[list[str]] = []
    start = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        windows.append(tokens[start:end])

        if end == len(tokens):
            break

        start = end - overlap

    return windows
