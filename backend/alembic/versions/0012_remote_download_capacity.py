"""Reserve disk capacity for parallel downloads.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("remote_imports", sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("remote_imports", "reserved_bytes")
