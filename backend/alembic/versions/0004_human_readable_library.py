"""add human readable library paths

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("library_path", sa.String(length=2048), nullable=True))
    op.add_column("artifacts", sa.Column("materialization_mode", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_artifacts_library_path"), "artifacts", ["library_path"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_artifacts_library_path"), table_name="artifacts")
    op.drop_column("artifacts", "materialization_mode")
    op.drop_column("artifacts", "library_path")
