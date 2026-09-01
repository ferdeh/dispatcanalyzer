from __future__ import annotations

import math
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
from fastapi import HTTPException
from sqlalchemy import Integer, desc, func, select
from sqlalchemy.orm import Session

from .affinity_intelligence import _enrich_entity_tags, _load_observation_rows, _prepare_observations
from .models import (
    FactShipment,
    MLConcentrationAnalysisRun,
    MLConcentrationSavedAnalysis,
    MLSPBUConcentrationProfile,
    MasterDepot,
    MasterMT,
    MasterSPBU,
)
from .normalization import clean_str, normalize_key
from .phase5_constants import (
    CONCENTRATION_ALGORITHM_VERSION,
    CONCENTRATION_CLASSIFICATION_THRESHOLDS,
    DEFAULT_ENGINE_A_PARAMETERS,
)
from .phase5_readiness import require_phase5_readiness


FEATURE_NAMES = [
    "compatible_mt_count",
    "historically_used_mt_count",
    "utilization_breadth",
    "dominant_mt_share",
    "hhi",
    "normalized_entropy",
]


def utilization_breadth(historically_used_mt_count: int, compatible_mt_count: int) -> float:
    return historically_used_mt_count / compatible_mt_count if compatible_mt_count > 0 else 0.0


def concentration_statistics(counts: list[int | float]) -> dict[str, float]:
    positive = [float(value) for value in counts if value > 0]
    total = sum(positive)
    if not total:
        return {"hhi": 0.0, "entropy": 0.0, "normalized_entropy": 0.0}
    probabilities = [value / total for value in positive]
    hhi = sum(value * value for value in probabilities)
    entropy = -sum(value * math.log(value) for value in probabilities if value > 0)
    normalized = entropy / math.log(len(probabilities)) if len(probabilities) > 1 else 0.0
    return {
        "hhi": round(hhi, 8),
        "entropy": round(entropy, 8),
        "normalized_entropy": round(max(0.0, min(1.0, normalized)), 8),
    }


def validate_classification_thresholds(thresholds: dict[str, Any] | None) -> dict[str, float]:
    candidate = {**CONCENTRATION_CLASSIFICATION_THRESHOLDS, **(thresholds or {})}
    values = [float(candidate[key]) for key in ("moderate", "high", "investigation")]
    if not (0 <= values[0] < values[1] < values[2] <= 100):
        raise HTTPException(status_code=400, detail="Engine A thresholds must be ascending within 0-100.")
    return dict(zip(("moderate", "high", "investigation"), values))


def concentration_classification(score: float | None, thresholds: dict[str, float]) -> str:
    if score is None:
        return "INSUFFICIENT_DATA"
    if score >= thresholds["investigation"]:
        return "INVESTIGATION_RECOMMENDED"
    if score >= thresholds["high"]:
        return "HIGH_CONCENTRATION"
    if score >= thresholds["moderate"]:
        return "MODERATE_CONCENTRATION"
    return "NORMAL"


def transform_anomaly_scores(raw_scores: list[float]) -> list[float]:
    """Map Isolation Forest severity to 0-100 by within-run min-max scaling.

    Raw severity is ``-IsolationForest.score_samples`` so larger values are more
    unusual. The transform preserves ordering and anchors the least/most unusual
    eligible profiles at 0/100. Equal-score runs map to 0 because the model found
    no relative anomaly evidence.
    """
    if not raw_scores:
        return []
    low, high = min(raw_scores), max(raw_scores)
    if math.isclose(low, high):
        return [0.0 for _ in raw_scores]
    return [round(100.0 * (value - low) / (high - low), 4) for value in raw_scores]


