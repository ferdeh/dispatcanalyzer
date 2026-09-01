"""Persist saved Phase 3 pairing analysis configurations.

Revision ID: 0024_phase3_pairing_config
Revises: 0023_phase2_saved_shift_analysis
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_phase3_pairing_config"
down_revision = "0023_phase2_saved_shift_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pairing_analysis_config",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("search", sa.Text(), nullable=True),
        sa.Column("sort_column", sa.String(80), nullable=False, server_default="evidence_strength"),
        sa.Column("sort_direction", sa.String(10), nullable=False, server_default="desc"),
        sa.Column("ui_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("pairing_analysis_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("depot_id", "normalized_name", name="uq_pairing_config_depot_name"),
    )
    op.create_index("ix_pairing_analysis_config_depot_id", "pairing_analysis_config", ["depot_id"])
    op.create_index("ix_pairing_config_depot_created", "pairing_analysis_config", ["depot_id", "created_at"])


def downgrade() -> None:
    op.drop_table("pairing_analysis_config")
