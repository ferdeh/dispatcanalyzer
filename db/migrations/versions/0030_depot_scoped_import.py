"""Make imported master identities depot scoped and add durable import jobs.

Revision ID: 0030_depot_scoped_import
Revises: 0029_phase10_amt_connector
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_depot_scoped_import"
down_revision = "0029_phase10_amt_connector"
branch_labels = None
depends_on = None


MT_REFERENCE_COLUMNS = (
    ("actual_bay_state", "current_vehicle_id"),
    ("actual_vehicle_event", "mt_id"),
    ("affinity_analysis_config", "selected_mt_id"),
    ("bridge_mt_tag", "mt_id"),
    ("fact_gps_event", "mt_id"),
    ("fact_shipment", "mt_id"),
    ("fact_spbu_mt_pair", "mt_id"),
    ("fact_spbu_mt_profile", "dominant_mt_id"),
    ("fact_spbu_mt_temporal_profile", "mt_id"),
    ("fact_spbu_visit", "mt_id"),
    ("lo_operational_state", "current_vehicle_id"),
    ("lo_operational_state", "phase6_predicted_vehicle_id"),
    ("manual_dispatch_vehicle", "mt_id"),
    ("ml_spbu_concentration_profile", "dominant_mt_id"),
    ("optimization_initial_queue", "vehicle_id"),
    ("prediction_assignment", "final_vehicle_id"),
    ("prediction_assignment", "original_vehicle_id"),
    ("prediction_mt_candidate", "vehicle_id"),
    ("prediction_trip", "vehicle_id"),
    ("route_version_lo_assignment", "vehicle_id"),
    ("route_version_trip", "vehicle_id"),
    ("route_version_vehicle_assignment", "vehicle_id"),
    ("vehicle_operational_state", "mt_id"),
)

SPBU_REFERENCE_COLUMNS = (
    ("affinity_analysis_config", "selected_spbu_id"),
    ("bridge_spbu_tag", "spbu_id"),
    ("fact_loading_order_line", "spbu_id"),
    ("fact_shipment_spbu", "spbu_id"),
    ("fact_shipment_stop", "spbu_id"),
    ("fact_spbu_mt_pair", "spbu_id"),
    ("fact_spbu_mt_profile", "spbu_id"),
    ("fact_spbu_mt_temporal_profile", "spbu_id"),
    ("fact_spbu_pair", "spbu_a_id"),
    ("fact_spbu_pair", "spbu_b_id"),
    ("fact_spbu_transition", "from_spbu_id"),
    ("fact_spbu_transition", "to_spbu_id"),
    ("fact_spbu_visit", "spbu_id"),
    ("lo_operational_state", "spbu_id"),
    ("manual_dispatch_loading_order", "spbu_id"),
    ("ml_spbu_cluster_assignment", "spbu_id"),
    ("ml_spbu_concentration_profile", "spbu_id"),
    ("prediction_shipment_line", "spbu_id"),
    ("route_version_lo_assignment", "spbu_id"),
    ("route_version_stop", "spbu_id"),
    ("spbu_geofence", "spbu_id"),
    ("spbu_identifier_alias", "spbu_id"),
)


def _rekey_references(table_name: str, column_name: str, map_table: str) -> None:
    op.execute(
        sa.text(
            f'UPDATE "{table_name}" child SET "{column_name}" = ids.new_id '
            f'FROM {map_table} ids WHERE child."{column_name}" = ids.old_id'
        )
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.add_column("import_audit", sa.Column("stored_path", sa.Text(), nullable=True))
    op.add_column("import_audit", sa.Column("file_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("import_audit", sa.Column("processed_rows", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("import_audit", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("import_audit", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("import_audit", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("import_audit", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))

    # Preserve all relationships while converting legacy global IDs to the same
    # deterministic depot-scoped IDs used by the v2 importer.
    op.execute(
        """
        CREATE TEMP TABLE mt_id_map ON COMMIT DROP AS
        SELECT mt_id AS old_id,
               'mt_' || substr(encode(digest(convert_to(depot_id || '|' ||
                    vehicle_registration, 'UTF8'), 'sha1'), 'hex'), 1, 24) AS new_id
        FROM master_mt
        WHERE depot_id IS NOT NULL
          AND nullif(btrim(vehicle_registration), '') IS NOT NULL;

        CREATE TEMP TABLE spbu_id_map ON COMMIT DROP AS
        SELECT spbu_id AS old_id,
               'spbu_' || substr(encode(digest(convert_to(primary_depot_id || '|' || spbu_code,
                    'UTF8'), 'sha1'), 'hex'), 1, 24) AS new_id
        FROM master_spbu
        WHERE primary_depot_id IS NOT NULL;

        SET LOCAL session_replication_role = replica;
        """
    )
    for table_name, column_name in MT_REFERENCE_COLUMNS:
        _rekey_references(table_name, column_name, "mt_id_map")
    for table_name, column_name in SPBU_REFERENCE_COLUMNS:
        _rekey_references(table_name, column_name, "spbu_id_map")
    op.execute(
        """
        UPDATE master_mt parent SET mt_id = ids.new_id
        FROM mt_id_map ids WHERE parent.mt_id = ids.old_id;
        UPDATE master_spbu parent SET spbu_id = ids.new_id
        FROM spbu_id_map ids WHERE parent.spbu_id = ids.old_id;
        SET LOCAL session_replication_role = origin;
        """
    )

    op.drop_index("uq_master_mt_registration_active", table_name="master_mt")
    op.drop_constraint("master_spbu_spbu_code_key", "master_spbu", type_="unique")
    op.create_unique_constraint(
        "uq_master_mt_depot_registration", "master_mt", ["depot_id", "vehicle_registration"]
    )
    op.create_unique_constraint(
        "uq_master_spbu_depot_code", "master_spbu", ["primary_depot_id", "spbu_code"]
    )

    op.add_column("fact_loading_order_line", sa.Column("loading_order_id", sa.String(64), nullable=True))
    op.add_column(
        "fact_loading_order_line",
        sa.Column("depot_id", sa.String(64), sa.ForeignKey("master_depot.depot_id"), nullable=True),
    )
    op.execute(
        """
        UPDATE fact_loading_order_line line
        SET depot_id = shipment.depot_id
        FROM fact_shipment shipment
        WHERE shipment.shipment_id = line.shipment_id;

        DELETE FROM fact_loading_order_line older
        USING fact_loading_order_line newer
        WHERE older.loading_order_number = newer.loading_order_number
          AND coalesce(older.depot_id, older.source_depot_name) =
              coalesce(newer.depot_id, newer.source_depot_name)
          AND (coalesce(older.created_at, '-infinity'::timestamptz), older.ctid) <
              (coalesce(newer.created_at, '-infinity'::timestamptz), newer.ctid);

        UPDATE fact_loading_order_line
        SET loading_order_id = 'lo_' || substr(
            encode(digest(convert_to(coalesce(depot_id, source_depot_name) || '|' || loading_order_number,
            'UTF8'), 'sha1'), 'hex'), 1, 24
        );
        """
    )
    op.alter_column("fact_loading_order_line", "loading_order_id", existing_type=sa.String(64), nullable=False)
    op.drop_constraint("fact_loading_order_line_pkey", "fact_loading_order_line", type_="primary")
    op.create_primary_key("fact_loading_order_line_pkey", "fact_loading_order_line", ["loading_order_id"])
    op.create_unique_constraint(
        "uq_loading_order_depot_number", "fact_loading_order_line", ["depot_id", "loading_order_number"]
    )
    op.create_index(
        "ix_fact_loading_order_line_loading_order_number",
        "fact_loading_order_line",
        ["loading_order_number"],
    )
    op.create_index("ix_fact_loading_order_line_depot_id", "fact_loading_order_line", ["depot_id"])

    # Remove legacy PROJECT tag bridges that are no longer present in the latest
    # raw master row, then restore any expected bridge that was missing.
    for owner_table, bridge_table, owner_id in (
        ("master_mt", "bridge_mt_tag", "mt_id"),
        ("master_spbu", "bridge_spbu_tag", "spbu_id"),
    ):
        op.execute(
            sa.text(
                f"""
                DELETE FROM {bridge_table} bridge
                USING {owner_table} owner, master_tag tag, master_tag_type tag_type
                WHERE bridge.{owner_id} = owner.{owner_id}
                  AND bridge.tag_id = tag.tag_id
                  AND tag.tag_type_id = tag_type.tag_type_id
                  AND tag_type.code = 'PROJECT'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM unnest(string_to_array(coalesce(owner.project_tag_raw, ''), ',')) raw(value)
                    WHERE regexp_replace(upper(btrim(raw.value)), '[^A-Z0-9]+', '', 'g') = tag.normalized_tag
                  );

                INSERT INTO {bridge_table} ({owner_id}, tag_id, source_import_id)
                SELECT owner.{owner_id}, tag.tag_id, owner.source_import_id
                FROM {owner_table} owner
                CROSS JOIN LATERAL unnest(string_to_array(coalesce(owner.project_tag_raw, ''), ',')) raw(value)
                JOIN master_tag tag
                  ON tag.normalized_tag = regexp_replace(upper(btrim(raw.value)), '[^A-Z0-9]+', '', 'g')
                JOIN master_tag_type tag_type ON tag_type.tag_type_id = tag.tag_type_id
                WHERE tag_type.code = 'PROJECT'
                  AND btrim(raw.value) <> ''
                ON CONFLICT DO NOTHING;
                """
            )
        )


def downgrade() -> None:
    op.drop_index("ix_fact_loading_order_line_depot_id", table_name="fact_loading_order_line")
    op.drop_index("ix_fact_loading_order_line_loading_order_number", table_name="fact_loading_order_line")
    op.drop_constraint("uq_loading_order_depot_number", "fact_loading_order_line", type_="unique")
    op.drop_constraint("fact_loading_order_line_pkey", "fact_loading_order_line", type_="primary")
    op.create_primary_key(
        "fact_loading_order_line_pkey",
        "fact_loading_order_line",
        ["loading_order_number", "source_depot_name"],
    )
    op.drop_column("fact_loading_order_line", "depot_id")
    op.drop_column("fact_loading_order_line", "loading_order_id")

    op.drop_constraint("uq_master_spbu_depot_code", "master_spbu", type_="unique")
    op.drop_constraint("uq_master_mt_depot_registration", "master_mt", type_="unique")
    op.create_unique_constraint("master_spbu_spbu_code_key", "master_spbu", ["spbu_code"])
    op.create_index(
        "uq_master_mt_registration_active",
        "master_mt",
        ["vehicle_registration"],
        unique=True,
        postgresql_where=sa.text("vehicle_registration IS NOT NULL"),
    )

    for column_name in (
        "attempt_count",
        "completed_at",
        "started_at",
        "error_message",
        "processed_rows",
        "file_size_bytes",
        "stored_path",
    ):
        op.drop_column("import_audit", column_name)
