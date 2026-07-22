"""add user email reminder preference

Revision ID: 0030_email_reminder_pref
Revises: 0029_reminder_email_fields
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0030_email_reminder_pref"
down_revision: str | None = "0029_reminder_email_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_reminders_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "email_reminders_enabled")
