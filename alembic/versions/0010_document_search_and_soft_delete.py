"""add document soft deletion and search indexes

Revision ID: 0010_document_search
Revises: 0009_google_drive_oauth
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010_document_search"
down_revision: str | None = "0009_google_drive_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_documents_deleted_at"),
        "documents",
        ["deleted_at"],
    )
    op.drop_index(
        "uq_documents_owner_checksum_sha256",
        table_name="documents",
    )
    op.create_index(
        "uq_documents_owner_checksum_sha256",
        "documents",
        ["owner_id", "checksum_sha256"],
        unique=True,
        postgresql_where=sa.text(
            "checksum_sha256 IS NOT NULL AND deleted_at IS NULL"
        ),
    )
    op.create_index(
        "ix_documents_owner_active_created_at",
        "documents",
        ["owner_id", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_documents_owner_active_document_type",
        "documents",
        ["owner_id", "document_type"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_documents_owner_active_status",
        "documents",
        ["owner_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_documents_owner_active_status", table_name="documents")
    op.drop_index(
        "ix_documents_owner_active_document_type",
        table_name="documents",
    )
    op.drop_index(
        "ix_documents_owner_active_created_at",
        table_name="documents",
    )
    op.drop_index(op.f("ix_documents_deleted_at"), table_name="documents")
    op.drop_index(
        "uq_documents_owner_checksum_sha256",
        table_name="documents",
    )
    op.create_index(
        "uq_documents_owner_checksum_sha256",
        "documents",
        ["owner_id", "checksum_sha256"],
        unique=True,
        postgresql_where=sa.text("checksum_sha256 IS NOT NULL"),
    )
    op.drop_column("documents", "deleted_at")
