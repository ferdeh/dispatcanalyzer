from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .compatibility import evaluate_compatibility_entities
from .config import get_settings
from .models import (
    BridgeMTTag,
    BridgeSPBUTag,
    FactSPBUMTPair,
    MLBehavioralModel,
    MLModelArtifact,
    MLSPBUClusterAssignment,
    MasterMT,
    MasterSPBU,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_inference_evidence(db: Session, model: MLBehavioralModel) -> dict:
    """Load and verify the immutable Phase 5 package used for inference."""
    artifact = db.scalar(
        select(MLModelArtifact).where(
            MLModelArtifact.model_id == model.model_id,
            MLModelArtifact.artifact_type == "JOBLIB_MODEL_PACKAGE",
        )
    )
    if artifact:
        path = (get_settings().ml_artifact_dir.resolve() / artifact.storage_uri).resolve()
        root = get_settings().ml_artifact_dir.resolve()
        if root not in path.parents or not path.exists() or _sha256(path) != artifact.checksum_sha256:
            raise HTTPException(status_code=500, detail={"code": "INFERENCE_FAILED", "message": "Phase 5 model artifact failed integrity verification."})
        try:
            import joblib

            bundle = joblib.load(path)
            return {
                "assignments": bundle.get("assignments", []),
                "cluster_profiles": bundle.get("cluster_profiles", []),
                "artifact_checksum": artifact.checksum_sha256,
                "artifact_source": "VERIFIED_JOBLIB_MODEL_PACKAGE",
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"code": "INFERENCE_FAILED", "message": "Phase 5 model artifact could not be loaded."}) from exc

    # Registry rows are the normalized persisted representation of the same saved
    # Phase 5 result and keep older development databases usable.
    rows = db.scalars(
        select(MLSPBUClusterAssignment)
        .where(MLSPBUClusterAssignment.model_id == model.model_id)
        .order_by(MLSPBUClusterAssignment.spbu_id)
    ).all()
    if not rows:
        raise HTTPException(status_code=500, detail={"code": "INFERENCE_FAILED", "message": "Selected model has no saved inference assignments."})
    return {
        "assignments": [
            {
                "spbu_id": row.spbu_id,
                "cluster_id": row.cluster_id,
                "membership_probability": row.membership_probability,
                "is_noise": row.is_noise,
                "dominant_shift": row.dominant_shift,
            }
            for row in rows
        ],
        "cluster_profiles": [],
        "artifact_checksum": None,
        "artifact_source": "NORMALIZED_MODEL_REGISTRY",
    }


def confidence_level(score: float, parameters: dict) -> str:
    if score >= float(parameters["high_confidence_threshold"]):
        return "HIGH"
    if score >= float(parameters["medium_confidence_threshold"]):
        return "MEDIUM"
    return "LOW"


