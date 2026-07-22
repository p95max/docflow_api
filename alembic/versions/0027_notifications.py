"""add in-app notifications

Revision ID: 0027_notifications
Revises: 0026_event_reminders
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0027_notifications"
down_revision: str | None = "0026_event_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["calendar_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_owner_id", "notifications", ["owner_id"])
    op.create_index("ix_notifications_event_id", "notifications", ["event_id"])
    op.create_index(
        "ix_notifications_owner_read_created",
        "notifications",
        ["owner_id", "read_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_owner_read_created", table_name="notifications")
    op.drop_index("ix_notifications_event_id", table_name="notifications")
    op.drop_index("ix_notifications_owner_id", table_name="notifications")
    op.drop_table("notifications")
