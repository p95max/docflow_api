"""add read state for knowledge conversation messages

Revision ID: 0013_knowledge_message_read_state
Revises: 0012_knowledge_conversations
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013_knowledge_message_read_state"
down_revision: str | None = "0012_knowledge_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_conversations",
        sa.Column("last_read_assistant_message_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_conversations", "last_read_assistant_message_id")
