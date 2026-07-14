from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.schemas.knowledge import SemanticSearchResult
from app.services.embeddings import create_embeddings


def search_document_chunks(
    *,
    db: Session,
    owner_id: int,
    query: str,
    document_ids: list[int] | None,
    limit: int,
) -> list[SemanticSearchResult]:
    """Find nearest active standard-document chunks for a single user."""
    _require_postgresql(db)
    allowed_document_ids = _validate_document_ids(
        db=db,
        owner_id=owner_id,
        document_ids=document_ids,
    )
    if allowed_document_ids == []:
        return []

    query_embedding = create_embeddings(texts=[query]).vectors[0]
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
    conditions = [
        DocumentChunk.owner_id == owner_id,
        DocumentChunk.embedding.is_not(None),
        Document.owner_id == owner_id,
        Document.deleted_at.is_(None),
        Document.status == DocumentStatus.completed,
        Document.processing_mode == ProcessingMode.standard,
    ]
    if allowed_document_ids is not None:
        conditions.append(DocumentChunk.document_id.in_(allowed_document_ids))

    statement = (
        select(
            DocumentChunk,
            Document.original_filename,
            distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(*conditions)
        .order_by(distance, DocumentChunk.id)
        .limit(limit)
    )
    rows = db.execute(statement).all()

    return [
        SemanticSearchResult(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            filename=filename,
            page_from=chunk.page_from,
            page_to=chunk.page_to,
            snippet=chunk.content,
            score=round(1 - float(distance_value), 6),
        )
        for chunk, filename, distance_value in rows
    ]


def _validate_document_ids(
    *,
    db: Session,
    owner_id: int,
    document_ids: list[int] | None,
) -> list[int] | None:
    if document_ids is None:
        return None

    requested_ids = sorted(set(document_ids))
    if not requested_ids:
        return []

    statement = select(Document.id).where(
        Document.id.in_(requested_ids),
        Document.owner_id == owner_id,
        Document.deleted_at.is_(None),
        Document.status == DocumentStatus.completed,
        Document.processing_mode == ProcessingMode.standard,
    )
    allowed_ids = list(db.scalars(statement).all())
    if len(allowed_ids) != len(requested_ids):
        raise LookupError("One or more documents are not available for semantic search.")
    return allowed_ids


def _require_postgresql(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Semantic search requires PostgreSQL with pgvector enabled.")
