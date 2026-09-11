from __future__ import annotations

import logging
import signal
import time

from .config import get_settings
from .database import SessionLocal
from .import_jobs import claim_next_import, process_import, recover_interrupted_imports


logger = logging.getLogger(__name__)


def run_worker() -> None:
    settings = get_settings()
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with SessionLocal() as db:
        recovered = recover_interrupted_imports(db)
    if recovered:
        logger.warning("requeued_interrupted_imports count=%s", recovered)

    poll_seconds = max(0.2, settings.import_worker_poll_seconds)
    while not stopping:
        with SessionLocal() as db:
            import_id = claim_next_import(db)
        if not import_id:
            time.sleep(poll_seconds)
            continue
        logger.info("import_job_started import_id=%s", import_id)
        with SessionLocal() as db:
            process_import(db, import_id)
        logger.info("import_job_finished import_id=%s", import_id)


if __name__ == "__main__":
    run_worker()
