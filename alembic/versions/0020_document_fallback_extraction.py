"""add separate validation fallback extraction result

Revision ID: 0020_document_fallback_extraction
Revises: 0019_document_validation_quality
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020_document_fallback_extraction"
down_revision: str | None = "0019_document_validation_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("fallback_extraction", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "fallback_extraction")