def score_feature_rows(feature_rows: list[list[float]], parameters: dict[str, Any]) -> tuple[list[float], list[float], dict[str, Any]]:
    if not feature_rows:
        return [], [], {"feature_names": FEATURE_NAMES, "scaler": None}
    if len(feature_rows) == 1:
        return [0.0], [0.0], {"feature_names": FEATURE_NAMES, "scaler": None, "note": "Single eligible profile; no peer anomaly ranking."}
    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover - exercised only in an incomplete deployment
        raise HTTPException(status_code=503, detail="Phase 5 ML dependencies are not installed. Install apps/api/requirements.txt.") from exc

    contamination: str | float = parameters.get("contamination", "auto")
    if contamination != "auto":
        contamination = float(contamination)
        if not 0 < contamination <= 0.5:
            raise HTTPException(status_code=400, detail="Isolation Forest contamination must be 'auto' or within (0, 0.5].")
    scaler = StandardScaler()
    matrix = scaler.fit_transform(np.asarray(feature_rows, dtype=float))
    model = IsolationForest(
        n_estimators=max(50, min(2000, int(parameters.get("n_estimators", 200)))),
        contamination=contamination,
        random_state=int(parameters.get("random_seed", 42)),
        n_jobs=1,
    )
    model.fit(matrix)
    raw_scores = [-float(value) for value in model.score_samples(matrix)]
    normalized = transform_anomaly_scores(raw_scores)
    metadata = {
        "feature_names": FEATURE_NAMES,
        "scaler": {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()},
        "score_transform": "100 * (raw - run_min) / (run_max - run_min); equal-score run = 0",
        "raw_score_definition": "negative IsolationForest.score_samples; higher is more unusual",
    }
    return raw_scores, normalized, metadata


def _peer_band(compatible_mt_count: int) -> int:
    return int(math.floor(math.log2(max(1, compatible_mt_count))))


def _peer_context(profiles: list[dict]) -> None:
    eligible = [row for row in profiles if row["data_sufficiency_status"] == "SUFFICIENT_DATA"]
    by_band: dict[int, list[dict]] = defaultdict(list)
    for row in eligible:
        by_band[_peer_band(row["compatible_mt_count"])].append(row)
    for row in profiles:
        band = _peer_band(row["compatible_mt_count"])
        peers = by_band.get(band) or eligible
        lower = 2**band
        upper = 2 ** (band + 1) - 1
        row["peer_statistics"] = {
            "method": "log2 compatible-fleet band; all sufficient profiles are used as fallback when the band is empty",
            "compatible_mt_band": f"{lower}-{upper}",
            "peer_count": len(peers),
            "median_compatible_mt_count": round(statistics.median([peer["compatible_mt_count"] for peer in peers]), 2) if peers else 0.0,
            "median_historically_used_mt_count": round(statistics.median([peer["historically_used_mt_count"] for peer in peers]), 2) if peers else 0.0,
            "median_dominant_mt_share": round(statistics.median([peer["dominant_mt_share"] for peer in peers]), 4) if peers else 0.0,
            "median_utilization_breadth": round(statistics.median([peer["utilization_breadth"] for peer in peers]), 4) if peers else 0.0,
        }


