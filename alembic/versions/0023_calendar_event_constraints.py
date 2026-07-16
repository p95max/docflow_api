"""add calendar event constraints and indexes

Revision ID: 0023_calendar_event_constraints
Revises: 0022_calendar_event_model
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0023_calendar_event_constraints"
down_revision: str | None = "0022_calendar_event_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_calendar_events_time_representation",
        "calendar_events",
        "(all_day = true AND start_date IS NOT NULL AND start_at IS NULL) "
        "OR (all_day = false AND start_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_calendar_events_date_range",
        "calendar_events",
        "end_date IS NULL OR (start_date IS NOT NULL AND end_date >= start_date)",
    )
    op.create_check_constraint(
        "ck_calendar_events_datetime_range",
        "calendar_events",
        "end_at IS NULL OR (start_at IS NOT NULL AND end_at >= start_at)",
    )
    op.create_index("ix_calendar_events_ical_uid", "calendar_events", ["ical_uid"], unique=True)
    op.create_index("ix_calendar_events_owner_status", "calendar_events", ["owner_id", "status"])
    op.create_index("ix_calendar_events_owner_deleted_at", "calendar_events", ["owner_id", "deleted_at"])
    op.create_index(
        "uq_calendar_events_owner_ai_source_key",
        "calendar_events",
        ["owner_id", "source_key"],
        unique=True,
        postgresql_where=sa.text("source = 'ai' AND source_key IS NOT NULL AND deleted_at IS NULL"),
        sqlite_where=sa.text("source = 'ai' AND source_key IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_calendar_events_owner_ai_source_key", table_name="calendar_events")
    op.drop_index("ix_calendar_events_owner_deleted_at", table_name="calendar_events")
    op.drop_index("ix_calendar_events_owner_status", table_name="calendar_events")
    op.drop_index("ix_calendar_events_ical_uid", table_name="calendar_events")
    op.drop_constraint("ck_calendar_events_datetime_range", "calendar_events", type_="check")
    op.drop_constraint("ck_calendar_events_date_range", "calendar_events", type_="check")
    op.drop_constraint("ck_calendar_events_time_representation", "calendar_events", type_="check")
