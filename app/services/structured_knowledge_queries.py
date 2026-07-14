import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, ProcessingMode
from app.models.document_chunk import DocumentChunk
from app.models.document_index_job import DocumentIndexJob, DocumentIndexJobStatus
from app.schemas.knowledge import SemanticSearchResult


@dataclass(frozen=True)
class StructuredKnowledgeAnswer:
    answer: str
    sources: list[SemanticSearchResult]


def answer_structured_question(
    *,
    db: Session,
    owner_id: int,
    question: str,
    current_date: date | None = None,
) -> StructuredKnowledgeAnswer | None:
    """Answer deterministic metadata queries without vector retrieval or an LLM."""
    if not _is_current_month_invoice_deadline_question(question):
        return None

    resolved_date = current_date or datetime.now(UTC).date()
    month_start = resolved_date.replace(day=1)
    next_month_start = _next_month_start(month_start)

    documents = list(
        db.scalars(
            select(Document)
            .join(DocumentIndexJob, DocumentIndexJob.document_id == Document.id)
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(
                Document.owner_id == owner_id,
                Document.deleted_at.is_(None),
                Document.status == DocumentStatus.completed,
                Document.processing_mode == ProcessingMode.standard,
                Document.document_type == "invoice",
                Document.deadline >= month_start,
                Document.deadline < next_month_start,
                DocumentIndexJob.status == DocumentIndexJobStatus.completed,
            )
            .distinct()
            .order_by(Document.deadline, Document.id)
        ).all()
    )

    month_label = f"{month_start:%B} {month_start.year}"
    if not documents:
        return StructuredKnowledgeAnswer(
            answer=f"No indexed invoices are due in {month_label}.",
            sources=[],
        )

    sources = [
        _document_source(db=db, document=document)
        for document in documents
    ]
    verified_sources = [source for source in sources if source is not None]
    entries = "; ".join(_format_invoice(document) for document in documents)
    noun = "invoice is" if len(documents) == 1 else "invoices are"
    return StructuredKnowledgeAnswer(
        answer=(
            f"{len(documents)} indexed {noun} due in {month_label}: {entries}."
        ),
        sources=verified_sources,
    )


def _is_current_month_invoice_deadline_question(question: str) -> bool:
    normalized = " ".join(re.findall(r"[a-z0-9]+", question.casefold()))
    has_invoice = re.search(r"\binvoices?\b", normalized) is not None
    has_current_month = (
        re.search(r"\b(?:this|current) month\b", normalized) is not None
    )
    has_deadline_intent = (
        re.search(r"\b(?:due|deadline|deadlines|payable|payment)\b", normalized)
        is not None
    )
    return has_invoice and has_current_month and has_deadline_intent


def _next_month_start(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def _format_invoice(document: Document) -> str:
    parts = [document.original_filename]
    if document.sender:
        parts.append(f"from {document.sender}")
    if document.deadline:
        parts.append(f"due {_format_date(document.deadline)}")
    if document.amount is not None:
        amount = f"{document.amount:.2f}"
        parts.append(f"{document.currency or ''} {amount}".strip())
    return ", ".join(parts)


def _format_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def _document_source(
    *,
    db: Session,
    document: Document,
) -> SemanticSearchResult | None:
    chunk = db.scalar(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
        .limit(1)
    )
    if chunk is None:
        return None

    return SemanticSearchResult(
        chunk_id=chunk.id,
        document_id=document.id,
        filename=document.original_filename,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        snippet=chunk.content,
        score=1.0,
        document_type=document.document_type,
        sender=document.sender,
        summary=document.summary,
        amount=document.amount,
        currency=document.currency,
        document_date=document.document_date,
        deadline=document.deadline,
    )