def run_concentration_analysis(
    db: Session,
    *,
    depot_id: str,
    baseline_start_date: date,
    baseline_end_date: date,
    minimum_shipment_observation: int,
    parameters: dict[str, Any] | None,
    created_by: str,
) -> dict:
    if baseline_end_date < baseline_start_date:
        raise HTTPException(status_code=400, detail="baseline_end_date must be greater than or equal to baseline_start_date.")
    if minimum_shipment_observation < 1:
        raise HTTPException(status_code=400, detail="minimum_shipment_observation must be at least 1.")
    readiness = require_phase5_readiness(db, depot_id, include_matrix=True)
    effective_parameters = {**DEFAULT_ENGINE_A_PARAMETERS, **(parameters or {})}
    thresholds = validate_classification_thresholds(effective_parameters.get("classification_thresholds"))
    effective_parameters["classification_thresholds"] = thresholds
    run_id = uuid.uuid4().hex
    compatibility_snapshot = {key: value for key, value in readiness.items() if key != "compatible_mt_ids_by_spbu"}
    run = MLConcentrationAnalysisRun(
        analysis_run_id=run_id,
        depot_id=depot_id,
        baseline_start_date=baseline_start_date,
        baseline_end_date=baseline_end_date,
        minimum_shipment_observation=minimum_shipment_observation,
        algorithm_name="IsolationForest",
        algorithm_version=CONCENTRATION_ALGORITHM_VERSION,
        algorithm_parameters=effective_parameters,
        master_compatibility_snapshot=compatibility_snapshot,
        status="PREPARING_DATA",
        created_by=created_by,
    )
    db.add(run)
    db.commit()
    try:
        source_shipments = db.scalars(
            select(FactShipment).where(
                FactShipment.depot_id == depot_id,
                FactShipment.operating_date >= baseline_start_date,
                FactShipment.operating_date <= baseline_end_date,
            )
        ).all()
        raw_rows = _load_observation_rows(db, depot_id, baseline_start_date, baseline_end_date, None)
        prepared = _prepare_observations(source_shipments, raw_rows)
        observations = prepared["observations"]
        if not observations:
            raise HTTPException(status_code=422, detail="No eligible shipment-SPBU-MT observations exist in the selected baseline period.")

        run.status = "TRAINING"
        by_spbu: dict[str, list[dict]] = defaultdict(list)
        for observation in observations:
            by_spbu[observation["spbu_id"]].append(observation)
        compatible_by_spbu = readiness["compatible_mt_ids_by_spbu"]
        all_mt_ids = sorted({mt_id for values in compatible_by_spbu.values() for mt_id in values})
        mt_rows = db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(all_mt_ids))).all() if all_mt_ids else []
        mt_lookup = {
            mt.mt_id: mt.vehicle_registration or mt.vehicle_name_raw or mt.mt_id
            for mt in mt_rows
        }
        spbu_rows = db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(sorted(by_spbu)))).all()
        spbu_lookup = {spbu.spbu_id: spbu for spbu in spbu_rows}

        profiles: list[dict] = []
        eligible_feature_rows: list[list[float]] = []
        eligible_indexes: list[int] = []
        for spbu_id, rows in sorted(by_spbu.items()):
            compatible_ids = set(compatible_by_spbu.get(spbu_id, []))
            shipment_ids = {row["shipment_id"] for row in rows}
            mt_shipments: dict[str, set[str]] = defaultdict(set)
            for row in rows:
                if row["mt_id"] in compatible_ids:
                    mt_shipments[row["mt_id"]].add(row["shipment_id"])
            counts = Counter({mt_id: len(ids) for mt_id, ids in mt_shipments.items()})
            ordered_counts = sorted(counts.items(), key=lambda item: (-item[1], mt_lookup.get(item[0], item[0])))
            dominant_mt_id, dominant_count = ordered_counts[0] if ordered_counts else (None, 0)
            observation_count = len(shipment_ids)
            compatible_count = len(compatible_ids)
            used_count = len(counts)
            stats = concentration_statistics(list(counts.values()))
            row = {
                "spbu_id": spbu_id,
                "spbu_code": spbu_lookup.get(spbu_id).spbu_code if spbu_lookup.get(spbu_id) else spbu_id,
                "spbu_name": spbu_lookup.get(spbu_id).spbu_name if spbu_lookup.get(spbu_id) else None,
                "shipment_observation_count": observation_count,
                "compatible_mt_count": compatible_count,
                "historically_used_mt_count": used_count,
                "utilization_breadth": round(utilization_breadth(used_count, compatible_count), 8),
                "dominant_mt_id": dominant_mt_id,
                "dominant_mt_registration": mt_lookup.get(dominant_mt_id) if dominant_mt_id else None,
                "dominant_mt_share": round(dominant_count / observation_count, 8) if observation_count else 0.0,
                **stats,
                "raw_ml_anomaly_score": None,
                "concentration_anomaly_score": None,
                "concentration_classification": "INSUFFICIENT_DATA",
                "data_sufficiency_status": "SUFFICIENT_DATA" if observation_count >= minimum_shipment_observation else "INSUFFICIENT_DATA",
                "peer_statistics": {},
                "mt_distribution": [
                    {
                        "mt_id": mt_id,
                        "mt_registration": mt_lookup.get(mt_id, mt_id),
                        "shipment_count": counts.get(mt_id, 0),
                        "historical_share": round(counts.get(mt_id, 0) / observation_count, 8) if observation_count else 0.0,
                        "historically_used": counts.get(mt_id, 0) > 0,
                    }
                    for mt_id in sorted(compatible_ids, key=lambda value: (counts.get(value, 0) == 0, -counts.get(value, 0), mt_lookup.get(value, value)))
                ],
            }
            profiles.append(row)
            if row["data_sufficiency_status"] == "SUFFICIENT_DATA":
                eligible_indexes.append(len(profiles) - 1)
                eligible_feature_rows.append([float(row[name]) for name in FEATURE_NAMES])

        raw_scores, normalized_scores, score_metadata = score_feature_rows(eligible_feature_rows, effective_parameters)
        for index, raw_score, normalized_score in zip(eligible_indexes, raw_scores, normalized_scores):
            profiles[index]["raw_ml_anomaly_score"] = round(raw_score, 8)
            profiles[index]["concentration_anomaly_score"] = normalized_score
            profiles[index]["concentration_classification"] = concentration_classification(normalized_score, thresholds)
        _peer_context(profiles)

        run.status = "CALCULATING_PROFILES"
        run.algorithm_parameters = {**effective_parameters, "scoring_metadata": score_metadata}
        for row in profiles:
            db.add(
                MLSPBUConcentrationProfile(
                    profile_id=uuid.uuid4().hex,
                    analysis_run_id=run_id,
                    depot_id=depot_id,
                    spbu_id=row["spbu_id"],
                    shipment_observation_count=row["shipment_observation_count"],
                    compatible_mt_count=row["compatible_mt_count"],
                    historically_used_mt_count=row["historically_used_mt_count"],
                    utilization_breadth=row["utilization_breadth"],
                    dominant_mt_id=row["dominant_mt_id"],
                    dominant_mt_share=row["dominant_mt_share"],
                    hhi=row["hhi"],
                    entropy=row["entropy"],
                    normalized_entropy=row["normalized_entropy"],
                    raw_ml_anomaly_score=row["raw_ml_anomaly_score"],
                    concentration_anomaly_score=row["concentration_anomaly_score"],
                    concentration_classification=row["concentration_classification"],
                    data_sufficiency_status=row["data_sufficiency_status"],
                    peer_statistics=row["peer_statistics"],
                    mt_distribution=row["mt_distribution"],
                )
            )
        run.status = "COMPLETED"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return get_concentration_run(db, run_id)
    except HTTPException as exc:
        db.rollback()
        persisted = db.get(MLConcentrationAnalysisRun, run_id)
        if persisted:
            persisted.status = "FAILED"
            persisted.error_message = str(exc.detail)
            persisted.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
    except Exception as exc:
        db.rollback()
        persisted = db.get(MLConcentrationAnalysisRun, run_id)
        if persisted:
            persisted.status = "FAILED"
            persisted.error_message = f"{type(exc).__name__}: {exc}"
            persisted.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(status_code=500, detail="Concentration analysis failed. Review server logs for the recorded run.") from exc


