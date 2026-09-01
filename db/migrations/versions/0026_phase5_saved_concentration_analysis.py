"""Persist named Phase 5 concentration analysis selections.

Revision ID: 0026_phase5_saved_concentration
Revises: 0025_phase4_affinity_config
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_phase5_saved_concentration"
down_revision = "0025_phase4_affinity_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_concentration_saved_analysis",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column(
            "analysis_run_id",
            sa.String(64),
            sa.ForeignKey("ml_concentration_analysis_run.analysis_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ui_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("depot_id", "normalized_name", name="uq_ml_concentration_saved_depot_name"),
    )
    op.create_index(
        "ix_ml_concentration_saved_analysis_depot_id",
        "ml_concentration_saved_analysis",
        ["depot_id"],
    )
    op.create_index(
        "ix_ml_concentration_saved_analysis_analysis_run_id",
        "ml_concentration_saved_analysis",
        ["analysis_run_id"],
    )
    op.create_index(
        "ix_ml_concentration_saved_depot_created",
        "ml_concentration_saved_analysis",
        ["depot_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("ml_concentration_saved_analysis")
