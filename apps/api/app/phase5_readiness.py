from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .compatibility import build_depot_compatibility_matrix
from .config import get_settings
from .models import MasterDepot


def build_phase5_readiness(db: Session, depot_id: str, *, include_matrix: bool = False) -> dict:
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="Depot not found.")
    matrix = build_depot_compatibility_matrix(
        db,
        depot_id,
        vehicle_mode=get_settings().vehicle_compatibility_mode,
    )
    payload = {
        "phase": 5,
        "depot_id": depot.depot_id,
        "depot_name": depot.depot_name,
        **{key: value for key, value in matrix.items() if key != "compatible_mt_ids_by_spbu"},
        "status": "READY_FOR_MACHINE_LEARNING" if matrix["is_ready"] else "NOT_READY",
        "requirement": "Master Tag Compatibility must equal exactly 100.00% PASS.",
        "compatibility_issues_path": f"/tag-consistency?depot_id={depot_id}",
    }
    if include_matrix:
        payload["compatible_mt_ids_by_spbu"] = matrix["compatible_mt_ids_by_spbu"]
    return payload


def require_phase5_readiness(db: Session, depot_id: str, *, include_matrix: bool = False) -> dict:
    readiness = build_phase5_readiness(db, depot_id, include_matrix=include_matrix)
    if not readiness["is_ready"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PHASE5_NOT_READY",
                "message": readiness["requirement"],
                "master_compatibility_pass_percentage": readiness["master_compatibility_pass_percentage"],
                "failed_pair_count": readiness["failed_pair_count"],
            },
        )
    return readiness
