"""use loading_order_number as loading-order line primary key

Revision ID: 0002_lo_number_pk
Revises: 0001_phase0
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_lo_number_pk"
down_revision = "0001_phase0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("fact_loading_order_line")}
    pk_columns = inspector.get_pk_constraint("fact_loading_order_line").get("constrained_columns") or []
    if "loading_order_line_id" not in columns and pk_columns == ["loading_order_number"]:
        op.alter_column("fact_loading_order_line", "loading_order_number", existing_type=sa.String(120), nullable=False)
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM fact_loading_order_line
                WHERE loading_order_number IS NULL OR btrim(loading_order_number) = ''
            ) THEN
                RAISE EXCEPTION 'Cannot promote loading_order_number to primary key: blank values exist.';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM (
                    SELECT loading_order_number
                    FROM fact_loading_order_line
                    GROUP BY loading_order_number
                    HAVING count(*) > 1
                ) duplicates
            ) THEN
                RAISE EXCEPTION 'Cannot promote loading_order_number to primary key: duplicate values exist.';
            END IF;
        END $$;
        """
    )
    op.drop_constraint("fact_loading_order_line_pkey", "fact_loading_order_line", type_="primary")
    op.alter_column("fact_loading_order_line", "loading_order_number", existing_type=sa.String(120), nullable=False)
    op.create_primary_key("fact_loading_order_line_pkey", "fact_loading_order_line", ["loading_order_number"])
    op.drop_column("fact_loading_order_line", "loading_order_line_id")


def downgrade() -> None:
    op.add_column("fact_loading_order_line", sa.Column("loading_order_line_id", sa.String(64)))
    op.execute(
        """
        UPDATE fact_loading_order_line
        SET loading_order_line_id = 'lo_' || substr(md5(shipment_id || '|' || loading_order_number), 1, 24)
        WHERE loading_order_line_id IS NULL;
        """
    )
    op.drop_constraint("fact_loading_order_line_pkey", "fact_loading_order_line", type_="primary")
    op.alter_column("fact_loading_order_line", "loading_order_line_id", existing_type=sa.String(64), nullable=False)
    op.create_primary_key("fact_loading_order_line_pkey", "fact_loading_order_line", ["loading_order_line_id"])
    op.alter_column("fact_loading_order_line", "loading_order_number", existing_type=sa.String(120), nullable=True)
