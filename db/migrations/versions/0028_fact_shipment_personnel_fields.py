"""Store LO shipment personnel fields directly on fact_shipment.

Revision ID: 0028_shipment_personnel
Revises: 0027_phase9_route_alignment
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_shipment_personnel"
down_revision = "0027_phase9_route_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fact_shipment", sa.Column("driver_name", sa.String(length=255), nullable=True))
    op.add_column("fact_shipment", sa.Column("driver_nip", sa.String(length=120), nullable=True))
    op.add_column("fact_shipment", sa.Column("assistant_name", sa.String(length=255), nullable=True))
    op.add_column("fact_shipment", sa.Column("assistant_nip", sa.String(length=120), nullable=True))

    op.execute(
        """
        UPDATE fact_shipment shipment
        SET
            driver_name = COALESCE(shipment.driver_name, personnel.name),
            driver_nip = COALESCE(shipment.driver_nip, personnel.nip)
        FROM master_personnel personnel
        WHERE shipment.driver_id = personnel.personnel_id
        """
    )
    op.execute(
        """
        UPDATE fact_shipment shipment
        SET
            assistant_name = COALESCE(shipment.assistant_name, personnel.name),
            assistant_nip = COALESCE(shipment.assistant_nip, personnel.nip)
        FROM master_personnel personnel
        WHERE shipment.assistant_id = personnel.personnel_id
        """
    )


def downgrade() -> None:
    op.drop_column("fact_shipment", "assistant_nip")
    op.drop_column("fact_shipment", "assistant_name")
    op.drop_column("fact_shipment", "driver_nip")
    op.drop_column("fact_shipment", "driver_name")
