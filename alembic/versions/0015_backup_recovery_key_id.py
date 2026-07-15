"""track the recovery key used by each backup

Revision ID: 0015_backup_recovery_key_id
Revises: 0014_recovery_backup_key
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0015_backup_recovery_key_id"
down_revision: str | None = "0014_recovery_backup_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backup_jobs",
        sa.Column("recovery_key_id", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backup_jobs", "recovery_key_id")
