import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class KnowledgeMessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class KnowledgeMessage(Base):
    __tablename__ = "knowledge_messages"
    __table_args__ = (
        Index("ix_knowledge_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[KnowledgeMessageRole] = mapped_column(
        Enum(KnowledgeMessageRole, name="knowledge_message_role"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    conversation = relationship("KnowledgeConversation", back_populates="messages")
    sources = relationship(
        "KnowledgeMessageSource",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="KnowledgeMessageSource.id",
    )
    openai_usage_logs = relationship(
        "OpenAIUsageLog",
        back_populates="message",
        cascade="all, delete-orphan",
    )
