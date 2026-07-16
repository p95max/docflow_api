"""add deterministic AI extraction validation fields

Revision ID: 0017_document_extraction_validation
Revises: 0016_document_user_note
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0017_document_extraction_validation"
down_revision: str | None = "0016_document_user_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("validation_status", sa.String(length=20), nullable=True))
    op.add_column("documents", sa.Column("validation_errors", sa.JSON(), nullable=True))
    op.add_column("documents", sa.Column("validation_warnings", sa.JSON(), nullable=True))
    op.add_column("documents", sa.Column("validation_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "validation_score")
    op.drop_column("documents", "validation_warnings")
    op.drop_column("documents", "validation_errors")
    op.drop_column("documents", "validation_status")
