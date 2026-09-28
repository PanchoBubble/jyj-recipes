"""chat action timeout status

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28 20:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE chat_action_status ADD VALUE IF NOT EXISTS 'timeout'")


def downgrade() -> None:
    # Postgres cannot drop an enum value: fold timeouts into failed and rebuild the type.
    op.execute("ALTER TABLE chat_actions ALTER COLUMN status TYPE text")
    op.execute("UPDATE chat_actions SET status = 'failed' WHERE status = 'timeout'")
    op.execute("DROP TYPE chat_action_status")
    op.execute(
        "CREATE TYPE chat_action_status AS ENUM ('proposed', 'executed', 'rejected', 'failed')"
    )
    op.execute(
        "ALTER TABLE chat_actions ALTER COLUMN status TYPE chat_action_status "
        "USING status::chat_action_status"
    )