def predict_shipments(
    loading_orders: list[dict], model: MLBehavioralModel, evidence: dict, parameters: dict
) -> list[dict]:
    assignments = {row["spbu_id"]: row for row in evidence["assignments"]}
    pairing_by_codes: dict[frozenset[str], float] = {}
    for profile in evidence.get("cluster_profiles", []):
        for pair in profile.get("top_internal_pairings", []):
            pairing_by_codes[frozenset((str(pair["spbu_a_code"]), str(pair["spbu_b_code"])))] = float(pair["pairing_strength"])
    weights = {**{"tag": 0.4, "shift": 0.25, "pairing": 0.35}, **(model.feature_weights or {})}
    by_shift: dict[str, list[dict]] = defaultdict(list)
    for row in loading_orders:
        by_shift[row["shift_id"]].append(row)
    shipments: list[dict] = []
    minimum = float(parameters["minimum_prediction_confidence"])
    for shift_id in sorted(by_shift):
        rows = sorted(by_shift[shift_id], key=lambda row: (row["spbu_no"], row["loading_order_no"]))
        graph = nx.Graph()
        graph.add_nodes_from(range(len(rows)))
        edge_explanations: dict[frozenset[int], dict] = {}
        for left in range(len(rows)):
            for right in range(left + 1, len(rows)):
                first, second = assignments.get(rows[left]["spbu_id"]), assignments.get(rows[right]["spbu_id"])
                if not first or not second or first.get("is_noise") or second.get("is_noise") or first.get("cluster_id") != second.get("cluster_id"):
                    continue
                cluster_match = math.sqrt(max(0.0, float(first.get("membership_probability", 0))) * max(0.0, float(second.get("membership_probability", 0))))
                shift_match = (
                    float((first.get("dominant_shift") == rows[left]["shift"]) + (second.get("dominant_shift") == rows[right]["shift"])) / 2.0
                )
                historical_pairing = pairing_by_codes.get(frozenset((rows[left]["spbu_no"], rows[right]["spbu_no"])), 0.0)
                score = round(
                    weights["tag"] * cluster_match + weights["shift"] * shift_match + weights["pairing"] * historical_pairing,
                    6,
                )
                if score >= minimum:
                    graph.add_edge(left, right, weight=score)
                    edge_explanations[frozenset((left, right))] = {
                        "cluster_match": "same cluster",
                        "cluster_membership_component": round(cluster_match, 6),
                        "historical_pairing_strength": historical_pairing,
                        "shift_match": round(shift_match, 6),
                        "feature_weights": weights,
                        "normalized_model_score": score,
                    }
        matching = nx.algorithms.matching.max_weight_matching(graph, maxcardinality=False, weight="weight")
        groups = [sorted(edge) for edge in matching]
        matched_nodes = {index for group in groups for index in group}
        groups.extend([[index] for index in range(len(rows)) if index not in matched_nodes])
        groups.sort(key=lambda group: min(rows[index]["loading_order_no"] for index in group))
        for number, group in enumerate(groups, start=1):
            selected_rows = [rows[index] for index in group]
            if len(group) > 1:
                explanation = edge_explanations[frozenset(group)]
                score = float(explanation["normalized_model_score"])
            else:
                assignment = assignments.get(selected_rows[0]["spbu_id"])
                score = round(float(assignment.get("membership_probability", 0.0)), 6) if assignment else 0.0
                explanation = {
                    "single_spbu_reason": "No same-shift pairing met the configured prediction threshold.",
                    "cluster_membership_probability": score if assignment else None,
                    "model_coverage": "COVERED" if assignment else "UNSEEN_SPBU",
                }
            shipment_number = f"PRED-{shift_id.upper()}-{number:03d}"
            shipments.append(
                {
                    "predicted_shipment_id": shipment_number,
                    "shift_id": shift_id,
                    "shift": selected_rows[0]["shift"],
                    "score": score,
                    "confidence_level": confidence_level(score, parameters),
                    "low_confidence": score < minimum,
                    "lines": selected_rows,
                    "explanation": {**explanation, "model_id": model.model_id, "model_version": model.model_version},
                }
            )
    return shipments


