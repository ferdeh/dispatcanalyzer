"""Add canonical product snapshot to Phase 6 Loading Order lines.

Revision ID: 0018_phase6_lo_product
Revises: 0017_phase5_sufficiency_geo
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_phase6_lo_product"
down_revision = "0017_phase5_sufficiency_geo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prediction_shipment_line", sa.Column("product_id", sa.String(length=64), nullable=True))
    op.add_column("prediction_shipment_line", sa.Column("product_name", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_prediction_shipment_line_product",
        "prediction_shipment_line",
        "master_product",
        ["product_id"],
        ["product_id"],
    )
    op.create_index("ix_prediction_shipment_line_product_id", "prediction_shipment_line", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_prediction_shipment_line_product_id", table_name="prediction_shipment_line")
    op.drop_constraint("fk_prediction_shipment_line_product", "prediction_shipment_line", type_="foreignkey")
    op.drop_column("prediction_shipment_line", "product_name")
    op.drop_column("prediction_shipment_line", "product_id")
