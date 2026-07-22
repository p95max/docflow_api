"""add private calendar feed token

Revision ID: 0028_calendar_feed_token
Revises: 0027_notifications
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0028_calendar_feed_token"
down_revision: str | None = "0027_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("calendar_feed_token_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_users_calendar_feed_token_hash", "users", ["calendar_feed_token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_calendar_feed_token_hash", table_name="users")
    op.drop_column("users", "calendar_feed_token_hash")
