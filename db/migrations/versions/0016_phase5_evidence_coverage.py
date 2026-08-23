"""Separate Phase 5 historical evidence from cold-start coverage.

Revision ID: 0016_phase5_evidence_coverage
Revises: 0015_phase6_road_geometry
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_phase5_evidence_coverage"
down_revision = "0015_phase6_road_geometry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ml_behavioral_model", sa.Column("total_covered_spbu_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ml_behavioral_model", sa.Column("cold_start_covered_spbu_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ml_behavioral_model", sa.Column("no_history_spbu_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ml_behavioral_model", sa.Column("insufficient_history_spbu_count", sa.Integer(), nullable=False, server_default="0"))

    op.add_column("ml_spbu_cluster_assignment", sa.Column("shipment_observation_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ml_spbu_cluster_assignment", sa.Column("coverage_source", sa.String(length=50), nullable=False, server_default="BEHAVIORAL_HISTORY"))
    op.add_column("ml_spbu_cluster_assignment", sa.Column("history_eligible", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.add_column("ml_cluster_profile", sa.Column("historical_member_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ml_cluster_profile", sa.Column("cold_start_member_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ml_cluster_profile", sa.Column("no_history_member_count", sa.Integer(), nullable=False, server_default="0"))

    # Saved v4 training runs retain the evidence metadata in result_payload. Use
    # that immutable snapshot to classify existing registry assignments.
    op.execute(
        """
        UPDATE ml_spbu_cluster_assignment AS assignment
        SET shipment_observation_count = COALESCE((payload.item->>'shipment_observation_count')::integer, 0),
            coverage_source = COALESCE(payload.item->>'coverage_source', 'BEHAVIORAL_HISTORY'),
            history_eligible = COALESCE((payload.item->>'history_eligible')::boolean, true)
        FROM ml_behavioral_model AS model
        JOIN ml_training_run AS run ON run.training_run_id = model.source_training_run_id
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(run.result_payload::jsonb->'assignments', '[]'::jsonb)) AS payload(item)
        WHERE assignment.model_id = model.model_id
          AND payload.item->>'spbu_id' = assignment.spbu_id
        """
    )
    op.execute(
        """
        UPDATE ml_behavioral_model AS model
        SET training_spbu_count = evidence.historical_count,
            total_covered_spbu_count = evidence.total_count,
            cold_start_covered_spbu_count = evidence.cold_start_count,
            no_history_spbu_count = evidence.no_history_count,
            insufficient_history_spbu_count = evidence.insufficient_history_count,
            noise_spbu_count = evidence.historical_noise_count,
            average_membership_probability = evidence.historical_average_membership
        FROM (
            SELECT model_id,
                   COUNT(*)::integer AS total_count,
                   COUNT(*) FILTER (WHERE history_eligible)::integer AS historical_count,
                   COUNT(*) FILTER (WHERE NOT history_eligible)::integer AS cold_start_count,
                   COUNT(*) FILTER (WHERE NOT history_eligible AND shipment_observation_count = 0)::integer AS no_history_count,
                   COUNT(*) FILTER (WHERE NOT history_eligible AND shipment_observation_count > 0)::integer AS insufficient_history_count,
                   COUNT(*) FILTER (WHERE history_eligible AND is_noise)::integer AS historical_noise_count,
                   COALESCE(AVG(membership_probability) FILTER (WHERE history_eligible), 0) AS historical_average_membership
            FROM ml_spbu_cluster_assignment
            GROUP BY model_id
        ) AS evidence
        WHERE model.model_id = evidence.model_id
        """
    )
    op.execute(
        """
        UPDATE ml_cluster_profile AS profile
        SET cluster_size = evidence.total_count,
            historical_member_count = evidence.historical_count,
            cold_start_member_count = evidence.cold_start_count,
            no_history_member_count = evidence.no_history_count,
            average_membership_probability = evidence.historical_average_membership,
            low_confidence_member_count = evidence.historical_low_confidence_count
        FROM (
            SELECT model_id,
                   cluster_id,
                   COUNT(*)::integer AS total_count,
                   COUNT(*) FILTER (WHERE history_eligible)::integer AS historical_count,
                   COUNT(*) FILTER (WHERE NOT history_eligible)::integer AS cold_start_count,
                   COUNT(*) FILTER (WHERE NOT history_eligible AND shipment_observation_count = 0)::integer AS no_history_count,
                   COALESCE(AVG(membership_probability) FILTER (WHERE history_eligible), 0) AS historical_average_membership,
                   COUNT(*) FILTER (WHERE history_eligible AND membership_probability < 0.5)::integer AS historical_low_confidence_count
            FROM ml_spbu_cluster_assignment
            WHERE NOT is_noise AND cluster_id IS NOT NULL
            GROUP BY model_id, cluster_id
        ) AS evidence
        WHERE profile.model_id = evidence.model_id
          AND profile.cluster_id = evidence.cluster_id
        """
    )


def downgrade() -> None:
    op.drop_column("ml_cluster_profile", "no_history_member_count")
    op.drop_column("ml_cluster_profile", "cold_start_member_count")
    op.drop_column("ml_cluster_profile", "historical_member_count")
    op.drop_column("ml_spbu_cluster_assignment", "history_eligible")
    op.drop_column("ml_spbu_cluster_assignment", "coverage_source")
    op.drop_column("ml_spbu_cluster_assignment", "shipment_observation_count")
    op.drop_column("ml_behavioral_model", "insufficient_history_spbu_count")
    op.drop_column("ml_behavioral_model", "no_history_spbu_count")
    op.drop_column("ml_behavioral_model", "cold_start_covered_spbu_count")
    op.drop_column("ml_behavioral_model", "total_covered_spbu_count")
