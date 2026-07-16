"""mark automatic backup jobs

Revision ID: 0021_backup_job_automatic
Revises: 0020_fallback_extraction
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0021_backup_job_automatic"
down_revision: str | None = "0020_fallback_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backup_jobs",
        sa.Column("is_automatic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_backup_jobs_is_automatic", "backup_jobs", ["is_automatic"])


def downgrade() -> None:
    op.drop_index("ix_backup_jobs_is_automatic", table_name="backup_jobs")
    op.drop_column("backup_jobs", "is_automatic")
