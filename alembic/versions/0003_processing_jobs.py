"""create processing jobs

Revision ID: 0003_processing_jobs
Revises: 0002_file_storage_fields
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_processing_jobs"
down_revision: str | None = "0002_file_storage_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    processing_job_status = postgresql.ENUM(
        "pending",
        "running",
        "completed",
        "failed",
        name="processing_job_status",
        create_type=False,
    )

    processing_operation_type = postgresql.ENUM(
        "text_extraction",
        name="processing_operation_type",
        create_type=False,
    )

    processing_job_status.create(op.get_bind(), checkfirst=True)
    processing_operation_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation_type",
            processing_operation_type,
            nullable=False,
            server_default="text_extraction",
        ),
        sa.Column(
            "status",
            processing_job_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        op.f("ix_processing_jobs_id"),
        "processing_jobs",
        ["id"],
    )
    op.create_index(
        op.f("ix_processing_jobs_document_id"),
        "processing_jobs",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_processing_jobs_status"),
        "processing_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_processing_jobs_status"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_document_id"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_id"), table_name="processing_jobs")

    op.drop_table("processing_jobs")

    processing_operation_type = postgresql.ENUM(
        "text_extraction",
        name="processing_operation_type",
        create_type=False,
    )

    processing_job_status = postgresql.ENUM(
        "pending",
        "running",
        "completed",
        "failed",
        name="processing_job_status",
        create_type=False,
    )

    processing_operation_type.drop(op.get_bind(), checkfirst=True)
    processing_job_status.drop(op.get_bind(), checkfirst=True)