"""Persist the administrator's download concurrency preference."""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("remote_download_settings",
                    sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("concurrency", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("remote_download_settings")
