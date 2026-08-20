from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from time import perf_counter

from fastapi import HTTPException
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session
from openpyxl import Workbook

from .config import get_settings
from .models import (
    MLBehavioralModel,
    MasterDepot,
    MasterMT,
    MasterSPBU,
    PredictionAssignment,
    PredictionMTCandidate,
    PredictionRun,
    PredictionShipment,
    PredictionShipmentLine,
)
from .phase5_registry import _model_summary
from .phase6_assignment import optimize_global_assignment
from .phase6_constants import DEFAULT_PREDICTION_PARAMETERS, PHASE6_ALGORITHM_VERSION
from .phase6_inference import load_model_inference_evidence, predict_mt_candidates, predict_shipments
from .phase6_validation import require_prediction_model, validate_loading_orders, validate_mt_availability


logger = logging.getLogger(__name__)


def list_prediction_models(db: Session, depot_id: str) -> list[dict]:
    models = db.scalars(
        select(MLBehavioralModel)
        .where(MLBehavioralModel.depot_id == depot_id, MLBehavioralModel.model_status.in_(("SAVED", "ACTIVE", "READY")))
        .order_by(desc(MLBehavioralModel.created_at))
    ).all()
    depot = db.get(MasterDepot, depot_id)
    return [
        {
            **_model_summary(model, depot.depot_name if depot else depot_id),
            "algorithm": "Node2Vec + UMAP + HDBSCAN behavioral clustering",
            "number_of_training_shipments": model.training_shipment_count,
            "number_of_spbu": model.training_spbu_count,
            "number_of_clusters": model.cluster_count,
            "model_quality_metrics": {
                "average_membership_probability": model.average_membership_probability,
                "noise_spbu_count": model.noise_spbu_count,
            },
        }
        for model in models
    ]


def _parameters(overrides: dict | None) -> dict:
    parameters = {**DEFAULT_PREDICTION_PARAMETERS, **(overrides or {})}
    for key in ("minimum_prediction_confidence", "high_confidence_threshold", "medium_confidence_threshold"):
        parameters[key] = float(parameters[key])
        if not 0 <= parameters[key] <= 1:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": f"{key} must be between 0 and 1."})
    if parameters["high_confidence_threshold"] < parameters["medium_confidence_threshold"]:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": "High confidence threshold must not be below medium threshold."})
    blocking = parameters.get("blocking_prediction_confidence")
    if blocking in ("", None):
        parameters["blocking_prediction_confidence"] = None
    else:
        parameters["blocking_prediction_confidence"] = float(blocking)
        if not 0 <= parameters["blocking_prediction_confidence"] <= 1:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": "Blocking confidence must be between 0 and 1."})
    parameters["random_seed"] = int(parameters.get("random_seed", 42))
    return parameters


