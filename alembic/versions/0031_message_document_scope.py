"""store selected document scope on assistant messages

Revision ID: 0031_message_doc_scope
Revises: 0030_email_reminder_pref
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0031_message_doc_scope"
down_revision: str | None = "0030_email_reminder_pref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_messages") as batch_op:
        batch_op.add_column(sa.Column("scoped_document_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_knowledge_messages_scoped_document_id",
            "documents",
            ["scoped_document_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_knowledge_messages_scoped_document_id",
            ["scoped_document_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_messages") as batch_op:
        batch_op.drop_index("ix_knowledge_messages_scoped_document_id")
        batch_op.drop_constraint(
            "fk_knowledge_messages_scoped_document_id",
            type_="foreignkey",
        )
        batch_op.drop_column("scoped_document_id")