def _profile_dict(profile: MLSPBUConcentrationProfile, spbu: MasterSPBU | None, dominant_mt: MasterMT | None) -> dict:
    return {
        "spbu_id": profile.spbu_id,
        "spbu_code": spbu.spbu_code if spbu else profile.spbu_id,
        "spbu_name": spbu.spbu_name if spbu else None,
        "shipment_observation_count": profile.shipment_observation_count,
        "compatible_mt_count": profile.compatible_mt_count,
        "historically_used_mt_count": profile.historically_used_mt_count,
        "utilization_breadth": profile.utilization_breadth,
        "dominant_mt_id": profile.dominant_mt_id,
        "dominant_mt_registration": (dominant_mt.vehicle_registration or dominant_mt.vehicle_name_raw) if dominant_mt else None,
        "dominant_mt_share": profile.dominant_mt_share,
        "hhi": profile.hhi,
        "entropy": profile.entropy,
        "normalized_entropy": profile.normalized_entropy,
        "raw_ml_anomaly_score": profile.raw_ml_anomaly_score,
        "concentration_anomaly_score": profile.concentration_anomaly_score,
        "concentration_classification": profile.concentration_classification,
        "data_sufficiency_status": profile.data_sufficiency_status,
        "peer_statistics": profile.peer_statistics,
        "mt_distribution": profile.mt_distribution,
    }


