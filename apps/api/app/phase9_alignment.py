from __future__ import annotations

import hashlib
import json
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from itertools import combinations
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.orm import Session

from .affinity_intelligence import (
    ALGORITHM_VERSION as AFFINITY_ALGORITHM_VERSION,
    _build_profiles as build_affinity_profiles,
    _load_observation_rows as load_affinity_observation_rows,
    _prepare_observations as prepare_affinity_observations,
)
from .departure_intelligence import shift_for_minute
from .models import (
    AffinityAnalysisConfig,
    DepartureShiftAnalysisConfig,
    FactShipment,
    FactSPBUMTPair,
    MLBehavioralModel,
    MLSPBUClusterAssignment,
    MLTrainingRun,
    MasterDepot,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    OptimizationJob,
    PairingAnalysisConfig,
    PredictionRun,
    RouteAlignmentEvaluationRow,
    RouteAlignmentEvaluationRun,
    RouteAlignmentPairEvidence,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionTrip,
)


ALGORITHM_VERSION = "phase9.route_model_alignment.v1"
NEUTRAL_NOTE = (
    "Alignment describes similarity to historical patterns. It does not rate route quality "
    "or recommend operational changes."
)


def _iso(value: datetime | date | time | None) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value.isoformat() if value else None


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date, time)):
        return _iso(value) or ""
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _checksum(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _round_pct(value: float | None) -> float | None:
    return None if value is None else round(max(0.0, min(100.0, float(value))), 2)


def _timezone(depot: MasterDepot) -> ZoneInfo:
    try:
        return ZoneInfo(depot.timezone or "Asia/Jakarta")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Jakarta")


def _local_datetime(value: datetime, depot: MasterDepot) -> datetime:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(_timezone(depot))


def _canonical_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((str(left), str(right))))


def _cluster_evaluable(assignment: MLSPBUClusterAssignment | None) -> bool:
    return bool(assignment and assignment.cluster_id is not None and not assignment.is_noise)


def _shift_signature(shifts: list[dict]) -> list[dict]:
    return [
        {
            "shift_id": str(row.get("shift_id") or ""),
            "name": str(row.get("name") or ""),
            "start_time": str(row.get("start_time") or ""),
            "end_time": str(row.get("end_time") or ""),
        }
        for row in shifts
    ]


def _distribution(values: list[float], total_relevant: int) -> list[dict]:
    buckets = [
        ("0–20", 0.0, 20.0),
        (">20–40", 20.0, 40.0),
        (">40–60", 40.0, 60.0),
        (">60–80", 60.0, 80.0),
        (">80–100", 80.0, 100.0),
    ]
    rows = []
    for label, lower, upper in buckets:
        count = sum(
            (value >= lower if lower == 0 else value > lower) and value <= upper
            for value in values
        )
        rows.append({"label": label, "count": count})
    rows.append({"label": "N/A", "count": max(0, total_relevant - len(values))})
    return rows


def _metric(
    values: list[float],
    total_relevant: int,
    *,
    source: str,
    resolution_method: str,
    score_override: float | None = None,
) -> dict:
    evaluated = len(values)
    score = score_override if score_override is not None else statistics.fmean(values) if values else None
    return {
        "score_pct": _round_pct(score),
        "status": "AVAILABLE" if evaluated else "UNAVAILABLE",
        "evaluated_observations": evaluated,
        "total_relevant_observations": total_relevant,
        "coverage_pct": round(100 * evaluated / total_relevant, 2) if total_relevant else 0.0,
        "source": source,
        "resolution_method": resolution_method,
        "distribution": _distribution(values, total_relevant),
    }


def list_route_options(db: Session, depot_id: str) -> dict:
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="TBBM not found.")
    source_rows = db.execute(
        select(RouteVersion, OptimizationJob)
        .join(OptimizationJob, OptimizationJob.job_id == RouteVersion.job_id)
        .where(OptimizationJob.depot_id == depot_id)
        .order_by(desc(OptimizationJob.operating_date), desc(RouteVersion.version_number), desc(RouteVersion.created_at))
    ).all()
    rows = []
    for version, job in source_rows:
        prediction = db.get(PredictionRun, job.source_prediction_run_id) if job.source_prediction_run_id else None
        model = db.get(MLBehavioralModel, prediction.model_id) if prediction else None
        training = db.get(MLTrainingRun, model.source_training_run_id) if model and model.source_training_run_id else None
        lo_count = db.scalar(
            select(func.count()).select_from(RouteVersionLOAssignment).where(
                RouteVersionLOAssignment.route_version_id == version.route_version_id
            )
        ) or 0
        dropped_count = db.scalar(
            select(func.count()).select_from(RouteVersionLOAssignment).where(
                RouteVersionLOAssignment.route_version_id == version.route_version_id,
                RouteVersionLOAssignment.assignment_status == "DROPPED",
            )
        ) or 0
        trip_count = db.scalar(
            select(func.count()).select_from(RouteVersionTrip).where(
                RouteVersionTrip.route_version_id == version.route_version_id
            )
        ) or 0
        lineage_ready = bool(prediction and model and training)
        if not prediction:
            lineage_reason = "SOURCE_PREDICTION_RUN_MISSING"
        elif not model:
            lineage_reason = "SOURCE_MODEL_MISSING"
        elif not training:
            lineage_reason = "SOURCE_TRAINING_SNAPSHOT_MISSING"
        elif model.training_end_date >= job.operating_date:
            lineage_ready = False
            lineage_reason = "FUTURE_EVIDENCE"
        else:
            lineage_reason = None
        rows.append(
            {
                "route_version_id": version.route_version_id,
                "version_number": version.version_number,
                "version_label": version.version_label,
                "job_id": job.job_id,
                "job_no": job.job_no,
                "job_name": job.job_name,
                "operating_date": job.operating_date.isoformat(),
                "created_at": _iso(version.created_at),
                "reason": version.reason,
                "solver_status": version.solver_status,
                "is_current": job.current_route_version_id == version.route_version_id,
                "source_prediction_run_id": prediction.id if prediction else job.source_prediction_run_id,
                "source_prediction_run_no": prediction.prediction_run_no if prediction else None,
                "source_model_id": model.model_id if model else prediction.model_id if prediction else None,
                "source_model_name": model.model_name if model else None,
                "source_model_version": model.model_version if model else None,
                "trip_count": int(trip_count),
                "lo_count": int(lo_count),
                "routed_lo_count": int(lo_count - dropped_count),
                "dropped_lo_count": int(dropped_count),
                "lineage_status": "READY" if lineage_ready else "DISABLED",
                "lineage_reason": lineage_reason,
            }
        )
    return {"depot_id": depot_id, "depot_name": depot.depot_name, "total": len(rows), "rows": rows}


def _source_context(db: Session, depot_id: str, route_version_id: str) -> dict:
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="TBBM not found.")
    version = db.get(RouteVersion, route_version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Phase 7 Route Version not found.")
    job = db.get(OptimizationJob, version.job_id)
    if not job or job.depot_id != depot_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "ROUTE_DEPOT_MISMATCH", "message": "Selected route does not belong to the selected TBBM."},
        )
    prediction = db.get(PredictionRun, job.source_prediction_run_id) if job.source_prediction_run_id else None
    if not prediction:
        raise HTTPException(status_code=422, detail={"code": "SOURCE_PREDICTION_RUN_MISSING", "message": "Route has no readable source Phase 6 Prediction Run."})
    model = db.get(MLBehavioralModel, prediction.model_id)
    if not model:
        raise HTTPException(status_code=422, detail={"code": "SOURCE_MODEL_MISSING", "message": "Route source Phase 5 model is unavailable."})
    training = db.get(MLTrainingRun, model.source_training_run_id) if model.source_training_run_id else None
    if not training:
        raise HTTPException(status_code=422, detail={"code": "SOURCE_TRAINING_SNAPSHOT_MISSING", "message": "Source Phase 5 training snapshot is unavailable."})
    if prediction.depot_id != depot_id or model.depot_id != depot_id or training.depot_id != depot_id:
        raise HTTPException(status_code=422, detail={"code": "SOURCE_DEPOT_MISMATCH", "message": "Route lineage crosses TBBM boundaries."})
    if model.training_end_date >= job.operating_date:
        raise HTTPException(
            status_code=422,
            detail={"code": "BLOCKED_FUTURE_EVIDENCE", "message": "Source model training period does not end before the route operating date."},
        )
    assignments = db.scalars(
        select(RouteVersionLOAssignment)
        .where(RouteVersionLOAssignment.route_version_id == route_version_id)
        .order_by(RouteVersionLOAssignment.loading_order_id)
    ).all()
    trips = db.scalars(
        select(RouteVersionTrip)
        .where(RouteVersionTrip.route_version_id == route_version_id)
        .order_by(RouteVersionTrip.gate_out, RouteVersionTrip.vehicle_id, RouteVersionTrip.trip_number)
    ).all()
    if not assignments:
        raise HTTPException(status_code=422, detail={"code": "ROUTE_HAS_NO_LOADING_ORDERS", "message": "Selected Route Version has no Loading Orders to evaluate."})
    return {
        "depot": depot,
        "version": version,
        "job": job,
        "prediction": prediction,
        "model": model,
        "training": training,
        "assignments": assignments,
        "trips": trips,
    }


