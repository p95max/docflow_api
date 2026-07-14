from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import String, asc, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus


DocumentSortField = Literal[
    "created_at",
    "document_date",
    "deadline",
    "amount",
    "original_filename",
    "document_type",
    "status",
]
SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class DocumentSearchFilters:
    query: str | None = None
    document_type: str | None = None
    status: DocumentStatus | None = None
    document_date_from: date | None = None
    document_date_to: date | None = None
    uploaded_from: date | None = None
    uploaded_to: date | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    deadline_from: date | None = None
    deadline_to: date | None = None
    requires_action: bool | None = None
    page: int = 1
    page_size: int = 25
    sort_by: DocumentSortField = "created_at"
    sort_direction: SortDirection = "desc"


def search_documents(
    *,
    db: Session,
    owner_id: int,
    filters: DocumentSearchFilters,
) -> tuple[list[Document], int]:
    """Return one owner's active documents and the matching total."""
    conditions = [
        Document.owner_id == owner_id,
        Document.deleted_at.is_(None),
    ]

    if filters.query:
        pattern = f"%{filters.query.strip()}%"
        conditions.append(
            or_(
                Document.original_filename.ilike(pattern),
                Document.raw_text.ilike(pattern),
                Document.document_type.ilike(pattern),
                Document.summary.ilike(pattern),
                Document.sender.ilike(pattern),
                cast(Document.ai_extracted_data, String).ilike(pattern),
            )
        )
    if filters.document_type:
        conditions.append(Document.document_type == filters.document_type)
    if filters.status:
        conditions.append(Document.status == filters.status)
    if filters.document_date_from:
        conditions.append(Document.document_date >= filters.document_date_from)
    if filters.document_date_to:
        conditions.append(Document.document_date <= filters.document_date_to)
    if filters.uploaded_from:
        conditions.append(
            Document.created_at >= _start_of_day(filters.uploaded_from)
        )
    if filters.uploaded_to:
        conditions.append(
            Document.created_at < _start_of_day(filters.uploaded_to + timedelta(days=1))
        )
    if filters.amount_min is not None:
        conditions.append(Document.amount >= filters.amount_min)
    if filters.amount_max is not None:
        conditions.append(Document.amount <= filters.amount_max)
    if filters.deadline_from:
        conditions.append(Document.deadline >= filters.deadline_from)
    if filters.deadline_to:
        conditions.append(Document.deadline <= filters.deadline_to)
    if filters.requires_action is not None:
        conditions.append(
            Document.ai_extracted_data["requires_action"].as_boolean().is_(
                filters.requires_action
            )
        )

    ordering_column = getattr(Document, filters.sort_by)
    ordering = (
        asc(ordering_column)
        if filters.sort_direction == "asc"
        else desc(ordering_column)
    )
    total = db.scalar(select(func.count()).select_from(Document).where(*conditions))
    statement = (
        select(Document)
        .where(*conditions)
        .order_by(ordering, Document.id.desc())
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    )
    return list(db.scalars(statement).all()), total or 0


def _start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)
