"""add optional per-document user note

Revision ID: 0016_document_user_note
Revises: 0015_backup_recovery_key_id
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0016_document_user_note"
down_revision: str | None = "0015_backup_recovery_key_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("user_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "user_note")
