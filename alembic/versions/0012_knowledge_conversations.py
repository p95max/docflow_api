"""add knowledge conversations and grounded answer sources

Revision ID: 0012_knowledge_conversations
Revises: 0011_knowledge_indexing
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012_knowledge_conversations"
down_revision: str | None = "0011_knowledge_indexing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_conversations_owner_updated",
        "knowledge_conversations",
        ["owner_id", "updated_at"],
    )
    op.create_index(
        op.f("ix_knowledge_conversations_owner_id"),
        "knowledge_conversations",
        ["owner_id"],
    )

    op.create_table(
        "knowledge_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("user", "assistant", name="knowledge_message_role"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["knowledge_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_messages_conversation_created",
        "knowledge_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        op.f("ix_knowledge_messages_conversation_id"),
        "knowledge_messages",
        ["conversation_id"],
    )

    op.create_table(
        "knowledge_message_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=False),
        sa.Column("page_to", sa.Integer(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["knowledge_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_message_sources_message",
        "knowledge_message_sources",
        ["message_id"],
    )

    op.alter_column(
        "openai_usage_logs",
        "document_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "openai_usage_logs",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "openai_usage_logs",
        sa.Column("conversation_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "openai_usage_logs",
        sa.Column("message_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_openai_usage_logs_owner_id"),
        "openai_usage_logs",
        ["owner_id"],
    )
    op.create_index(
        op.f("ix_openai_usage_logs_conversation_id"),
        "openai_usage_logs",
        ["conversation_id"],
    )
    op.create_index(
        op.f("ix_openai_usage_logs_message_id"),
        "openai_usage_logs",
        ["message_id"],
    )
    op.create_foreign_key(
        "fk_openai_usage_logs_owner_id_users",
        "openai_usage_logs",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_openai_usage_logs_conversation_id_knowledge_conversations",
        "openai_usage_logs",
        "knowledge_conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_openai_usage_logs_message_id_knowledge_messages",
        "openai_usage_logs",
        "knowledge_messages",
        ["message_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_openai_usage_logs_message_id_knowledge_messages",
        "openai_usage_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_openai_usage_logs_conversation_id_knowledge_conversations",
        "openai_usage_logs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_openai_usage_logs_owner_id_users",
        "openai_usage_logs",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_openai_usage_logs_message_id"), table_name="openai_usage_logs")
    op.drop_index(
        op.f("ix_openai_usage_logs_conversation_id"),
        table_name="openai_usage_logs",
    )
    op.drop_index(op.f("ix_openai_usage_logs_owner_id"), table_name="openai_usage_logs")
    op.drop_column("openai_usage_logs", "message_id")
    op.drop_column("openai_usage_logs", "conversation_id")
    op.drop_column("openai_usage_logs", "owner_id")
    op.alter_column(
        "openai_usage_logs",
        "document_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_index("ix_knowledge_message_sources_message", table_name="knowledge_message_sources")
    op.drop_table("knowledge_message_sources")
    op.drop_index(
        "ix_knowledge_messages_conversation_created",
        table_name="knowledge_messages",
    )
    op.drop_index(
        op.f("ix_knowledge_messages_conversation_id"),
        table_name="knowledge_messages",
    )
    op.drop_table("knowledge_messages")
    op.execute("DROP TYPE knowledge_message_role")
    op.drop_index(
        "ix_knowledge_conversations_owner_updated",
        table_name="knowledge_conversations",
    )
    op.drop_index(
        op.f("ix_knowledge_conversations_owner_id"),
        table_name="knowledge_conversations",
    )
    op.drop_table("knowledge_conversations")
