"""Make SPBU and Depot operating windows canonical required master data.

Revision ID: 0021_master_operating_windows
Revises: 0020_phase7_reference_time
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_master_operating_windows"
down_revision = "0020_phase7_reference_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "master_depot",
        sa.Column("depot_operational_start", sa.Time(), nullable=True, server_default=sa.text("'00:00:00'")),
    )
    op.add_column(
        "master_depot",
        sa.Column("depot_operational_end", sa.Time(), nullable=True, server_default=sa.text("'23:59:00'")),
    )
    op.execute("UPDATE master_depot SET depot_operational_start = '00:00:00' WHERE depot_operational_start IS NULL")
    op.execute("UPDATE master_depot SET depot_operational_end = '23:59:00' WHERE depot_operational_end IS NULL")
    op.alter_column("master_depot", "depot_operational_start", nullable=False, server_default=sa.text("'00:00:00'"))
    op.alter_column("master_depot", "depot_operational_end", nullable=False, server_default=sa.text("'23:59:00'"))

    op.execute("UPDATE master_spbu SET official_window_start = '00:00:00' WHERE official_window_start IS NULL")
    op.execute("UPDATE master_spbu SET official_window_end = '23:59:00' WHERE official_window_end IS NULL")
    op.alter_column("master_spbu", "official_window_start", nullable=False, server_default=sa.text("'00:00:00'"))
    op.alter_column("master_spbu", "official_window_end", nullable=False, server_default=sa.text("'23:59:00'"))

    op.execute(
        """
        UPDATE optimization_job AS job
        SET depot_operational_start = depot.depot_operational_start,
            depot_operational_end = depot.depot_operational_end
        FROM master_depot AS depot
        WHERE depot.depot_id = job.depot_id
        """
    )
    op.alter_column("optimization_job", "depot_operational_start", server_default=sa.text("'00:00:00'"))
    op.alter_column("optimization_job", "depot_operational_end", server_default=sa.text("'23:59:00'"))


def downgrade() -> None:
    op.alter_column("optimization_job", "depot_operational_end", server_default=sa.text("'22:00:00'"))
    op.alter_column("optimization_job", "depot_operational_start", server_default=sa.text("'05:00:00'"))
    op.alter_column("master_spbu", "official_window_end", nullable=True, server_default=None)
    op.alter_column("master_spbu", "official_window_start", nullable=True, server_default=None)
    op.drop_column("master_depot", "depot_operational_end")
    op.drop_column("master_depot", "depot_operational_start")
