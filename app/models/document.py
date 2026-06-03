import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ProcessingMode(str, enum.Enum):
    standard = "standard"
    confidential = "confidential"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "uq_documents_owner_checksum_sha256",
            "owner_id",
            "checksum_sha256",
            unique=True,
            postgresql_where=text("checksum_sha256 IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.uploaded,
        nullable=False,
    )

    processing_mode: Mapped[ProcessingMode] = mapped_column(
        Enum(ProcessingMode, name="processing_mode"),
        default=ProcessingMode.standard,
        nullable=False,
    )

    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        nullable=True,
    )

    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    owner = relationship("User", back_populates="documents")


    document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    ai_extracted_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    ai_extraction_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    ai_extraction_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    processing_jobs = relationship(
        "ProcessingJob",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    openai_usage_logs = relationship(
        "OpenAIUsageLog",
        back_populates="document",
        cascade="all, delete-orphan",
    )
