"""Persist the optional Phase 6 Loading Order quantity in KL.

Revision ID: 0011_phase6_demo_lo
Revises: 0010_phase6_prediction
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_phase6_demo_lo"
down_revision = "0010_phase6_prediction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prediction_shipment_line", sa.Column("order_quantity_kl", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("prediction_shipment_line", "order_quantity_kl")
