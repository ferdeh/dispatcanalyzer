"""Add Phase 7 dynamic multi-trip VRP and depot bay scheduling.

Revision ID: 0019_phase7_dynamic_vrp
Revises: 0018_phase6_lo_product
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_phase7_dynamic_vrp"
down_revision = "0018_phase6_lo_product"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optimization_job",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("job_no", sa.String(80), nullable=False, unique=True),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("operating_date", sa.Date(), nullable=False),
        sa.Column("source_prediction_run_id", sa.String(64), sa.ForeignKey("prediction_run.id"), nullable=True),
        sa.Column("current_route_version_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("depot_operational_start", sa.Time(), nullable=False, server_default="05:00"),
        sa.Column("depot_operational_end", sa.Time(), nullable=False, server_default="22:00"),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_optimization_job_job_no", "optimization_job", ["job_no"])
    op.create_index("ix_optimization_job_depot_id", "optimization_job", ["depot_id"])
    op.create_index("ix_optimization_job_operating_date", "optimization_job", ["operating_date"])
    op.create_index("ix_optimization_job_source_prediction_run_id", "optimization_job", ["source_prediction_run_id"])
    op.create_index("ix_optimization_job_status", "optimization_job", ["status"])
    op.create_index("ix_optimization_job_depot_date", "optimization_job", ["depot_id", "operating_date"])
    op.create_index("ix_optimization_job_depot_status", "optimization_job", ["depot_id", "status"])

    op.create_table(
        "operational_state_snapshot",
        sa.Column("state_snapshot_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_reason", sa.String(80), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("captured_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("lo_state_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("vehicle_state_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("bay_state_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("queue_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("audit_events", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_operational_state_snapshot_job_id", "operational_state_snapshot", ["job_id"])

    op.create_table(
        "optimization_parameter_profile",
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("profile_name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("profile_name", "version", name="uq_optimization_parameter_profile_name_version"),
    )
    op.create_index("ix_optimization_parameter_profile_profile_name", "optimization_parameter_profile", ["profile_name"])
    op.create_index("ix_optimization_parameter_profile_is_active", "optimization_parameter_profile", ["is_active"])

    op.create_table(
        "optimization_parameter_value",
        sa.Column("parameter_value_id", sa.String(64), primary_key=True),
        sa.Column("profile_id", sa.String(64), sa.ForeignKey("optimization_parameter_profile.profile_id", ondelete="CASCADE"), nullable=False),
        sa.Column("parameter_key", sa.String(120), nullable=False),
        sa.Column("parameter_value", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("profile_id", "parameter_key", name="uq_optimization_parameter_value_key"),
    )
    op.create_index("ix_optimization_parameter_value_profile_id", "optimization_parameter_value", ["profile_id"])

    op.create_table(
        "optimization_vehicle_cost_rule",
        sa.Column("cost_rule_id", sa.String(64), primary_key=True),
        sa.Column("profile_id", sa.String(64), sa.ForeignKey("optimization_parameter_profile.profile_id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_class", sa.Integer(), nullable=True),
        sa.Column("vehicle_tag", sa.String(160), nullable=True),
        sa.Column("activation_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_status", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_optimization_vehicle_cost_rule_profile_id", "optimization_vehicle_cost_rule", ["profile_id"])

    op.create_table(
        "optimization_parameter_snapshot",
        sa.Column("parameter_snapshot_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_profile_id", sa.String(64), sa.ForeignKey("optimization_parameter_profile.profile_id"), nullable=True),
        sa.Column("source_profile_version", sa.Integer(), nullable=True),
        sa.Column("effective_parameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("parameter_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
    )
    op.create_index("ix_optimization_parameter_snapshot_job_id", "optimization_parameter_snapshot", ["job_id"])
    op.create_index("ix_optimization_parameter_snapshot_parameter_checksum", "optimization_parameter_snapshot", ["parameter_checksum"])

    op.create_table(
        "route_version",
        sa.Column("route_version_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.Column("state_snapshot_id", sa.String(64), sa.ForeignKey("operational_state_snapshot.state_snapshot_id"), nullable=False),
        sa.Column("parameter_snapshot_id", sa.String(64), sa.ForeignKey("optimization_parameter_snapshot.parameter_snapshot_id"), nullable=False),
        sa.Column("objective", sa.String(60), nullable=False),
        sa.Column("solver_status", sa.String(30), nullable=False),
        sa.Column("objective_value", sa.Float(), nullable=True),
        sa.Column("first_gate_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_gate_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("depot_dispatch_span_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cost_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("comparison_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("job_id", "version_number", name="uq_route_version_job_number"),
    )
    op.create_index("ix_route_version_job_id", "route_version", ["job_id"])
    op.create_index("ix_route_version_job_created", "route_version", ["job_id", "created_at"])

    op.create_table(
        "optimization_run",
        sa.Column("optimization_run_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_type", sa.String(30), nullable=False, server_default="INITIAL"),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("route_version_id", sa.String(64), sa.ForeignKey("route_version.route_version_id"), nullable=True),
        sa.Column("state_snapshot_id", sa.String(64), sa.ForeignKey("operational_state_snapshot.state_snapshot_id"), nullable=False),
        sa.Column("parameter_snapshot_id", sa.String(64), sa.ForeignKey("optimization_parameter_snapshot.parameter_snapshot_id"), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("solve_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("solver_status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("objective", sa.String(60), nullable=False),
        sa.Column("objective_value", sa.Float(), nullable=True),
        sa.Column("coordination_iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("solver_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_optimization_run_job_id", "optimization_run", ["job_id"])
    op.create_index("ix_optimization_run_status", "optimization_run", ["status"])
    op.create_index("ix_optimization_run_job_started", "optimization_run", ["job_id", "start_time"])

    op.create_table(
        "lo_operational_state",
        sa.Column("lo_state_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("loading_order_id", sa.String(120), nullable=False),
        sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("spbu_name_snapshot", sa.String(255), nullable=True),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("product_name_snapshot", sa.String(255), nullable=True),
        sa.Column("volume_kl", sa.Float(), nullable=False),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("operating_date", sa.Date(), nullable=False),
        sa.Column("source_prediction_run_id", sa.String(64), sa.ForeignKey("prediction_run.id"), nullable=False),
        sa.Column("phase6_predicted_shipment_id", sa.String(120), nullable=True),
        sa.Column("phase6_predicted_vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("phase6_predicted_spbu_pairing", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("phase6_shipment_confidence", sa.Float(), nullable=True),
        sa.Column("phase6_vehicle_assignment_confidence", sa.Float(), nullable=True),
        sa.Column("phase6_model_id", sa.String(64), nullable=True),
        sa.Column("current_vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("current_trip_number", sa.Integer(), nullable=True),
        sa.Column("current_shipment_id", sa.String(120), nullable=True),
        sa.Column("current_compartment_id", sa.String(80), nullable=True),
        sa.Column("planned_gate_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PLANNED"),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("frozen_reason", sa.String(80), nullable=True),
        sa.Column("actual_gate_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(120), nullable=True),
        sa.UniqueConstraint("job_id", "loading_order_id", name="uq_lo_operational_state_job_lo"),
    )
    for name, columns in (
        ("ix_lo_operational_state_job_id", ["job_id"]),
        ("ix_lo_operational_state_loading_order_id", ["loading_order_id"]),
        ("ix_lo_operational_state_spbu_id", ["spbu_id"]),
        ("ix_lo_operational_state_depot_id", ["depot_id"]),
        ("ix_lo_operational_state_phase6_predicted_shipment_id", ["phase6_predicted_shipment_id"]),
        ("ix_lo_operational_state_current_vehicle_id", ["current_vehicle_id"]),
        ("ix_lo_operational_state_status", ["status"]),
        ("ix_lo_operational_state_job_status", ["job_id", "status"]),
    ):
        op.create_index(name, "lo_operational_state", columns)

    op.create_table(
        "vehicle_operational_state",
        sa.Column("vehicle_state_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("registration_snapshot", sa.String(80), nullable=True),
        sa.Column("vehicle_class", sa.Integer(), nullable=True),
        sa.Column("tag_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("capacity_kl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("number_of_compartments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compartment_configuration", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("planned_eta_depot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("system_eta_depot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_eta_override", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_eta_depot", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operational_status", sa.String(30), nullable=False, server_default="READY"),
        sa.Column("working_time_limit_minutes", sa.Integer(), nullable=False, server_default="720"),
        sa.Column("working_time_used_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("working_time_remaining_minutes", sa.Integer(), nullable=False, server_default="720"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(120), nullable=True),
        sa.UniqueConstraint("job_id", "mt_id", name="uq_vehicle_operational_state_job_mt"),
    )
    op.create_index("ix_vehicle_operational_state_job_id", "vehicle_operational_state", ["job_id"])
    op.create_index("ix_vehicle_operational_state_mt_id", "vehicle_operational_state", ["mt_id"])
    op.create_index("ix_vehicle_operational_state_effective_eta_depot", "vehicle_operational_state", ["effective_eta_depot"])
    op.create_index("ix_vehicle_operational_state_job_status", "vehicle_operational_state", ["job_id", "operational_status"])

    op.create_table(
        "actual_vehicle_event",
        sa.Column("vehicle_event_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="USER"),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_actual_vehicle_event_job_id", "actual_vehicle_event", ["job_id"])
    op.create_index("ix_actual_vehicle_event_mt_id", "actual_vehicle_event", ["mt_id"])
    op.create_index("ix_actual_vehicle_event_event_type", "actual_vehicle_event", ["event_type"])

    op.create_table(
        "master_loading_bay",
        sa.Column("master_bay_id", sa.String(64), primary_key=True),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("bay_id", sa.String(80), nullable=False),
        sa.Column("bay_name", sa.String(160), nullable=False),
        sa.Column("all_products_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("operational_start", sa.Time(), nullable=False, server_default="05:00"),
        sa.Column("operational_end", sa.Time(), nullable=False, server_default="22:00"),
        sa.Column("number_of_loading_arms", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("loading_mode", sa.String(20), nullable=False, server_default="SEQUENTIAL"),
        sa.Column("active_status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("depot_id", "bay_id", name="uq_master_loading_bay_depot_bay"),
    )
    op.create_index("ix_master_loading_bay_depot_id", "master_loading_bay", ["depot_id"])
    op.create_index("ix_master_loading_bay_bay_id", "master_loading_bay", ["bay_id"])

    op.create_table(
        "loading_bay_product_compatibility",
        sa.Column("master_bay_id", sa.String(64), sa.ForeignKey("master_loading_bay.master_bay_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), primary_key=True),
    )
    op.create_table(
        "product_compartment_loading_duration",
        sa.Column("loading_duration_id", sa.String(64), primary_key=True),
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=False),
        sa.Column("duration_minutes_per_compartment", sa.Integer(), nullable=False),
        sa.Column("active_status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("depot_id", "product_id", name="uq_product_compartment_loading_duration"),
    )
    op.create_index("ix_product_compartment_loading_duration_depot_id", "product_compartment_loading_duration", ["depot_id"])
    op.create_index("ix_product_compartment_loading_duration_product_id", "product_compartment_loading_duration", ["product_id"])

    op.create_table(
        "actual_bay_state",
        sa.Column("actual_bay_state_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("master_bay_id", sa.String(64), sa.ForeignKey("master_loading_bay.master_bay_id"), nullable=False),
        sa.Column("current_vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("current_compartment_id", sa.String(80), nullable=True),
        sa.Column("current_product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("remaining_loading_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_queue_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="USER"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(120), nullable=True),
        sa.UniqueConstraint("job_id", "master_bay_id", name="uq_actual_bay_state_job_bay"),
    )
    op.create_index("ix_actual_bay_state_job_id", "actual_bay_state", ["job_id"])
    op.create_index("ix_actual_bay_state_master_bay_id", "actual_bay_state", ["master_bay_id"])

    op.create_table(
        "optimization_initial_queue",
        sa.Column("initial_queue_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("master_bay_id", sa.String(64), sa.ForeignKey("master_loading_bay.master_bay_id"), nullable=False),
        sa.Column("queue_position", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("compartment_id", sa.String(80), nullable=True),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("estimated_loading_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("state_effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "master_bay_id", "queue_position", name="uq_optimization_initial_queue_position"),
    )
    op.create_index("ix_optimization_initial_queue_job_id", "optimization_initial_queue", ["job_id"])
    op.create_index("ix_optimization_initial_queue_master_bay_id", "optimization_initial_queue", ["master_bay_id"])

    op.create_table(
        "route_version_trip",
        sa.Column("route_version_trip_id", sa.String(64), primary_key=True),
        sa.Column("route_version_id", sa.String(64), sa.ForeignKey("route_version.route_version_id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("trip_number", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.String(120), nullable=False),
        sa.Column("vehicle_ready_at_depot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queue_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loading_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("loading_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gate_out", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estimated_return_depot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("distance_meters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("driving_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("service_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queue_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loading_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operating_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assignment_status", sa.String(30), nullable=False, server_default="PLANNED"),
        sa.Column("route_geometry", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("route_geometry_source", sa.String(80), nullable=True),
        sa.Column("cost_breakdown", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("route_version_id", "vehicle_id", "trip_number", name="uq_route_version_trip_vehicle_number"),
    )
    op.create_index("ix_route_version_trip_route_version_id", "route_version_trip", ["route_version_id"])
    op.create_index("ix_route_version_trip_vehicle_id", "route_version_trip", ["vehicle_id"])
    op.create_index("ix_route_version_trip_shipment_id", "route_version_trip", ["shipment_id"])
    op.create_index("ix_route_version_trip_gate_out", "route_version_trip", ["route_version_id", "gate_out"])

    op.create_table(
        "route_version_stop",
        sa.Column("route_version_stop_id", sa.String(64), primary_key=True),
        sa.Column("route_version_trip_id", sa.String(64), sa.ForeignKey("route_version_trip.route_version_trip_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("stop_type", sa.String(30), nullable=False, server_default="SPBU"),
        sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=True),
        sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departure_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distance_from_previous_meters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("travel_from_previous_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loading_order_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("products", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("volume_kl", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint("route_version_trip_id", "sequence_number", name="uq_route_version_stop_sequence"),
    )
    op.create_index("ix_route_version_stop_route_version_trip_id", "route_version_stop", ["route_version_trip_id"])

    op.create_table(
        "route_version_lo_assignment",
        sa.Column("route_version_lo_assignment_id", sa.String(64), primary_key=True),
        sa.Column("route_version_id", sa.String(64), sa.ForeignKey("route_version.route_version_id", ondelete="CASCADE"), nullable=False),
        sa.Column("route_version_trip_id", sa.String(64), sa.ForeignKey("route_version_trip.route_version_trip_id", ondelete="CASCADE"), nullable=True),
        sa.Column("loading_order_id", sa.String(120), nullable=False),
        sa.Column("vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("trip_number", sa.Integer(), nullable=True),
        sa.Column("shipment_id", sa.String(120), nullable=True),
        sa.Column("compartment_id", sa.String(80), nullable=True),
        sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("volume_kl", sa.Float(), nullable=False),
        sa.Column("stop_sequence", sa.Integer(), nullable=True),
        sa.Column("planned_gate_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assignment_status", sa.String(30), nullable=False, server_default="PLANNED"),
        sa.Column("dropped_reason_code", sa.String(80), nullable=True),
        sa.Column("dropped_reason_description", sa.Text(), nullable=True),
        sa.Column("phase6_deviation", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("route_version_id", "loading_order_id", name="uq_route_version_lo_assignment"),
    )
    op.create_index("ix_route_version_lo_assignment_route_version_id", "route_version_lo_assignment", ["route_version_id"])
    op.create_index("ix_route_version_lo_assignment_route_version_trip_id", "route_version_lo_assignment", ["route_version_trip_id"])
    op.create_index("ix_route_version_lo_assignment_loading_order_id", "route_version_lo_assignment", ["loading_order_id"])
    op.create_index("ix_route_version_lo_status", "route_version_lo_assignment", ["route_version_id", "assignment_status"])

    op.create_table(
        "route_version_vehicle_assignment",
        sa.Column("route_version_vehicle_assignment_id", sa.String(64), primary_key=True),
        sa.Column("route_version_id", sa.String(64), sa.ForeignKey("route_version.route_version_id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trip_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_kl", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_distance_meters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_operating_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("working_time_remaining_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activation_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("system_eta_depot", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("route_version_id", "vehicle_id", name="uq_route_version_vehicle_assignment"),
    )
    op.create_index("ix_route_version_vehicle_assignment_route_version_id", "route_version_vehicle_assignment", ["route_version_id"])
    op.create_index("ix_route_version_vehicle_assignment_vehicle_id", "route_version_vehicle_assignment", ["vehicle_id"])

    op.create_table(
        "optimization_bay_assignment",
        sa.Column("bay_assignment_id", sa.String(64), primary_key=True),
        sa.Column("route_version_id", sa.String(64), sa.ForeignKey("route_version.route_version_id", ondelete="CASCADE"), nullable=False),
        sa.Column("route_version_trip_id", sa.String(64), sa.ForeignKey("route_version_trip.route_version_trip_id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("master_bay_id", sa.String(64), sa.ForeignKey("master_loading_bay.master_bay_id"), nullable=False),
        sa.Column("vehicle_ready_at_depot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queue_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("loading_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("loading_finish", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gate_out", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queue_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loading_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_optimization_bay_assignment_route_version_id", "optimization_bay_assignment", ["route_version_id"])
    op.create_index("ix_optimization_bay_assignment_master_bay_id", "optimization_bay_assignment", ["master_bay_id"])

    op.create_table(
        "optimization_bay_operation",
        sa.Column("bay_operation_id", sa.String(64), primary_key=True),
        sa.Column("bay_assignment_id", sa.String(64), sa.ForeignKey("optimization_bay_assignment.bay_assignment_id", ondelete="CASCADE"), nullable=False),
        sa.Column("master_bay_id", sa.String(64), sa.ForeignKey("master_loading_bay.master_bay_id"), nullable=False),
        sa.Column("compartment_id", sa.String(80), nullable=False),
        sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=True),
        sa.Column("loading_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("loading_finish", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("loading_mode", sa.String(20), nullable=False, server_default="SEQUENTIAL"),
    )
    op.create_index("ix_optimization_bay_operation_bay_assignment_id", "optimization_bay_operation", ["bay_assignment_id"])
    op.create_index("ix_optimization_bay_operation_master_bay_id", "optimization_bay_operation", ["master_bay_id"])
    op.create_index("ix_optimization_bay_operation_bay_start", "optimization_bay_operation", ["master_bay_id", "loading_start"])

    op.create_table(
        "route_matrix_cache",
        sa.Column("route_matrix_cache_id", sa.String(64), primary_key=True),
        sa.Column("cache_key", sa.String(128), nullable=False, unique=True),
        sa.Column("origin_location_id", sa.String(120), nullable=False),
        sa.Column("destination_location_id", sa.String(120), nullable=False),
        sa.Column("departure_time_bucket", sa.DateTime(timezone=True), nullable=True),
        sa.Column("route_vehicle_mode", sa.String(30), nullable=False, server_default="GENERAL_VEHICLE"),
        sa.Column("traffic_aware", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("distance_meters", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("route_polyline", sa.Text(), nullable=True),
        sa.Column("route_geometry", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("provider", sa.String(60), nullable=False, server_default="GOOGLE_ROUTES"),
        sa.Column("response_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_route_matrix_cache_cache_key", "route_matrix_cache", ["cache_key"])
    op.create_index("ix_phase7_route_matrix_cache_expiry", "route_matrix_cache", ["expires_at"])

    op.create_table(
        "route_api_request_log",
        sa.Column("request_log_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("optimization_job.job_id"), nullable=True),
        sa.Column("request_type", sa.String(60), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False, server_default="GOOGLE_ROUTES"),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("requested_pair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_route_api_request_log_job_id", "route_api_request_log", ["job_id"])
    op.create_index("ix_route_api_request_log_request_fingerprint", "route_api_request_log", ["request_fingerprint"])


def downgrade() -> None:
    for table in (
        "route_api_request_log",
        "route_matrix_cache",
        "optimization_bay_operation",
        "optimization_bay_assignment",
        "route_version_vehicle_assignment",
        "route_version_lo_assignment",
        "route_version_stop",
        "route_version_trip",
        "optimization_initial_queue",
        "actual_bay_state",
        "product_compartment_loading_duration",
        "loading_bay_product_compatibility",
        "master_loading_bay",
        "actual_vehicle_event",
        "vehicle_operational_state",
        "lo_operational_state",
        "optimization_run",
        "route_version",
        "optimization_parameter_snapshot",
        "optimization_vehicle_cost_rule",
        "optimization_parameter_value",
        "optimization_parameter_profile",
        "operational_state_snapshot",
        "optimization_job",
    ):
        op.drop_table(table)