def _enrich_profile_tags(db: Session, rows: list[dict]) -> None:
    """Attach current canonical master tags to persisted Engine A evidence.

    Concentration observations stay immutable. Tags are display metadata from
    the master bridges, so batching them at read time also makes older saved
    analysis runs reflect the current canonical tag labels without recompute.
    """
    spbu_lookup = {
        row["spbu_id"]: {"spbu_tags": []}
        for row in rows
    }
    mt_ids = {
        distribution["mt_id"]
        for row in rows
        for distribution in row.get("mt_distribution", [])
        if distribution.get("mt_id")
    }
    mt_lookup = {mt_id: {"mt_tags": []} for mt_id in mt_ids}
    _enrich_entity_tags(db, spbu_lookup, mt_lookup)
    for row in rows:
        row["spbu_tags"] = list(spbu_lookup.get(row["spbu_id"], {}).get("spbu_tags", []))
        row["mt_distribution"] = [
            {
                **distribution,
                "mt_tags": list(mt_lookup.get(distribution.get("mt_id"), {}).get("mt_tags", [])),
            }
            for distribution in row.get("mt_distribution", [])
        ]


def get_concentration_run(db: Session, analysis_run_id: str) -> dict:
    run = db.get(MLConcentrationAnalysisRun, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Concentration analysis run not found.")
    depot = db.get(MasterDepot, run.depot_id)
    profiles = db.scalars(
        select(MLSPBUConcentrationProfile)
        .where(MLSPBUConcentrationProfile.analysis_run_id == analysis_run_id)
        .order_by(desc(MLSPBUConcentrationProfile.concentration_anomaly_score), MLSPBUConcentrationProfile.spbu_id)
    ).all()
    spbu_ids = [profile.spbu_id for profile in profiles]
    mt_ids = [profile.dominant_mt_id for profile in profiles if profile.dominant_mt_id]
    spbus = {row.spbu_id: row for row in (db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all() if spbu_ids else [])}
    mts = {row.mt_id: row for row in (db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(mt_ids))).all() if mt_ids else [])}
    rows = [_profile_dict(profile, spbus.get(profile.spbu_id), mts.get(profile.dominant_mt_id)) for profile in profiles]
    _enrich_profile_tags(db, rows)
    sufficient = [row for row in rows if row["data_sufficiency_status"] == "SUFFICIENT_DATA"]
    return {
        "analysis_run_id": run.analysis_run_id,
        "depot_id": run.depot_id,
        "depot_name": depot.depot_name if depot else run.depot_id,
        "baseline_start_date": run.baseline_start_date.isoformat(),
        "baseline_end_date": run.baseline_end_date.isoformat(),
        "minimum_shipment_observation": run.minimum_shipment_observation,
        "algorithm_name": run.algorithm_name,
        "algorithm_version": run.algorithm_version,
        "algorithm_parameters": run.algorithm_parameters,
        "master_compatibility_snapshot": run.master_compatibility_snapshot,
        "status": run.status,
        "error_message": run.error_message,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "summary": {
            "spbu_count": len(rows),
            "sufficient_data_count": len(sufficient),
            "insufficient_data_count": len(rows) - len(sufficient),
            "investigation_recommended_count": sum(row["concentration_classification"] == "INVESTIGATION_RECOMMENDED" for row in rows),
        },
        "profiles": rows,
        "methodology": {
            "observation_key": ["depot_id", "shipment_id", "spbu_id", "mt_id"],
            "deduplication": "One observation per canonical depot-shipment-SPBU-MT assignment; LO product/compartment duplicates do not increase counts.",
            "peer_method": "Log2 bands of compatible MT count.",
            "scope": "Historical concentration discovery only; no assignment correctness or future dispatch recommendation.",
        },
    }