def _saved_analysis_links(db: Session, context: dict) -> dict:
    depot_id = context["depot"].depot_id
    model: MLBehavioralModel = context["model"]
    dependency_metadata = (context["training"].dataset_payload or {}).get("dependency_metadata") or {}
    shifts = _shift_signature(model.shift_definition_snapshot or [])
    shift_candidates = db.scalars(
        select(DepartureShiftAnalysisConfig)
        .where(
            DepartureShiftAnalysisConfig.depot_id == depot_id,
            DepartureShiftAnalysisConfig.start_date == model.training_start_date,
            DepartureShiftAnalysisConfig.end_date == model.training_end_date,
        )
        .order_by(desc(DepartureShiftAnalysisConfig.updated_at), DepartureShiftAnalysisConfig.id)
    ).all()
    shift_match = next(
        (
            row
            for row in shift_candidates
            if _shift_signature(row.shift_config or []) == shifts
            and (row.shift_analysis_snapshot or {}).get("algorithm_version")
            == dependency_metadata.get("shift_assignment_algorithm_version")
        ),
        None,
    )
    pairing_candidates = db.scalars(
        select(PairingAnalysisConfig)
        .where(
            PairingAnalysisConfig.depot_id == depot_id,
            PairingAnalysisConfig.start_date == model.training_start_date,
            PairingAnalysisConfig.end_date == model.training_end_date,
            PairingAnalysisConfig.product_id.is_(None),
        )
        .order_by(desc(PairingAnalysisConfig.updated_at), PairingAnalysisConfig.id)
    ).all()
    pairing_match = next(
        (
            row
            for row in pairing_candidates
            if (row.pairing_analysis_snapshot or {}).get("algorithm_version")
            == dependency_metadata.get("pairing_algorithm_version")
        ),
        None,
    )
    affinity_candidates = db.scalars(
        select(AffinityAnalysisConfig)
        .where(
            AffinityAnalysisConfig.depot_id == depot_id,
            AffinityAnalysisConfig.start_date == model.training_start_date,
            AffinityAnalysisConfig.end_date == model.training_end_date,
            AffinityAnalysisConfig.product_id.is_(None),
            AffinityAnalysisConfig.minimum_observations == 1,
            AffinityAnalysisConfig.confidence_filter == "ALL",
        )
        .order_by(desc(AffinityAnalysisConfig.updated_at), AffinityAnalysisConfig.id)
    ).all()
    affinity_match = next(
        (
            row
            for row in affinity_candidates
            if (row.affinity_analysis_snapshot or {}).get("algorithm_version") == AFFINITY_ALGORITHM_VERSION
        ),
        None,
    )
    return {
        "phase2_shift_saved_analysis_id": shift_match.id if shift_match else None,
        "phase3_pairing_saved_analysis_id": pairing_match.id if pairing_match else None,
        "phase4_affinity_saved_analysis_id": affinity_match.id if affinity_match else None,
    }


def _affinity_row_payload(row: FactSPBUMTPair) -> dict:
    return {
        "spbu_id": row.spbu_id,
        "mt_id": row.mt_id,
        "shipment_count": int(row.shipment_count or 0),
        "total_spbu_shipment_count": int(row.total_spbu_shipment_count or 0),
        "total_mt_shipment_count": int(row.total_mt_shipment_count or 0),
        "probability_mt_given_spbu": float(row.probability_mt_given_spbu or 0),
        "probability_spbu_given_mt": float(row.probability_spbu_given_mt or 0),
        "first_observed": row.first_observed.isoformat(),
        "last_observed": row.last_observed.isoformat(),
        "operating_day_count": int(row.operating_day_count or 0),
        "confidence_score": float(row.confidence_score or 0),
        "confidence_level": row.confidence_level,
        "analysis_start_date": row.analysis_start_date.isoformat(),
        "analysis_end_date": row.analysis_end_date.isoformat(),
        "product_filter": row.product_filter,
        "algorithm_version": row.algorithm_version,
        "calculated_at": _iso(row.calculated_at),
    }


