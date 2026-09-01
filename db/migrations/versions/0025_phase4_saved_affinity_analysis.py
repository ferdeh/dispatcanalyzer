"""Persist saved Phase 4 affinity analysis configurations.

Revision ID: 0025_phase4_affinity_config
Revises: 0024_phase3_pairing_config
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_phase4_affinity_config"
down_revision = "0024_phase3_pairing_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "affinity_analysis_config",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("minimum_observations", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confidence_filter", sa.String(20), nullable=False, server_default="ALL"),
        sa.Column("temporal_bucket", sa.String(20), nullable=False, server_default="WEEKLY"),
        sa.Column("recent_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("top_n", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("edge_metric", sa.String(40), nullable=False, server_default="SHIPMENT_COUNT"),
        sa.Column("selected_spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=True),
        sa.Column("selected_mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("ui_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("affinity_analysis_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("depot_id", "normalized_name", name="uq_affinity_config_depot_name"),
    )
    op.create_index("ix_affinity_analysis_config_depot_id", "affinity_analysis_config", ["depot_id"])
    op.create_index("ix_affinity_config_depot_created", "affinity_analysis_config", ["depot_id", "created_at"])


def downgrade() -> None:
    op.drop_table("affinity_analysis_config")
