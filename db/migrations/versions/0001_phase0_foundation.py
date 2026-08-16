"""phase 0 foundation schema

Revision ID: 0001_phase0
Revises:
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_phase0"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "import_audit",
        sa.Column("import_id", sa.String(64), primary_key=True),
        sa.Column("domain", sa.String(40), nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_checksum", sa.String(128)),
        sa.Column("sheet_name", sa.String(255)),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("uploaded_by", sa.String(120)),
        sa.Column("total_rows", sa.Integer, default=0),
        sa.Column("valid_rows", sa.Integer, default=0),
        sa.Column("warning_rows", sa.Integer, default=0),
        sa.Column("rejected_rows", sa.Integer, default=0),
        sa.Column("status", sa.String(40), nullable=False, default="STAGED"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("mapping_version", sa.String(40), nullable=False, default="phase0.v1"),
    )
    for table in ("stg_mt", "stg_spbu", "stg_loading_order", "stg_gps_data"):
        op.create_table(
            table,
            sa.Column("staging_id", sa.String(64), primary_key=True),
            sa.Column("import_id", sa.String(64), sa.ForeignKey("import_audit.import_id"), nullable=False, index=True),
            sa.Column("source_row_number", sa.Integer, nullable=False),
            sa.Column("raw_payload", sa.JSON, nullable=False),
            sa.Column("normalized_payload", sa.JSON, nullable=False, default={}),
            sa.Column("validation_status", sa.String(20), nullable=False, default="VALID"),
            sa.Column("validation_messages", sa.JSON, nullable=False, default=[]),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
    op.create_table("master_depot", sa.Column("depot_id", sa.String(64), primary_key=True), sa.Column("depot_code", sa.String(80), unique=True), sa.Column("depot_name", sa.String(255), nullable=False), sa.Column("latitude", sa.Float), sa.Column("longitude", sa.Float), sa.Column("region", sa.String(120)), sa.Column("timezone", sa.String(80), default="Asia/Jakarta"), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.execute("ALTER TABLE master_depot ADD COLUMN IF NOT EXISTS location geography(Point,4326)")
    op.create_table("depot_identifier_alias", sa.Column("depot_identifier_alias_id", sa.String(64), primary_key=True), sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False), sa.Column("identifier_type", sa.String(40), nullable=False), sa.Column("identifier_value", sa.String(255), nullable=False), sa.Column("normalized_identifier", sa.String(255), nullable=False), sa.Column("source_system", sa.String(80)), sa.Column("active_status", sa.String(20), default="ACTIVE"))
    op.create_table("master_tag_type", sa.Column("tag_type_id", sa.String(64), primary_key=True), sa.Column("code", sa.String(80), nullable=False, unique=True), sa.Column("name", sa.String(120), nullable=False), sa.Column("description", sa.Text), sa.Column("admin_editable", sa.Boolean, default=True))
    op.create_table("master_tag", sa.Column("tag_id", sa.String(64), primary_key=True), sa.Column("tag_type_id", sa.String(64), sa.ForeignKey("master_tag_type.tag_type_id")), sa.Column("tag_value", sa.String(255), nullable=False), sa.Column("normalized_tag", sa.String(255), nullable=False, unique=True), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("tag_alias", sa.Column("tag_alias_id", sa.String(64), primary_key=True), sa.Column("alias_value", sa.String(255), nullable=False), sa.Column("normalized_alias", sa.String(255), nullable=False, index=True), sa.Column("canonical_tag_id", sa.String(64), sa.ForeignKey("master_tag.tag_id"), nullable=False), sa.Column("source_domain", sa.String(80)), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("master_mt", sa.Column("mt_id", sa.String(64), primary_key=True), sa.Column("source_mt_id", sa.String(120)), sa.Column("vehicle_name_raw", sa.String(255), nullable=False), sa.Column("vehicle_registration", sa.String(80), index=True), sa.Column("capacity_label", sa.String(80)), sa.Column("vehicle_type_tag", sa.Integer), sa.Column("project_tag_raw", sa.Text), sa.Column("number_of_compartments", sa.Integer), sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id")), sa.Column("source_hub_id", sa.String(120)), sa.Column("assignee", sa.String(255)), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("effective_start_date", sa.Date), sa.Column("effective_end_date", sa.Date), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_index("uq_master_mt_registration_active", "master_mt", ["vehicle_registration"], unique=True, postgresql_where=sa.text("vehicle_registration IS NOT NULL"))
    op.create_table("bridge_mt_tag", sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id"), primary_key=True), sa.Column("tag_id", sa.String(64), sa.ForeignKey("master_tag.tag_id"), primary_key=True), sa.Column("source_import_id", sa.String(64)))
    op.create_table("master_spbu", sa.Column("spbu_id", sa.String(64), primary_key=True), sa.Column("spbu_code", sa.String(120), nullable=False, unique=True), sa.Column("spbu_name", sa.String(255)), sa.Column("address", sa.Text), sa.Column("city", sa.String(120)), sa.Column("latitude", sa.Float), sa.Column("longitude", sa.Float), sa.Column("source_coordinate", sa.String(255)), sa.Column("master_distance_km", sa.Float), sa.Column("master_travel_time_min", sa.Float), sa.Column("vehicle_type_tag", sa.Integer), sa.Column("project_tag_raw", sa.Text), sa.Column("primary_depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id")), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("official_window_start", sa.Time), sa.Column("official_window_end", sa.Time), sa.Column("effective_start_date", sa.Date), sa.Column("effective_end_date", sa.Date), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.execute("ALTER TABLE master_spbu ADD COLUMN IF NOT EXISTS location geography(Point,4326)")
    op.create_index("ix_master_spbu_location", "master_spbu", ["location"], postgresql_using="gist")
    op.create_table("bridge_spbu_tag", sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), primary_key=True), sa.Column("tag_id", sa.String(64), sa.ForeignKey("master_tag.tag_id"), primary_key=True), sa.Column("source_import_id", sa.String(64)))
    op.create_table("spbu_identifier_alias", sa.Column("spbu_identifier_alias_id", sa.String(64), primary_key=True), sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False), sa.Column("identifier_type", sa.String(40), nullable=False), sa.Column("identifier_value", sa.String(255), nullable=False), sa.Column("normalized_identifier", sa.String(255), nullable=False), sa.Column("source_system", sa.String(80)), sa.Column("active_status", sa.String(20), default="ACTIVE"))
    op.create_table("master_product", sa.Column("product_id", sa.String(64), primary_key=True), sa.Column("product_name", sa.String(255), nullable=False), sa.Column("normalized_product", sa.String(255), nullable=False, unique=True), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("product_alias", sa.Column("product_alias_id", sa.String(64), primary_key=True), sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id"), nullable=False), sa.Column("alias_value", sa.String(255), nullable=False), sa.Column("normalized_alias", sa.String(255), nullable=False), sa.Column("source_system", sa.String(80)), sa.Column("active_status", sa.String(20), default="ACTIVE"))
    op.create_table("master_personnel", sa.Column("personnel_id", sa.String(64), primary_key=True), sa.Column("source_parent_id", sa.String(120)), sa.Column("name", sa.String(255)), sa.Column("nip", sa.String(120)), sa.Column("role", sa.String(40), nullable=False), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("fact_shipment", sa.Column("shipment_id", sa.String(64), primary_key=True), sa.Column("source_shipment_id", sa.String(120), nullable=False, unique=True), sa.Column("operating_date", sa.Date), sa.Column("area_id", sa.String(80)), sa.Column("area", sa.String(120)), sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id")), sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id")), sa.Column("vehicle_registration", sa.String(80)), sa.Column("vehicle_mapping_status", sa.String(40), default="UNMATCHED"), sa.Column("vehicle_type_tag_observed", sa.String(80)), sa.Column("project_tag_raw", sa.Text), sa.Column("validation_datetime", sa.DateTime(timezone=True)), sa.Column("gate_out_datetime", sa.DateTime(timezone=True)), sa.Column("shipment_end_datetime", sa.DateTime(timezone=True)), sa.Column("driver_id", sa.String(64), sa.ForeignKey("master_personnel.personnel_id")), sa.Column("assistant_id", sa.String(64), sa.ForeignKey("master_personnel.personnel_id")), sa.Column("status", sa.String(80)), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("fact_loading_order_line", sa.Column("loading_order_number", sa.String(120), primary_key=True), sa.Column("shipment_id", sa.String(64), sa.ForeignKey("fact_shipment.shipment_id"), nullable=False), sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id")), sa.Column("spbu_mapping_status", sa.String(40), default="UNMATCHED"), sa.Column("source_spbu_code", sa.String(120)), sa.Column("shipto", sa.String(120)), sa.Column("product_id", sa.String(64), sa.ForeignKey("master_product.product_id")), sa.Column("source_product_name", sa.String(255)), sa.Column("quantity", sa.Float), sa.Column("status", sa.String(80)), sa.Column("source_distance_km", sa.Float), sa.Column("actual_km", sa.Float), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("fact_shipment_spbu", sa.Column("shipment_id", sa.String(64), sa.ForeignKey("fact_shipment.shipment_id"), primary_key=True), sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), primary_key=True), sa.Column("assignment_source", sa.String(40), default="LO"), sa.Column("source_import_id", sa.String(64)))
    op.create_table("fact_gps_event", sa.Column("gps_event_id", sa.String(64), primary_key=True), sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id")), sa.Column("vehicle_registration", sa.String(80)), sa.Column("vehicle_mapping_status", sa.String(40), default="UNMATCHED"), sa.Column("source_device_id", sa.String(120)), sa.Column("event_datetime", sa.DateTime(timezone=True)), sa.Column("latitude", sa.Float), sa.Column("longitude", sa.Float), sa.Column("speed", sa.Float), sa.Column("heading", sa.Float), sa.Column("ignition_status", sa.String(40)), sa.Column("event_type", sa.String(80)), sa.Column("nearest_spbu_id", sa.String(64)), sa.Column("nearest_depot_id", sa.String(64)), sa.Column("distance_to_spbu_m", sa.Float), sa.Column("distance_to_depot_m", sa.Float), sa.Column("source_import_id", sa.String(64)), sa.Column("raw_event_reference", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.execute("ALTER TABLE fact_gps_event ADD COLUMN IF NOT EXISTS location geography(Point,4326)")
    op.create_table("spbu_geofence", sa.Column("spbu_geofence_id", sa.String(64), primary_key=True), sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id"), nullable=False), sa.Column("geofence_type", sa.String(40), default="CIRCLE"), sa.Column("radius_m", sa.Float, nullable=False), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("effective_start", sa.Date), sa.Column("effective_end", sa.Date))
    op.execute("ALTER TABLE spbu_geofence ADD COLUMN IF NOT EXISTS geometry geography(Geometry,4326)")
    op.create_table("depot_geofence", sa.Column("depot_geofence_id", sa.String(64), primary_key=True), sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=False), sa.Column("geofence_type", sa.String(40), default="CIRCLE"), sa.Column("radius_m", sa.Float, nullable=False), sa.Column("active_status", sa.String(20), default="ACTIVE"), sa.Column("effective_start", sa.Date), sa.Column("effective_end", sa.Date))
    op.execute("ALTER TABLE depot_geofence ADD COLUMN IF NOT EXISTS geometry geography(Geometry,4326)")
    op.create_table("fact_spbu_visit", sa.Column("spbu_visit_id", sa.String(64), primary_key=True), sa.Column("mt_id", sa.String(64), sa.ForeignKey("master_mt.mt_id")), sa.Column("shipment_id", sa.String(64), sa.ForeignKey("fact_shipment.shipment_id")), sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id")), sa.Column("arrival_datetime", sa.DateTime(timezone=True)), sa.Column("departure_datetime", sa.DateTime(timezone=True)), sa.Column("dwell_minutes", sa.Float), sa.Column("first_gps_event_id", sa.String(64)), sa.Column("last_gps_event_id", sa.String(64)), sa.Column("gps_event_count", sa.Integer), sa.Column("min_distance_to_spbu_m", sa.Float), sa.Column("visit_match_method", sa.String(80)), sa.Column("visit_confidence", sa.Float), sa.Column("source_import_id", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.create_table("fact_shipment_stop", sa.Column("shipment_stop_id", sa.String(64), primary_key=True), sa.Column("shipment_id", sa.String(64), sa.ForeignKey("fact_shipment.shipment_id")), sa.Column("spbu_id", sa.String(64), sa.ForeignKey("master_spbu.spbu_id")), sa.Column("stop_sequence", sa.Integer), sa.Column("arrival_datetime", sa.DateTime(timezone=True)), sa.Column("departure_datetime", sa.DateTime(timezone=True)), sa.Column("dwell_minutes", sa.Float), sa.Column("sequence_source", sa.String(40), default="UNKNOWN"), sa.Column("sequence_confidence", sa.Float), sa.Column("source_import_id", sa.String(64)))
    op.create_table("data_quality_issue", sa.Column("issue_id", sa.String(64), primary_key=True), sa.Column("entity_type", sa.String(80), nullable=False), sa.Column("entity_id", sa.String(120)), sa.Column("source_import_id", sa.String(64)), sa.Column("rule_code", sa.String(120), nullable=False), sa.Column("severity", sa.String(20), nullable=False), sa.Column("description", sa.Text, nullable=False), sa.Column("status", sa.String(40), default="OPEN"), sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("resolved_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for table in [
        "data_quality_issue", "fact_shipment_stop", "fact_spbu_visit", "depot_geofence", "spbu_geofence",
        "fact_gps_event", "fact_shipment_spbu", "fact_loading_order_line", "fact_shipment", "master_personnel",
        "product_alias", "master_product", "spbu_identifier_alias", "bridge_spbu_tag", "master_spbu",
        "bridge_mt_tag", "master_mt", "tag_alias", "master_tag", "master_tag_type", "depot_identifier_alias",
        "master_depot", "stg_gps_data", "stg_loading_order", "stg_spbu", "stg_mt", "import_audit",
    ]:
        op.drop_table(table)
