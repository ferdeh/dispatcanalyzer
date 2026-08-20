"""phase 6 shipment prediction and mt assignment

Revision ID: 0010_phase6_prediction
Revises: 0009_phase5_machine_learning
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_phase6_prediction"
down_revision = "0009_phase5_machine_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_run",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("prediction_run_no", sa.String(length=64), nullable=False, unique=True),
        sa.Column("depot_id", sa.String(length=64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("model_id", sa.String(length=64), sa.ForeignKey("ml_behavioral_model.model_id"), nullable=False),
        sa.Column("model_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="PENDING"),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_loading_order_filename", sa.String(length=255), nullable=False),
        sa.Column("input_mt_availability_filename", sa.String(length=255), nullable=False),
        sa.Column("input_loading_order_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_mt_availability_snapshot", sa.JSON(), nullable=False),
        sa.Column("validation_snapshot", sa.JSON(), nullable=False),
        sa.Column("parameter_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("original_prediction_snapshot", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=100), nullable=False),
        sa.Column("validation_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipment_prediction_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mt_prediction_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assignment_optimization_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_prediction_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_prediction_run_prediction_run_no", "prediction_run", ["prediction_run_no"])
    op.create_index("ix_prediction_run_depot_id", "prediction_run", ["depot_id"])
    op.create_index("ix_prediction_run_model_id", "prediction_run", ["model_id"])
    op.create_index("ix_prediction_run_status", "prediction_run", ["status"])
    op.create_index("ix_prediction_run_depot_created", "prediction_run", ["depot_id", "created_at"])
    op.create_index("ix_prediction_run_model_created", "prediction_run", ["model_id", "created_at"])

    op.create_table(
        "prediction_shipment",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("prediction_run_id", sa.String(length=64), sa.ForeignKey("prediction_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("predicted_shipment_id", sa.String(length=120), nullable=False),
        sa.Column("shift_id", sa.String(length=80), nullable=False),
        sa.Column("shift_name", sa.String(length=120), nullable=False),
        sa.Column("shipment_prediction_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(length=20), nullable=False, server_default="LOW"),
        sa.Column("low_confidence", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_manual_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("prediction_run_id", "predicted_shipment_id", name="uq_prediction_shipment_run_number"),
    )
    op.create_index("ix_prediction_shipment_prediction_run_id", "prediction_shipment", ["prediction_run_id"])
    op.create_index("ix_prediction_shipment_predicted_shipment_id", "prediction_shipment", ["predicted_shipment_id"])
    op.create_index("ix_prediction_shipment_shift_id", "prediction_shipment", ["shift_id"])
    op.create_index("ix_prediction_shipment_run_shift", "prediction_shipment", ["prediction_run_id", "shift_id"])

    op.create_table(
        "prediction_shipment_line",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("prediction_run_id", sa.String(length=64), sa.ForeignKey("prediction_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prediction_shipment_id", sa.String(length=64), sa.ForeignKey("prediction_shipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("loading_order_no", sa.String(length=120), nullable=False),
        sa.Column("spbu_id", sa.String(length=64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("spbu_no", sa.String(length=120), nullable=False),
        sa.Column("model_predicted_shipment_id", sa.String(length=120), nullable=False),
        sa.UniqueConstraint("prediction_run_id", "loading_order_no", name="uq_prediction_line_run_lo"),
    )
    op.create_index("ix_prediction_shipment_line_prediction_run_id", "prediction_shipment_line", ["prediction_run_id"])
    op.create_index("ix_prediction_shipment_line_prediction_shipment_id", "prediction_shipment_line", ["prediction_shipment_id"])
    op.create_index("ix_prediction_shipment_line_loading_order_no", "prediction_shipment_line", ["loading_order_no"])
    op.create_index("ix_prediction_shipment_line_spbu_id", "prediction_shipment_line", ["spbu_id"])
    op.create_index("ix_prediction_line_shipment_spbu", "prediction_shipment_line", ["prediction_shipment_id", "spbu_id"])

    op.create_table(
        "prediction_mt_candidate",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("prediction_shipment_id", sa.String(length=64), sa.ForeignKey("prediction_shipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("prediction_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("compatibility_status", sa.String(length=20), nullable=False),
        sa.Column("candidate_rank", sa.Integer(), nullable=True),
        sa.Column("exclusion_reason", sa.String(length=120), nullable=True),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.UniqueConstraint("prediction_shipment_id", "vehicle_id", name="uq_prediction_candidate_shipment_vehicle"),
    )
    op.create_index("ix_prediction_mt_candidate_prediction_shipment_id", "prediction_mt_candidate", ["prediction_shipment_id"])
    op.create_index("ix_prediction_mt_candidate_vehicle_id", "prediction_mt_candidate", ["vehicle_id"])
    op.create_index("ix_prediction_candidate_shipment_rank", "prediction_mt_candidate", ["prediction_shipment_id", "candidate_rank"])

    op.create_table(
        "prediction_assignment",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("prediction_shipment_id", sa.String(length=64), sa.ForeignKey("prediction_shipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_vehicle_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("original_assignment_score", sa.Float(), nullable=True),
        sa.Column("final_vehicle_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("final_assignment_score", sa.Float(), nullable=True),
        sa.Column("assignment_status", sa.String(length=40), nullable=False, server_default="UNASSIGNED"),
        sa.Column("unassigned_reason", sa.String(length=80), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_user", sa.String(length=120), nullable=True),
        sa.Column("override_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("prediction_shipment_id", name="uq_prediction_assignment_shipment"),
    )
    op.create_index("ix_prediction_assignment_prediction_shipment_id", "prediction_assignment", ["prediction_shipment_id"])
    op.create_index("ix_prediction_assignment_final_vehicle_id", "prediction_assignment", ["final_vehicle_id"])


def downgrade() -> None:
    op.drop_table("prediction_assignment")
    op.drop_table("prediction_mt_candidate")
    op.drop_table("prediction_shipment_line")
    op.drop_table("prediction_shipment")
    op.drop_table("prediction_run")
