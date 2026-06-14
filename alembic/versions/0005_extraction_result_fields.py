"""add extraction result fields

Revision ID: 0005_add_extraction_result_fields
Revises: 0004_add_ai_processing_fields
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_extraction_result_fields"
down_revision: str | None = "0004_add_ai_processing_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("deadline", sa.Date(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("sender", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("confidence_score", sa.Float(), nullable=True),
    )

    op.create_check_constraint(
        "ck_documents_confidence_score_range",
        "documents",
        (
            "confidence_score IS NULL "
            "OR (confidence_score >= 0 AND confidence_score <= 1)"
        ),
    )

    op.create_index(
        op.f("ix_documents_amount"),
        "documents",
        ["amount"],
    )
    op.create_index(
        op.f("ix_documents_deadline"),
        "documents",
        ["deadline"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_documents_deadline"),
        table_name="documents",
    )
    op.drop_index(
        op.f("ix_documents_amount"),
        table_name="documents",
    )

    op.drop_constraint(
        "ck_documents_confidence_score_range",
        "documents",
        type_="check",
    )

    op.drop_column("documents", "confidence_score")
    op.drop_column("documents", "sender")
    op.drop_column("documents", "deadline")
    op.drop_column("documents", "currency")
    op.drop_column("documents", "amount")
    op.drop_column("documents", "summary")