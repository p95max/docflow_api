"""add calendar event persistence model

Revision ID: 0022_calendar_event_model
Revises: 0021_backup_job_automatic
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0022_calendar_event_model"
down_revision: str | None = "0021_backup_job_automatic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


calendar_event_type = postgresql.ENUM(
    "payment_due",
    "response_deadline",
    "action_deadline",
    "appointment",
    "contract_start",
    "contract_end",
    "cancellation_deadline",
    "renewal",
    "custom",
    name="calendar_event_type",
    create_type=False,
)
calendar_event_status = postgresql.ENUM(
    "suggested",
    "confirmed",
    "completed",
    "cancelled",
    name="calendar_event_status",
    create_type=False,
)
calendar_event_source = postgresql.ENUM(
    "ai",
    "user",
    "system",
    "external",
    name="calendar_event_source",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    calendar_event_type.create(bind, checkfirst=True)
    calendar_event_status.create(bind, checkfirst=True)
    calendar_event_source.create(bind, checkfirst=True)

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "event_type",
            calendar_event_type,
            nullable=False,
        ),
        sa.Column(
            "status",
            calendar_event_status,
            server_default="suggested",
            nullable=False,
        ),
        sa.Column(
            "source",
            calendar_event_source,
            server_default="user",
            nullable=False,
        ),
        sa.Column("all_day", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("source_field", sa.String(length=100), nullable=True),
        sa.Column("source_key", sa.String(length=255), nullable=True),
        sa.Column("source_evidence", sa.JSON(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("requires_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("detached_from_source", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ical_uid", sa.String(length=255), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ical_uid"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("ix_calendar_events_document_id", "calendar_events", ["document_id"])
    op.create_index("ix_calendar_events_owner_id", "calendar_events", ["owner_id"])
    op.create_index("ix_calendar_events_public_id", "calendar_events", ["public_id"], unique=True)
    op.create_index("ix_calendar_events_owner_start_date", "calendar_events", ["owner_id", "start_date"])
    op.create_index("ix_calendar_events_owner_start_at", "calendar_events", ["owner_id", "start_at"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_owner_start_at", table_name="calendar_events")
    op.drop_index("ix_calendar_events_owner_start_date", table_name="calendar_events")
    op.drop_index("ix_calendar_events_public_id", table_name="calendar_events")
    op.drop_index("ix_calendar_events_owner_id", table_name="calendar_events")
    op.drop_index("ix_calendar_events_document_id", table_name="calendar_events")
    op.drop_table("calendar_events")

    bind = op.get_bind()
    calendar_event_source.drop(bind, checkfirst=True)
    calendar_event_status.drop(bind, checkfirst=True)
    calendar_event_type.drop(bind, checkfirst=True)
