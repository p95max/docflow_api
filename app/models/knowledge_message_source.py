from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class KnowledgeMessageSource(Base):
    __tablename__ = "knowledge_message_sources"
    __table_args__ = (
        Index("ix_knowledge_message_sources_message", "message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Chunk IDs are snapshots rather than foreign keys: reindexing may replace chunks.
    chunk_id: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    page_from: Mapped[int] = mapped_column(Integer, nullable=False)
    page_to: Mapped[int] = mapped_column(Integer, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    message = relationship("KnowledgeMessage", back_populates="sources")
