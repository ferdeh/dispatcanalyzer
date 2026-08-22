from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from time import perf_counter

from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from .config import get_settings
from .google_routes import GoogleRoutesError, configuration_snapshot, get_google_routes_configuration
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
    PredictionTrip,
)
from .phase5_registry import _model_summary
from .phase6_constants import DEFAULT_PREDICTION_PARAMETERS, PHASE6_ALGORITHM_VERSION
from .phase6_inference import load_model_inference_evidence, predict_mt_candidates, predict_shipments
from .phase6_routing import Phase6RouteEstimationService
from .phase6_validation import require_prediction_model, validate_loading_orders, validate_mt_availability


logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value else None


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
            "algorithm": "Phase 5 behavioral clustering and historical SPBU-MT affinity",
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
        try:
            parameters[key] = float(parameters[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": f"{key} must be numeric."}) from exc
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
    parameters["assignment_mode"] = str(parameters.get("assignment_mode", "STRICT_START")).upper()
    if parameters["assignment_mode"] not in {"STRICT_START", "ALLOW_DELAY"}:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": "assignment_mode must be STRICT_START or ALLOW_DELAY."})
    bounds = {
        "maximum_pairing_time_gap_minutes": (0, 1440),
        "maximum_allowed_delay_minutes": (0, 1440),
        "max_exact_sequence_stops": (1, 7),
        "maximum_planning_horizon_days": (1, 31),
        "random_seed": (0, 2_147_483_647),
    }
    for field, (minimum, maximum) in bounds.items():
        try:
            parameters[field] = int(parameters[field])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": f"{field} must be an integer."}) from exc
        if not minimum <= parameters[field] <= maximum:
            raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": f"{field} must be between {minimum} and {maximum}."})
    return parameters


def _run_number() -> str:
    return f"PRED-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _persist_candidates(
    db: Session,
    shipment_entities: dict[str, PredictionShipment],
    candidate_map: dict[str, list[dict]],
) -> None:
    for external_id, candidates in candidate_map.items():
        shipment = shipment_entities[external_id]
        for candidate in candidates:
            db.add(
                PredictionMTCandidate(
                    id=uuid.uuid4().hex,
                    prediction_shipment_id=shipment.id,
                    vehicle_id=candidate["vehicle_id"],
                    prediction_score=candidate["prediction_score"],
                    compatibility_status=candidate["compatibility_status"],
                    candidate_rank=candidate["candidate_rank"],
                    exclusion_reason=candidate["exclusion_reason"],
                    explanation=candidate["explanation"],
                )
            )


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
        db,
        depot_id=depot_id,
        model=model,
        content=loading_order_content,
        file_name=loading_order_filename,
        maximum_planning_horizon_days=parameter_snapshot["maximum_planning_horizon_days"],
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
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail={"code": "DEPOT_NOT_FOUND", "message": "Depot was not found."})
    configuration = get_google_routes_configuration(db)
    assert configuration is not None
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
        final_prediction_snapshot=[],
        routing_configuration_snapshot=configuration_snapshot(configuration),
        routing_metrics_snapshot={},
        algorithm_version=PHASE6_ALGORITHM_VERSION,
        validation_duration_ms=lo_validation["duration_ms"] + mt_validation["duration_ms"],
    )
    db.add(run)
    db.commit()
    logger.info("prediction_run_started", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
    try:
        shipment_started = perf_counter()
        evidence = load_model_inference_evidence(db, model)
        run.model_snapshot = {**run.model_snapshot, **{key: evidence[key] for key in ("artifact_checksum", "artifact_source")}}
        predictions = predict_shipments(lo_validation["normalized_rows"], model, evidence, parameter_snapshot)
        run.shipment_prediction_duration_ms = round((perf_counter() - shipment_started) * 1000)

        candidate_started = perf_counter()
        candidate_map = predict_mt_candidates(
            db,
            depot_id=depot_id,
            shipments=predictions,
            availability=mt_validation["normalized_rows"],
            vehicle_compatibility_mode=get_settings().vehicle_compatibility_mode,
        )
        run.mt_prediction_duration_ms = round((perf_counter() - candidate_started) * 1000)

        entities: dict[str, PredictionShipment] = {}
        original_snapshot = []
        for prediction in predictions:
            entity = PredictionShipment(
                id=uuid.uuid4().hex,
                prediction_run_id=run_id,
                predicted_shipment_id=prediction["predicted_shipment_id"],
                shift_id=prediction["shift_id"],
                shift_name=prediction["shift"],
                planned_start_datetime=prediction["planned_start_datetime"],
                shipment_prediction_score=prediction["score"],
                confidence_level=prediction["confidence_level"],
                low_confidence=prediction["low_confidence"],
                explanation=prediction["explanation"],
            )
            db.add(entity)
            entities[prediction["predicted_shipment_id"]] = entity
            original_snapshot.append(
                {
                    "predicted_shipment_id": prediction["predicted_shipment_id"],
                    "shift_id": prediction["shift_id"],
                    "planned_start_datetime": _iso(prediction["planned_start_datetime"]),
                    "score": prediction["score"],
                    "loading_order_nos": [line["loading_order_no"] for line in prediction["lines"]],
                    "spbu_ids": [line["spbu_id"] for line in prediction["lines"]],
                }
            )

        # These models intentionally use scalar foreign-key IDs instead of ORM
        # relationships. Flush the parent rows explicitly so PostgreSQL never
        # receives shipment lines or MT candidates before their shipment.
        db.flush()

        for prediction in predictions:
            entity = entities[prediction["predicted_shipment_id"]]
            for line in prediction["lines"]:
                db.add(
                    PredictionShipmentLine(
                        id=uuid.uuid4().hex,
                        prediction_run_id=run_id,
                        prediction_shipment_id=entity.id,
                        loading_order_no=line["loading_order_no"],
                        spbu_id=line["spbu_id"],
                        spbu_no=line["spbu_no"],
                        order_quantity_kl=line.get("order_quantity_kl"),
                        shipment_start_datetime=datetime.fromisoformat(line["shipment_start_datetime"]),
                        model_predicted_shipment_id=prediction["predicted_shipment_id"],
                    )
                )
        run.original_prediction_snapshot = original_snapshot
        _persist_candidates(db, entities, candidate_map)
        db.flush()
        assignment_started = perf_counter()
        _rolling_assign_and_persist(db, run, initial=True)
        run.assignment_optimization_duration_ms = round((perf_counter() - assignment_started) * 1000)
        run.total_prediction_duration_ms = round((perf_counter() - total_started) * 1000)
        run.status = "COMPLETED"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("rolling_assignment_completed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        return get_prediction_run(db, run_id)
    except HTTPException as exc:
        _mark_run_failed(db, run_id, total_started, exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)})
        raise
    except Exception as exc:
        _mark_run_failed(db, run_id, total_started, {"code": "INFERENCE_FAILED", "message": f"{type(exc).__name__}: {exc}"})
        logger.exception("prediction_run_failed", extra={"prediction_run_id": run_number, "model_id": model_id, "depot_id": depot_id})
        raise HTTPException(status_code=500, detail={"code": "INFERENCE_FAILED", "message": "Prediction failed; the run was retained for audit."}) from exc