def get_concentration_profile(db: Session, analysis_run_id: str, spbu_id: str) -> dict:
    profile = db.scalar(
        select(MLSPBUConcentrationProfile).where(
            MLSPBUConcentrationProfile.analysis_run_id == analysis_run_id,
            MLSPBUConcentrationProfile.spbu_id == spbu_id,
        )
    )
    if not profile:
        raise HTTPException(status_code=404, detail="SPBU concentration profile not found.")
    run = db.get(MLConcentrationAnalysisRun, analysis_run_id)
    row = {
        **_profile_dict(profile, db.get(MasterSPBU, spbu_id), db.get(MasterMT, profile.dominant_mt_id) if profile.dominant_mt_id else None),
        "analysis_run_id": analysis_run_id,
        "baseline_start_date": run.baseline_start_date.isoformat() if run else None,
        "baseline_end_date": run.baseline_end_date.isoformat() if run else None,
    }
    _enrich_profile_tags(db, [row])
    return row


def list_concentration_runs(db: Session, depot_id: str | None = None) -> list[dict]:
    statement = select(MLConcentrationAnalysisRun)
    if depot_id:
        statement = statement.where(MLConcentrationAnalysisRun.depot_id == depot_id)
    runs = db.scalars(statement.order_by(desc(MLConcentrationAnalysisRun.created_at))).all()
    depot_ids = sorted({run.depot_id for run in runs})
    depots = {row.depot_id: row.depot_name for row in (db.scalars(select(MasterDepot).where(MasterDepot.depot_id.in_(depot_ids))).all() if depot_ids else [])}
    return [
        {
            "analysis_run_id": run.analysis_run_id,
            "depot_id": run.depot_id,
            "depot_name": depots.get(run.depot_id, run.depot_id),
            "baseline_start_date": run.baseline_start_date.isoformat(),
            "baseline_end_date": run.baseline_end_date.isoformat(),
            "minimum_shipment_observation": run.minimum_shipment_observation,
            "status": run.status,
            "created_by": run.created_by,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }
        for run in runs
    ]


def _saved_concentration_summary(
    saved: MLConcentrationSavedAnalysis,
    run: MLConcentrationAnalysisRun,
    depot_name: str | None,
    counts: dict[str, dict[str, int]],
) -> dict:
    run_counts = counts.get(run.analysis_run_id, {})
    return {
        "id": saved.id,
        "name": saved.name,
        "depot_id": saved.depot_id,
        "depot_name": depot_name or saved.depot_id,
        "analysis_run_id": run.analysis_run_id,
        "baseline_start_date": run.baseline_start_date.isoformat(),
        "baseline_end_date": run.baseline_end_date.isoformat(),
        "minimum_shipment_observation": run.minimum_shipment_observation,
        "status": run.status,
        "spbu_count": run_counts.get("spbu_count", 0),
        "investigation_recommended_count": run_counts.get("investigation_recommended_count", 0),
        "created_by": saved.created_by,
        "created_at": saved.created_at.isoformat() if saved.created_at else None,
        "updated_at": saved.updated_at.isoformat() if saved.updated_at else None,
    }


