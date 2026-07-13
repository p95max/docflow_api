"""add manual correction workflow and audit log

Revision ID: 0007_manual_correction
Revises: 0006_document_result_view
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0007_manual_correction"
down_revision: str | None = "0006_document_result_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


extraction_status_enum = postgresql.ENUM(
    "draft",
    "confirmed",
    "corrected",
    name="extraction_status",
)


def upgrade() -> None:
    extraction_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "documents",
        sa.Column("document_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "extraction_status",
            extraction_status_enum,
            server_default="draft",
            nullable=False,
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "extraction_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Preserve corrections created by the document result endpoint before
    # the explicit extraction lifecycle was introduced.
    op.execute(
        """
        UPDATE documents
        SET extraction_status = 'corrected'
        WHERE manual_corrections IS NOT NULL
          AND manual_corrections <> '{}'::jsonb
        """
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=True),
        sa.Column(
            "old_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_logs_document_id"),
        "audit_logs",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_audit_logs_user_id"),
        "audit_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_audit_logs_document_created_at",
        "audit_logs",
        ["document_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_logs_document_created_at",
        table_name="audit_logs",
    )
    op.drop_index(
        op.f("ix_audit_logs_user_id"),
        table_name="audit_logs",
    )
    op.drop_index(
        op.f("ix_audit_logs_document_id"),
        table_name="audit_logs",
    )
    op.drop_table("audit_logs")

    op.drop_column("documents", "extraction_confirmed_at")
    op.drop_column("documents", "extraction_status")
    op.drop_column("documents", "document_date")

    extraction_status_enum.drop(op.get_bind(), checkfirst=True)
