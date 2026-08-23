"""Persist road-following geometry for Phase 6 prediction trips.

Revision ID: 0015_phase6_road_geometry
Revises: 0014_phase6_worker
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_phase6_road_geometry"
down_revision = "0014_phase6_worker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prediction_trip",
        sa.Column("route_geometry", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "prediction_trip",
        sa.Column("route_geometry_source", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prediction_trip", "route_geometry_source")
    op.drop_column("prediction_trip", "route_geometry")
