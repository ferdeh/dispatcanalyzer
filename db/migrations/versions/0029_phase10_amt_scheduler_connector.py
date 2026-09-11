"""Add Phase 10 AMT Scheduler connector security and observability tables.

Revision ID: 0029_phase10_amt_connector
Revises: 0028_shipment_personnel
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_phase10_amt_connector"
down_revision = "0028_shipment_personnel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_client",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("integration_name", sa.String(80), nullable=False),
        sa.Column("client_name", sa.String(160), nullable=False),
        sa.Column("client_code", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("token_hint", sa.String(24), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default=sa.text("'[\"read\"]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("token_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.UniqueConstraint("integration_name", "client_code", name="uq_integration_client_name_code"),
    )
    op.create_index("ix_integration_client_integration_name", "integration_client", ["integration_name"])
    op.create_index("ix_integration_client_client_code", "integration_client", ["client_code"])
    op.create_index("ix_integration_client_token_hash", "integration_client", ["token_hash"])
    op.create_index("ix_integration_client_active", "integration_client", ["integration_name", "active"])

    op.create_table(
        "integration_dataset_version",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("integration_name", sa.String(80), nullable=False),
        sa.Column("dataset_name", sa.String(80), nullable=False),
        sa.Column("data_version", sa.String(80), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("integration_name", "dataset_name", name="uq_integration_dataset_name"),
    )
    op.create_index("ix_integration_dataset_version_integration_name", "integration_dataset_version", ["integration_name"])
    op.create_index("ix_integration_dataset_version_dataset_name", "integration_dataset_version", ["dataset_name"])

    op.create_table(
        "integration_api_log",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("integration_name", sa.String(80), nullable=False),
        sa.Column("client_id", sa.String(64), sa.ForeignKey("integration_client.id"), nullable=True),
        sa.Column("http_method", sa.String(12), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column("query_params", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(80), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_integration_api_log_request_id", "integration_api_log", ["request_id"], unique=True)
    op.create_index("ix_integration_api_log_integration_name", "integration_api_log", ["integration_name"])
    op.create_index("ix_integration_api_log_client_id", "integration_api_log", ["client_id"])
    op.create_index("ix_integration_api_log_endpoint", "integration_api_log", ["endpoint"])
    op.create_index("ix_integration_api_log_requested_at", "integration_api_log", ["requested_at"])
    op.create_index("ix_integration_api_log_response_status", "integration_api_log", ["response_status"])
    op.create_index("ix_integration_api_log_requested", "integration_api_log", ["integration_name", "requested_at"])
    op.create_index("ix_integration_api_log_status", "integration_api_log", ["integration_name", "response_status"])
    op.create_index("ix_integration_api_log_client_requested", "integration_api_log", ["client_id", "requested_at"])

    # Existing source tables already index most lookup keys. These compound
    # indexes keep Phase 10 date-scoped history and route timeline reads bounded.
    op.create_index("ix_fact_shipment_depot_operating_date", "fact_shipment", ["depot_id", "operating_date"])
    op.create_index("ix_fact_shipment_created_at", "fact_shipment", ["created_at"])
    op.create_index("ix_fact_loading_order_line_shipment_created", "fact_loading_order_line", ["shipment_id", "created_at"])
    op.create_index("ix_route_version_trip_version_vehicle_gate", "route_version_trip", ["route_version_id", "vehicle_id", "gate_out"])
    op.create_index("ix_manual_dispatch_trip_vehicle_departure_return", "manual_dispatch_trip", ["dispatch_vehicle_id", "departure_datetime", "estimated_return_datetime"])


def downgrade() -> None:
    op.drop_index("ix_manual_dispatch_trip_vehicle_departure_return", table_name="manual_dispatch_trip")
    op.drop_index("ix_route_version_trip_version_vehicle_gate", table_name="route_version_trip")
    op.drop_index("ix_fact_loading_order_line_shipment_created", table_name="fact_loading_order_line")
    op.drop_index("ix_fact_shipment_created_at", table_name="fact_shipment")
    op.drop_index("ix_fact_shipment_depot_operating_date", table_name="fact_shipment")
    op.drop_table("integration_api_log")
    op.drop_table("integration_dataset_version")
    op.drop_table("integration_client")