def _resolve_affinity(db: Session, context: dict, spbu_ids: set[str]) -> dict:
    model: MLBehavioralModel = context["model"]
    job: OptimizationJob = context["job"]
    depot_id = context["depot"].depot_id
    saved_config = db.scalar(
        select(AffinityAnalysisConfig)
        .where(
            AffinityAnalysisConfig.depot_id == depot_id,
            AffinityAnalysisConfig.start_date == model.training_start_date,
            AffinityAnalysisConfig.end_date == model.training_end_date,
            AffinityAnalysisConfig.product_id.is_(None),
            AffinityAnalysisConfig.minimum_observations == 1,
            AffinityAnalysisConfig.confidence_filter == "ALL",
        )
        .order_by(desc(AffinityAnalysisConfig.updated_at), AffinityAnalysisConfig.id)
    )
    saved_snapshot = (saved_config.affinity_analysis_snapshot or {}) if saved_config else {}
    saved_filters = saved_snapshot.get("effective_filters") or {}
    saved_profiles = saved_snapshot.get("profiles") or []
    saved_is_exact = bool(
        saved_config
        and saved_snapshot.get("algorithm_version") == AFFINITY_ALGORITHM_VERSION
        and saved_filters.get("depot_id") == depot_id
        and saved_filters.get("start_date") == model.training_start_date.isoformat()
        and saved_filters.get("end_date") == model.training_end_date.isoformat()
        and not saved_filters.get("product_id")
        and saved_profiles
    )
    payload_rows: list[dict] = []
    if saved_is_exact:
        selected_pairs = {
            (row.get("spbu_id"), row.get("mt_id")): row
            for row in (saved_snapshot.get("affinity_distribution") or [])
        }
        for profile in saved_profiles:
            spbu_id = profile.get("spbu_id")
            if spbu_id not in spbu_ids:
                continue
            total_spbu = int(profile.get("shipment_count") or 0)
            for mt_id, probability in (profile.get("fleet_affinity_vector") or {}).items():
                detail = selected_pairs.get((spbu_id, mt_id)) or {}
                payload_rows.append(
                    {
                        "spbu_id": spbu_id,
                        "mt_id": mt_id,
                        "shipment_count": int(detail.get("shipment_count") or round(float(probability or 0) * total_spbu)),
                        "total_spbu_shipment_count": total_spbu,
                        "total_mt_shipment_count": int(detail.get("total_mt_shipment_count") or 0),
                        "probability_mt_given_spbu": float(probability or 0),
                        "probability_spbu_given_mt": float(detail.get("probability_spbu_given_mt") or 0),
                        "first_observed": detail.get("first_observed") or profile.get("first_observed"),
                        "last_observed": detail.get("last_observed") or profile.get("last_observed"),
                        "operating_day_count": int(detail.get("operating_day_count") or profile.get("operating_day_count") or 0),
                        "confidence_score": float(detail.get("confidence_score") or profile.get("confidence_score") or 0),
                        "confidence_level": detail.get("confidence_level") or profile.get("confidence_level") or "LOW",
                        "analysis_start_date": model.training_start_date.isoformat(),
                        "analysis_end_date": model.training_end_date.isoformat(),
                        "product_filter": "ALL",
                        "algorithm_version": AFFINITY_ALGORITHM_VERSION,
                        "calculated_at": (saved_snapshot.get("traceability") or {}).get("calculated_at"),
                        "saved_analysis_id": saved_config.id,
                    }
                )
        resolution = "EXACT_SAVED_SNAPSHOT" if payload_rows else "UNAVAILABLE"
    exact_rows = db.scalars(
        select(FactSPBUMTPair).where(
            FactSPBUMTPair.depot_id == depot_id,
            FactSPBUMTPair.spbu_id.in_(spbu_ids),
            FactSPBUMTPair.analysis_start_date == model.training_start_date,
            FactSPBUMTPair.analysis_end_date == model.training_end_date,
            FactSPBUMTPair.analysis_end_date < job.operating_date,
            FactSPBUMTPair.product_filter == "ALL",
            FactSPBUMTPair.algorithm_version == AFFINITY_ALGORITHM_VERSION,
        )
    ).all() if spbu_ids else []
    if not payload_rows and exact_rows:
        payload_rows = [_affinity_row_payload(row) for row in exact_rows]
        resolution = "EXACT_FACT"
    elif not payload_rows:
        eligible = db.scalars(
            select(FactSPBUMTPair).where(
                FactSPBUMTPair.depot_id == depot_id,
                FactSPBUMTPair.spbu_id.in_(spbu_ids),
                FactSPBUMTPair.analysis_end_date < job.operating_date,
                FactSPBUMTPair.product_filter == "ALL",
            )
        ).all() if spbu_ids else []
        latest: dict[tuple[str, str], FactSPBUMTPair] = {}
        for row in sorted(
            eligible,
            key=lambda item: (item.analysis_end_date, _iso(item.calculated_at) or "", item.analysis_start_date, item.id),
        ):
            latest[(row.spbu_id, row.mt_id)] = row
        if latest:
            payload_rows = [_affinity_row_payload(row) for row in latest.values()]
            resolution = "AS_OF_FALLBACK"
        else:
            source_shipments = db.scalars(
                select(FactShipment).where(
                    FactShipment.depot_id == depot_id,
                    FactShipment.operating_date >= model.training_start_date,
                    FactShipment.operating_date <= model.training_end_date,
                )
            ).all()
            raw_rows = load_affinity_observation_rows(
                db,
                depot_id,
                model.training_start_date,
                model.training_end_date,
                None,
            )
            prepared = prepare_affinity_observations(source_shipments, raw_rows)
            profiles = build_affinity_profiles(
                prepared["observations"],
                prepared["spbu_lookup"],
                prepared["mt_lookup"],
                model.training_start_date,
                model.training_end_date,
                "MONTHLY",
                7,
            ) if prepared["observations"] else {"pairs": []}
            payload_rows = [
                {
                    **row,
                    "product_filter": "ALL",
                    "analysis_start_date": model.training_start_date.isoformat(),
                    "analysis_end_date": model.training_end_date.isoformat(),
                    "calculated_at": None,
                }
                for row in profiles.get("pairs", [])
                if row["spbu_id"] in spbu_ids
            ]
            resolution = "CANONICAL_PHASE4_REBUILD" if payload_rows else "UNAVAILABLE"
    pair_map = {(row["spbu_id"], row["mt_id"]): row for row in payload_rows}
    denominators: dict[str, int] = defaultdict(int)
    for row in payload_rows:
        denominators[row["spbu_id"]] = max(denominators[row["spbu_id"]], int(row.get("total_spbu_shipment_count") or 0))
    return {
        "rows": sorted(payload_rows, key=lambda row: (row["spbu_id"], row["mt_id"])),
        "pair_map": pair_map,
        "denominators": dict(denominators),
        "resolution_method": resolution,
    }


def _bundle(db: Session, context: dict) -> dict:
    assignments: list[RouteVersionLOAssignment] = context["assignments"]
    spbu_ids = {row.spbu_id for row in assignments}
    vehicle_ids = {row.vehicle_id for row in assignments if row.vehicle_id}
    model: MLBehavioralModel = context["model"]
    training: MLTrainingRun = context["training"]
    clusters = {
        row.spbu_id: row
        for row in db.scalars(
            select(MLSPBUClusterAssignment).where(
                MLSPBUClusterAssignment.model_id == model.model_id,
                MLSPBUClusterAssignment.spbu_id.in_(spbu_ids),
            )
        ).all()
    } if spbu_ids else {}
    source_records = {
        str(row.get("spbu_id")): row
        for row in (training.dataset_payload or {}).get("records", [])
        if row.get("spbu_id") in spbu_ids
    }
    route_pairs = {
        _canonical_pair(left, right)
        for trip_id in {row.route_version_trip_id for row in assignments if row.route_version_trip_id}
        for left, right in combinations(
            sorted({row.spbu_id for row in assignments if row.route_version_trip_id == trip_id}),
            2,
        )
    }
    pair_rows = {
        _canonical_pair(str(row["spbu_a_id"]), str(row["spbu_b_id"])): row
        for row in (training.dataset_payload or {}).get("pair_rows", [])
        if _canonical_pair(str(row.get("spbu_a_id")), str(row.get("spbu_b_id"))) in route_pairs
    }
    affinity = _resolve_affinity(db, context, spbu_ids)
    saved_links = _saved_analysis_links(db, context)
    cluster_snapshot = {
        spbu_id: {
            "cluster_id": row.cluster_id,
            "cluster_label": row.cluster_label,
            "membership_probability": row.membership_probability,
            "is_noise": row.is_noise,
            "cluster_assignment_type": row.cluster_assignment_type,
            "data_sufficiency_status": row.data_sufficiency_status,
            "projection_confidence": row.projection_confidence,
        }
        for spbu_id, row in clusters.items()
    }
    record_snapshot = {
        spbu_id: {
            "shipment_observation_count": int(row.get("shipment_observation_count") or 0),
            "valid_shift_observation_count": int(row.get("valid_shift_observation_count") or 0),
            "shift_distribution": row.get("shift_distribution") or [],
            "dominant_shift": row.get("dominant_shift"),
            "data_sufficiency_status": row.get("data_sufficiency_status"),
        }
        for spbu_id, row in source_records.items()
    }
    pair_snapshot = {
        f"{left}|{right}": {
            key: value
            for key, value in row.items()
            if key
            in {
                "spbu_a_id",
                "spbu_b_id",
                "pair_count",
                "shipment_a_count",
                "shipment_b_count",
                "total_shipment_count",
                "probability_b_given_a",
                "probability_a_given_b",
                "support",
                "lift",
                "confidence_score",
                "confidence_level",
                "analysis_start_date",
                "analysis_end_date",
                "algorithm_version",
            }
        }
        for (left, right), row in pair_rows.items()
    }
    snapshot = {
        "mode": "SOURCE_ALIGNED",
        "lineage": {
            "depot_id": context["depot"].depot_id,
            "depot_name": context["depot"].depot_name,
            "depot_timezone": context["depot"].timezone or "Asia/Jakarta",
            "job_id": context["job"].job_id,
            "job_no": context["job"].job_no,
            "route_version_id": context["version"].route_version_id,
            "route_version_label": context["version"].version_label,
            "operating_date": context["job"].operating_date.isoformat(),
            "source_prediction_run_id": context["prediction"].id,
            "source_prediction_run_no": context["prediction"].prediction_run_no,
            "phase5_model_id": model.model_id,
            "phase5_model_name": model.model_name,
            "phase5_model_version": model.model_version,
            "source_training_run_id": training.training_run_id,
        },
        "historical_scope": {
            "start_date": model.training_start_date.isoformat(),
            "end_date": model.training_end_date.isoformat(),
            "product_scope": "ALL",
        },
        "components": {
            "cluster_cohesion": {"source": "PHASE_5_MODEL_ASSIGNMENTS", "resolution_method": "EXACT_MODEL_SOURCE", "record_count": len(cluster_snapshot)},
            "shift_alignment": {"source": "PHASE_2_MODEL_SNAPSHOT", "resolution_method": "EXACT_MODEL_SOURCE", "record_count": len(record_snapshot)},
            "historical_spbu_pairing": {"source": "PHASE_3_MODEL_SNAPSHOT", "resolution_method": "EXACT_MODEL_SOURCE", "record_count": len(pair_snapshot)},
            "historical_mt_affinity": {"source": "PHASE_4_AFFINITY", "resolution_method": affinity["resolution_method"], "record_count": len(affinity["rows"])},
        },
        "saved_analysis_links": saved_links,
        "shift_definition_snapshot": model.shift_definition_snapshot or [],
        "spbu_records": record_snapshot,
        "cluster_assignments": cluster_snapshot,
        "pair_rows": pair_snapshot,
        "affinity_rows": affinity["rows"],
        "affinity_denominators": affinity["denominators"],
        "algorithm_versions": {
            "phase5_model": model.algorithm_version,
            "phase5_training": training.algorithm_version,
            "phase9": ALGORITHM_VERSION,
        },
        "scope_counts": {"spbu": len(spbu_ids), "vehicle": len(vehicle_ids), "route_pairs": len(route_pairs)},
    }
    source_available = all(
        component["record_count"] > 0
        for name, component in snapshot["components"].items()
        if name != "historical_spbu_pairing" or len(route_pairs) > 0
    )
    snapshot["status"] = "COMPLETE" if source_available else "PARTIAL"
    snapshot["checksum"] = _checksum(snapshot)
    return {
        "snapshot": snapshot,
        "checksum": snapshot["checksum"],
        "clusters": clusters,
        "records": source_records,
        "pair_rows": pair_rows,
        "affinity": affinity,
    }


