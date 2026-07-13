"""add Google Drive JSON backup jobs

Revision ID: 0008_google_drive_json_backup
Revises: 0007_manual_correction
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_google_drive_json_backup"
down_revision: str | None = "0007_manual_correction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

backup_job_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    name="backup_job_status",
)


def upgrade() -> None:
    backup_job_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("status", backup_job_status_enum, nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("drive_folder_id", sa.String(length=255), nullable=True),
        sa.Column("drive_file_id", sa.String(length=255), nullable=True),
        sa.Column("drive_file_name", sa.String(length=255), nullable=True),
        sa.Column("drive_web_view_link", sa.String(length=1000), nullable=True),
        sa.Column(
            "content_type",
            sa.String(length=100),
            server_default="application/gzip",
            nullable=False,
        ),
        sa.Column("compressed_size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "record_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            ["owner_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_backup_jobs_id"), "backup_jobs", ["id"])
    op.create_index(op.f("ix_backup_jobs_owner_id"), "backup_jobs", ["owner_id"])
    op.create_index(op.f("ix_backup_jobs_status"), "backup_jobs", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_backup_jobs_status"), table_name="backup_jobs")
    op.drop_index(op.f("ix_backup_jobs_owner_id"), table_name="backup_jobs")
    op.drop_index(op.f("ix_backup_jobs_id"), table_name="backup_jobs")
    op.drop_table("backup_jobs")
    backup_job_status_enum.drop(op.get_bind(), checkfirst=True)
