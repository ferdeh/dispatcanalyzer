"""phase 4 spbu-mt historical affinity and stability tables

Revision ID: 0008_phase4_spbu_mt_affinity
Revises: 0007_phase3_spbu_pairing
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_phase4_spbu_mt_affinity"
down_revision = "0007_phase3_spbu_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fact_spbu_mt_pair",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("mt_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_spbu_shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_mt_shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("probability_mt_given_spbu", sa.Float(), nullable=False, server_default="0"),
        sa.Column("probability_spbu_given_mt", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_observed", sa.Date(), nullable=False),
        sa.Column("last_observed", sa.Date(), nullable=False),
        sa.Column("operating_day_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(length=20), nullable=False, server_default="LOW"),
        sa.Column("analysis_start_date", sa.Date(), nullable=False),
        sa.Column("analysis_end_date", sa.Date(), nullable=False),
        sa.Column("product_filter", sa.String(length=120), nullable=False, server_default="ALL"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="spbu_mt_affinity.jsd_v1"),
        sa.UniqueConstraint("depot_id", "spbu_id", "mt_id", "analysis_start_date", "analysis_end_date", "product_filter", "algorithm_version", name="uq_fact_spbu_mt_pair_scope"),
    )
    op.create_index("ix_fact_spbu_mt_pair_depot_id", "fact_spbu_mt_pair", ["depot_id"])
    op.create_index("ix_fact_spbu_mt_pair_spbu_id", "fact_spbu_mt_pair", ["spbu_id"])
    op.create_index("ix_fact_spbu_mt_pair_mt_id", "fact_spbu_mt_pair", ["mt_id"])
    op.create_index("ix_fact_spbu_mt_pair_depot_dates", "fact_spbu_mt_pair", ["depot_id", "analysis_start_date", "analysis_end_date"])
    op.create_index("ix_fact_spbu_mt_pair_entities", "fact_spbu_mt_pair", ["spbu_id", "mt_id"])

    op.create_table(
        "fact_spbu_mt_profile",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operating_day_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_mt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dominant_mt_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("dominant_mt_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("second_mt_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("top3_mt_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("hhi", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalized_hhi", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalized_entropy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("consistency_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("variability_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("dominant_mt_persistence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("temporal_stability_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pattern_shift_level", sa.String(length=40), nullable=False, server_default="STABLE"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(length=20), nullable=False, server_default="LOW"),
        sa.Column("analysis_start_date", sa.Date(), nullable=False),
        sa.Column("analysis_end_date", sa.Date(), nullable=False),
        sa.Column("product_filter", sa.String(length=120), nullable=False, server_default="ALL"),
        sa.Column("temporal_bucket", sa.String(length=20), nullable=False, server_default="WEEKLY"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="spbu_mt_affinity.jsd_v1"),
        sa.UniqueConstraint("depot_id", "spbu_id", "analysis_start_date", "analysis_end_date", "product_filter", "temporal_bucket", "algorithm_version", name="uq_fact_spbu_mt_profile_scope"),
    )
    op.create_index("ix_fact_spbu_mt_profile_depot_id", "fact_spbu_mt_profile", ["depot_id"])
    op.create_index("ix_fact_spbu_mt_profile_spbu_id", "fact_spbu_mt_profile", ["spbu_id"])
    op.create_index("ix_fact_spbu_mt_profile_depot_dates", "fact_spbu_mt_profile", ["depot_id", "analysis_start_date", "analysis_end_date"])

    op.create_table(
        "fact_spbu_mt_temporal_profile",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("mt_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("period_type", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_spbu_shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("probability_mt_given_spbu", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_dominant_mt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("analysis_start_date", sa.Date(), nullable=False),
        sa.Column("analysis_end_date", sa.Date(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="spbu_mt_affinity.jsd_v1"),
        sa.UniqueConstraint("depot_id", "spbu_id", "mt_id", "period_type", "period_start", "analysis_start_date", "analysis_end_date", "algorithm_version", name="uq_fact_spbu_mt_temporal_scope"),
    )
    op.create_index("ix_fact_spbu_mt_temporal_depot_id", "fact_spbu_mt_temporal_profile", ["depot_id"])
    op.create_index("ix_fact_spbu_mt_temporal_spbu_id", "fact_spbu_mt_temporal_profile", ["spbu_id"])
    op.create_index("ix_fact_spbu_mt_temporal_mt_id", "fact_spbu_mt_temporal_profile", ["mt_id"])
    op.create_index("ix_fact_spbu_mt_temporal_depot_period", "fact_spbu_mt_temporal_profile", ["depot_id", "period_type", "period_start"])
    op.create_index("ix_fact_spbu_mt_temporal_entities", "fact_spbu_mt_temporal_profile", ["spbu_id", "mt_id"])


def downgrade() -> None:
    for name in ["ix_fact_spbu_mt_temporal_entities", "ix_fact_spbu_mt_temporal_depot_period", "ix_fact_spbu_mt_temporal_mt_id", "ix_fact_spbu_mt_temporal_spbu_id", "ix_fact_spbu_mt_temporal_depot_id"]:
        op.drop_index(name, table_name="fact_spbu_mt_temporal_profile")
    op.drop_table("fact_spbu_mt_temporal_profile")
    for name in ["ix_fact_spbu_mt_profile_depot_dates", "ix_fact_spbu_mt_profile_spbu_id", "ix_fact_spbu_mt_profile_depot_id"]:
        op.drop_index(name, table_name="fact_spbu_mt_profile")
    op.drop_table("fact_spbu_mt_profile")
    for name in ["ix_fact_spbu_mt_pair_entities", "ix_fact_spbu_mt_pair_depot_dates", "ix_fact_spbu_mt_pair_mt_id", "ix_fact_spbu_mt_pair_spbu_id", "ix_fact_spbu_mt_pair_depot_id"]:
        op.drop_index(name, table_name="fact_spbu_mt_pair")
    op.drop_table("fact_spbu_mt_pair")
