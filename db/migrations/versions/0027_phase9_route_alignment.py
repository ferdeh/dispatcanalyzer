"""Add persisted Phase 9 route-model alignment evaluations.

Revision ID: 0027_phase9_route_alignment
Revises: 0026_phase5_saved_concentration
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_phase9_route_alignment"
down_revision = "0026_phase5_saved_concentration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_alignment_evaluation_run",
        sa.Column("evaluation_run_id", sa.String(64), primary_key=True),
        sa.Column("evaluation_run_no", sa.String(80), nullable=False, unique=True),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("route_version_id", sa.String(64), nullable=False),
        sa.Column("operating_date", sa.Date(), nullable=False),
        sa.Column("source_prediction_run_id", sa.String(64), nullable=False),
        sa.Column("phase5_model_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="PREPARING"),
        sa.Column("source_bundle_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_bundle_checksum", sa.String(64), nullable=False),
        sa.Column("summary_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("data_quality_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("algorithm_version", sa.String(100), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "route_version_id",
            "source_bundle_checksum",
            "algorithm_version",
            name="uq_route_alignment_run_source",
        ),
    )
    op.create_index("ix_route_alignment_run_run_no", "route_alignment_evaluation_run", ["evaluation_run_no"])
    op.create_index("ix_route_alignment_run_depot_id", "route_alignment_evaluation_run", ["depot_id"])
    op.create_index("ix_route_alignment_run_job_id", "route_alignment_evaluation_run", ["job_id"])
    op.create_index("ix_route_alignment_run_route_version_id", "route_alignment_evaluation_run", ["route_version_id"])
    op.create_index("ix_route_alignment_run_operating_date", "route_alignment_evaluation_run", ["operating_date"])
    op.create_index("ix_route_alignment_run_source_prediction_run_id", "route_alignment_evaluation_run", ["source_prediction_run_id"])
    op.create_index("ix_route_alignment_run_phase5_model_id", "route_alignment_evaluation_run", ["phase5_model_id"])
    op.create_index("ix_route_alignment_run_status", "route_alignment_evaluation_run", ["status"])
    op.create_index("ix_route_alignment_run_source_bundle_checksum", "route_alignment_evaluation_run", ["source_bundle_checksum"])
    op.create_index("ix_route_alignment_run_depot_created", "route_alignment_evaluation_run", ["depot_id", "created_at"])
    op.create_index("ix_route_alignment_run_route", "route_alignment_evaluation_run", ["route_version_id", "created_at"])

    op.create_table(
        "route_alignment_evaluation_row",
        sa.Column("evaluation_row_id", sa.String(64), primary_key=True),
        sa.Column(
            "evaluation_run_id",
            sa.String(64),
            sa.ForeignKey("route_alignment_evaluation_run.evaluation_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("route_version_lo_assignment_id", sa.String(64), nullable=False),
        sa.Column("route_version_trip_id", sa.String(64), nullable=True),
        sa.Column("loading_order_id", sa.String(120), nullable=False),
        sa.Column("shipment_id", sa.String(120), nullable=True),
        sa.Column("trip_number", sa.Integer(), nullable=True),
        sa.Column("stop_sequence", sa.Integer(), nullable=True),
        sa.Column("assignment_status", sa.String(30), nullable=False),
        sa.Column("planned_gate_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("spbu_id", sa.String(64), nullable=False),
        sa.Column("spbu_code", sa.String(120), nullable=True),
        sa.Column("spbu_name", sa.String(255), nullable=True),
        sa.Column("product_id", sa.String(64), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=True),
        sa.Column("volume_kl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vehicle_id", sa.String(64), nullable=True),
        sa.Column("vehicle_registration", sa.String(80), nullable=True),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("cluster_label", sa.String(120), nullable=True),
        sa.Column("cluster_assignment_type", sa.String(40), nullable=True),
        sa.Column("route_shift_id", sa.String(80), nullable=True),
        sa.Column("route_shift_name", sa.String(120), nullable=True),
        sa.Column("cluster_cohesion_score", sa.Float(), nullable=True),
        sa.Column("cluster_cohesion_status", sa.String(40), nullable=False, server_default="NOT_EVALUATED"),
        sa.Column("cluster_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("shift_alignment_score", sa.Float(), nullable=True),
        sa.Column("shift_alignment_status", sa.String(40), nullable=False, server_default="NOT_EVALUATED"),
        sa.Column("shift_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("spbu_pairing_score", sa.Float(), nullable=True),
        sa.Column("spbu_pairing_status", sa.String(40), nullable=False, server_default="NOT_EVALUATED"),
        sa.Column("pairing_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("mt_affinity_score", sa.Float(), nullable=True),
        sa.Column("mt_affinity_status", sa.String(40), nullable=False, server_default="NOT_EVALUATED"),
        sa.Column("mt_affinity_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evaluable_category_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "route_version_lo_assignment_id",
            name="uq_route_alignment_row_assignment",
        ),
    )
    op.create_index("ix_route_alignment_row_evaluation_run_id", "route_alignment_evaluation_row", ["evaluation_run_id"])
    op.create_index("ix_route_alignment_row_loading_order_id", "route_alignment_evaluation_row", ["loading_order_id"])
    op.create_index("ix_route_alignment_row_shipment_id", "route_alignment_evaluation_row", ["shipment_id"])
    op.create_index("ix_route_alignment_row_assignment_status", "route_alignment_evaluation_row", ["assignment_status"])
    op.create_index("ix_route_alignment_row_spbu_id", "route_alignment_evaluation_row", ["spbu_id"])
    op.create_index("ix_route_alignment_row_vehicle_id", "route_alignment_evaluation_row", ["vehicle_id"])
    op.create_index("ix_route_alignment_row_run_gate", "route_alignment_evaluation_row", ["evaluation_run_id", "planned_gate_out"])
    op.create_index("ix_route_alignment_row_run_trip", "route_alignment_evaluation_row", ["evaluation_run_id", "route_version_trip_id"])
    op.create_index("ix_route_alignment_row_run_spbu", "route_alignment_evaluation_row", ["evaluation_run_id", "spbu_id"])

    op.create_table(
        "route_alignment_pair_evidence",
        sa.Column("pair_evidence_id", sa.String(64), primary_key=True),
        sa.Column(
            "evaluation_run_id",
            sa.String(64),
            sa.ForeignKey("route_alignment_evaluation_run.evaluation_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("route_version_trip_id", sa.String(64), nullable=False),
        sa.Column("spbu_a_id", sa.String(64), nullable=False),
        sa.Column("spbu_b_id", sa.String(64), nullable=False),
        sa.Column("cluster_a_id", sa.Integer(), nullable=True),
        sa.Column("cluster_b_id", sa.Integer(), nullable=True),
        sa.Column("same_cluster", sa.Boolean(), nullable=True),
        sa.Column("probability_b_given_a", sa.Float(), nullable=True),
        sa.Column("probability_a_given_b", sa.Float(), nullable=True),
        sa.Column("symmetric_pairing_score", sa.Float(), nullable=True),
        sa.Column("pair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipment_a_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipment_b_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("support", sa.Float(), nullable=False, server_default="0"),
        sa.Column("lift", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(40), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("evidence_status", sa.String(40), nullable=False, server_default="INSUFFICIENT_EVIDENCE"),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "route_version_trip_id",
            "spbu_a_id",
            "spbu_b_id",
            name="uq_route_alignment_pair_trip",
        ),
    )
    op.create_index("ix_route_alignment_pair_evaluation_run_id", "route_alignment_pair_evidence", ["evaluation_run_id"])
    op.create_index("ix_route_alignment_pair_run_trip", "route_alignment_pair_evidence", ["evaluation_run_id", "route_version_trip_id"])


def downgrade() -> None:
    op.drop_table("route_alignment_pair_evidence")
    op.drop_table("route_alignment_evaluation_row")
    op.drop_table("route_alignment_evaluation_run")
