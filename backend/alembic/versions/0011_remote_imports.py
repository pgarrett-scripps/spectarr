"""Persist online repository imports.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "remote_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("experiment_id", sa.String(36), sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("accession", sa.String(32), nullable=False),
        sa.Column("file_id", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("run_name", sa.String(255), nullable=False),
        sa.Column("sample_name", sa.String(255), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("bytes_received", sa.BigInteger(), nullable=False),
        sa.Column("validator", sa.String(1024)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("batch_id", "project_id", "state"):
        op.create_index(f"ix_remote_imports_{name}", "remote_imports", [name])


def downgrade():
    op.drop_table("remote_imports")