def _pair_payload(pair_row: dict | None, left: str, right: str, records: dict[str, dict]) -> dict:
    count_left = int((records.get(left) or {}).get("shipment_observation_count") or 0)
    count_right = int((records.get(right) or {}).get("shipment_observation_count") or 0)
    if not count_left or not count_right:
        return {
            "score": None,
            "status": "INSUFFICIENT_EVIDENCE",
            "probability_b_given_a": None,
            "probability_a_given_b": None,
            "pair_count": 0,
            "shipment_a_count": count_left,
            "shipment_b_count": count_right,
            "support": 0.0,
            "lift": 0.0,
            "confidence_score": 0.0,
            "confidence_level": "INSUFFICIENT_DATA",
        }
    if not pair_row:
        return {
            "score": 0.0,
            "status": "UNSEEN_PAIR",
            "probability_b_given_a": 0.0,
            "probability_a_given_b": 0.0,
            "pair_count": 0,
            "shipment_a_count": count_left,
            "shipment_b_count": count_right,
            "support": 0.0,
            "lift": 0.0,
            "confidence_score": 0.0,
            "confidence_level": "LOW",
        }
    source_a = str(pair_row.get("spbu_a_id"))
    probability_b_given_a = float(pair_row.get("probability_b_given_a") or 0)
    probability_a_given_b = float(pair_row.get("probability_a_given_b") or 0)
    shipment_a_count = int(pair_row.get("shipment_a_count") or count_left)
    shipment_b_count = int(pair_row.get("shipment_b_count") or count_right)
    if source_a != left:
        probability_b_given_a, probability_a_given_b = probability_a_given_b, probability_b_given_a
        shipment_a_count, shipment_b_count = shipment_b_count, shipment_a_count
    return {
        "score": _round_pct(50 * (probability_b_given_a + probability_a_given_b)),
        "status": "OBSERVED",
        "probability_b_given_a": probability_b_given_a,
        "probability_a_given_b": probability_a_given_b,
        "pair_count": int(pair_row.get("pair_count") or 0),
        "shipment_a_count": shipment_a_count,
        "shipment_b_count": shipment_b_count,
        "support": float(pair_row.get("support") or 0),
        "lift": float(pair_row.get("lift") or 0),
        "confidence_score": float(pair_row.get("confidence_score") or 0),
        "confidence_level": str(pair_row.get("confidence_level") or "LOW"),
    }