def _run_number() -> str:
    return f"PRED-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def create_prediction_run(
    db: Session,
    *,
    depot_id: str,
    model_id: str,
    loading_order_content: bytes,
    loading_order_filename: str,
    availability_content: bytes,
    availability_filename: str,
    parameters: dict | None,
    created_by: str,
) -> dict:
    total_started = perf_counter()
    model = require_prediction_model(db, depot_id, model_id)
    parameter_snapshot = _parameters(parameters)
    lo_validation = validate_loading_orders(
        db, depot_id=depot_id, model=model, content=loading_order_content, file_name=loading_order_filename
    )
    mt_validation = validate_mt_availability(
        db, depot_id=depot_id, model=model, content=availability_content, file_name=availability_filename
    )
    validation_issues = [*lo_validation["issues"], *mt_validation["issues"]]
    if lo_validation["blocking_error_count"] or mt_validation["blocking_error_count"]:
        raise HTTPException(
            status_code=422,
            detail={"code": "INPUT_VALIDATION_FAILED", "message": "Prediction input contains blocking validation errors.", "issues": validation_issues},
        )
    run_id = uuid.uuid4().hex
    run_number = _run_number()
    run = PredictionRun(
        id=run_id,
        prediction_run_no=run_number,
        depot_id=depot_id,
        model_id=model_id,
        model_version=model.model_version,
        status="RUNNING",
        created_by=created_by,
        input_loading_order_filename=loading_order_filename,
        input_mt_availability_filename=availability_filename,
        input_loading_order_snapshot=lo_validation["normalized_rows"],
        input_mt_availability_snapshot=mt_validation["normalized_rows"],
        validation_snapshot=validation_issues,
        parameter_snapshot=parameter_snapshot,
        model_snapshot={
            "model_id": model.model_id,
            "model_name": model.model_name,
            "model_version": model.model_version,
            "algorithm_version": model.algorithm_version,
            "random_seed": model.random_seed,
            "shift_definition_snapshot": model.shift_definition_snapshot,
            "feature_weights": model.feature_weights,
        },
        original_prediction_snapshot=[],
        algorithm_version=PHASE6_ALGORITHM_VERSION,
        validation_duration_ms=lo_validation["duration_ms"] + mt_validation["duration_ms"],
    )
    db.add(run)
    db.commit()
    logger.info("prediction_run_started", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
    logger.info("input_validation_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
    try:
        shipment_started = perf_counter()
        evidence = load_model_inference_evidence(db, model)
        run.model_snapshot = {**run.model_snapshot, **{key: evidence[key] for key in ("artifact_checksum", "artifact_source")}}
        logger.info("phase5_model_loaded", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        shipment_predictions = predict_shipments(lo_validation["normalized_rows"], model, evidence, parameter_snapshot)
        run.shipment_prediction_duration_ms = round((perf_counter() - shipment_started) * 1000)
        logger.info("shipment_prediction_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})

        mt_started = perf_counter()
        candidate_map = predict_mt_candidates(
            db,
            depot_id=depot_id,
            shipments=shipment_predictions,
            availability=mt_validation["normalized_rows"],
            vehicle_compatibility_mode=get_settings().vehicle_compatibility_mode,
        )
        run.mt_prediction_duration_ms = round((perf_counter() - mt_started) * 1000)
        logger.info("mt_candidate_prediction_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        logger.info("compatibility_filter_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})

        shipment_entities: dict[str, PredictionShipment] = {}
        original_snapshot = []
        for prediction in shipment_predictions:
            entity = PredictionShipment(
                id=uuid.uuid4().hex,
                prediction_run_id=run_id,
                predicted_shipment_id=prediction["predicted_shipment_id"],
                shift_id=prediction["shift_id"],
                shift_name=prediction["shift"],
                shipment_prediction_score=prediction["score"],
                confidence_level=prediction["confidence_level"],
                low_confidence=prediction["low_confidence"],
                explanation=prediction["explanation"],
            )
            db.add(entity)
            shipment_entities[prediction["predicted_shipment_id"]] = entity
            original_snapshot.append(
                {
                    "predicted_shipment_id": prediction["predicted_shipment_id"],
                    "shift_id": prediction["shift_id"],
                    "score": prediction["score"],
                    "loading_order_nos": [line["loading_order_no"] for line in prediction["lines"]],
                    "spbu_ids": [line["spbu_id"] for line in prediction["lines"]],
                }
            )
            for line in prediction["lines"]:
                db.add(
                    PredictionShipmentLine(
                        id=uuid.uuid4().hex,
                        prediction_run_id=run_id,
                        prediction_shipment_id=entity.id,
                        loading_order_no=line["loading_order_no"],
                        spbu_id=line["spbu_id"],
                        spbu_no=line["spbu_no"],
                        model_predicted_shipment_id=prediction["predicted_shipment_id"],
                    )
                )
            for candidate in candidate_map[prediction["predicted_shipment_id"]]:
                db.add(
                    PredictionMTCandidate(
                        id=uuid.uuid4().hex,
                        prediction_shipment_id=entity.id,
                        vehicle_id=candidate["vehicle_id"],
                        prediction_score=candidate["prediction_score"],
                        compatibility_status=candidate["compatibility_status"],
                        candidate_rank=candidate["candidate_rank"],
                        exclusion_reason=candidate["exclusion_reason"],
                        explanation=candidate["explanation"],
                    )
                )
        run.original_prediction_snapshot = original_snapshot
        db.flush()
        assignment_started = perf_counter()
        _optimize_and_persist_assignments(db, run, shipment_entities, candidate_map, initial=True)
        db.flush()
        original_assignments = {
            assignment.prediction_shipment_id: assignment
            for assignment in db.scalars(
                select(PredictionAssignment).where(
                    PredictionAssignment.prediction_shipment_id.in_([shipment.id for shipment in shipment_entities.values()])
                )
            ).all()
        }
        run.original_prediction_snapshot = [
            {
                **item,
                "assigned_vehicle_id": original_assignments[shipment_entities[item["predicted_shipment_id"]].id].original_vehicle_id,
                "assignment_score": original_assignments[shipment_entities[item["predicted_shipment_id"]].id].original_assignment_score,
            }
            for item in original_snapshot
        ]
        run.assignment_optimization_duration_ms = round((perf_counter() - assignment_started) * 1000)
        run.total_prediction_duration_ms = round((perf_counter() - total_started) * 1000)
        run.status = "COMPLETED"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("global_assignment_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        logger.info("prediction_run_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        return get_prediction_run(db, run_id)
    except HTTPException as exc:
        db.rollback()
        failed = db.get(PredictionRun, run_id)
        if failed:
            failed.status = "FAILED"
            failed.error_code = exc.detail.get("code", "INFERENCE_FAILED") if isinstance(exc.detail, dict) else "INFERENCE_FAILED"
            failed.error_message = exc.detail.get("message", str(exc.detail)) if isinstance(exc.detail, dict) else str(exc.detail)
            failed.total_prediction_duration_ms = round((perf_counter() - total_started) * 1000)
            db.commit()
        logger.exception("prediction_run_failed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        raise
    except Exception as exc:
        db.rollback()
        failed = db.get(PredictionRun, run_id)
        if failed:
            failed.status = "FAILED"
            failed.error_code = "INFERENCE_FAILED"
            failed.error_message = f"{type(exc).__name__}: {exc}"
            failed.total_prediction_duration_ms = round((perf_counter() - total_started) * 1000)
            db.commit()
        logger.exception("prediction_run_failed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        raise HTTPException(status_code=500, detail={"code": "INFERENCE_FAILED", "message": "Prediction failed; the run was retained for audit."}) from exc


def _optimize_and_persist_assignments(
    db: Session,
    run: PredictionRun,
    shipment_entities: dict[str, PredictionShipment],
    candidate_map: dict[str, list[dict]],
    *,
    initial: bool,
    fixed_vehicle_by_shipment_id: dict[str, str] | None = None,
) -> None:
    fixed = fixed_vehicle_by_shipment_id or {}
    by_shift: dict[str, list[str]] = defaultdict(list)
    for external_id, shipment in shipment_entities.items():
        by_shift[shipment.shift_id].append(external_id)
    available_by_shift: dict[str, set[str]] = defaultdict(set)
    for row in run.input_mt_availability_snapshot:
        available_by_shift[row["shift_id"]].add(row["vehicle_id"])
    for shift_id, external_ids in by_shift.items():
        fixed_vehicles = {fixed[shipment_entities[external_id].id] for external_id in external_ids if shipment_entities[external_id].id in fixed}
        optimizable_ids = [external_id for external_id in external_ids if shipment_entities[external_id].id not in fixed]
        scores = {
            (external_id, candidate["vehicle_id"]): candidate["prediction_score"]
            for external_id in optimizable_ids
            for candidate in candidate_map.get(external_id, [])
            if candidate["compatibility_status"] == "PASS" and candidate["vehicle_id"] not in fixed_vehicles
        }
        blocking = run.parameter_snapshot.get("blocking_prediction_confidence")
        if blocking is not None:
            scores = {
                key: value
                for key, value in scores.items()
                if shipment_entities[key[0]].shipment_prediction_score >= float(blocking)
            }
        optimized = optimize_global_assignment(optimizable_ids, available_by_shift[shift_id] - fixed_vehicles, scores)
        for external_id in external_ids:
            shipment = shipment_entities[external_id]
            candidate_rows = candidate_map.get(external_id, [])
            compatible_rows = [candidate for candidate in candidate_rows if candidate["compatibility_status"] == "PASS"]
            if shipment.id in fixed:
                vehicle_id = fixed[shipment.id]
                selected = next(candidate for candidate in compatible_rows if candidate["vehicle_id"] == vehicle_id)
                assignment = db.scalar(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id == shipment.id))
                if assignment:
                    assignment.final_vehicle_id = vehicle_id
                    assignment.final_assignment_score = selected["prediction_score"]
                    assignment.assignment_status = "MANUAL_OVERRIDE"
                    assignment.unassigned_reason = None
                continue
            selected_pair = optimized.get(external_id)
            reason = None
            if not selected_pair:
                if not available_by_shift[shift_id]:
                    reason = "NO_AVAILABLE_MT"
                elif not compatible_rows:
                    reason = "NO_COMPATIBLE_MT"
                elif blocking is not None and shipment.shipment_prediction_score < float(blocking):
                    reason = "LOW_CONFIDENCE"
                else:
                    reason = "ALL_COMPATIBLE_MT_ALLOCATED"
            vehicle_id, score = selected_pair if selected_pair else (None, None)
            existing = db.scalar(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id == shipment.id))
            if existing:
                existing.final_vehicle_id = vehicle_id
                existing.final_assignment_score = score
                existing.assignment_status = "ASSIGNED" if vehicle_id else "UNASSIGNED"
                existing.unassigned_reason = reason
            else:
                db.add(
                    PredictionAssignment(
                        id=uuid.uuid4().hex,
                        prediction_shipment_id=shipment.id,
                        original_vehicle_id=vehicle_id if initial else None,
                        original_assignment_score=score if initial else None,
                        final_vehicle_id=vehicle_id,
                        final_assignment_score=score,
                        assignment_status="ASSIGNED" if vehicle_id else "UNASSIGNED",
                        unassigned_reason=reason,
                    )
                )


def list_prediction_runs(db: Session, depot_id: str | None = None) -> list[dict]:
    statement = select(PredictionRun)
    if depot_id:
        statement = statement.where(PredictionRun.depot_id == depot_id)
    runs = db.scalars(statement.order_by(desc(PredictionRun.created_at))).all()
    return [_run_history_row(db, run) for run in runs]


def _run_history_row(db: Session, run: PredictionRun) -> dict:
    shipments = db.scalars(select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id)).all()
    shipment_ids = [shipment.id for shipment in shipments]
    assignments = db.scalars(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    model = db.get(MLBehavioralModel, run.model_id)
    depot = db.get(MasterDepot, run.depot_id)
    return {
        "id": run.id,
        "prediction_run_id": run.prediction_run_no,
        "date": run.created_at.isoformat() if run.created_at else None,
        "depot_id": run.depot_id,
        "depot": depot.depot_name if depot else run.depot_id,
        "model_id": run.model_id,
        "model": run.model_snapshot.get("model_name") or (model.model_name if model else run.model_id),
        "loading_orders": len(run.input_loading_order_snapshot),
        "shipments": len(shipments),
        "assigned": sum(assignment.final_vehicle_id is not None for assignment in assignments),
        "unassigned": sum(assignment.final_vehicle_id is None for assignment in assignments),
        "user": run.created_by,
        "status": run.status,
    }


def get_prediction_run(db: Session, run_id: str) -> dict:
    run = db.get(PredictionRun, run_id)
    if not run:
        run = db.scalar(select(PredictionRun).where(PredictionRun.prediction_run_no == run_id))
    if not run:
        raise HTTPException(status_code=404, detail={"code": "PREDICTION_RUN_NOT_FOUND", "message": "Prediction run was not found."})
    depot = db.get(MasterDepot, run.depot_id)
    shipments = db.scalars(
        select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id).order_by(PredictionShipment.shift_id, PredictionShipment.predicted_shipment_id)
    ).all()
    shipment_ids = [shipment.id for shipment in shipments]
    lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    candidates = db.scalars(select(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    assignments = db.scalars(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    lines_by_shipment: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
    candidates_by_shipment: dict[str, list[PredictionMTCandidate]] = defaultdict(list)
    for line in lines:
        lines_by_shipment[line.prediction_shipment_id].append(line)
    for candidate in candidates:
        candidates_by_shipment[candidate.prediction_shipment_id].append(candidate)
    assignment_by_shipment = {assignment.prediction_shipment_id: assignment for assignment in assignments}
    mt_ids = {candidate.vehicle_id for candidate in candidates} | {assignment.final_vehicle_id for assignment in assignments if assignment.final_vehicle_id}
    mts = {row.mt_id: row for row in (db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(mt_ids))).all() if mt_ids else [])}
    spbu_ids = {line.spbu_id for line in lines}
    spbus = {row.spbu_id: row for row in (db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all() if spbu_ids else [])}
    output_shipments = []
    for shipment in shipments:
        assignment = assignment_by_shipment.get(shipment.id)
        shipment_lines = sorted(lines_by_shipment[shipment.id], key=lambda line: line.loading_order_no)
        shipment_candidates = sorted(
            candidates_by_shipment[shipment.id],
            key=lambda candidate: (candidate.compatibility_status != "PASS", candidate.candidate_rank or 999999, -candidate.prediction_score),
        )
        output_shipments.append(
            {
                "id": shipment.id,
                "predicted_shipment_id": shipment.predicted_shipment_id,
                "shift_id": shipment.shift_id,
                "shift": shipment.shift_name,
                "shipment_prediction_score": shipment.shipment_prediction_score,
                "shipment_confidence_level": shipment.confidence_level,
                "low_confidence": shipment.low_confidence,
                "is_manual_override": shipment.is_manual_override,
                "explanation": shipment.explanation,
                "lines": [
                    {
                        "id": line.id,
                        "loading_order_no": line.loading_order_no,
                        "spbu_id": line.spbu_id,
                        "spbu_no": line.spbu_no,
                        "spbu_name": spbus[line.spbu_id].spbu_name if line.spbu_id in spbus else None,
                        "model_predicted_shipment_id": line.model_predicted_shipment_id,
                    }
                    for line in shipment_lines
                ],
                "assignment": {
                    "id": assignment.id if assignment else None,
                    "original_vehicle_id": assignment.original_vehicle_id if assignment else None,
                    "original_prediction_score": assignment.original_assignment_score if assignment else None,
                    "assigned_vehicle_id": assignment.final_vehicle_id if assignment else None,
                    "assigned_vehicle_registration": mts[assignment.final_vehicle_id].vehicle_registration if assignment and assignment.final_vehicle_id in mts else None,
                    "mt_assignment_score": assignment.final_assignment_score if assignment else None,
                    "assignment_status": assignment.assignment_status if assignment else "UNASSIGNED",
                    "unassigned_reason": assignment.unassigned_reason if assignment else "ASSIGNMENT_INFEASIBLE",
                    "override_reason": assignment.override_reason if assignment else None,
                    "override_user": assignment.override_user if assignment else None,
                    "override_timestamp": assignment.override_timestamp.isoformat() if assignment and assignment.override_timestamp else None,
                },
                "candidates": [
                    {
                        "id": candidate.id,
                        "vehicle_id": candidate.vehicle_id,
                        "vehicle_registration_no": mts[candidate.vehicle_id].vehicle_registration if candidate.vehicle_id in mts else candidate.vehicle_id,
                        "prediction_score": candidate.prediction_score,
                        "compatibility_status": candidate.compatibility_status,
                        "candidate_rank": candidate.candidate_rank,
                        "exclusion_reason": candidate.exclusion_reason,
                        "explanation": candidate.explanation,
                    }
                    for candidate in shipment_candidates
                ],
            }
        )
    assigned = sum(item["assignment"]["assigned_vehicle_id"] is not None for item in output_shipments)
    avg_shipment = sum(item["shipment_prediction_score"] for item in output_shipments) / len(output_shipments) if output_shipments else 0
    mt_scores = [item["assignment"]["mt_assignment_score"] for item in output_shipments if item["assignment"]["mt_assignment_score"] is not None]
    summary_by_shift = []
    for shift_id in sorted({item["shift_id"] for item in output_shipments}):
        shift_shipments = [item for item in output_shipments if item["shift_id"] == shift_id]
        available = {row["vehicle_id"] for row in run.input_mt_availability_snapshot if row["shift_id"] == shift_id}
        summary_by_shift.append(
            {
                "shift_id": shift_id,
                "shift": shift_shipments[0]["shift"],
                "loading_orders": sum(len(item["lines"]) for item in shift_shipments),
                "unique_spbu": len({line["spbu_id"] for item in shift_shipments for line in item["lines"]}),
                "predicted_shipments": len(shift_shipments),
                "available_mt": len(available),
                "assigned": sum(item["assignment"]["assigned_vehicle_id"] is not None for item in shift_shipments),
                "unassigned": sum(item["assignment"]["assigned_vehicle_id"] is None for item in shift_shipments),
            }
        )
    return {
        "id": run.id,
        "prediction_run_id": run.prediction_run_no,
        "status": run.status,
        "depot_id": run.depot_id,
        "depot": depot.depot_name if depot else run.depot_id,
        "model_id": run.model_id,
        "model": run.model_snapshot,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "parameters": run.parameter_snapshot,
        "validation": run.validation_snapshot,
        "durations_ms": {
            "validation": run.validation_duration_ms,
            "shipment_prediction": run.shipment_prediction_duration_ms,
            "mt_prediction": run.mt_prediction_duration_ms,
            "assignment_optimization": run.assignment_optimization_duration_ms,
            "total": run.total_prediction_duration_ms,
        },
        "summary": {
            "loading_orders": len(run.input_loading_order_snapshot),
            "unique_spbu": len({row["spbu_id"] for row in run.input_loading_order_snapshot}),
            "predicted_shipments": len(output_shipments),
            "available_mt": len({(row["shift_id"], row["vehicle_id"]) for row in run.input_mt_availability_snapshot}),
            "assigned_shipments": assigned,
            "unassigned_shipments": len(output_shipments) - assigned,
            "average_shipment_confidence": round(avg_shipment, 6),
            "average_mt_assignment_confidence": round(sum(mt_scores) / len(mt_scores), 6) if mt_scores else 0,
        },
        "summary_by_shift": summary_by_shift,
        "shipments": output_shipments,
        "original_model_prediction": run.original_prediction_snapshot,
    }


def override_assignment(db: Session, run_id: str, assignment_id: str, vehicle_id: str, reason: str | None, user_id: str) -> dict:
    run = db.get(PredictionRun, run_id)
    assignment = db.get(PredictionAssignment, assignment_id)
    if not run or not assignment:
        raise HTTPException(status_code=404, detail={"code": "ASSIGNMENT_NOT_FOUND", "message": "Prediction assignment was not found."})
    shipment = db.get(PredictionShipment, assignment.prediction_shipment_id)
    if not shipment or shipment.prediction_run_id != run.id:
        raise HTTPException(status_code=404, detail={"code": "ASSIGNMENT_NOT_FOUND", "message": "Assignment does not belong to this prediction run."})
    candidate = db.scalar(
        select(PredictionMTCandidate).where(
            PredictionMTCandidate.prediction_shipment_id == shipment.id,
            PredictionMTCandidate.vehicle_id == vehicle_id,
            PredictionMTCandidate.compatibility_status == "PASS",
        )
    )
    if not candidate:
        raise HTTPException(status_code=409, detail={"code": "MASTER_COMPATIBILITY_FAIL", "message": "Override vehicle is not an available compatible candidate for this shipment."})
    conflicting_manual = db.scalar(
        select(PredictionAssignment)
        .join(PredictionShipment, PredictionShipment.id == PredictionAssignment.prediction_shipment_id)
        .where(
            PredictionShipment.prediction_run_id == run.id,
            PredictionShipment.shift_id == shipment.shift_id,
            PredictionAssignment.prediction_shipment_id != shipment.id,
            PredictionAssignment.final_vehicle_id == vehicle_id,
            PredictionAssignment.assignment_status == "MANUAL_OVERRIDE",
        )
    )
    if conflicting_manual:
        raise HTTPException(
            status_code=409,
            detail={"code": "VEHICLE_ALREADY_MANUALLY_ALLOCATED", "message": "Vehicle is already fixed by another manual override in this shift."},
        )
    assignment.final_vehicle_id = vehicle_id
    assignment.final_assignment_score = candidate.prediction_score
    assignment.assignment_status = "MANUAL_OVERRIDE"
    assignment.unassigned_reason = None
    assignment.override_reason = (reason or "").strip() or None
    assignment.override_user = user_id
    assignment.override_timestamp = datetime.now(timezone.utc)
    shipment.is_manual_override = True
    db.flush()
    _reoptimize_shift(db, run, shipment.shift_id)
    db.commit()
    logger.info("manual_override_applied", extra={"prediction_run_id": run.prediction_run_no, "model_id": run.model_id, "depot_id": run.depot_id})
    return get_prediction_run(db, run.id)


def adjust_shipment(db: Session, run_id: str, shipment_id: str, *, action: str, line_ids: list[str], target_shipment_id: str | None, user_id: str) -> dict:
    run = db.get(PredictionRun, run_id)
    source = db.get(PredictionShipment, shipment_id)
    if not run or not source or source.prediction_run_id != run.id:
        raise HTTPException(status_code=404, detail={"code": "PREDICTION_SHIPMENT_NOT_FOUND", "message": "Predicted shipment was not found."})
    action = action.upper()
    selected_lines = db.scalars(
        select(PredictionShipmentLine).where(
            PredictionShipmentLine.prediction_shipment_id == source.id,
            PredictionShipmentLine.id.in_(line_ids),
        )
    ).all() if line_ids else []
    if action in {"MOVE_LINES", "CREATE_SHIPMENT", "SPLIT_SINGLE"} and not selected_lines:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "Select at least one Loading Order line."})
    if action == "MOVE_LINES":
        target = db.get(PredictionShipment, target_shipment_id)
        if not target or target.prediction_run_id != run.id or target.shift_id != source.shift_id or target.id == source.id:
            raise HTTPException(status_code=409, detail={"code": "CROSS_SHIFT_SHIPMENT", "message": "Target shipment must be a different shipment in the same shift."})
    elif action in {"CREATE_SHIPMENT", "SPLIT_SINGLE"}:
        if action == "SPLIT_SINGLE" and len(selected_lines) != 1:
            raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "SPLIT_SINGLE requires exactly one line."})
        target = PredictionShipment(
            id=uuid.uuid4().hex,
            prediction_run_id=run.id,
            predicted_shipment_id=f"MAN-{source.shift_id.upper()}-{uuid.uuid4().hex[:6].upper()}",
            shift_id=source.shift_id,
            shift_name=source.shift_name,
            shipment_prediction_score=source.shipment_prediction_score,
            confidence_level=source.confidence_level,
            low_confidence=source.low_confidence,
            is_manual_override=True,
            explanation={"manual_adjustment": action, "adjusted_by": user_id, "model_score_reused_from_source": source.predicted_shipment_id},
        )
        db.add(target)
        db.flush()
    elif action == "COMBINE":
        target = db.get(PredictionShipment, target_shipment_id) if target_shipment_id else source
        if not target or target.prediction_run_id != run.id or target.shift_id != source.shift_id:
            raise HTTPException(status_code=409, detail={"code": "CROSS_SHIFT_SHIPMENT", "message": "Shipments may only be combined inside one shift."})
        selected_lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id == source.id)).all()
        if target.id == source.id:
            raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "Choose another same-shift shipment as the combine target."})
    else:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "Unsupported shipment override action."})
    for line in selected_lines:
        line.prediction_shipment_id = target.id
    target.is_manual_override = True
    target.explanation = {**(target.explanation or {}), "manual_adjustment": action, "adjusted_by": user_id}
    db.flush()
    remaining = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id == source.id)).all()
    if not remaining:
        db.execute(delete(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id == source.id))
        db.execute(delete(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id == source.id))
        db.delete(source)
    _rebuild_shift_candidates_and_assignments(db, run, target.shift_id)
    db.commit()
    logger.info("manual_override_applied", extra={"prediction_run_id": run.prediction_run_no, "model_id": run.model_id, "depot_id": run.depot_id})
    return get_prediction_run(db, run.id)


