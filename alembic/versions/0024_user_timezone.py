"""add user timezone

Revision ID: 0024_user_timezone
Revises: 0023_calendar_event_constraints
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0024_user_timezone"
down_revision: str | None = "0023_calendar_event_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'Europe/Berlin'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "timezone")
