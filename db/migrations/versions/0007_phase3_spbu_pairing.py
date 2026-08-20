"""phase 3 spbu pairing intelligence tables

Revision ID: 0007_phase3_spbu_pairing
Revises: 0006_vehicle_class_integer
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_phase3_spbu_pairing"
down_revision = "0006_vehicle_class_integer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_spbu_pair",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("spbu_a_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("spbu_b_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("pair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipment_a_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipment_b_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("probability_b_given_a", sa.Float(), nullable=False, server_default="0"),
        sa.Column("probability_a_given_b", sa.Float(), nullable=False, server_default="0"),
        sa.Column("support", sa.Float(), nullable=False, server_default="0"),
        sa.Column("lift", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(length=40), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_start_date", sa.Date(), nullable=False),
        sa.Column("analysis_end_date", sa.Date(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="pairing_v1"),
        sa.UniqueConstraint(
            "depot_id",
            "spbu_a_id",
            "spbu_b_id",
            "analysis_start_date",
            "analysis_end_date",
            "algorithm_version",
            name="uq_fact_spbu_pair_scope",
        ),
    )
    op.create_index("ix_fact_spbu_pair_depot_id", "fact_spbu_pair", ["depot_id"])
    op.create_index("ix_fact_spbu_pair_spbu_a_id", "fact_spbu_pair", ["spbu_a_id"])
    op.create_index("ix_fact_spbu_pair_spbu_b_id", "fact_spbu_pair", ["spbu_b_id"])
    op.create_index("ix_fact_spbu_pair_depot_dates", "fact_spbu_pair", ["depot_id", "analysis_start_date", "analysis_end_date"])
    op.create_index("ix_fact_spbu_pair_spbus", "fact_spbu_pair", ["spbu_a_id", "spbu_b_id"])

    op.create_table(
        "fact_spbu_transition",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("from_spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("to_spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("transition_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transition_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("analysis_start_date", sa.Date(), nullable=False),
        sa.Column("analysis_end_date", sa.Date(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(length=40), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="spbu_transition.consecutive_v1"),
        sa.UniqueConstraint(
            "depot_id",
            "from_spbu_id",
            "to_spbu_id",
            "analysis_start_date",
            "analysis_end_date",
            "algorithm_version",
            name="uq_fact_spbu_transition_scope",
        ),
    )
    op.create_index("ix_fact_spbu_transition_depot_id", "fact_spbu_transition", ["depot_id"])
    op.create_index("ix_fact_spbu_transition_from_spbu_id", "fact_spbu_transition", ["from_spbu_id"])
    op.create_index("ix_fact_spbu_transition_to_spbu_id", "fact_spbu_transition", ["to_spbu_id"])
    op.create_index("ix_fact_spbu_transition_depot_dates", "fact_spbu_transition", ["depot_id", "analysis_start_date", "analysis_end_date"])
    op.create_index("ix_fact_spbu_transition_spbus", "fact_spbu_transition", ["from_spbu_id", "to_spbu_id"])


def downgrade() -> None:
    op.drop_index("ix_fact_spbu_transition_spbus", table_name="fact_spbu_transition")
    op.drop_index("ix_fact_spbu_transition_depot_dates", table_name="fact_spbu_transition")
    op.drop_index("ix_fact_spbu_transition_to_spbu_id", table_name="fact_spbu_transition")
    op.drop_index("ix_fact_spbu_transition_from_spbu_id", table_name="fact_spbu_transition")
    op.drop_index("ix_fact_spbu_transition_depot_id", table_name="fact_spbu_transition")
    op.drop_table("fact_spbu_transition")
    op.drop_index("ix_fact_spbu_pair_spbus", table_name="fact_spbu_pair")
    op.drop_index("ix_fact_spbu_pair_depot_dates", table_name="fact_spbu_pair")
    op.drop_index("ix_fact_spbu_pair_spbu_b_id", table_name="fact_spbu_pair")
    op.drop_index("ix_fact_spbu_pair_spbu_a_id", table_name="fact_spbu_pair")
    op.drop_index("ix_fact_spbu_pair_depot_id", table_name="fact_spbu_pair")
    op.drop_table("fact_spbu_pair")
