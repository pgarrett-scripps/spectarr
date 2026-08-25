"""add versioned processing profiles and batches

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversion_recipes", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "conversion_recipes",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "conversion_recipes",
        sa.Column("system", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "processing_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_ids", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_processing_batches_scope_type"),
        "processing_batches",
        ["scope_type"],
        unique=False,
    )
    op.create_table(
        "processing_batch_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("input_artifact_id", sa.String(length=36), nullable=False),
        sa.Column("recipe_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["processing_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["input_artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recipe_id"], ["conversion_recipes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "input_artifact_id",
            "recipe_id",
            name="uq_processing_batch_artifact_recipe",
        ),
    )
    for column in ["batch_id", "run_id", "input_artifact_id", "recipe_id", "job_id"]:
        op.create_index(
            op.f(f"ix_processing_batch_items_{column}"),
            "processing_batch_items",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("processing_batch_items")
    op.drop_index(op.f("ix_processing_batches_scope_type"), table_name="processing_batches")
    op.drop_table("processing_batches")
    op.drop_column("conversion_recipes", "system")
    op.drop_column("conversion_recipes", "revision")
    op.drop_column("conversion_recipes", "description")