def predict_mt_candidates(
    db: Session,
    *,
    depot_id: str,
    shipments: list[dict],
    availability: list[dict],
    vehicle_compatibility_mode: str,
) -> dict[str, list[dict]]:
    vehicle_ids = sorted({row["vehicle_id"] for row in availability})
    spbu_ids = sorted({line["spbu_id"] for shipment in shipments for line in shipment["lines"]})
    mts = {row.mt_id: row for row in (db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(vehicle_ids))).all() if vehicle_ids else [])}
    spbus = {row.spbu_id: row for row in (db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all() if spbu_ids else [])}
    mt_tags: dict[str, set[str]] = defaultdict(set)
    spbu_tags: dict[str, set[str]] = defaultdict(set)
    if vehicle_ids:
        for vehicle_id, tag_id in db.execute(select(BridgeMTTag.mt_id, BridgeMTTag.tag_id).where(BridgeMTTag.mt_id.in_(vehicle_ids))).all():
            mt_tags[vehicle_id].add(tag_id)
    if spbu_ids:
        for spbu_id, tag_id in db.execute(select(BridgeSPBUTag.spbu_id, BridgeSPBUTag.tag_id).where(BridgeSPBUTag.spbu_id.in_(spbu_ids))).all():
            spbu_tags[spbu_id].add(tag_id)

    affinity_rows = db.scalars(
        select(FactSPBUMTPair).where(
            FactSPBUMTPair.depot_id == depot_id,
            FactSPBUMTPair.spbu_id.in_(spbu_ids),
            FactSPBUMTPair.mt_id.in_(vehicle_ids),
        )
    ).all() if spbu_ids and vehicle_ids else []
    latest_affinity: dict[tuple[str, str], FactSPBUMTPair] = {}
    spbu_totals: dict[str, int] = defaultdict(int)
    for row in sorted(affinity_rows, key=lambda item: (item.analysis_end_date, str(item.calculated_at or ""))):
        latest_affinity[(row.spbu_id, row.mt_id)] = row
        spbu_totals[row.spbu_id] = max(spbu_totals[row.spbu_id], row.total_spbu_shipment_count)

    available_by_shift: dict[str, set[str]] = defaultdict(set)
    for row in availability:
        available_by_shift[row["shift_id"]].add(row["vehicle_id"])
    result: dict[str, list[dict]] = {}
    for shipment in shipments:
        candidates = []
        for vehicle_id in sorted(available_by_shift.get(shipment["shift_id"], set())):
            mt = mts[vehicle_id]
            checks = [
                evaluate_compatibility_entities(
                    mt,
                    spbus[line["spbu_id"]],
                    mt_tag_ids=mt_tags.get(vehicle_id, set()),
                    spbu_tag_ids=spbu_tags.get(line["spbu_id"], set()),
                    vehicle_mode=vehicle_compatibility_mode,
                )
                for line in shipment["lines"]
            ]
            compatible = all(check["compatible"] for check in checks)
            affinity_components = []
            evidence_confidence = []
            for line in shipment["lines"]:
                affinity = latest_affinity.get((line["spbu_id"], vehicle_id))
                total = spbu_totals.get(line["spbu_id"], 0)
                # Laplace smoothing is deterministic and avoids inventing a high
                # affinity when a compatible vehicle has no historical use.
                affinity_components.append(((affinity.shipment_count if affinity else 0) + 1) / (total + max(1, len(vehicle_ids))))
                evidence_confidence.append(float(affinity.confidence_score) / 100.0 if affinity else 0.0)
            prediction_score = round(sum(affinity_components) / len(affinity_components), 6)
            failed_rules = sorted({rule for check in checks for rule in check["failed_rules"]})
            candidates.append(
                {
                    "vehicle_id": vehicle_id,
                    "vehicle_registration_no": mt.vehicle_registration or mt.mt_id,
                    "prediction_score": prediction_score,
                    "compatibility_status": "PASS" if compatible else "FAIL",
                    "candidate_rank": None,
                    "exclusion_reason": None if compatible else "MASTER_COMPATIBILITY_FAIL",
                    "explanation": {
                        "historical_mt_affinity": round(sum(affinity_components) / len(affinity_components), 6),
                        "historical_evidence_confidence": round(sum(evidence_confidence) / len(evidence_confidence), 6),
                        "score_method": "mean Laplace-smoothed Phase 4 P(MT|SPBU)",
                        "master_compatibility": "PASS" if compatible else "FAIL",
                        "failed_master_rules": failed_rules,
                        "availability": shipment["shift"],
                    },
                }
            )
        compatible_rows = sorted(
            (candidate for candidate in candidates if candidate["compatibility_status"] == "PASS"),
            key=lambda candidate: (-candidate["prediction_score"], candidate["vehicle_registration_no"]),
        )
        for rank, candidate in enumerate(compatible_rows, start=1):
            candidate["candidate_rank"] = rank
        result[shipment["predicted_shipment_id"]] = candidates
    return result
