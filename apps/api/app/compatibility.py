from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BridgeMTTag, BridgeSPBUTag, MasterMT, MasterSPBU, MasterTag


def evaluate_mt_spbu_compatibility(db: Session, mt_id: str, spbu_id: str, product_id: str | None = None, vehicle_mode: str = "EXACT_MATCH") -> dict:
    mt = db.get(MasterMT, mt_id)
    spbu = db.get(MasterSPBU, spbu_id)
    if not mt or not spbu:
        return {
            "compatible": False,
            "vehicle_type_check": "UNKNOWN",
            "project_tag_check": "UNKNOWN",
            "product_check": "NOT_AVAILABLE",
            "depot_check": "UNKNOWN",
            "matched_tags": [],
            "failed_rules": ["MT_OR_SPBU_NOT_FOUND"],
            "warnings": [],
            "explanation": "MT or SPBU does not exist in canonical master data.",
        }

    failed_rules: list[str] = []
    warnings: list[str] = []

    if vehicle_mode == "EXACT_MATCH":
        vehicle_ok = bool(mt.vehicle_type_tag and spbu.vehicle_type_tag and str(mt.vehicle_type_tag) == str(spbu.vehicle_type_tag))
        vehicle_check = "PASS" if vehicle_ok else "FAIL"
    elif vehicle_mode == "MT_CAPACITY_LE_SPBU_LIMIT":
        try:
            vehicle_ok = float(mt.vehicle_type_tag or 0) <= float(spbu.vehicle_type_tag or 0)
        except ValueError:
            vehicle_ok = False
        vehicle_check = "PASS" if vehicle_ok else "FAIL"
    else:
        vehicle_ok = False
        vehicle_check = "UNKNOWN_MODE"
        warnings.append(f"Unsupported vehicle compatibility mode: {vehicle_mode}")
    if not vehicle_ok:
        failed_rules.append("VEHICLE_TYPE")

    mt_tag_ids = {row[0] for row in db.execute(select(BridgeMTTag.tag_id).where(BridgeMTTag.mt_id == mt_id)).all()}
    spbu_tag_ids = {row[0] for row in db.execute(select(BridgeSPBUTag.tag_id).where(BridgeSPBUTag.spbu_id == spbu_id)).all()}
    missing_spbu_required_tags = spbu_tag_ids - mt_tag_ids
    project_ok = len(missing_spbu_required_tags) == 0
    if not project_ok:
        failed_rules.append("PROJECT_TAGS")
    matched_tag_ids = sorted(mt_tag_ids & spbu_tag_ids)
    matched_tags = [tag.tag_value for tag in db.scalars(select(MasterTag).where(MasterTag.tag_id.in_(matched_tag_ids))).all()] if matched_tag_ids else []

    if mt.depot_id and spbu.primary_depot_id:
        depot_ok = mt.depot_id == spbu.primary_depot_id
        depot_check = "PASS" if depot_ok else "FAIL"
        if not depot_ok:
            failed_rules.append("DEPOT")
    else:
        depot_check = "INSUFFICIENT_DATA"
        warnings.append("Depot is missing on MT or SPBU.")

    product_check = "NOT_AVAILABLE" if product_id is None else "INSUFFICIENT_DATA"
    if product_id:
        warnings.append("Product compatibility rules are configured but no explicit product rule table exists yet.")

    compatible = vehicle_ok and project_ok and depot_check in {"PASS", "INSUFFICIENT_DATA"}
    explanation = "Compatible by active Phase 0 master rules." if compatible else f"Incompatible by: {', '.join(failed_rules)}."
    return {
        "compatible": compatible,
        "vehicle_type_check": vehicle_check,
        "project_tag_check": "PASS" if project_ok else "FAIL",
        "product_check": product_check,
        "depot_check": depot_check,
        "matched_tags": matched_tags,
        "failed_rules": failed_rules,
        "warnings": warnings,
        "explanation": explanation,
    }
