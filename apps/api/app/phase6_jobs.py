from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import PredictionJob, PredictionRun


class PredictionLeaseLost(RuntimeError):
    """Raised when an obsolete worker tries to commit after losing its lease."""


@dataclass(frozen=True)
class ClaimedPredictionJob:
    run_id: str
    run_no: str
    lease_token: str
    attempt_count: int
    max_attempts: int


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def enqueue_prediction_job(db: Session, run_id: str) -> PredictionJob:
    job = PredictionJob(
        prediction_run_id=run_id,
        status="QUEUED",
        max_attempts=max(1, get_settings().phase6_prediction_max_attempts),
    )
    db.add(job)
    return job


def claim_next_prediction_job(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: float,
) -> ClaimedPredictionJob | None:
    job = db.scalar(
        select(PredictionJob)
        .where(PredictionJob.status == "QUEUED")
        .order_by(PredictionJob.queued_at, PredictionJob.prediction_run_id)
        .with_for_update(skip_locked=True)
    )
    if not job:
        return None
    run = db.get(PredictionRun, job.prediction_run_id)
    if not run:
        job.status = "FAILED"
        job.last_error = "Prediction run no longer exists."
        job.completed_at = utc_now()
        db.commit()
        return None
    if run.status == "COMPLETED":
        job.status = "COMPLETED"
        job.completed_at = run.completed_at or utc_now()
        db.commit()
        return None

    now = utc_now()
    token = uuid.uuid4().hex
    job.status = "RUNNING"
    job.worker_id = worker_id
    job.lease_token = token
    job.attempt_count += 1
    job.started_at = now
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.completed_at = None
    run.status = "RUNNING"
    run.completed_at = None
    run.error_code = None
    run.error_message = None
    db.commit()
    return ClaimedPredictionJob(
        run_id=run.id,
        run_no=run.prediction_run_no,
        lease_token=token,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
    )


def heartbeat_prediction_job(
    db: Session,
    *,
    run_id: str,
    lease_token: str,
    lease_seconds: float,
) -> bool:
    job = db.scalar(
        select(PredictionJob).where(
            PredictionJob.prediction_run_id == run_id,
            PredictionJob.status == "RUNNING",
            PredictionJob.lease_token == lease_token,
        )
    )
    if not job:
        return False
    now = utc_now()
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.commit()
    return True


def require_prediction_lease(db: Session, *, run_id: str, lease_token: str) -> PredictionJob:
    job = db.scalar(
        select(PredictionJob)
        .where(PredictionJob.prediction_run_id == run_id)
        .with_for_update()
    )
    now = utc_now()
    if (
        not job
        or job.status != "RUNNING"
        or job.lease_token != lease_token
        or not job.lease_expires_at
        or _utc(job.lease_expires_at) <= now
    ):
        raise PredictionLeaseLost(f"Lease was lost for prediction run {run_id}.")
    return job


def complete_prediction_job(db: Session, *, run_id: str, lease_token: str | None) -> None:
    if lease_token:
        job = require_prediction_lease(db, run_id=run_id, lease_token=lease_token)
    else:
        job = db.get(PredictionJob, run_id)
        if not job:
            return
    now = utc_now()
    job.status = "COMPLETED"
    job.heartbeat_at = now
    job.lease_expires_at = None
    job.completed_at = now
    job.last_error = None


def fail_prediction_job(
    db: Session,
    *,
    run_id: str,
    lease_token: str | None,
    message: str,
) -> None:
    job = db.get(PredictionJob, run_id)
    if not job or (lease_token and job.lease_token != lease_token):
        return
    now = utc_now()
    job.status = "FAILED"
    job.heartbeat_at = now
    job.lease_expires_at = None
    job.completed_at = now
    job.last_error = message


def release_prediction_job_for_retry(
    db: Session,
    *,
    run_id: str,
    lease_token: str,
    error_code: str,
    message: str,
) -> str | None:
    job = db.scalar(
        select(PredictionJob)
        .where(PredictionJob.prediction_run_id == run_id)
        .with_for_update()
    )
    if not job or job.status != "RUNNING" or job.lease_token != lease_token:
        db.rollback()
        return None
    run = db.get(PredictionRun, run_id)
    now = utc_now()
    job.worker_id = None
    job.lease_token = None
    job.heartbeat_at = None
    job.lease_expires_at = None
    job.last_error = message
    if job.attempt_count < job.max_attempts:
        job.status = "QUEUED"
        job.queued_at = now
        job.last_recovered_at = now
        if run:
            run.status = "QUEUED"
            run.error_code = "WORKER_RETRY_SCHEDULED"
            run.error_message = (
                f"{message} Automatic retry {job.attempt_count + 1}/{job.max_attempts} has been queued."
            )
        outcome = "QUEUED"
    else:
        job.status = "FAILED"
        job.completed_at = now
        if run:
            run.status = "FAILED"
            run.completed_at = now
            run.error_code = error_code
            run.error_message = f"{message} Retry limit ({job.max_attempts}) reached."
        outcome = "FAILED"
    db.commit()
    return outcome


def recover_stale_prediction_jobs(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    recovered_at = now or utc_now()
    jobs = db.scalars(
        select(PredictionJob)
        .where(
            PredictionJob.status == "RUNNING",
            or_(PredictionJob.lease_expires_at.is_(None), PredictionJob.lease_expires_at <= recovered_at),
        )
        .with_for_update(skip_locked=True)
    ).all()
    counts = {"requeued": 0, "failed": 0}
    for job in jobs:
        run = db.get(PredictionRun, job.prediction_run_id)
        message = "Worker heartbeat expired before prediction completed."
        job.worker_id = None
        job.lease_token = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.last_recovered_at = recovered_at
        job.last_error = message
        if job.attempt_count < job.max_attempts:
            job.status = "QUEUED"
            job.queued_at = recovered_at
            if run:
                run.status = "QUEUED"
                run.error_code = "WORKER_RETRY_SCHEDULED"
                run.error_message = (
                    f"{message} Automatic retry {job.attempt_count + 1}/{job.max_attempts} has been queued."
                )
            counts["requeued"] += 1
        else:
            job.status = "FAILED"
            job.completed_at = recovered_at
            if run:
                run.status = "FAILED"
                run.completed_at = recovered_at
                run.error_code = "WORKER_HEARTBEAT_TIMEOUT"
                run.error_message = f"{message} Retry limit ({job.max_attempts}) reached."
            counts["failed"] += 1
    if jobs:
        db.commit()
    return counts


def prediction_job_payload(db: Session, run_id: str) -> dict:
    job = db.get(PredictionJob, run_id)
    if not job:
        return {
            "attempt_count": 0,
            "max_attempts": 0,
            "worker_id": None,
            "heartbeat_at": None,
            "lease_expires_at": None,
            "last_error": None,
        }
    return {
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "worker_id": job.worker_id,
        "heartbeat_at": job.heartbeat_at,
        "lease_expires_at": job.lease_expires_at,
        "last_error": job.last_error,
    }
