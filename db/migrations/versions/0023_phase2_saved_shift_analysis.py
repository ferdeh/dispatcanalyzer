"""Persist saved Phase 2 shift analysis configurations.

Revision ID: 0023_phase2_saved_shift_analysis
Revises: 0022_phase8_manual_dispatch
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_phase2_saved_shift_analysis"
down_revision = "0022_phase8_manual_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departure_shift_analysis_config",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("bucket_minutes", sa.Integer(), nullable=False),
        sa.Column("search", sa.Text(), nullable=True),
        sa.Column("sort_column", sa.String(80), nullable=False, server_default="observation_count"),
        sa.Column("sort_direction", sa.String(10), nullable=False, server_default="desc"),
        sa.Column("assignment_method", sa.String(60), nullable=False),
        sa.Column("shift_config", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("ui_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("departure_analysis_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("shift_analysis_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("depot_id", "normalized_name", name="uq_departure_shift_config_depot_name"),
    )
    op.create_index("ix_departure_shift_analysis_config_depot_id", "departure_shift_analysis_config", ["depot_id"])
    op.create_index("ix_departure_shift_config_depot_created", "departure_shift_analysis_config", ["depot_id", "created_at"])


def downgrade() -> None:
    op.drop_table("departure_shift_analysis_config")
