from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.importer import ImportProcessor


def import_phase0_examples() -> dict[str, str]:
    settings = get_settings()
    with SessionLocal() as db:
        return ImportProcessor(db).import_examples(Path(settings.example_data_dir))


def gated(phase: int, job: str) -> None:
    print(f"{job}: NOT_STARTED. Phase {phase} is gated until Phase 0 completion criteria pass.")
