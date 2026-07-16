"""add validated extraction evidence

Revision ID: 0018_document_validation_evidence
Revises: 0017_document_extraction_validation
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0018_document_validation_evidence"
down_revision: str | None = "0017_document_extraction_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("validation_evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "validation_evidence")
