"""create users and documents

Revision ID: 0001_create_users_and_documents
Revises:
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_create_users_and_documents"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    document_status = postgresql.ENUM(
        "uploaded",
        "processing",
        "completed",
        "failed",
        name="document_status",
        create_type=False,
    )

    processing_mode = postgresql.ENUM(
        "standard",
        "confidential",
        name="processing_mode",
        create_type=False,
    )

    document_status.create(op.get_bind(), checkfirst=True)
    processing_mode.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        op.f("ix_users_id"),
        "users",
        ["id"],
    )

    op.create_index(
        op.f("ix_users_email"),
        "users",
        ["email"],
        unique=True,
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            document_status,
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column(
            "processing_mode",
            processing_mode,
            nullable=False,
            server_default="standard",
        ),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        op.f("ix_documents_id"),
        "documents",
        ["id"],
    )

    op.create_index(
        op.f("ix_documents_owner_id"),
        "documents",
        ["owner_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_owner_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_id"), table_name="documents")
    op.drop_table("documents")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")

    processing_mode = postgresql.ENUM(
        "standard",
        "confidential",
        name="processing_mode",
        create_type=False,
    )

    document_status = postgresql.ENUM(
        "uploaded",
        "processing",
        "completed",
        "failed",
        name="document_status",
        create_type=False,
    )

    processing_mode.drop(op.get_bind(), checkfirst=True)
    document_status.drop(op.get_bind(), checkfirst=True)