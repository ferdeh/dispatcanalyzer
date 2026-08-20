"""phase 5 machine learning intelligence

Revision ID: 0009_phase5_machine_learning
Revises: 0008_phase4_spbu_mt_affinity
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_phase5_machine_learning"
down_revision = "0008_phase4_spbu_mt_affinity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_concentration_analysis_run",
        sa.Column("analysis_run_id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("baseline_start_date", sa.Date(), nullable=False),
        sa.Column("baseline_end_date", sa.Date(), nullable=False),
        sa.Column("minimum_shipment_observation", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("algorithm_name", sa.String(length=80), nullable=False, server_default="IsolationForest"),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="phase5.concentration.iforest.v1"),
        sa.Column("algorithm_parameters", sa.JSON(), nullable=False),
        sa.Column("master_compatibility_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ml_concentration_analysis_run_depot_id", "ml_concentration_analysis_run", ["depot_id"])
    op.create_index("ix_ml_concentration_analysis_run_status", "ml_concentration_analysis_run", ["status"])
    op.create_index("ix_ml_concentration_run_depot_dates", "ml_concentration_analysis_run", ["depot_id", "baseline_start_date", "baseline_end_date"])
    op.create_index("ix_ml_concentration_run_status_created", "ml_concentration_analysis_run", ["status", "created_at"])

    op.create_table(
        "ml_spbu_concentration_profile",
        sa.Column("profile_id", sa.String(length=64), primary_key=True),
        sa.Column("analysis_run_id", sa.String(length=64), sa.ForeignKey("ml_concentration_analysis_run.analysis_run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("shipment_observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compatible_mt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("historically_used_mt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("utilization_breadth", sa.Float(), nullable=False, server_default="0"),
        sa.Column("dominant_mt_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("dominant_mt_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("hhi", sa.Float(), nullable=False, server_default="0"),
        sa.Column("entropy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("normalized_entropy", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_ml_anomaly_score", sa.Float(), nullable=True),
        sa.Column("concentration_anomaly_score", sa.Float(), nullable=True),
        sa.Column("concentration_classification", sa.String(length=50), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("data_sufficiency_status", sa.String(length=40), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("peer_statistics", sa.JSON(), nullable=False),
        sa.Column("mt_distribution", sa.JSON(), nullable=False),
        sa.UniqueConstraint("analysis_run_id", "spbu_id", name="uq_ml_concentration_profile_run_spbu"),
    )
    op.create_index("ix_ml_spbu_concentration_profile_analysis_run_id", "ml_spbu_concentration_profile", ["analysis_run_id"])
    op.create_index("ix_ml_spbu_concentration_profile_depot_id", "ml_spbu_concentration_profile", ["depot_id"])
    op.create_index("ix_ml_spbu_concentration_profile_spbu_id", "ml_spbu_concentration_profile", ["spbu_id"])
    op.create_index("ix_ml_spbu_concentration_profile_concentration_anomaly_score", "ml_spbu_concentration_profile", ["concentration_anomaly_score"])
    op.create_index("ix_ml_concentration_profile_run_score", "ml_spbu_concentration_profile", ["analysis_run_id", "concentration_anomaly_score"])
    op.create_index("ix_ml_concentration_profile_depot_spbu", "ml_spbu_concentration_profile", ["depot_id", "spbu_id"])

    op.create_table(
        "ml_training_run",
        sa.Column("training_run_id", sa.String(length=64), primary_key=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("training_start_date", sa.Date(), nullable=False),
        sa.Column("training_end_date", sa.Date(), nullable=False),
        sa.Column("minimum_shipment_observation", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="PENDING"),
        sa.Column("training_configuration", sa.JSON(), nullable=False),
        sa.Column("dataset_summary", sa.JSON(), nullable=False),
        sa.Column("dataset_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("shift_definition_snapshot", sa.JSON(), nullable=False),
        sa.Column("master_compatibility_snapshot", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="phase5.behavioral.n2v_umap_hdbscan.v1"),
        sa.Column("library_versions", sa.JSON(), nullable=False),
        sa.Column("artifact_temp_path", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ml_training_run_depot_id", "ml_training_run", ["depot_id"])
    op.create_index("ix_ml_training_run_status", "ml_training_run", ["status"])
    op.create_index("ix_ml_training_run_depot_dates", "ml_training_run", ["depot_id", "training_start_date", "training_end_date"])
    op.create_index("ix_ml_training_run_status_created", "ml_training_run", ["status", "created_at"])

    op.create_table(
        "ml_behavioral_model",
        sa.Column("model_id", sa.String(length=64), primary_key=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("model_description", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("source_training_run_id", sa.String(length=64), sa.ForeignKey("ml_training_run.training_run_id"), nullable=True),
        sa.Column("training_start_date", sa.Date(), nullable=False),
        sa.Column("training_end_date", sa.Date(), nullable=False),
        sa.Column("training_shipment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("training_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minimum_shipment_observation", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("tag_feature_configuration", sa.JSON(), nullable=False),
        sa.Column("tag_encoder_reference", sa.JSON(), nullable=False),
        sa.Column("shift_definition_snapshot", sa.JSON(), nullable=False),
        sa.Column("feature_weights", sa.JSON(), nullable=False),
        sa.Column("node2vec_parameters", sa.JSON(), nullable=False),
        sa.Column("umap_parameters", sa.JSON(), nullable=False),
        sa.Column("hdbscan_parameters", sa.JSON(), nullable=False),
        sa.Column("dependency_metadata", sa.JSON(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("noise_spbu_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_membership_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("algorithm_version", sa.String(length=80), nullable=False, server_default="phase5.behavioral.n2v_umap_hdbscan.v1"),
        sa.Column("library_versions", sa.JSON(), nullable=False),
        sa.Column("random_seed", sa.Integer(), nullable=False, server_default="42"),
        sa.Column("model_status", sa.String(length=30), nullable=False, server_default="SAVED"),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("depot_id", "model_name", "model_version", name="uq_ml_behavioral_model_name_version"),
    )
    op.create_index("ix_ml_behavioral_model_model_name", "ml_behavioral_model", ["model_name"])
    op.create_index("ix_ml_behavioral_model_depot_id", "ml_behavioral_model", ["depot_id"])
    op.create_index("ix_ml_behavioral_model_model_status", "ml_behavioral_model", ["model_status"])
    op.create_index("ix_ml_behavioral_model_depot_status", "ml_behavioral_model", ["depot_id", "model_status"])
    op.create_index("ix_ml_behavioral_model_created", "ml_behavioral_model", ["created_at"])

    op.create_table(
        "ml_model_artifact",
        sa.Column("artifact_id", sa.String(length=64), primary_key=True),
        sa.Column("model_id", sa.String(length=64), sa.ForeignKey("ml_behavioral_model.model_id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.String(length=60), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_id", "artifact_type", name="uq_ml_model_artifact_type"),
    )
    op.create_index("ix_ml_model_artifact_model_id", "ml_model_artifact", ["model_id"])

    op.create_table(
        "ml_spbu_cluster_assignment",
        sa.Column("assignment_id", sa.String(length=64), primary_key=True),
        sa.Column("model_id", sa.String(length=64), sa.ForeignKey("ml_behavioral_model.model_id", ondelete="CASCADE"), nullable=False),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("cluster_label", sa.String(length=120), nullable=False),
        sa.Column("membership_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_noise", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dominant_shift", sa.String(length=120), nullable=True),
        sa.Column("key_tags", sa.JSON(), nullable=False),
        sa.Column("visualization_x", sa.Float(), nullable=True),
        sa.Column("visualization_y", sa.Float(), nullable=True),
        sa.UniqueConstraint("model_id", "spbu_id", name="uq_ml_cluster_assignment_model_spbu"),
    )
    op.create_index("ix_ml_spbu_cluster_assignment_model_id", "ml_spbu_cluster_assignment", ["model_id"])
    op.create_index("ix_ml_spbu_cluster_assignment_depot_id", "ml_spbu_cluster_assignment", ["depot_id"])
    op.create_index("ix_ml_spbu_cluster_assignment_spbu_id", "ml_spbu_cluster_assignment", ["spbu_id"])
    op.create_index("ix_ml_cluster_assignment_depot_spbu", "ml_spbu_cluster_assignment", ["depot_id", "spbu_id"])
    op.create_index("ix_ml_cluster_assignment_model_cluster", "ml_spbu_cluster_assignment", ["model_id", "cluster_id"])

    op.create_table(
        "ml_cluster_profile",
        sa.Column("cluster_profile_id", sa.String(length=64), primary_key=True),
        sa.Column("model_id", sa.String(length=64), sa.ForeignKey("ml_behavioral_model.model_id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("cluster_label", sa.String(length=120), nullable=False),
        sa.Column("cluster_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("training_spbu_percentage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("common_tags", sa.JSON(), nullable=False),
        sa.Column("shift_distribution", sa.JSON(), nullable=False),
        sa.Column("dominant_shift", sa.String(length=120), nullable=True),
        sa.Column("top_internal_pairings", sa.JSON(), nullable=False),
        sa.Column("average_membership_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("low_confidence_member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("model_id", "cluster_id", name="uq_ml_cluster_profile_model_cluster"),
    )
    op.create_index("ix_ml_cluster_profile_model_id", "ml_cluster_profile", ["model_id"])


def downgrade() -> None:
    op.drop_index("ix_ml_cluster_profile_model_id", table_name="ml_cluster_profile")
    op.drop_table("ml_cluster_profile")
    for name in [
        "ix_ml_cluster_assignment_model_cluster",
        "ix_ml_cluster_assignment_depot_spbu",
        "ix_ml_spbu_cluster_assignment_spbu_id",
        "ix_ml_spbu_cluster_assignment_depot_id",
        "ix_ml_spbu_cluster_assignment_model_id",
    ]:
        op.drop_index(name, table_name="ml_spbu_cluster_assignment")
    op.drop_table("ml_spbu_cluster_assignment")
    op.drop_index("ix_ml_model_artifact_model_id", table_name="ml_model_artifact")
    op.drop_table("ml_model_artifact")
    for name in [
        "ix_ml_behavioral_model_created",
        "ix_ml_behavioral_model_depot_status",
        "ix_ml_behavioral_model_model_status",
        "ix_ml_behavioral_model_depot_id",
        "ix_ml_behavioral_model_model_name",
    ]:
        op.drop_index(name, table_name="ml_behavioral_model")
    op.drop_table("ml_behavioral_model")
    for name in ["ix_ml_training_run_status_created", "ix_ml_training_run_depot_dates", "ix_ml_training_run_status", "ix_ml_training_run_depot_id"]:
        op.drop_index(name, table_name="ml_training_run")
    op.drop_table("ml_training_run")
    for name in [
        "ix_ml_concentration_profile_depot_spbu",
        "ix_ml_concentration_profile_run_score",
        "ix_ml_spbu_concentration_profile_concentration_anomaly_score",
        "ix_ml_spbu_concentration_profile_spbu_id",
        "ix_ml_spbu_concentration_profile_depot_id",
        "ix_ml_spbu_concentration_profile_analysis_run_id",
    ]:
        op.drop_index(name, table_name="ml_spbu_concentration_profile")
    op.drop_table("ml_spbu_concentration_profile")
    for name in [
        "ix_ml_concentration_run_status_created",
        "ix_ml_concentration_run_depot_dates",
        "ix_ml_concentration_analysis_run_status",
        "ix_ml_concentration_analysis_run_depot_id",
    ]:
        op.drop_index(name, table_name="ml_concentration_analysis_run")
    op.drop_table("ml_concentration_analysis_run")
