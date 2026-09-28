"""recipe photo credit

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-28 23:30:00.000000+00:00

Attribution for photos imported from a stock photo search: provider, photographer and links.
Null for uploaded photos and recipes without one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("photo_credit", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("recipes", "photo_credit")
