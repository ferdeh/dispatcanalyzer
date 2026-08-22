from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import socket
import time
import uuid

from fastapi import HTTPException

from .config import get_settings
from .database import SessionLocal, engine
from .models import PredictionJob
from .phase6_jobs import (
    PredictionLeaseLost,
    claim_next_prediction_job,
    heartbeat_prediction_job,
    recover_stale_prediction_jobs,
    release_prediction_job_for_retry,
)
from .phase6_service import process_prediction_run


logger = logging.getLogger(__name__)


def _execute_claimed_job(run_id: str, lease_token: str) -> None:
    # A spawned child must create fresh database connections of its own.
    engine.dispose()
    with SessionLocal() as db:
        try:
            process_prediction_run(db, run_id, lease_token=lease_token)
        except (HTTPException, PredictionLeaseLost):
            # Auditable failure or lease fencing is already persisted/handled.
            return


def _terminate(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def run_worker() -> None:
    settings = get_settings()
    poll_seconds = max(0.1, settings.phase6_worker_poll_seconds)
    heartbeat_seconds = max(0.5, settings.phase6_worker_heartbeat_seconds)
    lease_seconds = max(settings.phase6_worker_lease_seconds, heartbeat_seconds * 2 + 1)
    timeout_seconds = max(1.0, settings.phase6_prediction_timeout_seconds)
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("phase6_worker_started worker_id=%s", worker_id)
    context = multiprocessing.get_context("spawn")

    while not stopping:
        with SessionLocal() as db:
            recovered = recover_stale_prediction_jobs(db)
            claimed = claim_next_prediction_job(db, worker_id=worker_id, lease_seconds=lease_seconds)
        if recovered["requeued"] or recovered["failed"]:
            logger.warning("phase6_worker_recovered_jobs worker_id=%s result=%s", worker_id, recovered)
        if not claimed:
            time.sleep(poll_seconds)
            continue

        logger.info(
            "phase6_job_claimed run=%s attempt=%s/%s",
            claimed.run_no,
            claimed.attempt_count,
            claimed.max_attempts,
        )
        process = context.Process(
            target=_execute_claimed_job,
            args=(claimed.run_id, claimed.lease_token),
            name=f"phase6-{claimed.run_no}",
        )
        process.start()
        started = time.monotonic()
        next_heartbeat = started + heartbeat_seconds
        release_reason: tuple[str, str] | None = None
        terminal_committed = False

        while process.is_alive():
            process.join(timeout=min(0.5, heartbeat_seconds))
            elapsed = time.monotonic() - started
            if stopping:
                release_reason = ("WORKER_STOPPED", "Prediction worker stopped before the job completed.")
                break
            if elapsed >= timeout_seconds:
                release_reason = (
                    "PREDICTION_TIMEOUT",
                    f"Prediction exceeded the {timeout_seconds:g}-second execution timeout.",
                )
                break
            if time.monotonic() >= next_heartbeat:
                with SessionLocal() as db:
                    lease_alive = heartbeat_prediction_job(
                        db,
                        run_id=claimed.run_id,
                        lease_token=claimed.lease_token,
                        lease_seconds=lease_seconds,
                    )
                next_heartbeat = time.monotonic() + heartbeat_seconds
                if not lease_alive:
                    # The child commits PredictionRun and PredictionJob together,
                    # then may spend a moment building its return payload. A
                    # terminal job is success/failure, not a lost lease.
                    process.join(timeout=2)
                    with SessionLocal() as db:
                        job = db.get(PredictionJob, claimed.run_id)
                        terminal_committed = bool(job and job.status in {"COMPLETED", "FAILED"})
                    if terminal_committed:
                        _terminate(process)
                    else:
                        release_reason = ("WORKER_LEASE_LOST", "Prediction worker lost its database lease.")
                    break

        if release_reason:
            _terminate(process)
            with SessionLocal() as db:
                outcome = release_prediction_job_for_retry(
                    db,
                    run_id=claimed.run_id,
                    lease_token=claimed.lease_token,
                    error_code=release_reason[0],
                    message=release_reason[1],
                )
            logger.warning("phase6_job_released run=%s outcome=%s reason=%s", claimed.run_no, outcome, release_reason[0])
            continue

        if terminal_committed:
            logger.info("phase6_job_finished run=%s", claimed.run_no)
            continue

        process.join()
        if process.exitcode != 0:
            with SessionLocal() as db:
                outcome = release_prediction_job_for_retry(
                    db,
                    run_id=claimed.run_id,
                    lease_token=claimed.lease_token,
                    error_code="WORKER_PROCESS_EXITED",
                    message=f"Prediction child process exited with code {process.exitcode}.",
                )
            logger.warning("phase6_job_process_exit run=%s outcome=%s code=%s", claimed.run_no, outcome, process.exitcode)
        else:
            logger.info("phase6_job_finished run=%s", claimed.run_no)

    logger.info("phase6_worker_stopped worker_id=%s", worker_id)


if __name__ == "__main__":
    run_worker()