def _rebuild_shift_candidates_and_assignments(db: Session, run: PredictionRun, shift_id: str) -> None:
    shipments = db.scalars(
        select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id, PredictionShipment.shift_id == shift_id)
    ).all()
    shipment_ids = [shipment.id for shipment in shipments]
    lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id.in_(shipment_ids))).all()
    lines_by_shipment: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
    for line in lines:
        lines_by_shipment[line.prediction_shipment_id].append(line)
    payload = [
        {
            "predicted_shipment_id": shipment.predicted_shipment_id,
            "shift_id": shipment.shift_id,
            "shift": shipment.shift_name,
            "score": shipment.shipment_prediction_score,
            "lines": [
                {"loading_order_no": line.loading_order_no, "spbu_id": line.spbu_id, "spbu_no": line.spbu_no, "shift_id": shipment.shift_id, "shift": shipment.shift_name}
                for line in lines_by_shipment[shipment.id]
            ],
        }
        for shipment in shipments
    ]
    candidate_map = predict_mt_candidates(
        db,
        depot_id=run.depot_id,
        shipments=payload,
        availability=run.input_mt_availability_snapshot,
        vehicle_compatibility_mode=get_settings().vehicle_compatibility_mode,
    )
    db.execute(delete(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id.in_(shipment_ids)))
    db.execute(delete(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_(shipment_ids)))
    entities = {shipment.predicted_shipment_id: shipment for shipment in shipments}
    for external_id, candidates in candidate_map.items():
        for candidate in candidates:
            db.add(
                PredictionMTCandidate(
                    id=uuid.uuid4().hex,
                    prediction_shipment_id=entities[external_id].id,
                    vehicle_id=candidate["vehicle_id"],
                    prediction_score=candidate["prediction_score"],
                    compatibility_status=candidate["compatibility_status"],
                    candidate_rank=candidate["candidate_rank"],
                    exclusion_reason=candidate["exclusion_reason"],
                    explanation=candidate["explanation"],
                )
            )
    db.flush()
    _optimize_and_persist_assignments(db, run, entities, candidate_map, initial=False)