def _evaluate(db: Session, run: RouteAlignmentEvaluationRun, context: dict, bundle: dict) -> None:
    assignments: list[RouteVersionLOAssignment] = context["assignments"]
    trips = {row.route_version_trip_id: row for row in context["trips"]}
    clusters: dict[str, MLSPBUClusterAssignment] = bundle["clusters"]
    records: dict[str, dict] = bundle["records"]
    pair_rows: dict[tuple[str, str], dict] = bundle["pair_rows"]
    affinity = bundle["affinity"]
    depot: MasterDepot = context["depot"]

    spbu_ids = {row.spbu_id for row in assignments}
    vehicle_ids = {row.vehicle_id for row in assignments if row.vehicle_id}
    product_ids = {row.product_id for row in assignments if row.product_id}
    spbus = {row.spbu_id: row for row in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()} if spbu_ids else {}
    vehicles = {row.mt_id: row for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(vehicle_ids))).all()} if vehicle_ids else {}
    products = {row.product_id: row for row in db.scalars(select(MasterProduct).where(MasterProduct.product_id.in_(product_ids))).all()} if product_ids else {}

    by_trip: dict[str, dict[str, list[RouteVersionLOAssignment]]] = defaultdict(lambda: defaultdict(list))
    for row in assignments:
        if row.route_version_trip_id and row.vehicle_id and row.assignment_status != "DROPPED":
            by_trip[row.route_version_trip_id][row.spbu_id].append(row)

    pair_evidence: dict[tuple[str, str, str], dict] = {}
    cluster_pair_values: list[float] = []
    pairing_pair_values: list[float] = []
    total_route_pairs = 0
    evaluated_cluster_pairs = 0
    same_cluster_pairs = 0
    trip_rows = []
    per_trip_cluster: dict[str, float | None] = {}
    per_trip_pairing: dict[str, float | None] = {}

    for trip_id, members in by_trip.items():
        unique_spbus = sorted(members)
        trip_cluster_values: list[float] = []
        trip_pairing_values: list[float] = []
        for left, right in combinations(unique_spbus, 2):
            total_route_pairs += 1
            left_cluster = clusters.get(left)
            right_cluster = clusters.get(right)
            same_cluster: bool | None = None
            if _cluster_evaluable(left_cluster) and _cluster_evaluable(right_cluster):
                same_cluster = left_cluster.cluster_id == right_cluster.cluster_id
                evaluated_cluster_pairs += 1
                same_cluster_pairs += int(same_cluster)
                value = 100.0 if same_cluster else 0.0
                cluster_pair_values.append(value)
                trip_cluster_values.append(value)
            canonical = _canonical_pair(left, right)
            pairing = _pair_payload(pair_rows.get(canonical), left, right, records)
            if pairing["score"] is not None:
                pairing_pair_values.append(float(pairing["score"]))
                trip_pairing_values.append(float(pairing["score"]))
            evidence = {
                "trip_id": trip_id,
                "spbu_a_id": left,
                "spbu_b_id": right,
                "cluster_a_id": left_cluster.cluster_id if left_cluster else None,
                "cluster_b_id": right_cluster.cluster_id if right_cluster else None,
                "same_cluster": same_cluster,
                **pairing,
                "analysis_start_date": context["model"].training_start_date.isoformat(),
                "analysis_end_date": context["model"].training_end_date.isoformat(),
                "algorithm_version": context["training"].dataset_payload.get("dependency_metadata", {}).get("pairing_algorithm_version"),
            }
            pair_evidence[(trip_id, left, right)] = evidence
            db.add(
                RouteAlignmentPairEvidence(
                    pair_evidence_id=uuid.uuid4().hex,
                    evaluation_run_id=run.evaluation_run_id,
                    route_version_trip_id=trip_id,
                    spbu_a_id=left,
                    spbu_b_id=right,
                    cluster_a_id=evidence["cluster_a_id"],
                    cluster_b_id=evidence["cluster_b_id"],
                    same_cluster=same_cluster,
                    probability_b_given_a=pairing["probability_b_given_a"],
                    probability_a_given_b=pairing["probability_a_given_b"],
                    symmetric_pairing_score=pairing["score"],
                    pair_count=pairing["pair_count"],
                    shipment_a_count=pairing["shipment_a_count"],
                    shipment_b_count=pairing["shipment_b_count"],
                    support=pairing["support"],
                    lift=pairing["lift"],
                    confidence_score=pairing["confidence_score"],
                    confidence_level=pairing["confidence_level"],
                    evidence_status=pairing["status"],
                    evidence_snapshot=evidence,
                )
            )
        per_trip_cluster[trip_id] = _round_pct(statistics.fmean(trip_cluster_values)) if trip_cluster_values else None
        per_trip_pairing[trip_id] = _round_pct(statistics.fmean(trip_pairing_values)) if trip_pairing_values else None

    shifts = context["model"].shift_definition_snapshot or []
    route_shift_by_trip: dict[str, dict | None] = {}
    shift_values: list[float] = []
    mt_values: list[float] = []
    total_route_spbu = sum(len(members) for members in by_trip.values())
    total_mt_observations = total_route_spbu
    unique_context: dict[tuple[str, str], dict] = {}

    for trip_id, members in by_trip.items():
        trip = trips.get(trip_id)
        route_shift = None
        if trip and trip.gate_out and shifts:
            local_gate_out = _local_datetime(trip.gate_out, depot)
            route_shift = shift_for_minute(local_gate_out.hour * 60 + local_gate_out.minute, shifts)
        route_shift_by_trip[trip_id] = route_shift
        for spbu_id, member_rows in members.items():
            cluster = clusters.get(spbu_id)
            evaluable_peers = [peer for peer in members if peer != spbu_id and _cluster_evaluable(clusters.get(peer))]
            if not _cluster_evaluable(cluster):
                cluster_score = None
                cluster_status = "NOISE_OR_UNASSIGNED_CLUSTER"
            elif not evaluable_peers:
                cluster_score = None
                cluster_status = "NO_PEER_SPBU"
            else:
                matching = sum(clusters[peer].cluster_id == cluster.cluster_id for peer in evaluable_peers)
                cluster_score = _round_pct(100 * matching / len(evaluable_peers))
                cluster_status = "AVAILABLE"
            record = records.get(spbu_id) or {}
            distribution = record.get("shift_distribution") or []
            valid_shift_count = int(record.get("valid_shift_observation_count") or 0)
            shift_score = None
            shift_status = "INSUFFICIENT_EVIDENCE"
            shift_share = None
            if not route_shift:
                shift_status = "ROUTE_SHIFT_UNAVAILABLE"
            elif valid_shift_count:
                match = next((row for row in distribution if row.get("shift_id") == route_shift.get("shift_id")), None)
                shift_share = float(match.get("share") or 0) if match else 0.0
                shift_score = _round_pct(100 * shift_share)
                shift_status = "AVAILABLE"
                shift_values.append(float(shift_score))
            peer_details = []
            peer_scores = []
            for peer in sorted(members):
                if peer == spbu_id:
                    continue
                left, right = _canonical_pair(spbu_id, peer)
                evidence = pair_evidence[(trip_id, left, right)]
                oriented = dict(evidence)
                if spbu_id != left:
                    oriented["probability_peer_given_spbu"], oriented["probability_spbu_given_peer"] = (
                        evidence["probability_a_given_b"],
                        evidence["probability_b_given_a"],
                    )
                else:
                    oriented["probability_peer_given_spbu"], oriented["probability_spbu_given_peer"] = (
                        evidence["probability_b_given_a"],
                        evidence["probability_a_given_b"],
                    )
                oriented["peer_spbu_id"] = peer
                oriented["peer_spbu_code"] = spbus.get(peer).spbu_code if peer in spbus else peer
                peer_details.append(oriented)
                if evidence["score"] is not None:
                    peer_scores.append(float(evidence["score"]))
            pairing_score = _round_pct(statistics.fmean(peer_scores)) if peer_scores else None
            if not peer_details:
                pairing_status = "NO_PEER_SPBU"
            elif peer_scores:
                pairing_status = "AVAILABLE"
            else:
                pairing_status = "INSUFFICIENT_EVIDENCE"
            vehicle_id = member_rows[0].vehicle_id
            affinity_row = affinity["pair_map"].get((spbu_id, vehicle_id)) if vehicle_id else None
            affinity_denominator = int(affinity["denominators"].get(spbu_id) or 0)
            if not vehicle_id:
                mt_score = None
                mt_status = "NO_ASSIGNED_MT"
            elif not affinity_denominator:
                mt_score = None
                mt_status = "INSUFFICIENT_EVIDENCE"
            elif not affinity_row:
                mt_score = 0.0
                mt_status = "UNSEEN_MT_PAIR"
                mt_values.append(0.0)
            else:
                mt_score = _round_pct(100 * float(affinity_row.get("probability_mt_given_spbu") or 0))
                mt_status = "OBSERVED"
                mt_values.append(float(mt_score))
            context_payload = {
                "cluster_score": cluster_score,
                "cluster_status": cluster_status,
                "cluster_evidence": {
                    "spbu_id": spbu_id,
                    "cluster_id": cluster.cluster_id if cluster else None,
                    "cluster_label": cluster.cluster_label if cluster else None,
                    "cluster_assignment_type": cluster.cluster_assignment_type if cluster else None,
                    "membership_probability": cluster.membership_probability if cluster else None,
                    "is_noise": cluster.is_noise if cluster else None,
                    "peer_count": len(evaluable_peers),
                    "peers": [
                        {
                            "spbu_id": peer,
                            "spbu_code": spbus.get(peer).spbu_code if peer in spbus else peer,
                            "cluster_id": clusters[peer].cluster_id,
                            "cluster_label": clusters[peer].cluster_label,
                            "same_cluster": clusters[peer].cluster_id == cluster.cluster_id if cluster else None,
                        }
                        for peer in evaluable_peers
                    ],
                },
                "shift_score": shift_score,
                "shift_status": shift_status,
                "shift_evidence": {
                    "gate_out": _iso(trip.gate_out) if trip else None,
                    "route_shift_id": route_shift.get("shift_id") if route_shift else None,
                    "route_shift_name": route_shift.get("name") if route_shift else None,
                    "historical_share": shift_share,
                    "dominant_shift": record.get("dominant_shift"),
                    "dominant_shift_match": record.get("dominant_shift") == route_shift.get("name") if route_shift else None,
                    "shift_distribution": distribution,
                    "valid_shift_observation_count": valid_shift_count,
                    "shipment_observation_count": int(record.get("shipment_observation_count") or 0),
                },
                "pairing_score": pairing_score,
                "pairing_status": pairing_status,
                "pairing_evidence": {
                    "peer_count": len(peer_details),
                    "evaluated_peer_count": len(peer_scores),
                    "pairs": peer_details,
                    "analysis_start_date": context["model"].training_start_date.isoformat(),
                    "analysis_end_date": context["model"].training_end_date.isoformat(),
                },
                "mt_score": mt_score,
                "mt_status": mt_status,
                "mt_evidence": {
                    "spbu_id": spbu_id,
                    "mt_id": vehicle_id,
                    "shipment_count": int((affinity_row or {}).get("shipment_count") or 0),
                    "total_spbu_shipment_count": affinity_denominator,
                    "total_mt_shipment_count": int((affinity_row or {}).get("total_mt_shipment_count") or 0),
                    "probability_mt_given_spbu": float((affinity_row or {}).get("probability_mt_given_spbu") or 0) if affinity_denominator else None,
                    "probability_spbu_given_mt": float((affinity_row or {}).get("probability_spbu_given_mt") or 0) if affinity_row else None,
                    "first_observed": (affinity_row or {}).get("first_observed"),
                    "last_observed": (affinity_row or {}).get("last_observed"),
                    "confidence_score": float((affinity_row or {}).get("confidence_score") or 0),
                    "confidence_level": (affinity_row or {}).get("confidence_level"),
                    "analysis_start_date": (affinity_row or {}).get("analysis_start_date") or context["model"].training_start_date.isoformat(),
                    "analysis_end_date": (affinity_row or {}).get("analysis_end_date") or context["model"].training_end_date.isoformat(),
                    "product_filter": (affinity_row or {}).get("product_filter") or "ALL",
                    "resolution_method": affinity["resolution_method"],
                },
                "route_shift": route_shift,
            }
            unique_context[(trip_id, spbu_id)] = context_payload

    for trip_id, members in by_trip.items():
        trip = trips[trip_id]
        contexts = [unique_context[(trip_id, spbu_id)] for spbu_id in members]
        shift_trip_values = [float(row["shift_score"]) for row in contexts if row["shift_score"] is not None]
        mt_trip_values = [float(row["mt_score"]) for row in contexts if row["mt_score"] is not None]
        member_assignments = [assignment for member_rows in members.values() for assignment in member_rows]
        loading_order_numbers = [
            row.loading_order_id
            for row in sorted(
                member_assignments,
                key=lambda row: (row.stop_sequence is None, row.stop_sequence or 0, row.loading_order_id),
            )
        ]
        spbu_numbers = sorted(
            {spbus[spbu_id].spbu_code if spbu_id in spbus else spbu_id for spbu_id in members}
        )
        spbu_names = sorted(
            {spbus[spbu_id].spbu_name for spbu_id in members if spbu_id in spbus and spbus[spbu_id].spbu_name}
        )
        vehicle_registration = vehicles.get(trip.vehicle_id).vehicle_registration if trip.vehicle_id in vehicles else trip.vehicle_id
        route_shift_name = (route_shift_by_trip.get(trip_id) or {}).get("name")
        trip_rows.append(
            {
                "route_version_trip_id": trip_id,
                "shipment_id": trip.shipment_id,
                "vehicle_id": trip.vehicle_id,
                "vehicle_registration": vehicle_registration,
                "trip_number": trip.trip_number,
                "gate_out": _iso(trip.gate_out),
                "route_shift": route_shift_name,
                "loading_order_numbers": loading_order_numbers,
                "spbu_numbers": spbu_numbers,
                "spbu_names": spbu_names,
                "unique_spbu_count": len(members),
                "lo_count": len(member_assignments),
                "cluster_cohesion_score": per_trip_cluster.get(trip_id),
                "shift_alignment_score": _round_pct(statistics.fmean(shift_trip_values)) if shift_trip_values else None,
                "spbu_pairing_score": per_trip_pairing.get(trip_id),
                "mt_affinity_score": _round_pct(statistics.fmean(mt_trip_values)) if mt_trip_values else None,
                "evaluable_category_count": sum(
                    value is not None
                    for value in [
                        per_trip_cluster.get(trip_id),
                        _round_pct(statistics.fmean(shift_trip_values)) if shift_trip_values else None,
                        per_trip_pairing.get(trip_id),
                        _round_pct(statistics.fmean(mt_trip_values)) if mt_trip_values else None,
                    ]
                ),
                "search_index": " ".join(
                    str(value or "")
                    for value in [
                        trip.shipment_id,
                        trip.trip_number,
                        trip.vehicle_id,
                        vehicle_registration,
                        route_shift_name,
                        *loading_order_numbers,
                        *spbu_numbers,
                        *spbu_names,
                    ]
                ).lower(),
            }
        )

    for assignment in assignments:
        trip = trips.get(assignment.route_version_trip_id or "")
        payload = unique_context.get((assignment.route_version_trip_id or "", assignment.spbu_id))
        cluster = clusters.get(assignment.spbu_id)
        spbu = spbus.get(assignment.spbu_id)
        product = products.get(assignment.product_id) if assignment.product_id else None
        vehicle = vehicles.get(assignment.vehicle_id) if assignment.vehicle_id else None
        planned_gate_out = assignment.planned_gate_out or (trip.gate_out if trip else None)
        if payload:
            cluster_score = payload["cluster_score"]
            cluster_status = payload["cluster_status"]
            cluster_evidence_payload = payload["cluster_evidence"]
            shift_score = payload["shift_score"]
            shift_status = payload["shift_status"]
            shift_evidence_payload = payload["shift_evidence"]
            pairing_score = payload["pairing_score"]
            pairing_status = payload["pairing_status"]
            pairing_evidence_payload = payload["pairing_evidence"]
            mt_score = payload["mt_score"]
            mt_status = payload["mt_status"]
            mt_evidence_payload = payload["mt_evidence"]
            route_shift = payload["route_shift"]
        else:
            cluster_score = shift_score = pairing_score = mt_score = None
            cluster_status = shift_status = pairing_status = mt_status = "NO_ROUTE_ASSIGNMENT"
            cluster_evidence_payload = shift_evidence_payload = pairing_evidence_payload = mt_evidence_payload = {
                "reason": assignment.dropped_reason_code or "Loading Order is not assigned to a route trip."
            }
            route_shift = None
        evaluable_count = sum(value is not None for value in [cluster_score, shift_score, pairing_score, mt_score])
        search_text = " ".join(
            str(value or "")
            for value in [
                assignment.loading_order_id,
                assignment.shipment_id,
                assignment.trip_number,
                assignment.assignment_status,
                assignment.spbu_id,
                spbu.spbu_code if spbu else None,
                spbu.spbu_name if spbu else None,
                assignment.product_id,
                product.product_name if product else None,
                assignment.vehicle_id,
                vehicle.vehicle_registration if vehicle else None,
                cluster.cluster_label if cluster else None,
                route_shift.get("name") if route_shift else None,
                cluster_status,
                shift_status,
                pairing_status,
                mt_status,
            ]
        ).lower()
        db.add(
            RouteAlignmentEvaluationRow(
                evaluation_row_id=uuid.uuid4().hex,
                evaluation_run_id=run.evaluation_run_id,
                route_version_lo_assignment_id=assignment.route_version_lo_assignment_id,
                route_version_trip_id=assignment.route_version_trip_id,
                loading_order_id=assignment.loading_order_id,
                shipment_id=assignment.shipment_id,
                trip_number=assignment.trip_number,
                stop_sequence=assignment.stop_sequence,
                assignment_status=assignment.assignment_status,
                planned_gate_out=planned_gate_out,
                spbu_id=assignment.spbu_id,
                spbu_code=spbu.spbu_code if spbu else None,
                spbu_name=spbu.spbu_name if spbu else None,
                product_id=assignment.product_id,
                product_name=product.product_name if product else assignment.product_id,
                volume_kl=float(assignment.volume_kl or 0),
                vehicle_id=assignment.vehicle_id,
                vehicle_registration=vehicle.vehicle_registration if vehicle else assignment.vehicle_id,
                cluster_id=cluster.cluster_id if cluster else None,
                cluster_label=cluster.cluster_label if cluster else None,
                cluster_assignment_type=cluster.cluster_assignment_type if cluster else None,
                route_shift_id=route_shift.get("shift_id") if route_shift else None,
                route_shift_name=route_shift.get("name") if route_shift else None,
                cluster_cohesion_score=cluster_score,
                cluster_cohesion_status=cluster_status,
                cluster_evidence=cluster_evidence_payload,
                shift_alignment_score=shift_score,
                shift_alignment_status=shift_status,
                shift_evidence=shift_evidence_payload,
                spbu_pairing_score=pairing_score,
                spbu_pairing_status=pairing_status,
                pairing_evidence=pairing_evidence_payload,
                mt_affinity_score=mt_score,
                mt_affinity_status=mt_status,
                mt_affinity_evidence=mt_evidence_payload,
                evaluable_category_count=evaluable_count,
                search_text=search_text,
            )
        )

    cluster_score_override = 100 * same_cluster_pairs / evaluated_cluster_pairs if evaluated_cluster_pairs else None
    metrics = {
        "cluster_cohesion": _metric(
            cluster_pair_values,
            total_route_pairs,
            source="PHASE_5_MODEL_ASSIGNMENTS",
            resolution_method="EXACT_MODEL_SOURCE",
            score_override=cluster_score_override,
        ),
        "shift_alignment": _metric(
            shift_values,
            total_route_spbu,
            source="PHASE_2_MODEL_SNAPSHOT",
            resolution_method="EXACT_MODEL_SOURCE",
        ),
        "historical_spbu_pairing": _metric(
            pairing_pair_values,
            total_route_pairs,
            source="PHASE_3_MODEL_SNAPSHOT",
            resolution_method="EXACT_MODEL_SOURCE",
        ),
        "historical_mt_affinity": _metric(
            mt_values,
            total_mt_observations,
            source="PHASE_4_AFFINITY",
            resolution_method=affinity["resolution_method"],
        ),
    }
    status_counts = Counter(row.assignment_status for row in assignments)
    run.summary_snapshot = {
        "note": NEUTRAL_NOTE,
        "metrics": metrics,
        "scope": {
            "total_lo": len(assignments),
            "routed_lo": sum(bool(row.route_version_trip_id and row.vehicle_id and row.assignment_status != "DROPPED") for row in assignments),
            "dropped_or_unassigned_lo": sum(not row.route_version_trip_id or not row.vehicle_id or row.assignment_status == "DROPPED" for row in assignments),
            "trip_count": len(by_trip),
            "unique_spbu_count": len({spbu_id for members in by_trip.values() for spbu_id in members}),
            "assigned_mt_count": len({trips[trip_id].vehicle_id for trip_id in by_trip if trip_id in trips}),
            "unique_trip_spbu_observations": total_route_spbu,
            "unique_spbu_pairs": total_route_pairs,
        },
        "trips": sorted(trip_rows, key=lambda row: (row["gate_out"] or "", row["vehicle_registration"] or "", row["trip_number"])),
    }
    run.data_quality_snapshot = {
        "assignment_status_counts": dict(status_counts),
        "cluster": {"evaluable_pairs": evaluated_cluster_pairs, "same_cluster_pairs": same_cluster_pairs, "total_pairs": total_route_pairs},
        "bundle_status": bundle["snapshot"]["status"],
        "component_resolution": {
            name: component["resolution_method"]
            for name, component in bundle["snapshot"]["components"].items()
        },
    }
    run.status = "COMPLETED"
    run.completed_at = datetime.now(timezone.utc)


