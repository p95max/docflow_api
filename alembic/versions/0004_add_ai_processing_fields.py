"""add ai processing fields

Revision ID: 0004_add_ai_processing_fields
Revises: 0003_processing_jobs
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_ai_processing_fields"
down_revision: str | None = "0003_processing_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("document_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "ai_extracted_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("ai_extraction_model", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("ai_extraction_completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        op.f("ix_documents_document_type"),
        "documents",
        ["document_type"],
    )

    op.create_table(
        "openai_usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation",
            sa.String(length=100),
            nullable=False,
            server_default="document_ai_extraction",
        ),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("response_id", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        op.f("ix_openai_usage_logs_id"),
        "openai_usage_logs",
        ["id"],
    )
    op.create_index(
        op.f("ix_openai_usage_logs_document_id"),
        "openai_usage_logs",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_openai_usage_logs_document_id"),
        table_name="openai_usage_logs",
    )
    op.drop_index(
        op.f("ix_openai_usage_logs_id"),
        table_name="openai_usage_logs",
    )
    op.drop_table("openai_usage_logs")

    op.drop_index(op.f("ix_documents_document_type"), table_name="documents")

    op.drop_column("documents", "ai_extraction_completed_at")
    op.drop_column("documents", "ai_extraction_model")
    op.drop_column("documents", "ai_extracted_data")
    op.drop_column("documents", "document_type")