"""Add Phase 5 data sufficiency, geographic features, and projection audit fields.

Revision ID: 0017_phase5_sufficiency_geo
Revises: 0016_phase5_evidence_coverage
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_phase5_sufficiency_geo"
down_revision = "0016_phase5_evidence_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    model_columns = (
        sa.Column("total_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sufficient_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("marginal_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("insufficient_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("core_training_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("core_cluster_member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("marginal_projected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("marginal_unassigned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("insufficient_unassigned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tag_weight", sa.Float(), nullable=False, server_default="0.30"),
        sa.Column("shift_weight", sa.Float(), nullable=False, server_default="0.20"),
        sa.Column("pairing_weight", sa.Float(), nullable=False, server_default="0.30"),
        sa.Column("geographic_weight", sa.Float(), nullable=False, server_default="0.20"),
        sa.Column("data_sufficiency_configuration", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("geographic_proximity_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("geographic_configuration", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("valid_coordinate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_coordinate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geographic_coverage_percentage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("projection_method", sa.String(length=80), nullable=False, server_default="UMAP_NEAREST_CORE_CENTROID"),
        sa.Column("projection_parameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("minimum_projection_confidence", sa.Float(), nullable=False, server_default="0.55"),
        sa.Column("average_projection_confidence", sa.Float(), nullable=False, server_default="0"),
    )
    for column in model_columns:
        op.add_column("ml_behavioral_model", column)

    assignment_columns = (
        sa.Column("operating_day_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("training_period_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("shift_observation_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pairing_observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pairing_observation_strength", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_operating_date", sa.Date(), nullable=True),
        sa.Column("recency_age_days", sa.Integer(), nullable=True),
        sa.Column("data_sufficiency_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("data_sufficiency_status", sa.String(length=20), nullable=False, server_default="INSUFFICIENT"),
        sa.Column("data_sufficiency_components", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cluster_assignment_type", sa.String(length=40), nullable=False, server_default="INSUFFICIENT_UNASSIGNED"),
        sa.Column("projected_cluster_id", sa.Integer(), nullable=True),
        sa.Column("projection_confidence", sa.Float(), nullable=True),
        sa.Column("projection_status", sa.String(length=30), nullable=False, server_default="UNASSIGNED"),
        sa.Column("unassigned_reason", sa.Text(), nullable=True),
        sa.Column("geographic_data_status", sa.String(length=20), nullable=False, server_default="MISSING"),
        sa.Column("geographic_metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in assignment_columns:
        op.add_column("ml_spbu_cluster_assignment", column)
    op.alter_column("ml_spbu_cluster_assignment", "membership_probability", existing_type=sa.Float(), nullable=True)
    op.add_column("ml_cluster_profile", sa.Column("projected_member_count", sa.Integer(), nullable=False, server_default="0"))

    op.create_index(
        "ix_ml_cluster_assignment_model_sufficiency",
        "ml_spbu_cluster_assignment",
        ["model_id", "data_sufficiency_status"],
    )
    op.create_index(
        "ix_ml_cluster_assignment_model_type",
        "ml_spbu_cluster_assignment",
        ["model_id", "cluster_assignment_type"],
    )
    op.create_index(
        "ix_ml_cluster_assignment_geographic_status",
        "ml_spbu_cluster_assignment",
        ["geographic_data_status"],
    )

    # Preserve legacy models while removing their misleading no-history cluster
    # assignment. Older positive-history cold-start rows are explicitly labeled
    # as legacy marginal projection; new v5 runs use the deterministic scorer.
    op.execute(
        """
        UPDATE ml_spbu_cluster_assignment AS assignment
        SET data_sufficiency_score = CASE WHEN history_eligible THEN 100 WHEN shipment_observation_count > 0 THEN 50 ELSE 0 END,
            data_sufficiency_status = CASE WHEN history_eligible THEN 'SUFFICIENT' WHEN shipment_observation_count > 0 THEN 'MARGINAL' ELSE 'INSUFFICIENT' END,
            cluster_assignment_type = CASE
                WHEN history_eligible AND is_noise THEN 'CORE_NOISE'
                WHEN history_eligible THEN 'CORE_MEMBER'
                WHEN shipment_observation_count > 0 AND cluster_id IS NOT NULL THEN 'MARGINAL_PROJECTED'
                WHEN shipment_observation_count > 0 THEN 'MARGINAL_UNASSIGNED'
                ELSE 'INSUFFICIENT_UNASSIGNED'
            END,
            projected_cluster_id = CASE WHEN NOT history_eligible AND shipment_observation_count > 0 THEN cluster_id ELSE NULL END,
            projection_confidence = CASE WHEN NOT history_eligible AND shipment_observation_count > 0 THEN membership_probability ELSE NULL END,
            projection_status = CASE
                WHEN NOT history_eligible AND shipment_observation_count > 0 AND cluster_id IS NOT NULL THEN 'PROJECTED'
                WHEN NOT history_eligible AND shipment_observation_count > 0 THEN 'UNASSIGNED'
                ELSE 'NOT_APPLICABLE'
            END,
            geographic_data_status = CASE
                WHEN spbu.latitude IS NULL OR spbu.longitude IS NULL THEN 'MISSING'
                WHEN spbu.latitude < -90 OR spbu.latitude > 90 OR spbu.longitude < -180 OR spbu.longitude > 180
                  OR (spbu.latitude = 0 AND spbu.longitude = 0) THEN 'INVALID'
                ELSE 'VALID'
            END,
            cluster_id = CASE WHEN NOT history_eligible AND shipment_observation_count = 0 THEN NULL ELSE cluster_id END,
            cluster_label = CASE WHEN NOT history_eligible AND shipment_observation_count = 0 THEN 'Not Assigned' ELSE cluster_label END,
            membership_probability = CASE WHEN history_eligible THEN membership_probability ELSE NULL END,
            is_noise = CASE WHEN history_eligible THEN is_noise ELSE false END,
            unassigned_reason = CASE WHEN NOT history_eligible AND shipment_observation_count = 0 THEN 'Insufficient historical evidence (legacy backfill)' ELSE NULL END
        FROM master_spbu AS spbu
        WHERE spbu.spbu_id = assignment.spbu_id
        """
    )
    op.execute(
        """
        UPDATE ml_behavioral_model AS model
        SET total_spbu_count = counts.total_count,
            sufficient_spbu_count = counts.sufficient_count,
            marginal_spbu_count = counts.marginal_count,
            insufficient_spbu_count = counts.insufficient_count,
            core_training_spbu_count = counts.sufficient_count,
            core_cluster_member_count = counts.core_member_count,
            marginal_projected_count = counts.marginal_projected_count,
            marginal_unassigned_count = counts.marginal_unassigned_count,
            insufficient_unassigned_count = counts.insufficient_count,
            average_projection_confidence = counts.average_projection_confidence,
            tag_weight = COALESCE((model.feature_weights::jsonb->>'tag')::double precision, 0.30),
            shift_weight = COALESCE((model.feature_weights::jsonb->>'shift')::double precision, 0.20),
            pairing_weight = COALESCE((model.feature_weights::jsonb->>'pairing')::double precision, 0.30),
            geographic_weight = COALESCE((model.feature_weights::jsonb->>'geographic')::double precision, 0),
            geographic_proximity_enabled = COALESCE((model.feature_weights::jsonb->>'geographic')::double precision, 0) > 0,
            projection_method = 'LEGACY_NEAREST_CLUSTER_CENTROID'
        FROM (
            SELECT model_id,
                   COUNT(*)::integer AS total_count,
                   COUNT(*) FILTER (WHERE data_sufficiency_status = 'SUFFICIENT')::integer AS sufficient_count,
                   COUNT(*) FILTER (WHERE data_sufficiency_status = 'MARGINAL')::integer AS marginal_count,
                   COUNT(*) FILTER (WHERE data_sufficiency_status = 'INSUFFICIENT')::integer AS insufficient_count,
                   COUNT(*) FILTER (WHERE cluster_assignment_type = 'CORE_MEMBER')::integer AS core_member_count,
                   COUNT(*) FILTER (WHERE cluster_assignment_type = 'MARGINAL_PROJECTED')::integer AS marginal_projected_count,
                   COUNT(*) FILTER (WHERE cluster_assignment_type = 'MARGINAL_UNASSIGNED')::integer AS marginal_unassigned_count,
                   COALESCE(AVG(projection_confidence) FILTER (WHERE cluster_assignment_type = 'MARGINAL_PROJECTED'), 0) AS average_projection_confidence
            FROM ml_spbu_cluster_assignment
            GROUP BY model_id
        ) AS counts
        WHERE counts.model_id = model.model_id
        """
    )
    op.execute("UPDATE ml_cluster_profile SET projected_member_count = cold_start_member_count")


def downgrade() -> None:
    op.drop_index("ix_ml_cluster_assignment_geographic_status", table_name="ml_spbu_cluster_assignment")
    op.drop_index("ix_ml_cluster_assignment_model_type", table_name="ml_spbu_cluster_assignment")
    op.drop_index("ix_ml_cluster_assignment_model_sufficiency", table_name="ml_spbu_cluster_assignment")
    op.drop_column("ml_cluster_profile", "projected_member_count")
    op.alter_column("ml_spbu_cluster_assignment", "membership_probability", existing_type=sa.Float(), nullable=False, server_default="0")
    for name in (
        "created_at",
        "geographic_metrics",
        "geographic_data_status",
        "unassigned_reason",
        "projection_status",
        "projection_confidence",
        "projected_cluster_id",
        "cluster_assignment_type",
        "data_sufficiency_components",
        "data_sufficiency_status",
        "data_sufficiency_score",
        "recency_age_days",
        "last_operating_date",
        "pairing_observation_strength",
        "pairing_observation_count",
        "shift_observation_coverage",
        "training_period_coverage",
        "operating_day_count",
    ):
        op.drop_column("ml_spbu_cluster_assignment", name)
    for name in (
        "average_projection_confidence",
        "minimum_projection_confidence",
        "projection_parameters",
        "projection_method",
        "geographic_coverage_percentage",
        "invalid_coordinate_count",
        "valid_coordinate_count",
        "geographic_configuration",
        "geographic_proximity_enabled",
        "data_sufficiency_configuration",
        "geographic_weight",
        "pairing_weight",
        "shift_weight",
        "tag_weight",
        "insufficient_unassigned_count",
        "marginal_unassigned_count",
        "marginal_projected_count",
        "core_cluster_member_count",
        "core_training_spbu_count",
        "insufficient_spbu_count",
        "marginal_spbu_count",
        "sufficient_spbu_count",
        "total_spbu_count",
    ):
        op.drop_column("ml_behavioral_model", name)