def create_evaluation(db: Session, depot_id: str, route_version_id: str, *, created_by: str = "local-user") -> dict:
    context = _source_context(db, depot_id, route_version_id)
    bundle = _bundle(db, context)
    existing = db.scalar(
        select(RouteAlignmentEvaluationRun).where(
            RouteAlignmentEvaluationRun.route_version_id == route_version_id,
            RouteAlignmentEvaluationRun.source_bundle_checksum == bundle["checksum"],
            RouteAlignmentEvaluationRun.algorithm_version == ALGORITHM_VERSION,
            RouteAlignmentEvaluationRun.status == "COMPLETED",
        )
    )
    if existing:
        return get_evaluation(db, existing.evaluation_run_id)
    run = RouteAlignmentEvaluationRun(
        evaluation_run_id=uuid.uuid4().hex,
        evaluation_run_no=f"RAE-{context['job'].operating_date.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
        depot_id=depot_id,
        job_id=context["job"].job_id,
        route_version_id=route_version_id,
        operating_date=context["job"].operating_date,
        source_prediction_run_id=context["prediction"].id,
        phase5_model_id=context["model"].model_id,
        status="CALCULATING",
        source_bundle_snapshot=bundle["snapshot"],
        source_bundle_checksum=bundle["checksum"],
        summary_snapshot={},
        data_quality_snapshot={},
        algorithm_version=ALGORITHM_VERSION,
        created_by=created_by,
    )
    db.add(run)
    db.commit()
    try:
        _evaluate(db, run, context, bundle)
        db.commit()
        return get_evaluation(db, run.evaluation_run_id)
    except HTTPException:
        db.rollback()
        failed = db.get(RouteAlignmentEvaluationRun, run.evaluation_run_id)
        if failed:
            failed.status = "FAILED"
            failed.error_code = "EVALUATION_FAILED"
            failed.error_message = "Route alignment evaluation failed."
            failed.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
    except Exception as exc:
        db.rollback()
        failed = db.get(RouteAlignmentEvaluationRun, run.evaluation_run_id)
        if failed:
            failed.status = "FAILED"
            failed.error_code = type(exc).__name__
            failed.error_message = str(exc)[:2000]
            failed.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(
            status_code=500,
            detail={"code": "EVALUATION_FAILED", "message": "Route alignment evaluation failed; the diagnostic run was retained."},
        ) from exc


