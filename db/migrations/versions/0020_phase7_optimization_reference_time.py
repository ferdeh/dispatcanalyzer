"""Persist the dispatcher-selected Phase 7 optimization reference time.

Revision ID: 0020_phase7_reference_time
Revises: 0019_phase7_dynamic_vrp
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_phase7_reference_time"
down_revision = "0019_phase7_dynamic_vrp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "optimization_run",
        sa.Column("optimization_reference_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE optimization_run
        SET optimization_reference_time = start_time
        WHERE optimization_reference_time IS NULL
          AND start_time IS NOT NULL
        """
    )
    op.create_index(
        "ix_optimization_run_optimization_reference_time",
        "optimization_run",
        ["optimization_reference_time"],
    )
    op.create_index(
        "ix_optimization_run_job_reference_time",
        "optimization_run",
        ["job_id", "optimization_reference_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_optimization_run_job_reference_time", table_name="optimization_run")
    op.drop_index("ix_optimization_run_optimization_reference_time", table_name="optimization_run")
    op.drop_column("optimization_run", "optimization_reference_time")
