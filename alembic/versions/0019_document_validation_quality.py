"""add extraction validation candidates and OCR quality

Revision ID: 0019_document_validation_quality
Revises: 0018_document_validation_evidence
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0019_document_validation_quality"
down_revision: str | None = "0018_document_validation_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("validation_candidates", sa.JSON(), nullable=True))
    op.add_column("documents", sa.Column("validation_flags", sa.JSON(), nullable=True))
    op.add_column("documents", sa.Column("ocr_quality_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "ocr_quality_score")
    op.drop_column("documents", "validation_flags")
    op.drop_column("documents", "validation_candidates")
