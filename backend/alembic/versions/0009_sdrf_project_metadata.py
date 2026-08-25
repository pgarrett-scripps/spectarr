"""Add SDRF project metadata and multiplexed run samples.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence
from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from alembic import op


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_table(
        "run_samples",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sample_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "sample_id", "label", name="uq_run_sample_label"),
    )
    op.create_index("ix_run_samples_run_id", "run_samples", ["run_id"])
    op.create_index("ix_run_samples_sample_id", "run_samples", ["sample_id"])
    op.create_index("ix_run_samples_run_position", "run_samples", ["run_id", "position"])

    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    legacy = connection.execute(
        sa.text("SELECT id, sample_id FROM runs WHERE sample_id IS NOT NULL")
    ).mappings()
    rows = [
        {
            "id": str(uuid.uuid4()),
            "run_id": row["id"],
            "sample_id": row["sample_id"],
            "position": 0,
            "label": "label free sample",
            "role": "analyte",
            "metadata_json": {},
            "created_at": now,
            "updated_at": now,
        }
        for row in legacy
    ]
    if rows:
        run_samples = sa.table(
            "run_samples",
            sa.column("id", sa.String()),
            sa.column("run_id", sa.String()),
            sa.column("sample_id", sa.String()),
            sa.column("position", sa.Integer()),
            sa.column("label", sa.String()),
            sa.column("role", sa.String()),
            sa.column("metadata_json", sa.JSON()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        connection.execute(run_samples.insert(), rows)

    op.create_table(
        "sdrf_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("specification_version", sa.String(length=32), nullable=False),
        sa.Column("templates", sa.JSON(), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_filename", sa.String(length=1024), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("validation_engine", sa.String(length=255), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_sdrf_documents_project_id", "sdrf_documents", ["project_id"])
    op.create_index("ix_sdrf_documents_status", "sdrf_documents", ["status"])
    op.create_index("ix_sdrf_documents_content_sha256", "sdrf_documents", ["content_sha256"])
    op.create_table(
        "sdrf_rows",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("sample_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["sdrf_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("document_id", "position", name="uq_sdrf_document_position"),
    )
    for column in ["document_id", "sample_id", "run_id", "artifact_id"]:
        op.create_index(f"ix_sdrf_rows_{column}", "sdrf_rows", [column])


def downgrade() -> None:
    op.drop_table("sdrf_rows")
    op.drop_table("sdrf_documents")
    op.drop_table("run_samples")
    op.drop_column("projects", "metadata_json")
