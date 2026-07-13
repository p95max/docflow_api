"""add per-user Google Drive OAuth connections

Revision ID: 0009_google_drive_oauth_connections
Revises: 0008_google_drive_json_backup
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_google_drive_oauth_connections"
down_revision: str | None = "0008_google_drive_json_backup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_drive_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_google_drive_connections_id"),
        "google_drive_connections",
        ["id"],
    )
    op.create_index(
        op.f("ix_google_drive_connections_user_id"),
        "google_drive_connections",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_google_drive_connections_user_id"),
        table_name="google_drive_connections",
    )
    op.drop_index(
        op.f("ix_google_drive_connections_id"),
        table_name="google_drive_connections",
    )
    op.drop_table("google_drive_connections")