def _serialize_run(run: RouteAlignmentEvaluationRun) -> dict:
    return {
        "evaluation_run_id": run.evaluation_run_id,
        "evaluation_run_no": run.evaluation_run_no,
        "depot_id": run.depot_id,
        "job_id": run.job_id,
        "route_version_id": run.route_version_id,
        "operating_date": run.operating_date.isoformat(),
        "source_prediction_run_id": run.source_prediction_run_id,
        "phase5_model_id": run.phase5_model_id,
        "status": run.status,
        "source_bundle": run.source_bundle_snapshot or {},
        "source_bundle_checksum": run.source_bundle_checksum,
        "summary": run.summary_snapshot or {},
        "data_quality": run.data_quality_snapshot or {},
        "algorithm_version": run.algorithm_version,
        "created_by": run.created_by,
        "created_at": _iso(run.created_at),
        "completed_at": _iso(run.completed_at),
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def get_evaluation(db: Session, evaluation_run_id: str) -> dict:
    run = db.get(RouteAlignmentEvaluationRun, evaluation_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Route alignment evaluation not found.")
    return _serialize_run(run)


def get_latest_evaluation_for_route(db: Session, route_version_id: str) -> dict:
    run = db.scalar(
        select(RouteAlignmentEvaluationRun)
        .where(
            RouteAlignmentEvaluationRun.route_version_id == route_version_id,
            RouteAlignmentEvaluationRun.status == "COMPLETED",
        )
        .order_by(desc(RouteAlignmentEvaluationRun.completed_at), desc(RouteAlignmentEvaluationRun.created_at))
    )
    if not run:
        raise HTTPException(status_code=404, detail="No completed evaluation exists for this Route Version.")
    return _serialize_run(run)


ROW_SORT_COLUMNS = {
    "loading_order_id": RouteAlignmentEvaluationRow.loading_order_id,
    "status": RouteAlignmentEvaluationRow.assignment_status,
    "shipment_id": RouteAlignmentEvaluationRow.shipment_id,
    "trip_number": RouteAlignmentEvaluationRow.trip_number,
    "planned_gate_out": RouteAlignmentEvaluationRow.planned_gate_out,
    "spbu": RouteAlignmentEvaluationRow.spbu_name,
    "product": RouteAlignmentEvaluationRow.product_name,
    "volume_kl": RouteAlignmentEvaluationRow.volume_kl,
    "assigned_mt": RouteAlignmentEvaluationRow.vehicle_registration,
    "cluster": RouteAlignmentEvaluationRow.cluster_label,
    "cluster_cohesion": RouteAlignmentEvaluationRow.cluster_cohesion_score,
    "shift_alignment": RouteAlignmentEvaluationRow.shift_alignment_score,
    "spbu_pairing": RouteAlignmentEvaluationRow.spbu_pairing_score,
    "mt_affinity": RouteAlignmentEvaluationRow.mt_affinity_score,
    "evidence_coverage": RouteAlignmentEvaluationRow.evaluable_category_count,
}


def _serialize_row(row: RouteAlignmentEvaluationRow, *, include_evidence: bool = False) -> dict:
    payload = {
        "evaluation_row_id": row.evaluation_row_id,
        "route_version_lo_assignment_id": row.route_version_lo_assignment_id,
        "route_version_trip_id": row.route_version_trip_id,
        "loading_order_id": row.loading_order_id,
        "shipment_id": row.shipment_id,
        "trip_number": row.trip_number,
        "stop_sequence": row.stop_sequence,
        "assignment_status": row.assignment_status,
        "planned_gate_out": _iso(row.planned_gate_out),
        "spbu_id": row.spbu_id,
        "spbu_code": row.spbu_code,
        "spbu_name": row.spbu_name,
        "product_id": row.product_id,
        "product_name": row.product_name,
        "volume_kl": row.volume_kl,
        "vehicle_id": row.vehicle_id,
        "vehicle_registration": row.vehicle_registration,
        "cluster_id": row.cluster_id,
        "cluster_label": row.cluster_label,
        "cluster_assignment_type": row.cluster_assignment_type,
        "route_shift_id": row.route_shift_id,
        "route_shift_name": row.route_shift_name,
        "cluster_cohesion_score": row.cluster_cohesion_score,
        "cluster_cohesion_status": row.cluster_cohesion_status,
        "shift_alignment_score": row.shift_alignment_score,
        "shift_alignment_status": row.shift_alignment_status,
        "spbu_pairing_score": row.spbu_pairing_score,
        "spbu_pairing_status": row.spbu_pairing_status,
        "mt_affinity_score": row.mt_affinity_score,
        "mt_affinity_status": row.mt_affinity_status,
        "evaluable_category_count": row.evaluable_category_count,
    }
    if include_evidence:
        payload.update(
            {
                "cluster_evidence": row.cluster_evidence or {},
                "shift_evidence": row.shift_evidence or {},
                "pairing_evidence": row.pairing_evidence or {},
                "mt_affinity_evidence": row.mt_affinity_evidence or {},
            }
        )
    return payload


def list_evaluation_rows(
    db: Session,
    evaluation_run_id: str,
    *,
    page: int = 1,
    page_size: int = 25,
    search: str | None = None,
    sort_by: str = "planned_gate_out",
    sort_direction: str = "asc",
) -> dict:
    if not db.get(RouteAlignmentEvaluationRun, evaluation_run_id):
        raise HTTPException(status_code=404, detail="Route alignment evaluation not found.")
    if sort_by not in ROW_SORT_COLUMNS:
        raise HTTPException(status_code=400, detail=f"Unsupported sort_by: {sort_by}.")
    direction = sort_direction.lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="sort_direction must be asc or desc.")
    bounded_page = max(1, page)
    bounded_size = page_size if page_size in {10, 25, 50, 100} else 25
    filters = [RouteAlignmentEvaluationRow.evaluation_run_id == evaluation_run_id]
    term = (search or "").strip().lower()
    if term:
        filters.append(func.lower(RouteAlignmentEvaluationRow.search_text).like(f"%{term}%"))
    total = db.scalar(select(func.count()).select_from(RouteAlignmentEvaluationRow).where(*filters)) or 0
    column = ROW_SORT_COLUMNS[sort_by]
    order = column.asc() if direction == "asc" else column.desc()
    rows = db.scalars(
        select(RouteAlignmentEvaluationRow)
        .where(*filters)
        .order_by(
            case((column.is_(None), 1), else_=0),
            order,
            RouteAlignmentEvaluationRow.loading_order_id.asc(),
        )
        .limit(bounded_size)
        .offset((bounded_page - 1) * bounded_size)
    ).all()
    page_count = max(1, (int(total) + bounded_size - 1) // bounded_size)
    return {
        "total": int(total),
        "page": bounded_page,
        "page_size": bounded_size,
        "page_count": page_count,
        "search": search or "",
        "sort_by": sort_by,
        "sort_direction": direction,
        "rows": [_serialize_row(row) for row in rows],
    }


def get_evaluation_row(db: Session, evaluation_run_id: str, evaluation_row_id: str) -> dict:
    row = db.scalar(
        select(RouteAlignmentEvaluationRow).where(
            RouteAlignmentEvaluationRow.evaluation_run_id == evaluation_run_id,
            RouteAlignmentEvaluationRow.evaluation_row_id == evaluation_row_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Loading Order evaluation row not found.")
    return _serialize_row(row, include_evidence=True)


TRIP_SORT_KEYS = {
    "shipment_id",
    "trip_number",
    "gate_out",
    "vehicle_registration",
    "route_shift",
    "loading_order_number",
    "spbu_number",
    "lo_count",
    "unique_spbu_count",
    "cluster_cohesion",
    "shift_alignment",
    "spbu_pairing",
    "mt_affinity",
    "evidence_coverage",
}


def list_evaluation_trips(
    db: Session,
    evaluation_run_id: str,
    *,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str = "gate_out",
    sort_direction: str = "asc",
) -> dict:
    run = db.get(RouteAlignmentEvaluationRun, evaluation_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Route alignment evaluation not found.")
    if sort_by not in TRIP_SORT_KEYS:
        raise HTTPException(status_code=400, detail=f"Unsupported trip sort_by: {sort_by}.")
    direction = sort_direction.lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="sort_direction must be asc or desc.")

    rows = [dict(row) for row in ((run.summary_snapshot or {}).get("trips") or [])]
    evaluation_rows = db.scalars(
        select(RouteAlignmentEvaluationRow).where(
            RouteAlignmentEvaluationRow.evaluation_run_id == evaluation_run_id,
            RouteAlignmentEvaluationRow.route_version_trip_id.is_not(None),
        )
    ).all()
    by_trip: dict[str, list[RouteAlignmentEvaluationRow]] = defaultdict(list)
    for row in evaluation_rows:
        by_trip[row.route_version_trip_id].append(row)

    for row in rows:
        details = by_trip.get(row["route_version_trip_id"], [])
        loading_order_numbers = [
            detail.loading_order_id
            for detail in sorted(
                details,
                key=lambda detail: (detail.stop_sequence is None, detail.stop_sequence or 0, detail.loading_order_id),
            )
        ]
        spbu_numbers = sorted({detail.spbu_code or detail.spbu_id for detail in details})
        spbu_names = sorted({detail.spbu_name for detail in details if detail.spbu_name})
        row["loading_order_numbers"] = loading_order_numbers or row.get("loading_order_numbers") or []
        row["spbu_numbers"] = spbu_numbers or row.get("spbu_numbers") or []
        row["spbu_names"] = spbu_names or row.get("spbu_names") or []
        row["lo_count"] = len(loading_order_numbers) if details else int(row.get("lo_count") or 0)
        row["unique_spbu_count"] = len(spbu_numbers) if details else int(row.get("unique_spbu_count") or 0)
        row["search_index"] = " ".join(
            [
                str(row.get("shipment_id") or ""),
                str(row.get("trip_number") or ""),
                str(row.get("vehicle_id") or ""),
                str(row.get("vehicle_registration") or ""),
                str(row.get("route_shift") or ""),
                *(row["loading_order_numbers"]),
                *(row["spbu_numbers"]),
                *(row["spbu_names"]),
                *(detail.search_text or "" for detail in details),
            ]
        ).lower()

    term = (search or "").strip().lower()
    if term:
        rows = [row for row in rows if term in row.get("search_index", "")]

    def sort_value(row: dict) -> Any:
        if sort_by == "loading_order_number":
            values = row.get("loading_order_numbers") or []
            return values[0].lower() if values else None
        if sort_by == "spbu_number":
            values = row.get("spbu_numbers") or []
            return values[0].lower() if values else None
        field = {
            "cluster_cohesion": "cluster_cohesion_score",
            "shift_alignment": "shift_alignment_score",
            "spbu_pairing": "spbu_pairing_score",
            "mt_affinity": "mt_affinity_score",
            "evidence_coverage": "evaluable_category_count",
        }.get(sort_by, sort_by)
        value = row.get(field)
        return value.lower() if isinstance(value, str) else value

    rows.sort(key=lambda row: (str(row.get("shipment_id") or ""), int(row.get("trip_number") or 0)))
    populated = [row for row in rows if sort_value(row) is not None]
    empty = [row for row in rows if sort_value(row) is None]
    populated.sort(key=sort_value, reverse=direction == "desc")
    sorted_rows = populated + empty

    bounded_page = max(1, page)
    bounded_size = page_size if page_size in {10, 25, 50, 100} else 10
    total = len(sorted_rows)
    page_count = max(1, (total + bounded_size - 1) // bounded_size)
    offset = (bounded_page - 1) * bounded_size
    visible = sorted_rows[offset : offset + bounded_size]
    return {
        "total": total,
        "page": bounded_page,
        "page_size": bounded_size,
        "page_count": page_count,
        "search": search or "",
        "sort_by": sort_by,
        "sort_direction": direction,
        "rows": [{key: value for key, value in row.items() if key != "search_index"} for row in visible],
    }
