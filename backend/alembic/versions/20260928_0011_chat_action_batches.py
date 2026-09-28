"""chat action batches

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-28 21:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_actions", sa.Column("batch_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_chat_actions_batch_id"), "chat_actions", ["batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_actions_batch_id"), table_name="chat_actions")
    op.drop_column("chat_actions", "batch_id")
