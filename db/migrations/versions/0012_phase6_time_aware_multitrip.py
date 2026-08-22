"""Phase 6 time-aware multi-trip prediction and Google Routes estimation.

Revision ID: 0012_phase6_multitrip
Revises: 0011_phase6_demo_lo
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_phase6_multitrip"
down_revision = "0011_phase6_demo_lo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "vehicle_height_mm",
        "vehicle_length_mm",
        "vehicle_weight_kg",
        "vehicle_width_mm",
        "vehicle_axle_count",
    ):
        op.add_column("master_mt", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("master_mt", sa.Column("hazmat_category", sa.Text(), nullable=True))
    op.add_column("master_mt", sa.Column("large_vehicle_profile_status", sa.String(length=20), nullable=False, server_default="INCOMPLETE"))

    op.add_column("prediction_run", sa.Column("final_prediction_snapshot", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("prediction_run", sa.Column("routing_configuration_snapshot", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("prediction_run", sa.Column("routing_metrics_snapshot", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("prediction_shipment", sa.Column("planned_start_datetime", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_prediction_shipment_planned_start_datetime", "prediction_shipment", ["planned_start_datetime"])
    op.add_column("prediction_shipment_line", sa.Column("shipment_start_datetime", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "google_routes_configuration",
        sa.Column("configuration_id", sa.String(length=64), primary_key=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("masked_api_key", sa.String(length=40), nullable=True),
        sa.Column("connection_status", sa.String(length=40), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("truck_routing_status", sa.String(length=40), nullable=False, server_default="UNKNOWN"),
        sa.Column("routing_mode", sa.String(length=20), nullable=False, server_default="AUTO"),
        sa.Column("routing_preference", sa.String(length=40), nullable=False, server_default="TRAFFIC_AWARE"),
        sa.Column("fallback_policy", sa.String(length=50), nullable=False, server_default="ALLOW_DRIVE_FALLBACK"),
        sa.Column("cache_ttl_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("departure_time_bucket_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("default_depot_processing_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("default_spbu_service_minutes", sa.Integer(), nullable=False, server_default="45"),
        sa.Column("default_return_processing_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("default_turnaround_buffer_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("default_route_duration_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("configuration_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_test_result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "route_estimation_cache",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("cache_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("origin_location_id", sa.String(length=120), nullable=False),
        sa.Column("destination_location_id", sa.String(length=120), nullable=False),
        sa.Column("origin_latitude", sa.Float(), nullable=False),
        sa.Column("origin_longitude", sa.Float(), nullable=False),
        sa.Column("destination_latitude", sa.Float(), nullable=False),
        sa.Column("destination_longitude", sa.Float(), nullable=False),
        sa.Column("departure_time_bucket", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vehicle_profile_hash", sa.String(length=64), nullable=False),
        sa.Column("routing_mode", sa.String(length=40), nullable=False),
        sa.Column("routing_preference", sa.String(length=40), nullable=False),
        sa.Column("distance_meters", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("static_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("provider_source", sa.String(length=80), nullable=False),
        sa.Column("response_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_route_estimation_cache_cache_key", "route_estimation_cache", ["cache_key"])
    op.create_index("ix_route_cache_expires", "route_estimation_cache", ["expires_at"])
    op.create_index("ix_route_cache_locations_mode", "route_estimation_cache", ["origin_location_id", "destination_location_id", "routing_mode"])

    op.create_table(
        "prediction_trip",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("prediction_run_id", sa.String(length=64), sa.ForeignKey("prediction_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prediction_shipment_id", sa.String(length=64), sa.ForeignKey("prediction_shipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trip_id", sa.String(length=120), nullable=False),
        sa.Column("trip_number", sa.Integer(), nullable=True),
        sa.Column("vehicle_id", sa.String(length=64), sa.ForeignKey("master_mt.mt_id"), nullable=True),
        sa.Column("planned_start_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("predicted_departure_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delay_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_visit_sequence", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("routing_provider", sa.String(length=80), nullable=True),
        sa.Column("routing_mode", sa.String(length=40), nullable=True),
        sa.Column("routing_preference", sa.String(length=40), nullable=True),
        sa.Column("large_vehicle_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("route_distance_meters", sa.Integer(), nullable=True),
        sa.Column("route_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("static_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("service_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("turnaround_buffer_seconds", sa.Integer(), nullable=True),
        sa.Column("total_cycle_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("estimated_return_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_available_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("routing_confidence", sa.String(length=20), nullable=True),
        sa.Column("route_estimation_source", sa.String(length=80), nullable=True),
        sa.Column("service_time_source", sa.String(length=80), nullable=True),
        sa.Column("assignment_status", sa.String(length=40), nullable=False, server_default="UNASSIGNED"),
        sa.Column("unassigned_reason", sa.String(length=120), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("warning_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("vehicle_profile_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("prediction_shipment_id", name="uq_prediction_trip_shipment"),
        sa.UniqueConstraint("prediction_run_id", "trip_id", name="uq_prediction_trip_run_number"),
    )
    op.create_index("ix_prediction_trip_prediction_run_id", "prediction_trip", ["prediction_run_id"])
    op.create_index("ix_prediction_trip_prediction_shipment_id", "prediction_trip", ["prediction_shipment_id"])
    op.create_index("ix_prediction_trip_trip_id", "prediction_trip", ["trip_id"])
    op.create_index("ix_prediction_trip_vehicle_id", "prediction_trip", ["vehicle_id"])
    op.create_index("ix_prediction_trip_planned_start_datetime", "prediction_trip", ["planned_start_datetime"])
    op.create_index("ix_prediction_trip_vehicle_departure", "prediction_trip", ["vehicle_id", "predicted_departure_datetime"])


def downgrade() -> None:
    op.drop_table("prediction_trip")
    op.drop_table("route_estimation_cache")
    op.drop_table("google_routes_configuration")
    op.drop_column("prediction_shipment_line", "shipment_start_datetime")
    op.drop_index("ix_prediction_shipment_planned_start_datetime", table_name="prediction_shipment")
    op.drop_column("prediction_shipment", "planned_start_datetime")
    op.drop_column("prediction_run", "routing_metrics_snapshot")
    op.drop_column("prediction_run", "routing_configuration_snapshot")
    op.drop_column("prediction_run", "final_prediction_snapshot")
    for name in (
        "large_vehicle_profile_status",
        "hazmat_category",
        "vehicle_axle_count",
        "vehicle_width_mm",
        "vehicle_weight_kg",
        "vehicle_length_mm",
        "vehicle_height_mm",
    ):
        op.drop_column("master_mt", name)
