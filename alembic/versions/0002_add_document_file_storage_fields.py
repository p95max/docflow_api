"""add document file storage fields

Revision ID: 0002_file_storage_fields
Revises: 0001_create_users_and_documents
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_file_storage_fields"
down_revision: str | None = "0001_create_users_and_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("content_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("storage_key", sa.String(length=500), nullable=True),
    )

    op.create_index(
        op.f("ix_documents_checksum_sha256"),
        "documents",
        ["checksum_sha256"],
        unique=False,
    )
    op.create_index(
        "uq_documents_owner_checksum_sha256",
        "documents",
        ["owner_id", "checksum_sha256"],
        unique=True,
        postgresql_where=sa.text("checksum_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_owner_checksum_sha256", table_name="documents")
    op.drop_index(op.f("ix_documents_checksum_sha256"), table_name="documents")

    op.drop_column("documents", "storage_key")
    op.drop_column("documents", "checksum_sha256")
    op.drop_column("documents", "file_size_bytes")
    op.drop_column("documents", "content_type")