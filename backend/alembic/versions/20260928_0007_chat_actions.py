"""chat actions audit table

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("proposed", "executed", "rejected", "failed", name="chat_action_status"),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name=op.f("fk_chat_actions_confirmed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_chat_actions_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_actions")),
    )
    op.create_index(
        "ix_chat_actions_conversation_id", "chat_actions", ["conversation_id"], unique=False
    )
    op.create_index("ix_chat_actions_created_at", "chat_actions", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_actions_created_at", table_name="chat_actions")
    op.drop_index("ix_chat_actions_conversation_id", table_name="chat_actions")
    op.drop_table("chat_actions")
    sa.Enum(name="chat_action_status").drop(op.get_bind())