def _mark_run_failed(db: Session, run_id: str, started: float, detail: dict) -> None:
    db.rollback()
    run = db.get(PredictionRun, run_id)
    if run:
        run.status = "FAILED"
        run.error_code = detail.get("code", "INFERENCE_FAILED")
        run.error_message = detail.get("message", "Prediction failed.")
        run.total_prediction_duration_ms = round((perf_counter() - started) * 1000)
        db.commit()


def _candidate_payloads(db: Session, shipment_ids: list[str]) -> dict[str, list[PredictionMTCandidate]]:
    result: dict[str, list[PredictionMTCandidate]] = defaultdict(list)
    if shipment_ids:
        for candidate in db.scalars(select(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id.in_(shipment_ids))).all():
            result[candidate.prediction_shipment_id].append(candidate)
    return result


def _rolling_assign_and_persist(db: Session, run: PredictionRun, *, initial: bool) -> None:
    """Chronological assignment; vehicle state is updated after each estimated trip."""
    shipments = db.scalars(
        select(PredictionShipment)
        .where(PredictionShipment.prediction_run_id == run.id)
        .order_by(PredictionShipment.planned_start_datetime, PredictionShipment.predicted_shipment_id)
    ).all()
    shipment_ids = [shipment.id for shipment in shipments]
    candidates_by_shipment = _candidate_payloads(db, shipment_ids)
    existing_assignments = {
        row.prediction_shipment_id: row
        for row in (db.scalars(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else [])
    }
    fixed_manual = {
        shipment_id: row.final_vehicle_id
        for shipment_id, row in existing_assignments.items()
        if row.assignment_status == "MANUAL_OVERRIDE" and row.final_vehicle_id
    }
    db.execute(delete(PredictionTrip).where(PredictionTrip.prediction_run_id == run.id))
    availability = {row["vehicle_id"]: datetime.fromisoformat(row["initial_available_datetime"]) for row in run.input_mt_availability_snapshot}
    trip_numbers = {vehicle_id: 0 for vehicle_id in availability}
    vehicle_ids = list(availability)
    mts = {row.mt_id: row for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(vehicle_ids))).all()} if vehicle_ids else {}
    lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    lines_by_shipment: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
    for line in lines:
        lines_by_shipment[line.prediction_shipment_id].append(line)
    spbu_ids = sorted({line.spbu_id for line in lines})
    spbus = {row.spbu_id: row for row in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()} if spbu_ids else {}
    depot = db.get(MasterDepot, run.depot_id)
    configuration = get_google_routes_configuration(db)
    assert depot is not None and configuration is not None
    metrics: dict[str, int] = {}
    routing = Phase6RouteEstimationService(db, configuration=configuration, model_id=run.model_id, metrics=metrics)
    mode = run.parameter_snapshot["assignment_mode"]
    allowed_delay = int(run.parameter_snapshot["maximum_allowed_delay_minutes"])
    blocking = run.parameter_snapshot.get("blocking_prediction_confidence")
    final_snapshot = []

    for sequence_number, shipment in enumerate(shipments, start=1):
        planned = _utc(shipment.planned_start_datetime)
        compatible = [candidate for candidate in candidates_by_shipment[shipment.id] if candidate.compatibility_status == "PASS"]
        compatible.sort(key=lambda row: (-row.prediction_score, (mts.get(row.vehicle_id).vehicle_registration if mts.get(row.vehicle_id) else row.vehicle_id)))
        fixed_vehicle = fixed_manual.get(shipment.id)
        if fixed_vehicle:
            compatible = sorted(compatible, key=lambda row: row.vehicle_id != fixed_vehicle)
        timing_eligible = []
        for candidate in compatible:
            next_available = _utc(availability[candidate.vehicle_id])
            delay_minutes = max(0, round((next_available - planned).total_seconds() / 60))
            if fixed_vehicle == candidate.vehicle_id or next_available <= planned or (mode == "ALLOW_DELAY" and delay_minutes <= allowed_delay):
                timing_eligible.append((candidate, next_available, delay_minutes))

        unassigned_reason = None
        if blocking is not None and shipment.shipment_prediction_score < float(blocking) and not fixed_vehicle:
            timing_eligible = []
            unassigned_reason = "LOW_PREDICTION_CONFIDENCE"
        elif not compatible:
            unassigned_reason = "NO_COMPATIBLE_MT"
        elif not timing_eligible:
            unassigned_reason = "NO_MT_AVAILABLE_AT_REQUIRED_TIME"

        selected = None
        estimate = None
        routing_failure = None
        for candidate, next_available, delay_minutes in timing_eligible:
            if fixed_vehicle and candidate.vehicle_id != fixed_vehicle:
                continue
            departure = max(planned, next_available)
            try:
                estimate = routing.estimate_trip(
                    depot=depot,
                    spbus=[spbus[line.spbu_id] for line in lines_by_shipment[shipment.id]],
                    mt=mts[candidate.vehicle_id],
                    predicted_departure_datetime=departure,
                    max_exact_sequence_stops=int(run.parameter_snapshot["max_exact_sequence_stops"]),
                )
                selected = (candidate, departure, delay_minutes)
                break
            except GoogleRoutesError as exc:
                routing_failure = exc.code
        if not selected and timing_eligible:
            unassigned_reason = routing_failure or "ROUTING_ESTIMATE_FAILED"

        assignment = existing_assignments.get(shipment.id)
        if not assignment:
            assignment = PredictionAssignment(id=uuid.uuid4().hex, prediction_shipment_id=shipment.id)
            db.add(assignment)
            existing_assignments[shipment.id] = assignment
        if selected and estimate:
            candidate, departure, delay_minutes = selected
            is_manual = fixed_vehicle == candidate.vehicle_id
            status = "MANUAL_OVERRIDE" if is_manual else "ASSIGNED_WITH_DELAY" if delay_minutes > 0 else "ASSIGNED"
            if initial:
                assignment.original_vehicle_id = candidate.vehicle_id
                assignment.original_assignment_score = candidate.prediction_score
            assignment.final_vehicle_id = candidate.vehicle_id
            assignment.final_assignment_score = candidate.prediction_score
            assignment.assignment_status = status
            assignment.unassigned_reason = None
            trip_numbers[candidate.vehicle_id] += 1
            availability[candidate.vehicle_id] = estimate["next_available_datetime"]
            trip = PredictionTrip(
                id=uuid.uuid4().hex,
                prediction_run_id=run.id,
                prediction_shipment_id=shipment.id,
                trip_id=f"TRIP-{sequence_number:04d}",
                trip_number=trip_numbers[candidate.vehicle_id],
                vehicle_id=candidate.vehicle_id,
                planned_start_datetime=planned,
                predicted_departure_datetime=departure,
                delay_minutes=delay_minutes,
                estimated_visit_sequence=estimate["estimated_visit_sequence"],
                routing_provider=estimate["routing_provider"],
                routing_mode=estimate["routing_mode"],
                routing_preference=estimate["routing_preference"],
                large_vehicle_used=estimate["large_vehicle_used"],
                route_distance_meters=estimate["route_distance_meters"],
                route_duration_seconds=estimate["route_duration_seconds"],
                static_duration_seconds=estimate["static_duration_seconds"],
                service_duration_seconds=estimate["service_duration_seconds"],
                turnaround_buffer_seconds=estimate["turnaround_buffer_seconds"],
                total_cycle_duration_seconds=estimate["total_cycle_duration_seconds"],
                estimated_return_datetime=estimate["estimated_return_datetime"],
                next_available_datetime=estimate["next_available_datetime"],
                routing_confidence=estimate["routing_confidence"],
                route_estimation_source=estimate["route_estimation_source"],
                service_time_source=estimate["service_time_source"],
                assignment_status=status,
                fallback_used=estimate["fallback_used"],
                warning_codes=estimate["warning_codes"],
                vehicle_profile_snapshot=estimate["vehicle_profile_snapshot"],
            )
        else:
            if initial:
                assignment.original_vehicle_id = None
                assignment.original_assignment_score = None
            assignment.final_vehicle_id = None
            assignment.final_assignment_score = None
            assignment.assignment_status = "UNASSIGNED"
            assignment.unassigned_reason = unassigned_reason or "NO_MT_AVAILABLE_AT_REQUIRED_TIME"
            trip = PredictionTrip(
                id=uuid.uuid4().hex,
                prediction_run_id=run.id,
                prediction_shipment_id=shipment.id,
                trip_id=f"TRIP-{sequence_number:04d}",
                planned_start_datetime=planned,
                delay_minutes=0,
                assignment_status="UNASSIGNED",
                unassigned_reason=assignment.unassigned_reason,
                warning_codes=[assignment.unassigned_reason],
            )
        db.add(trip)
        final_snapshot.append(
            {
                "trip_id": trip.trip_id,
                "predicted_shipment_id": shipment.predicted_shipment_id,
                "vehicle_id": trip.vehicle_id,
                "planned_start_datetime": _iso(planned),
                "predicted_departure_datetime": _iso(trip.predicted_departure_datetime),
                "estimated_return_datetime": _iso(trip.estimated_return_datetime),
                "next_available_datetime": _iso(trip.next_available_datetime),
                "predicted_visit_sequence": trip.estimated_visit_sequence,
                "shipment_score": shipment.shipment_prediction_score,
                "vehicle_score": assignment.final_assignment_score,
                "assignment_status": trip.assignment_status,
                "route_estimation_source": trip.route_estimation_source,
            }
        )
    db.flush()
    if initial:
        by_external = {shipment.id: shipment.predicted_shipment_id for shipment in shipments}
        original_assignments = {
            shipment_id: assignment for shipment_id, assignment in existing_assignments.items()
        }
        run.original_prediction_snapshot = [
            {
                **item,
                "assigned_vehicle_id": original_assignments[next(key for key, value in by_external.items() if value == item["predicted_shipment_id"])].original_vehicle_id,
                "assignment_score": original_assignments[next(key for key, value in by_external.items() if value == item["predicted_shipment_id"])].original_assignment_score,
            }
            for item in run.original_prediction_snapshot
        ]
    run.final_prediction_snapshot = final_snapshot
    run.routing_metrics_snapshot = metrics


