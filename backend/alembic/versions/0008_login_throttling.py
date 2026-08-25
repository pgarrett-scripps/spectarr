"""Add persistent login throttling.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_throttles",
        sa.Column("username", sa.String(length=255), primary_key=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_throttles_locked_until", "login_throttles", ["locked_until"])


def downgrade() -> None:
    op.drop_index("ix_login_throttles_locked_until", table_name="login_throttles")
    op.drop_table("login_throttles")
