"""add email delivery fields to event reminders

Revision ID: 0029_event_reminder_email_delivery_fields
Revises: 0028_calendar_feed_token
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0029_event_reminder_email_delivery_fields"
down_revision: str | None = "0028_calendar_feed_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("event_reminders", sa.Column("recipient_email", sa.String(length=320), nullable=True))
    op.add_column("event_reminders", sa.Column("provider_message_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("event_reminders", "provider_message_id")
    op.drop_column("event_reminders", "recipient_email")
