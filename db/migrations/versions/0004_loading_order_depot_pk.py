"""scope loading order primary key by depot name

Revision ID: 0004_loading_order_depot_pk
Revises: 0003_preserve_source_shipment_id
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_loading_order_depot_pk"
down_revision = "0003_preserve_source_shipment_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact_loading_order_line", sa.Column("source_depot_name", sa.String(255), nullable=True))
    op.execute(
        """
        UPDATE fact_loading_order_line line
        SET source_depot_name = COALESCE(depot.depot_name, shipment.source_shipment_id, 'UNKNOWN_DEPOT')
        FROM fact_shipment shipment
        LEFT JOIN master_depot depot ON depot.depot_id = shipment.depot_id
        WHERE line.shipment_id = shipment.shipment_id
          AND line.source_depot_name IS NULL;
        """
    )
    op.execute("UPDATE fact_loading_order_line SET source_depot_name = 'UNKNOWN_DEPOT' WHERE source_depot_name IS NULL OR btrim(source_depot_name) = ''")
    op.alter_column("fact_loading_order_line", "source_depot_name", existing_type=sa.String(255), nullable=False)
    op.drop_constraint("fact_loading_order_line_pkey", "fact_loading_order_line", type_="primary")
    op.create_primary_key("fact_loading_order_line_pkey", "fact_loading_order_line", ["loading_order_number", "source_depot_name"])


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM (
                    SELECT loading_order_number
                    FROM fact_loading_order_line
                    GROUP BY loading_order_number
                    HAVING count(*) > 1
                ) duplicates
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade: duplicate loading_order_number values exist across depots.';
            END IF;
        END $$;
        """
    )
    op.drop_constraint("fact_loading_order_line_pkey", "fact_loading_order_line", type_="primary")
    op.create_primary_key("fact_loading_order_line_pkey", "fact_loading_order_line", ["loading_order_number"])
    op.drop_column("fact_loading_order_line", "source_depot_name")
