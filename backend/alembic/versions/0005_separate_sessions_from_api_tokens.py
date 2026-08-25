"""separate login sessions from API tokens

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_tokens",
        sa.Column("kind", sa.String(length=32), server_default="api", nullable=False),
    )
    op.create_index(op.f("ix_api_tokens_kind"), "api_tokens", ["kind"], unique=False)
    op.execute(
        "UPDATE api_tokens SET kind = 'session', revoked_at = CURRENT_TIMESTAMP "
        "WHERE name IN ('login', 'bootstrap')"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_tokens_kind"), table_name="api_tokens")
    op.drop_column("api_tokens", "kind")
