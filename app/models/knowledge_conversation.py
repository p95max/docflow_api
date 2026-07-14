from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class KnowledgeConversation(Base):
    __tablename__ = "knowledge_conversations"
    __table_args__ = (
        Index("ix_knowledge_conversations_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner = relationship("User", back_populates="knowledge_conversations")
    messages = relationship(
        "KnowledgeMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="KnowledgeMessage.id",
    )
    openai_usage_logs = relationship(
        "OpenAIUsageLog",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
