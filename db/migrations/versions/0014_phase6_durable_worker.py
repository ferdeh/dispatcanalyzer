"""Add durable Phase 6 prediction job queue and worker lease metadata.

Revision ID: 0014_phase6_worker
Revises: 0013_phase6_drive_only
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_phase6_worker"
down_revision = "0013_phase6_drive_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_job",
        sa.Column(
            "prediction_run_id",
            sa.String(length=64),
            sa.ForeignKey("prediction_run.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="QUEUED"),
        sa.Column("worker_id", sa.String(length=160), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True, unique=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_prediction_job_status", "prediction_job", ["status"])
    op.create_index("ix_prediction_job_status_queued", "prediction_job", ["status", "queued_at"])
    op.create_index("ix_prediction_job_lease", "prediction_job", ["status", "lease_expires_at"])

    # Old RUNNING rows receive an expired lease and are recovered by the worker.
    op.execute(
        """
        INSERT INTO prediction_job (
            prediction_run_id, status, attempt_count, max_attempts, queued_at,
            started_at, heartbeat_at, lease_expires_at, completed_at, last_error
        )
        SELECT
            id,
            CASE
                WHEN status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED') THEN status
                ELSE 'FAILED'
            END,
            CASE WHEN status = 'RUNNING' THEN 0 ELSE 1 END,
            3,
            COALESCE(created_at, CURRENT_TIMESTAMP),
            CASE WHEN status = 'RUNNING' THEN created_at ELSE NULL END,
            NULL,
            CASE WHEN status = 'RUNNING' THEN CURRENT_TIMESTAMP - INTERVAL '1 second' ELSE NULL END,
            CASE WHEN status IN ('COMPLETED', 'FAILED') THEN completed_at ELSE NULL END,
            CASE WHEN status NOT IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')
                THEN 'Legacy transient status was closed during durable queue migration.' ELSE NULL END
        FROM prediction_run
        """
    )


def downgrade() -> None:
    op.drop_table("prediction_job")