def list_prediction_runs(db: Session, depot_id: str | None = None) -> list[dict]:
    statement = select(PredictionRun)
    if depot_id:
        statement = statement.where(PredictionRun.depot_id == depot_id)
    return [_run_history_row(db, run) for run in db.scalars(statement.order_by(desc(PredictionRun.created_at))).all()]


def _run_history_row(db: Session, run: PredictionRun) -> dict:
    shipments = db.scalars(select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id)).all()
    trips = db.scalars(select(PredictionTrip).where(PredictionTrip.prediction_run_id == run.id)).all()
    depot = db.get(MasterDepot, run.depot_id)
    return {
        "id": run.id,
        "prediction_run_id": run.prediction_run_no,
        "date": _iso(run.created_at),
        "depot_id": run.depot_id,
        "depot": depot.depot_name if depot else run.depot_id,
        "model_id": run.model_id,
        "model": run.model_snapshot.get("model_name") or run.model_id,
        "loading_orders": len(run.input_loading_order_snapshot),
        "shipments": len(shipments),
        "trips": len(trips),
        "assigned": sum(trip.vehicle_id is not None for trip in trips),
        "unassigned": sum(trip.vehicle_id is None for trip in trips),
        "user": run.created_by,
        "status": run.status,
    }


def get_prediction_run(db: Session, run_id: str) -> dict:
    run = db.get(PredictionRun, run_id) or db.scalar(select(PredictionRun).where(PredictionRun.prediction_run_no == run_id))
    if not run:
        raise HTTPException(status_code=404, detail={"code": "PREDICTION_RUN_NOT_FOUND", "message": "Prediction run was not found."})
    depot = db.get(MasterDepot, run.depot_id)
    shipments = db.scalars(
        select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id).order_by(PredictionShipment.planned_start_datetime, PredictionShipment.predicted_shipment_id)
    ).all()
    shipment_ids = [shipment.id for shipment in shipments]
    lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    candidates = db.scalars(select(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    assignments = db.scalars(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    trips = db.scalars(select(PredictionTrip).where(PredictionTrip.prediction_run_id == run.id).order_by(PredictionTrip.planned_start_datetime, PredictionTrip.trip_id)).all()
    lines_by: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
    candidates_by: dict[str, list[PredictionMTCandidate]] = defaultdict(list)
    for line in lines:
        lines_by[line.prediction_shipment_id].append(line)
    for candidate in candidates:
        candidates_by[candidate.prediction_shipment_id].append(candidate)
    assignments_by = {assignment.prediction_shipment_id: assignment for assignment in assignments}
    trips_by = {trip.prediction_shipment_id: trip for trip in trips}
    mt_ids = {candidate.vehicle_id for candidate in candidates} | {trip.vehicle_id for trip in trips if trip.vehicle_id}
    mts = {row.mt_id: row for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(mt_ids))).all()} if mt_ids else {}
    spbu_ids = {line.spbu_id for line in lines}
    spbus = {row.spbu_id: row for row in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()} if spbu_ids else {}
    output_shipments = []
    output_trips = []
    for shipment in shipments:
        assignment = assignments_by.get(shipment.id)
        trip = trips_by.get(shipment.id)
        shipment_lines = sorted(lines_by[shipment.id], key=lambda line: (_iso(line.shipment_start_datetime) or "", line.loading_order_no))
        shipment_candidates = sorted(candidates_by[shipment.id], key=lambda row: (row.compatibility_status != "PASS", row.candidate_rank or 999999, -row.prediction_score))
        assignment_payload = {
            "id": assignment.id if assignment else None,
            "original_vehicle_id": assignment.original_vehicle_id if assignment else None,
            "original_prediction_score": assignment.original_assignment_score if assignment else None,
            "assigned_vehicle_id": assignment.final_vehicle_id if assignment else None,
            "assigned_vehicle_registration": (mts[assignment.final_vehicle_id].vehicle_registration or assignment.final_vehicle_id) if assignment and assignment.final_vehicle_id in mts else None,
            "mt_assignment_score": assignment.final_assignment_score if assignment else None,
            "assignment_status": assignment.assignment_status if assignment else "UNASSIGNED",
            "unassigned_reason": assignment.unassigned_reason if assignment else "NO_MT_AVAILABLE_AT_REQUIRED_TIME",
            "override_reason": assignment.override_reason if assignment else None,
            "override_user": assignment.override_user if assignment else None,
            "override_timestamp": _iso(assignment.override_timestamp) if assignment else None,
        }
        line_payloads = [
            {
                "id": line.id,
                "loading_order_no": line.loading_order_no,
                "shipment_start_datetime": _iso(line.shipment_start_datetime),
                "spbu_id": line.spbu_id,
                "spbu_no": line.spbu_no,
                "spbu_name": spbus[line.spbu_id].spbu_name if line.spbu_id in spbus else None,
                "order_quantity_kl": line.order_quantity_kl,
                "model_predicted_shipment_id": line.model_predicted_shipment_id,
            }
            for line in shipment_lines
        ]
        candidate_payloads = [
            {
                "id": candidate.id,
                "vehicle_id": candidate.vehicle_id,
                "vehicle_registration_no": (mts[candidate.vehicle_id].vehicle_registration or candidate.vehicle_id) if candidate.vehicle_id in mts else candidate.vehicle_id,
                "prediction_score": candidate.prediction_score,
                "compatibility_status": candidate.compatibility_status,
                "candidate_rank": candidate.candidate_rank,
                "exclusion_reason": candidate.exclusion_reason,
                "explanation": candidate.explanation,
            }
            for candidate in shipment_candidates
        ]
        trip_payload = _trip_payload(trip, shipment, assignment_payload, mts) if trip else None
        output_shipments.append(
            {
                "id": shipment.id,
                "predicted_shipment_id": shipment.predicted_shipment_id,
                "planned_start_datetime": _iso(shipment.planned_start_datetime),
                "shift_id": shipment.shift_id,
                "shift": shipment.shift_name,
                "shipment_prediction_score": shipment.shipment_prediction_score,
                "shipment_confidence_level": shipment.confidence_level,
                "low_confidence": shipment.low_confidence,
                "is_manual_override": shipment.is_manual_override,
                "explanation": shipment.explanation,
                "lines": line_payloads,
                "assignment": assignment_payload,
                "trip": trip_payload,
                "candidates": candidate_payloads,
            }
        )
        if trip_payload:
            output_trips.append({**trip_payload, "lines": line_payloads, "candidates": candidate_payloads})

    assigned_trips = [trip for trip in output_trips if trip["vehicle_id"]]
    mt_scores = [trip["mt_assignment_score"] for trip in assigned_trips if trip["mt_assignment_score"] is not None]
    summary_by_shift = []
    for shift_id in sorted({item["shift_id"] for item in output_shipments}):
        rows = [item for item in output_shipments if item["shift_id"] == shift_id]
        summary_by_shift.append(
            {
                "shift_id": shift_id,
                "shift": rows[0]["shift"],
                "loading_orders": sum(len(item["lines"]) for item in rows),
                "total_order_kl": round(sum(line["order_quantity_kl"] or 0 for item in rows for line in item["lines"]), 3),
                "unique_spbu": len({line["spbu_id"] for item in rows for line in item["lines"]}),
                "predicted_shipments": len(rows),
                "assigned": sum(item["assignment"]["assigned_vehicle_id"] is not None for item in rows),
                "unassigned": sum(item["assignment"]["assigned_vehicle_id"] is None for item in rows),
            }
        )
    timeline: dict[str, list[dict]] = defaultdict(list)
    for trip in assigned_trips:
        timeline[trip["vehicle_id"]].append(
            {
                "trip_id": trip["trip_id"],
                "trip_number": trip["trip_number"],
                "shipment_id": trip["predicted_shipment_id"],
                "start": trip["predicted_departure_datetime"],
                "return": trip["estimated_return_datetime"],
                "next_available": trip["next_available_datetime"],
                "status": trip["assignment_status"],
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
        "created_at": _iso(run.created_at),
        "completed_at": _iso(run.completed_at),
        "parameters": run.parameter_snapshot,
        "routing_configuration": run.routing_configuration_snapshot,
        "routing_metrics": run.routing_metrics_snapshot,
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
            "total_order_kl": round(sum(row.get("order_quantity_kl") or 0 for row in run.input_loading_order_snapshot), 3),
            "unique_spbu": len({row["spbu_id"] for row in run.input_loading_order_snapshot}),
            "predicted_shipments": len(output_shipments),
            "total_trips": len(output_trips),
            "available_mt": len(run.input_mt_availability_snapshot),
            "assigned_shipments": len(assigned_trips),
            "assigned_with_delay": sum(trip["assignment_status"] == "ASSIGNED_WITH_DELAY" for trip in output_trips),
            "unassigned_shipments": len(output_trips) - len(assigned_trips),
            "multi_trip_mt": sum(1 for rows in timeline.values() if len(rows) > 1),
            "fallback_trips": sum(bool(trip["fallback_used"]) for trip in assigned_trips),
            "average_shipment_confidence": round(sum(item["shipment_prediction_score"] for item in output_shipments) / len(output_shipments), 6) if output_shipments else 0,
            "average_mt_assignment_confidence": round(sum(mt_scores) / len(mt_scores), 6) if mt_scores else 0,
        },
        "summary_by_shift": summary_by_shift,
        "shipments": output_shipments,
        "trips": output_trips,
        "mt_timeline": [
            {
                "vehicle_id": vehicle_id,
                "vehicle_registration_no": mts[vehicle_id].vehicle_registration or vehicle_id if vehicle_id in mts else vehicle_id,
                "trips": rows,
            }
            for vehicle_id, rows in sorted(timeline.items(), key=lambda item: (mts[item[0]].vehicle_registration if item[0] in mts else item[0]))
        ],
        "phase7_input": run.final_prediction_snapshot,
        "original_model_prediction": run.original_prediction_snapshot,
        "final_dispatch_prediction": run.final_prediction_snapshot,
    }


def _trip_payload(trip: PredictionTrip, shipment: PredictionShipment, assignment: dict, mts: dict[str, MasterMT]) -> dict:
    return {
        "id": trip.id,
        "trip_id": trip.trip_id,
        "trip_number": trip.trip_number,
        "predicted_shipment_id": shipment.predicted_shipment_id,
        "vehicle_id": trip.vehicle_id,
        "vehicle_registration_no": (mts[trip.vehicle_id].vehicle_registration or trip.vehicle_id) if trip.vehicle_id in mts else None,
        "planned_start_datetime": _iso(trip.planned_start_datetime),
        "predicted_departure_datetime": _iso(trip.predicted_departure_datetime),
        "delay_minutes": trip.delay_minutes,
        "estimated_visit_sequence": trip.estimated_visit_sequence,
        "routing_provider": trip.routing_provider,
        "routing_mode": trip.routing_mode,
        "routing_preference": trip.routing_preference,
        "large_vehicle_used": trip.large_vehicle_used,
        "route_distance_meters": trip.route_distance_meters,
        "route_duration_seconds": trip.route_duration_seconds,
        "static_duration_seconds": trip.static_duration_seconds,
        "service_duration_seconds": trip.service_duration_seconds,
        "service_time_source": trip.service_time_source,
        "turnaround_buffer_seconds": trip.turnaround_buffer_seconds,
        "total_cycle_duration_seconds": trip.total_cycle_duration_seconds,
        "estimated_return_datetime": _iso(trip.estimated_return_datetime),
        "next_available_datetime": _iso(trip.next_available_datetime),
        "shipment_prediction_score": shipment.shipment_prediction_score,
        "mt_assignment_score": assignment["mt_assignment_score"],
        "routing_confidence": trip.routing_confidence,
        "route_estimation_source": trip.route_estimation_source,
        "assignment_status": trip.assignment_status,
        "unassigned_reason": trip.unassigned_reason,
        "fallback_used": trip.fallback_used,
        "warning_codes": trip.warning_codes,
        "vehicle_profile_snapshot": trip.vehicle_profile_snapshot,
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
        raise HTTPException(status_code=409, detail={"code": "MASTER_COMPATIBILITY_FAIL", "message": "Override MT is not compatible with every SPBU in the shipment."})
    assignment.final_vehicle_id = vehicle_id
    assignment.final_assignment_score = candidate.prediction_score
    assignment.assignment_status = "MANUAL_OVERRIDE"
    assignment.unassigned_reason = None
    assignment.override_reason = (reason or "").strip() or None
    assignment.override_user = user_id
    assignment.override_timestamp = datetime.now(timezone.utc)
    shipment.is_manual_override = True
    db.flush()
    _rolling_assign_and_persist(db, run, initial=False)
    db.commit()
    logger.info("manual_override_applied", extra={"prediction_run_id": run.prediction_run_no, "model_id": run.model_id, "depot_id": run.depot_id})
    return get_prediction_run(db, run.id)


def override_trip_assignment(db: Session, run_id: str, trip_id: str, vehicle_id: str, reason: str | None, user_id: str) -> dict:
    trip = db.get(PredictionTrip, trip_id) or db.scalar(
        select(PredictionTrip).where(PredictionTrip.prediction_run_id == run_id, PredictionTrip.trip_id == trip_id)
    )
    if not trip or trip.prediction_run_id != run_id:
        raise HTTPException(status_code=404, detail={"code": "TRIP_NOT_FOUND", "message": "Prediction trip was not found."})
    assignment = db.scalar(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id == trip.prediction_shipment_id))
    if not assignment:
        raise HTTPException(status_code=404, detail={"code": "ASSIGNMENT_NOT_FOUND", "message": "Trip assignment was not found."})
    return override_assignment(db, run_id, assignment.id, vehicle_id, reason, user_id)


def _rebuild_candidates_and_timeline(db: Session, run: PredictionRun) -> None:
    shipments = db.scalars(select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id)).all()
    shipment_ids = [shipment.id for shipment in shipments]
    lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    lines_by: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
    for line in lines:
        lines_by[line.prediction_shipment_id].append(line)
    payload = [
        {
            "predicted_shipment_id": shipment.predicted_shipment_id,
            "shift_id": shipment.shift_id,
            "shift": shipment.shift_name,
            "planned_start_datetime": shipment.planned_start_datetime,
            "score": shipment.shipment_prediction_score,
            "lines": [
                {
                    "loading_order_no": line.loading_order_no,
                    "spbu_id": line.spbu_id,
                    "spbu_no": line.spbu_no,
                    "shipment_start_datetime": _iso(line.shipment_start_datetime),
                    "shift_id": shipment.shift_id,
                    "shift": shipment.shift_name,
                }
                for line in lines_by[shipment.id]
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
    if shipment_ids:
        db.execute(delete(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id.in_(shipment_ids)))
    _persist_candidates(db, {shipment.predicted_shipment_id: shipment for shipment in shipments}, candidate_map)
    db.flush()
    _rolling_assign_and_persist(db, run, initial=False)


def adjust_shipment(
    db: Session,
    run_id: str,
    shipment_id: str,
    *,
    action: str,
    line_ids: list[str],
    target_shipment_id: str | None,
    user_id: str,
) -> dict:
    run = db.get(PredictionRun, run_id)
    source = db.get(PredictionShipment, shipment_id)
    if not run or not source or source.prediction_run_id != run.id:
        raise HTTPException(status_code=404, detail={"code": "PREDICTION_SHIPMENT_NOT_FOUND", "message": "Predicted shipment was not found."})
    action = action.upper()
    selected_lines = db.scalars(
        select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id == source.id, PredictionShipmentLine.id.in_(line_ids))
    ).all() if line_ids else []
    if action in {"MOVE_LINES", "CREATE_SHIPMENT", "SPLIT_SINGLE"} and not selected_lines:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "Select at least one Loading Order line."})
    if action == "MOVE_LINES":
        target = db.get(PredictionShipment, target_shipment_id)
        if not target or target.prediction_run_id != run.id or target.shift_id != source.shift_id or target.id == source.id:
            raise HTTPException(status_code=409, detail={"code": "CROSS_SHIFT_SHIPMENT", "message": "Target shipment must be a different shipment in the same derived shift."})
    elif action in {"CREATE_SHIPMENT", "SPLIT_SINGLE"}:
        if action == "SPLIT_SINGLE" and len(selected_lines) != 1:
            raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "SPLIT_SINGLE requires exactly one line."})
        target = PredictionShipment(
            id=uuid.uuid4().hex,
            prediction_run_id=run.id,
            predicted_shipment_id=f"MAN-{source.shift_id.upper()}-{uuid.uuid4().hex[:6].upper()}",
            shift_id=source.shift_id,
            shift_name=source.shift_name,
            planned_start_datetime=max(_utc(line.shipment_start_datetime) for line in selected_lines),
            shipment_prediction_score=source.shipment_prediction_score,
            confidence_level=source.confidence_level,
            low_confidence=source.low_confidence,
            is_manual_override=True,
            explanation={"manual_adjustment": action, "adjusted_by": user_id, "model_score_reused_from_source": source.predicted_shipment_id},
        )
        db.add(target)
        db.flush()
    elif action == "COMBINE":
        target = db.get(PredictionShipment, target_shipment_id) if target_shipment_id else None
        if not target or target.prediction_run_id != run.id or target.shift_id != source.shift_id or target.id == source.id:
            raise HTTPException(status_code=409, detail={"code": "CROSS_SHIFT_SHIPMENT", "message": "Shipments may only be combined inside one derived shift."})
        selected_lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id == source.id)).all()
    else:
        raise HTTPException(status_code=400, detail={"code": "INVALID_SHIPMENT_OVERRIDE", "message": "Unsupported shipment override action."})
    for line in selected_lines:
        line.prediction_shipment_id = target.id
    target.is_manual_override = True
    target.explanation = {**(target.explanation or {}), "manual_adjustment": action, "adjusted_by": user_id}
    db.flush()
    target_lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id == target.id)).all()
    target.planned_start_datetime = max(_utc(line.shipment_start_datetime) for line in target_lines)
    remaining = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id == source.id)).all()
    if remaining:
        source.planned_start_datetime = max(_utc(line.shipment_start_datetime) for line in remaining)
    else:
        db.execute(delete(PredictionMTCandidate).where(PredictionMTCandidate.prediction_shipment_id == source.id))
        db.execute(delete(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id == source.id))
        db.execute(delete(PredictionTrip).where(PredictionTrip.prediction_shipment_id == source.id))
        db.delete(source)
    db.flush()
    _rebuild_candidates_and_timeline(db, run)
    db.commit()
    return get_prediction_run(db, run.id)


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
        ["loading_order_no", "shipment_start_datetime", "spbu_no", "order_quantity_kl"],
        [[row["loading_order_no"], row["shipment_start_datetime_local"], row["spbu_no"], row.get("order_quantity_kl")] for row in source.input_loading_order_snapshot],
    )
    mt_content = workbook_bytes(
        ["vehicle_registration_no", "initial_available_datetime"],
        [[row["vehicle_registration_no"], row["initial_available_datetime_local"]] for row in source.input_mt_availability_snapshot],
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
