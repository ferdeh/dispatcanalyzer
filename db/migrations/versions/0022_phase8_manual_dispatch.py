"""Add Phase 8 manual dispatching and operational simulation snapshots.

Revision ID: 0022_phase8_manual_dispatch
Revises: 0021_master_operating_windows
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_phase8_manual_dispatch"
down_revision = "0021_master_operating_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_dispatch_job",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(80), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("operational_date", sa.Date(), nullable=False),
        sa.Column("source_phase", sa.String(20), nullable=False),
        sa.Column("source_job_id", sa.String(64), nullable=False),
        sa.Column("source_run_id", sa.String(64), nullable=True),
        sa.Column("source_route_id", sa.String(64), nullable=True),
        sa.Column("source_route_version", sa.String(80), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_dispatch_job_id", sa.String(64), sa.ForeignKey("manual_dispatch_job.id"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_lineage_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finalized_by", sa.String(120), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_manual_dispatch_job_job_id", "manual_dispatch_job", ["job_id"], unique=True)
    op.create_index("ix_manual_dispatch_job_depot_id", "manual_dispatch_job", ["depot_id"])
    op.create_index("ix_manual_dispatch_job_operational_date", "manual_dispatch_job", ["operational_date"])
    op.create_index("ix_manual_dispatch_job_source_job_id", "manual_dispatch_job", ["source_job_id"])
    op.create_index("ix_manual_dispatch_job_source_run_id", "manual_dispatch_job", ["source_run_id"])
    op.create_index("ix_manual_dispatch_job_parent_dispatch_job_id", "manual_dispatch_job", ["parent_dispatch_job_id"])
    op.create_index("ix_manual_dispatch_job_status", "manual_dispatch_job", ["status"])
    op.create_index("ix_manual_dispatch_job_depot_date", "manual_dispatch_job", ["depot_id", "operational_date"])
    op.create_index("ix_manual_dispatch_job_status_updated", "manual_dispatch_job", ["status", "updated_at"])

    op.create_table(
        "manual_dispatch_vehicle",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dispatch_job_id", sa.String(64), sa.ForeignKey("manual_dispatch_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("vehicle_registration", sa.String(80), nullable=True),
        sa.Column("vehicle_class", sa.Integer(), nullable=True),
        sa.Column("capacity_kl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mt_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("number_of_compartments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compartment_configuration", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("initial_available_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_available_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="IDLE"),
        sa.UniqueConstraint("dispatch_job_id", "mt_id", name="uq_manual_dispatch_vehicle_job_mt"),
    )
    op.create_index("ix_manual_dispatch_vehicle_dispatch_job_id", "manual_dispatch_vehicle", ["dispatch_job_id"])
    op.create_index("ix_manual_dispatch_vehicle_mt_id", "manual_dispatch_vehicle", ["mt_id"])

    op.create_table(
        "manual_dispatch_loading_order",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dispatch_job_id", sa.String(64), sa.ForeignKey("manual_dispatch_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lo_id", sa.String(120), nullable=False),
        sa.Column("lo_number", sa.String(120), nullable=False),
        sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("spbu_number", sa.String(120), nullable=True),
        sa.Column("spbu_name", sa.String(255), nullable=True),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=True),
        sa.Column("volume_kl", sa.Float(), nullable=False),
        sa.Column("cluster_id", sa.String(80), nullable=True),
        sa.Column("cluster_name", sa.String(120), nullable=True),
        sa.Column("shift_id", sa.String(80), nullable=True),
        sa.Column("shift_name", sa.String(120), nullable=True),
        sa.Column("spbu_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("assignment_status", sa.String(30), nullable=False, server_default="UNASSIGNED"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("dispatch_job_id", "lo_id", name="uq_manual_dispatch_scope_job_lo"),
    )
    op.create_index("ix_manual_dispatch_loading_order_dispatch_job_id", "manual_dispatch_loading_order", ["dispatch_job_id"])
    op.create_index("ix_manual_dispatch_loading_order_lo_id", "manual_dispatch_loading_order", ["lo_id"])
    op.create_index("ix_manual_dispatch_loading_order_spbu_id", "manual_dispatch_loading_order", ["spbu_id"])
    op.create_index("ix_manual_dispatch_loading_order_assignment_status", "manual_dispatch_loading_order", ["assignment_status"])
    op.create_index("ix_manual_dispatch_scope_status", "manual_dispatch_loading_order", ["dispatch_job_id", "assignment_status"])

    op.create_table(
        "manual_dispatch_trip",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dispatch_vehicle_id", sa.String(64), sa.ForeignKey("manual_dispatch_vehicle.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_sequence", sa.Integer(), nullable=False),
        sa.Column("available_before_trip_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("departure_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_return_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turnaround_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_after_trip_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("distance_meter", sa.Integer(), nullable=True),
        sa.Column("travel_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("service_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operational_buffer_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("total_volume_kl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("route_provider", sa.String(80), nullable=True),
        sa.Column("route_response_status", sa.String(80), nullable=True),
        sa.Column("route_error_message", sa.Text(), nullable=True),
        sa.Column("route_geometry", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("route_calculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("dispatch_vehicle_id", "trip_sequence", name="uq_manual_dispatch_trip_sequence"),
    )
    op.create_index("ix_manual_dispatch_trip_dispatch_vehicle_id", "manual_dispatch_trip", ["dispatch_vehicle_id"])
    op.create_index("ix_manual_dispatch_trip_status", "manual_dispatch_trip", ["status"])
    op.create_index("ix_manual_dispatch_trip_departure", "manual_dispatch_trip", ["dispatch_vehicle_id", "departure_datetime"])

    op.create_table(
        "manual_dispatch_trip_lo",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dispatch_job_id", sa.String(64), sa.ForeignKey("manual_dispatch_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", sa.String(64), sa.ForeignKey("manual_dispatch_trip.id", ondelete="CASCADE"), nullable=False),
        sa.Column("manual_dispatch_lo_id", sa.String(64), sa.ForeignKey("manual_dispatch_loading_order.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stop_sequence", sa.Integer(), nullable=False),
        sa.Column("estimated_arrival_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("dispatch_job_id", "manual_dispatch_lo_id", name="uq_manual_dispatch_lo_assignment"),
    )
    op.create_index("ix_manual_dispatch_trip_lo_dispatch_job_id", "manual_dispatch_trip_lo", ["dispatch_job_id"])
    op.create_index("ix_manual_dispatch_trip_lo_trip_id", "manual_dispatch_trip_lo", ["trip_id"])
    op.create_index("ix_manual_dispatch_trip_lo_manual_dispatch_lo_id", "manual_dispatch_trip_lo", ["manual_dispatch_lo_id"])
    op.create_index("ix_manual_dispatch_trip_lo_trip_stop", "manual_dispatch_trip_lo", ["trip_id", "stop_sequence"])

    op.create_table(
        "manual_dispatch_route_leg",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trip_id", sa.String(64), sa.ForeignKey("manual_dispatch_trip.id", ondelete="CASCADE"), nullable=False),
        sa.Column("leg_sequence", sa.Integer(), nullable=False),
        sa.Column("origin_type", sa.String(30), nullable=False),
        sa.Column("origin_id", sa.String(120), nullable=False),
        sa.Column("destination_type", sa.String(30), nullable=False),
        sa.Column("destination_id", sa.String(120), nullable=False),
        sa.Column("origin_lat", sa.Float(), nullable=False),
        sa.Column("origin_lng", sa.Float(), nullable=False),
        sa.Column("destination_lat", sa.Float(), nullable=False),
        sa.Column("destination_lng", sa.Float(), nullable=False),
        sa.Column("distance_meter", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("traffic_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("route_provider", sa.String(80), nullable=False),
        sa.Column("request_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_status", sa.String(80), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("trip_id", "leg_sequence", name="uq_manual_dispatch_route_leg_sequence"),
    )
    op.create_index("ix_manual_dispatch_route_leg_trip_id", "manual_dispatch_route_leg", ["trip_id"])

    op.create_table(
        "manual_dispatch_audit_log",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dispatch_job_id", sa.String(64), sa.ForeignKey("manual_dispatch_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(120), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.String(120), nullable=False),
        sa.Column("old_value_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("new_value_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_manual_dispatch_audit_log_dispatch_job_id", "manual_dispatch_audit_log", ["dispatch_job_id"])
    op.create_index("ix_manual_dispatch_audit_log_action", "manual_dispatch_audit_log", ["action"])
    op.create_index("ix_manual_dispatch_audit_log_created_at", "manual_dispatch_audit_log", ["created_at"])
    op.create_index("ix_manual_dispatch_audit_job_created", "manual_dispatch_audit_log", ["dispatch_job_id", "created_at"])


def downgrade() -> None:
    op.drop_table("manual_dispatch_audit_log")
    op.drop_table("manual_dispatch_route_leg")
    op.drop_table("manual_dispatch_trip_lo")
    op.drop_table("manual_dispatch_trip")
    op.drop_table("manual_dispatch_loading_order")
    op.drop_table("manual_dispatch_vehicle")
    op.drop_table("manual_dispatch_job")