def _saved_concentration_counts(db: Session, run_ids: list[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {
        run_id: {"spbu_count": 0, "investigation_recommended_count": 0}
        for run_id in run_ids
    }
    if not run_ids:
        return counts
    rows = db.execute(
        select(
            MLSPBUConcentrationProfile.analysis_run_id,
            func.count(MLSPBUConcentrationProfile.profile_id),
            func.sum(
                (MLSPBUConcentrationProfile.concentration_classification == "INVESTIGATION_RECOMMENDED").cast(Integer)
            ),
        )
        .where(MLSPBUConcentrationProfile.analysis_run_id.in_(run_ids))
        .group_by(MLSPBUConcentrationProfile.analysis_run_id)
    ).all()
    for run_id, spbu_count, investigation_count in rows:
        counts[run_id] = {
            "spbu_count": int(spbu_count or 0),
            "investigation_recommended_count": int(investigation_count or 0),
        }
    return counts


def list_saved_concentration_analyses(
    db: Session,
    depot_id: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    filters = [MLConcentrationSavedAnalysis.depot_id == depot_id] if depot_id else []
    bounded_limit = max(1, min(limit, 100))
    bounded_offset = max(0, offset)
    total = db.scalar(
        select(func.count()).select_from(MLConcentrationSavedAnalysis).where(*filters)
    ) or 0
    saved_rows = db.scalars(
        select(MLConcentrationSavedAnalysis)
        .where(*filters)
        .order_by(
            desc(MLConcentrationSavedAnalysis.updated_at),
            desc(MLConcentrationSavedAnalysis.created_at),
        )
        .limit(bounded_limit)
        .offset(bounded_offset)
    ).all()
    run_ids = [row.analysis_run_id for row in saved_rows]
    runs = {
        row.analysis_run_id: row
        for row in (
            db.scalars(
                select(MLConcentrationAnalysisRun).where(
                    MLConcentrationAnalysisRun.analysis_run_id.in_(run_ids)
                )
            ).all()
            if run_ids
            else []
        )
    }
    depot_ids = sorted({row.depot_id for row in saved_rows})
    depots = {
        row.depot_id: row.depot_name
        for row in (
            db.scalars(select(MasterDepot).where(MasterDepot.depot_id.in_(depot_ids))).all()
            if depot_ids
            else []
        )
    }
    counts = _saved_concentration_counts(db, run_ids)
    return {
        "total": int(total),
        "limit": bounded_limit,
        "offset": bounded_offset,
        "rows": [
            _saved_concentration_summary(saved, runs[saved.analysis_run_id], depots.get(saved.depot_id), counts)
            for saved in saved_rows
            if saved.analysis_run_id in runs
        ],
    }


def save_concentration_analysis(
    db: Session,
    *,
    name: str,
    analysis_run_id: str,
    ui_state: dict | None,
    created_by: str,
) -> dict:
    cleaned_name = clean_str(name)
    normalized_name = normalize_key(cleaned_name)
    if not cleaned_name or not normalized_name:
        raise HTTPException(status_code=400, detail="Saved analysis name is required.")
    run = db.get(MLConcentrationAnalysisRun, analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Concentration analysis run not found.")
    if run.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only a completed concentration analysis can be saved.")
    saved = db.scalar(
        select(MLConcentrationSavedAnalysis).where(
            MLConcentrationSavedAnalysis.depot_id == run.depot_id,
            MLConcentrationSavedAnalysis.normalized_name == normalized_name,
        )
    )
    if not saved:
        saved = MLConcentrationSavedAnalysis(
            id=uuid.uuid4().hex,
            name=cleaned_name,
            normalized_name=normalized_name,
            depot_id=run.depot_id,
            analysis_run_id=analysis_run_id,
            created_by=created_by,
        )
    saved.name = cleaned_name
    saved.analysis_run_id = analysis_run_id
    saved.ui_state = ui_state or {}
    saved.updated_at = datetime.now(timezone.utc)
    db.add(saved)
    db.commit()
    db.refresh(saved)
    depot = db.get(MasterDepot, run.depot_id)
    result = _saved_concentration_summary(
        saved,
        run,
        depot.depot_name if depot else None,
        _saved_concentration_counts(db, [analysis_run_id]),
    )
    result.update({"ui_state": saved.ui_state or {}, "analysis_run": get_concentration_run(db, analysis_run_id)})
    return result


def get_saved_concentration_analysis(db: Session, saved_analysis_id: str) -> dict:
    saved = db.get(MLConcentrationSavedAnalysis, saved_analysis_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Saved concentration analysis not found.")
    run = db.get(MLConcentrationAnalysisRun, saved.analysis_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Source concentration analysis run not found.")
    depot = db.get(MasterDepot, saved.depot_id)
    result = _saved_concentration_summary(
        saved,
        run,
        depot.depot_name if depot else None,
        _saved_concentration_counts(db, [run.analysis_run_id]),
    )
    result.update({"ui_state": saved.ui_state or {}, "analysis_run": get_concentration_run(db, run.analysis_run_id)})
    return result


def delete_saved_concentration_analysis(db: Session, saved_analysis_id: str) -> dict:
    saved = db.get(MLConcentrationSavedAnalysis, saved_analysis_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Saved concentration analysis not found.")
    db.delete(saved)
    db.commit()
    return {"status": "DELETED", "id": saved_analysis_id}
