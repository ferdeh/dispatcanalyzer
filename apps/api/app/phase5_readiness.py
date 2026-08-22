from __future__ import annotations

from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .compatibility import build_depot_compatibility_matrix
from .models import MasterDepot
from .tag_consistency import build_tag_consistency_payload


PHASE1_VEHICLE_COMPATIBILITY_MODE = "MT_CAPACITY_LE_SPBU_LIMIT"


def build_phase5_readiness(db: Session, depot_id: str, *, include_matrix: bool = False) -> dict:
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="Depot not found.")

    # Readiness follows the same observed Loading Order assignment scope shown on
    # the Phase 1 Tag Consistency page.  A full active MT x SPBU cross-product is
    # an eligibility matrix: incompatible cells are expected exclusions, not bad
    # master data, and therefore must never reduce the readiness percentage.
    phase1 = build_tag_consistency_payload(db, depot_id=depot_id, limit=1)
    summary = phase1["summary"]
    scope = phase1["effective_filters"]
    evaluated = int(summary["total_lo_assignments"])
    passed = int(summary["matched"])
    mismatch = int(summary["mismatch"])
    data_issues = int(summary["data_issues"])
    failed = evaluated - passed
    percentage = round(100.0 * passed / evaluated, 2) if evaluated else 0.0
    is_ready = evaluated > 0 and passed == evaluated
    issue_query = urlencode(
        {
            key: value
            for key, value in {
                "depot_id": depot_id,
                "start_date": scope["start_date"],
                "end_date": scope["end_date"],
            }.items()
            if value is not None
        }
    )

    payload = {
        "phase": 5,
        "depot_id": depot.depot_id,
        "depot_name": depot.depot_name,
        "depot_latitude": float(depot.latitude) if depot.latitude is not None else None,
        "depot_longitude": float(depot.longitude) if depot.longitude is not None else None,
        "compatibility_scope": "OBSERVED_LOADING_ORDER_ASSIGNMENTS",
        "compatibility_scope_description": "Observed Loading Order assignments from the latest Phase 1 analysis scope.",
        "scope_start_date": scope["start_date"],
        "scope_end_date": scope["end_date"],
        "evaluated_assignment_count": evaluated,
        "passed_assignment_count": passed,
        "failed_assignment_count": failed,
        "mismatch_assignment_count": mismatch,
        "data_issue_assignment_count": data_issues,
        "master_compatibility_pass_percentage": percentage,
        "is_ready": is_ready,
        "status_counts": summary["status_counts"],
        "rule_source": "app.tag_consistency.evaluate_mt_spbu_tags",
        "status": "READY_FOR_MACHINE_LEARNING" if is_ready else "NOT_READY",
        "requirement": "All observed Loading Order assignments in the Phase 1 scope must pass Master Tag Compatibility.",
        "compatibility_issues_path": f"/tag-consistency?{issue_query}",
        # Backward-compatible aliases for early Phase 5 clients. These counts now
        # represent observed assignments, not the full master cross-product.
        "evaluated_pair_count": evaluated,
        "passed_pair_count": passed,
        "failed_pair_count": failed,
    }

    if not include_matrix:
        return payload

    matrix = build_depot_compatibility_matrix(
        db,
        depot_id,
        vehicle_mode=PHASE1_VEHICLE_COMPATIBILITY_MODE,
    )
    payload["master_eligibility_matrix"] = {
        "active_mt_count": matrix["active_mt_count"],
        "active_spbu_count": matrix["active_spbu_count"],
        "candidate_pair_count": matrix["evaluated_pair_count"],
        "eligible_pair_count": matrix["passed_pair_count"],
        "excluded_pair_count": matrix["failed_pair_count"],
        "eligible_pair_percentage": matrix["master_compatibility_pass_percentage"],
        "exclusion_counts": matrix["failure_counts"],
        "vehicle_compatibility_mode": matrix["vehicle_compatibility_mode"],
        "rule_source": matrix["rule_source"],
        "blocks_readiness": False,
    }
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
                "failed_assignment_count": readiness["failed_assignment_count"],
                "scope_start_date": readiness["scope_start_date"],
                "scope_end_date": readiness["scope_end_date"],
            },
        )
    return readiness
