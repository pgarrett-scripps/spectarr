"""add instrument inbox destinations and run assignment

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("system_key", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_projects_system_key"), "projects", ["system_key"], unique=True)
    op.add_column(
        "experiments",
        sa.Column("intake_agent_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_experiments_intake_agent_id"),
        "experiments",
        ["intake_agent_id"],
        unique=True,
    )
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("destination_mode", sa.String(length=32), server_default="inbox", nullable=False)
        )
        batch_op.add_column(
            sa.Column("destination_experiment_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_agents_destination_experiment_id_experiments",
            "experiments",
            ["destination_experiment_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        op.f("ix_agents_destination_experiment_id"),
        "agents",
        ["destination_experiment_id"],
        unique=False,
    )
    op.add_column(
        "runs",
        sa.Column("assignment_status", sa.String(length=32), server_default="assigned", nullable=False),
    )
    op.create_index(
        op.f("ix_runs_assignment_status"),
        "runs",
        ["assignment_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_assignment_status"), table_name="runs")
    op.drop_column("runs", "assignment_status")
    op.drop_index(op.f("ix_agents_destination_experiment_id"), table_name="agents")
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_constraint(
            "fk_agents_destination_experiment_id_experiments",
            type_="foreignkey",
        )
        batch_op.drop_column("destination_experiment_id")
        batch_op.drop_column("destination_mode")
    op.drop_index(op.f("ix_experiments_intake_agent_id"), table_name="experiments")
    op.drop_column("experiments", "intake_agent_id")
    op.drop_index(op.f("ix_projects_system_key"), table_name="projects")
    op.drop_column("projects", "system_key")