def _reoptimize_shift(db: Session, run: PredictionRun, shift_id: str) -> None:
    shipments = db.scalars(
        select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id, PredictionShipment.shift_id == shift_id)
    ).all()
    entities = {shipment.predicted_shipment_id: shipment for shipment in shipments}
    candidates = db.scalars(
        select(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id.in_([shipment.id for shipment in shipments]))
    ).all()
    candidate_map: dict[str, list[dict]] = defaultdict(list)
    by_internal = {shipment.id: shipment.predicted_shipment_id for shipment in shipments}
    for candidate in candidates:
        candidate_map[by_internal[candidate.prediction_shipment_id]].append(
            {
                "vehicle_id": candidate.vehicle_id,
                "prediction_score": candidate.prediction_score,
                "compatibility_status": candidate.compatibility_status,
            }
        )
    assignments = db.scalars(
        select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_([shipment.id for shipment in shipments]))
    ).all()
    fixed = {
        assignment.prediction_shipment_id: assignment.final_vehicle_id
        for assignment in assignments
        if assignment.assignment_status == "MANUAL_OVERRIDE" and assignment.final_vehicle_id
    }
    _optimize_and_persist_assignments(db, run, entities, candidate_map, initial=False, fixed_vehicle_by_shipment_id=fixed)


def duplicate_prediction_run(db: Session, run_id: str, *, model_id: str | None, created_by: str) -> dict:
    source = db.get(PredictionRun, run_id)
    if not source:
        raise HTTPException(status_code=404, detail={"code": "PREDICTION_RUN_NOT_FOUND", "message": "Prediction run was not found."})
    def workbook_bytes(headers: list[str], rows: list[list]) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    lo_content = workbook_bytes(
        ["loading_order_no", "shift_gate_out", "spbu_no"],
        [[row["loading_order_no"], row["shift"], row["spbu_no"]] for row in source.input_loading_order_snapshot],
    )
    mt_content = workbook_bytes(
        ["shift", "vehicle_registration_no"],
        [[row["shift"], row["vehicle_registration_no"]] for row in source.input_mt_availability_snapshot],
    )
    return create_prediction_run(
        db,
        depot_id=source.depot_id,
        model_id=model_id or source.model_id,
        loading_order_content=lo_content,
        loading_order_filename=f"rerun-{source.input_loading_order_filename}",
        availability_content=mt_content,
        availability_filename=f"rerun-{source.input_mt_availability_filename}",
        parameters=source.parameter_snapshot,
        created_by=created_by,
    )
