"""preserve imported shipment_id as canonical shipment key

Revision ID: 0003_preserve_source_shipment_id
Revises: 0002_lo_number_pk
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_preserve_source_shipment_id"
down_revision = "0002_lo_number_pk"
branch_labels = None
depends_on = None


SHIPMENT_FKS = (
    ("fact_loading_order_line", "fact_loading_order_line_shipment_id_fkey"),
    ("fact_shipment_spbu", "fact_shipment_spbu_shipment_id_fkey"),
    ("fact_spbu_visit", "fact_spbu_visit_shipment_id_fkey"),
    ("fact_shipment_stop", "fact_shipment_stop_shipment_id_fkey"),
)


def _drop_shipment_fks() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table_name, constraint_name in SHIPMENT_FKS:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def _create_shipment_fks() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table_name, constraint_name in SHIPMENT_FKS:
        op.create_foreign_key(
            constraint_name,
            table_name,
            "fact_shipment",
            ["shipment_id"],
            ["shipment_id"],
        )


def _alter_shipment_id_columns(type_: sa.String) -> None:
    op.alter_column("fact_shipment", "shipment_id", existing_type=sa.String(64), type_=type_, existing_nullable=False)
    op.alter_column("fact_loading_order_line", "shipment_id", existing_type=sa.String(64), type_=type_, existing_nullable=False)
    op.alter_column("fact_shipment_spbu", "shipment_id", existing_type=sa.String(64), type_=type_, existing_nullable=False)
    op.alter_column("fact_spbu_visit", "shipment_id", existing_type=sa.String(64), type_=type_, existing_nullable=True)
    op.alter_column("fact_shipment_stop", "shipment_id", existing_type=sa.String(64), type_=type_, existing_nullable=True)


def upgrade() -> None:
    _drop_shipment_fks()
    _alter_shipment_id_columns(sa.String(120))

    op.execute(
        """
        UPDATE fact_loading_order_line child
        SET shipment_id = parent.source_shipment_id
        FROM fact_shipment parent
        WHERE child.shipment_id = parent.shipment_id
          AND parent.source_shipment_id IS NOT NULL
          AND child.shipment_id <> parent.source_shipment_id;
        """
    )
    op.execute(
        """
        UPDATE fact_shipment_spbu child
        SET shipment_id = parent.source_shipment_id
        FROM fact_shipment parent
        WHERE child.shipment_id = parent.shipment_id
          AND parent.source_shipment_id IS NOT NULL
          AND child.shipment_id <> parent.source_shipment_id;
        """
    )
    op.execute(
        """
        UPDATE fact_spbu_visit child
        SET shipment_id = parent.source_shipment_id
        FROM fact_shipment parent
        WHERE child.shipment_id = parent.shipment_id
          AND parent.source_shipment_id IS NOT NULL
          AND child.shipment_id <> parent.source_shipment_id;
        """
    )
    op.execute(
        """
        UPDATE fact_shipment_stop child
        SET shipment_id = parent.source_shipment_id
        FROM fact_shipment parent
        WHERE child.shipment_id = parent.shipment_id
          AND parent.source_shipment_id IS NOT NULL
          AND child.shipment_id <> parent.source_shipment_id;
        """
    )
    op.execute(
        """
        UPDATE fact_shipment
        SET shipment_id = source_shipment_id
        WHERE source_shipment_id IS NOT NULL
          AND shipment_id <> source_shipment_id;
        """
    )

    _create_shipment_fks()


def downgrade() -> None:
    _drop_shipment_fks()
    _alter_shipment_id_columns(sa.String(64))
    _create_shipment_fks()
