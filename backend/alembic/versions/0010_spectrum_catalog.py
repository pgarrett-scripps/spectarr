"""Add persistent spectrum catalogs.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spectrum_catalogs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("extractor", sa.String(length=255), nullable=False),
        sa.Column("extractor_version", sa.String(length=100), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("spectrum_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_spectrum_catalogs_artifact_id", "spectrum_catalogs", ["artifact_id"])
    op.create_index(
        "ix_spectrum_catalog_artifact_status",
        "spectrum_catalogs",
        ["artifact_id", "status", "created_at"],
    )
    op.create_table(
        "spectrum_catalog_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("catalog_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("ms_level_index", sa.Integer(), nullable=False),
        sa.Column("native_id", sa.String(length=2048), nullable=True),
        sa.Column("scan_number", sa.Integer(), nullable=True),
        sa.Column("ms_level", sa.Integer(), nullable=False),
        sa.Column("retention_time_seconds", sa.Float(), nullable=True),
        sa.Column("precursor_mz", sa.Float(), nullable=True),
        sa.Column("precursor_charge", sa.Integer(), nullable=True),
        sa.Column("neutral_mass", sa.Float(), nullable=True),
        sa.Column("isolation_lower_mz", sa.Float(), nullable=True),
        sa.Column("isolation_upper_mz", sa.Float(), nullable=True),
        sa.Column("peak_count", sa.Integer(), nullable=True),
        sa.Column("total_ion_current", sa.Float(), nullable=True),
        sa.Column("base_peak_mz", sa.Float(), nullable=True),
        sa.Column("base_peak_intensity", sa.Float(), nullable=True),
        sa.Column("mz_min", sa.Float(), nullable=True),
        sa.Column("mz_max", sa.Float(), nullable=True),
        sa.Column("polarity", sa.String(length=32), nullable=True),
        sa.Column("representation", sa.String(length=32), nullable=True),
        sa.Column("collision_energy", sa.Float(), nullable=True),
        sa.Column("activation_type", sa.String(length=100), nullable=True),
        sa.Column("ion_mobility", sa.Float(), nullable=True),
        sa.Column("ion_mobility_unit", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["catalog_id"], ["spectrum_catalogs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("catalog_id", "ordinal", name="uq_spectrum_catalog_ordinal"),
    )
    op.create_index("ix_spectrum_catalog_entries_catalog_id", "spectrum_catalog_entries", ["catalog_id"])
    op.create_index("ix_spectrum_entry_level_rt", "spectrum_catalog_entries", ["catalog_id", "ms_level", "retention_time_seconds", "ordinal"])
    op.create_index("ix_spectrum_entry_scan", "spectrum_catalog_entries", ["catalog_id", "scan_number", "ordinal"])
    op.create_index("ix_spectrum_entry_native", "spectrum_catalog_entries", ["catalog_id", "native_id"])
    op.create_index("ix_spectrum_entry_precursor", "spectrum_catalog_entries", ["catalog_id", "ms_level", "precursor_mz", "ordinal"])
    op.create_index("ix_spectrum_entry_charge_precursor", "spectrum_catalog_entries", ["catalog_id", "precursor_charge", "precursor_mz", "ordinal"])


def downgrade() -> None:
    op.drop_table("spectrum_catalog_entries")
    op.drop_table("spectrum_catalogs")
