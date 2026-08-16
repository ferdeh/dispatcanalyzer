"""apply default tag type rules

Revision ID: 0005_default_tag_type_rules
Revises: 0004_loading_order_depot_pk
Create Date: 2026-08-11
"""
from hashlib import sha1

from alembic import op

revision = "0005_default_tag_type_rules"
down_revision = "0004_loading_order_depot_pk"
branch_labels = None
depends_on = None


def make_id(prefix: str, *parts) -> str:
    joined = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{sha1(joined.encode('utf-8')).hexdigest()[:24]}"


PROJECT_ID = make_id("tagtype", "PROJECT")
VEHICLE_CLASS_ID = make_id("tagtype", "VEHICLE_CLASS")


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO master_tag_type (tag_type_id, code, name, admin_editable)
        VALUES
            ('{PROJECT_ID}', 'PROJECT', 'Project', true),
            ('{VEHICLE_CLASS_ID}', 'VEHICLE_CLASS', 'Vehicle Class', true)
        ON CONFLICT (code) DO UPDATE
        SET name = EXCLUDED.name,
            admin_editable = EXCLUDED.admin_editable;
        """
    )
    op.execute(
        f"""
        UPDATE master_tag
        SET tag_type_id = CASE
            WHEN normalized_tag IN ('8', '16', '24', '32') THEN '{VEHICLE_CLASS_ID}'
            ELSE '{PROJECT_ID}'
        END;
        """
    )


def downgrade() -> None:
    pass
