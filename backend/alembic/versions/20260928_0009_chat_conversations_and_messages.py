"""chat conversations and messages

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_chat_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_conversations")),
    )
    op.create_index(
        "ix_chat_conversations_user_id_updated_at",
        "chat_conversations",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_conversations_updated_at", "chat_conversations", ["updated_at"], unique=False
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", "tool", name="chat_role"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input", sa.Enum("text", "voice", name="chat_input"), nullable=True),
        sa.Column("transcript_confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "transcript_confidence IS NULL OR (transcript_confidence >= 0 AND "
            "transcript_confidence <= 1)",
            name=op.f("ck_chat_messages_transcript_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversations.id"],
            name=op.f("fk_chat_messages_conversation_id_chat_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(
        "ix_chat_messages_conversation_id",
        "chat_messages",
        ["conversation_id", "id"],
        unique=False,
    )
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"], unique=False)

    # Rows written before conversations existed point at nothing; keep them as audit only.
    op.execute("UPDATE chat_actions SET conversation_id = NULL")
    op.create_foreign_key(
        op.f("fk_chat_actions_conversation_id_chat_conversations"),
        "chat_actions",
        "chat_conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("chat_actions", sa.Column("message_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_chat_actions_message_id_chat_messages"),
        "chat_actions",
        "chat_messages",
        ["message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_chat_actions_message_id"), "chat_actions", ["message_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_actions_message_id"), table_name="chat_actions")
    op.drop_constraint(
        op.f("fk_chat_actions_message_id_chat_messages"), "chat_actions", type_="foreignkey"
    )
    op.drop_column("chat_actions", "message_id")
    op.drop_constraint(
        op.f("fk_chat_actions_conversation_id_chat_conversations"),
        "chat_actions",
        type_="foreignkey",
    )
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_conversation_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_conversations_updated_at", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_user_id_updated_at", table_name="chat_conversations")
    op.drop_table("chat_conversations")
    sa.Enum(name="chat_input").drop(op.get_bind())
    sa.Enum(name="chat_role").drop(op.get_bind())
