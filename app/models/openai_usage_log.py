from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OpenAIUsageLog(Base):
    __tablename__ = "openai_usage_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_messages.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    operation: Mapped[str] = mapped_column(
        String(100),
        default="document_ai_extraction",
        nullable=False,
    )

    model: Mapped[str] = mapped_column(String(100), nullable=False)

    response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document = relationship("Document", back_populates="openai_usage_logs")
    owner = relationship("User", back_populates="openai_usage_logs")
    conversation = relationship(
        "KnowledgeConversation",
        back_populates="openai_usage_logs",
    )
    message = relationship("KnowledgeMessage", back_populates="openai_usage_logs")
