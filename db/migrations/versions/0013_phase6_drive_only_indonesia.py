"""Disable Phase 6 TRUCK routing for Indonesia and enforce DRIVE mode.

Revision ID: 0013_phase6_drive_only
Revises: 0012_phase6_multitrip
"""

from alembic import op


revision = "0013_phase6_drive_only"
down_revision = "0012_phase6_multitrip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE google_routes_configuration
        SET routing_mode = 'DRIVE',
            truck_routing_status = 'DISABLED_FOR_INDONESIA',
            fallback_policy = 'NOT_APPLICABLE',
            connection_status = CASE
                WHEN encrypted_api_key IS NULL THEN 'NOT_CONFIGURED'
                ELSE 'NOT_TESTED'
            END,
            last_test_result = '{}',
            configuration_version = configuration_version + 1
        """
    )
    op.execute("UPDATE master_mt SET large_vehicle_profile_status = 'NOT_REQUIRED'")
    op.alter_column("google_routes_configuration", "routing_mode", server_default="DRIVE")
    op.alter_column("google_routes_configuration", "truck_routing_status", server_default="DISABLED_FOR_INDONESIA")
    op.alter_column("google_routes_configuration", "fallback_policy", server_default="NOT_APPLICABLE")
    op.alter_column("master_mt", "large_vehicle_profile_status", server_default="NOT_REQUIRED")


def downgrade() -> None:
    op.alter_column("master_mt", "large_vehicle_profile_status", server_default="INCOMPLETE")
    op.alter_column("google_routes_configuration", "fallback_policy", server_default="ALLOW_DRIVE_FALLBACK")
    op.alter_column("google_routes_configuration", "truck_routing_status", server_default="UNKNOWN")
    op.alter_column("google_routes_configuration", "routing_mode", server_default="AUTO")
    op.execute("UPDATE master_mt SET large_vehicle_profile_status = 'INCOMPLETE'")
    op.execute(
        """
        UPDATE google_routes_configuration
        SET routing_mode = 'AUTO',
            truck_routing_status = 'UNKNOWN',
            fallback_policy = 'ALLOW_DRIVE_FALLBACK',
            last_test_result = '{}',
            configuration_version = configuration_version + 1
        """
    )
