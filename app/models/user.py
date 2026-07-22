from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timezones import DEFAULT_USER_TIMEZONE
from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_reminders_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default=DEFAULT_USER_TIMEZONE,
        server_default=DEFAULT_USER_TIMEZONE,
        nullable=False,
    )
    backup_recovery_key_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    calendar_feed_token_hash: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    documents = relationship(
        "Document",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    audit_logs = relationship(
        "AuditLog",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    backup_jobs = relationship(
        "BackupJob",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    google_drive_connection = relationship(
        "GoogleDriveConnection",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    document_chunks = relationship(
        "DocumentChunk",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    knowledge_conversations = relationship(
        "KnowledgeConversation",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    openai_usage_logs = relationship(
        "OpenAIUsageLog",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    calendar_events = relationship(
        "CalendarEvent",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    notifications = relationship(
        "Notification",
        back_populates="owner",
        cascade="all, delete-orphan",
    )
