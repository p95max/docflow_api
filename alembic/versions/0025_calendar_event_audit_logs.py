"""support calendar event audit logs

Revision ID: 0025_calendar_event_audit_logs
Revises: 0024_user_timezone
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0025_calendar_event_audit_logs"
down_revision: str | None = "0024_user_timezone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "document_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "audit_logs",
        sa.Column("calendar_event_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_logs_calendar_event_id_calendar_events",
        "audit_logs",
        "calendar_events",
        ["calendar_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_logs_calendar_event_id",
        "audit_logs",
        ["calendar_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_calendar_event_id", table_name="audit_logs")
    op.drop_constraint(
        "fk_audit_logs_calendar_event_id_calendar_events",
        "audit_logs",
        type_="foreignkey",
    )
    op.drop_column("audit_logs", "calendar_event_id")
    op.alter_column(
        "audit_logs",
        "document_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
