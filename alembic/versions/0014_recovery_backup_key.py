"""add encrypted recovery key for backups

Revision ID: 0014_recovery_backup_key
Revises: 0013_message_read_state
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0014_recovery_backup_key"
down_revision: str | None = "0013_message_read_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("backup_recovery_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "backup_recovery_key_encrypted")
