"""add event reminders

Revision ID: 0026_event_reminders
Revises: 0025_calendar_event_audit_logs
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0026_event_reminders"
down_revision: str | None = "0025_calendar_event_audit_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


event_reminder_channel = postgresql.ENUM(
    "in_app",
    "email",
    name="event_reminder_channel",
    create_type=False,
)
event_reminder_status = postgresql.ENUM(
    "pending",
    "sending",
    "sent",
    "failed",
    "cancelled",
    name="event_reminder_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    event_reminder_channel.create(bind, checkfirst=True)
    event_reminder_status.create(bind, checkfirst=True)

    op.create_table(
        "event_reminders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column(
            "channel",
            event_reminder_channel,
            nullable=False,
        ),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            event_reminder_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("offset_minutes >= 0", name="ck_event_reminders_offset_nonnegative"),
        sa.CheckConstraint("attempts >= 0", name="ck_event_reminders_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["event_id"], ["calendar_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_event_reminders_event_channel_offset",
        "event_reminders",
        ["event_id", "channel", "offset_minutes"],
        unique=True,
    )
    op.create_index(
        "ix_event_reminders_status_scheduled_for",
        "event_reminders",
        ["status", "scheduled_for"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_reminders_status_scheduled_for", table_name="event_reminders")
    op.drop_index("uq_event_reminders_event_channel_offset", table_name="event_reminders")
    op.drop_table("event_reminders")

    bind = op.get_bind()
    event_reminder_status.drop(bind, checkfirst=True)
    event_reminder_channel.drop(bind, checkfirst=True)
