from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .google_routes import GoogleRoutesError, get_google_routes_configuration
from .models import MLBehavioralModel, MasterDepot, MasterMT, MasterSPBU, PredictionRun
from .phase6_capacity import shipment_capacity
from .phase6_inference import predict_mt_candidates, predict_shipments
from .phase6_routing import Phase6RouteEstimationService


CAPACITY_ITERATION_TIERS = (4, 3, 2, 1)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def build_iterative_capacity_plan(
    db: Session,
    *,
    run: PredictionRun,
    model: MLBehavioralModel,
    evidence: dict,
) -> dict:
    """Group and assign Loading Orders in descending 32/24/16/8 KL tiers.

    Only a successfully assigned group consumes its Loading Orders. A group that
    cannot find an available, capacity- and tag-compatible MT is dissolved and
    reconsidered at the next smaller tier. This prevents 32 KL predictions from
    stranding Loading Orders when the actual fleet is dominated by 16/24 KL MT.
    """

    parameters = run.parameter_snapshot
    remaining = {
        row["loading_order_no"]: row
        for row in sorted(
            run.input_loading_order_snapshot,
            key=lambda item: (item["shipment_start_datetime"], item["spbu_no"], item["loading_order_no"]),
        )
    }
    availability = {
        row["vehicle_id"]: datetime.fromisoformat(row["initial_available_datetime"])
        for row in run.input_mt_availability_snapshot
    }
    vehicle_ids = sorted(availability)
    mts = {
        row.mt_id: row
        for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(vehicle_ids))).all()
    } if vehicle_ids else {}
    spbu_ids = sorted({row["spbu_id"] for row in remaining.values()})
    spbus = {
        row.spbu_id: row
        for row in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()
    } if spbu_ids else {}
    depot = db.get(MasterDepot, run.depot_id)
    configuration = get_google_routes_configuration(db)
    assert depot is not None and configuration is not None

    routing_metrics: dict[str, int] = {}
    routing = Phase6RouteEstimationService(
        db,
        configuration=configuration,
        model_id=run.model_id,
        metrics=routing_metrics,
    )
    trip_numbers = {vehicle_id: 0 for vehicle_id in availability}
    mode = parameters["assignment_mode"]
    allowed_delay = int(parameters["maximum_allowed_delay_minutes"])
    blocking = parameters.get("blocking_prediction_confidence")
    plan: list[dict] = []
    iteration_summary: list[dict] = []
    shipment_prediction_seconds = 0.0
    mt_prediction_seconds = 0.0
    assignment_seconds = 0.0

    for tier in CAPACITY_ITERATION_TIERS:
        if not remaining:
            iteration_summary.append(
                {
                    "tier_compartments": tier,
                    "tier_capacity_kl": tier * 8,
                    "loading_orders_considered": 0,
                    "candidate_shipments": 0,
                    "assigned_shipments": 0,
                    "assigned_loading_orders": 0,
                    "carried_forward_loading_orders": 0,
                }
            )
            continue

        considered_count = len(remaining)
        tier_parameters = {**parameters, "maximum_shipment_compartments": tier}
        started = perf_counter()
        partition = predict_shipments(list(remaining.values()), model, evidence, tier_parameters)
        shipment_prediction_seconds += perf_counter() - started
        tier_predictions = [
            prediction
            for prediction in partition
            if shipment_capacity(prediction["lines"])["required_compartments"] == tier
        ]
        for index, prediction in enumerate(tier_predictions, start=1):
            prediction["predicted_shipment_id"] = f"TIER-{tier}-{index:05d}"

        started = perf_counter()
        candidate_map = predict_mt_candidates(
            db,
            depot_id=run.depot_id,
            shipments=tier_predictions,
            availability=run.input_mt_availability_snapshot,
            vehicle_compatibility_mode=get_settings().vehicle_compatibility_mode,
            require_full_utilization=True,
        )
        mt_prediction_seconds += perf_counter() - started

        assigned_shipments = 0
        assigned_loading_orders = 0
        tier_predictions.sort(
            key=lambda prediction: (
                prediction["planned_start_datetime"],
                prediction["predicted_shipment_id"],
            )
        )
        for prediction in tier_predictions:
            # A previous candidate in the same tier may already have consumed one
            # of these rows only if a future grouping implementation overlaps its
            # partitions. Keep this guard so the planner remains safe.
            loading_order_nos = [line["loading_order_no"] for line in prediction["lines"]]
            if any(number not in remaining for number in loading_order_nos):
                continue

            started = perf_counter()
            candidates = candidate_map[prediction["predicted_shipment_id"]]
            compatible = [candidate for candidate in candidates if candidate["compatibility_status"] == "PASS"]
            compatible.sort(
                key=lambda candidate: (
                    -candidate["prediction_score"],
                    candidate["vehicle_registration_no"],
                )
            )
            planned = _utc(prediction["planned_start_datetime"])
            timing_eligible: list[tuple[dict, datetime, int]] = []
            for candidate in compatible:
                next_available = _utc(availability[candidate["vehicle_id"]])
                delay_minutes = max(0, round((next_available - planned).total_seconds() / 60))
                if next_available <= planned or (mode == "ALLOW_DELAY" and delay_minutes <= allowed_delay):
                    timing_eligible.append((candidate, next_available, delay_minutes))

            if blocking is not None and prediction["score"] < float(blocking):
                timing_eligible = []
                unassigned_reason = "LOW_PREDICTION_CONFIDENCE"
            elif not compatible:
                unassigned_reason = "NO_COMPATIBLE_MT"
            elif not timing_eligible:
                unassigned_reason = "NO_MT_AVAILABLE_AT_REQUIRED_TIME"
            else:
                unassigned_reason = None

            selected = None
            estimate = None
            routing_failure = None
            for candidate, next_available, delay_minutes in timing_eligible:
                departure = max(planned, next_available)
                try:
                    estimate = routing.estimate_trip(
                        depot=depot,
                        spbus=[spbus[line["spbu_id"]] for line in prediction["lines"]],
                        mt=mts[candidate["vehicle_id"]],
                        predicted_departure_datetime=departure,
                        max_exact_sequence_stops=int(parameters["max_exact_sequence_stops"]),
                    )
                    selected = (candidate, departure, delay_minutes)
                    break
                except GoogleRoutesError as exc:
                    routing_failure = exc.code
            if not selected and timing_eligible:
                unassigned_reason = routing_failure or "ROUTING_ESTIMATE_FAILED"

            if selected and estimate:
                candidate, departure, delay_minutes = selected
                status = "ASSIGNED_WITH_DELAY" if delay_minutes > 0 else "ASSIGNED"
                trip_numbers[candidate["vehicle_id"]] += 1
                availability[candidate["vehicle_id"]] = estimate["next_available_datetime"]
                for number in loading_order_nos:
                    remaining.pop(number, None)
                assigned_shipments += 1
                assigned_loading_orders += len(loading_order_nos)
                finalized = True
            else:
                candidate = None
                departure = None
                delay_minutes = 0
                status = "UNASSIGNED"
                # Failed larger groups are deliberately not persisted. Their LOs
                # remain in the pool and are regrouped at the next capacity tier.
                finalized = tier == 1
                if finalized:
                    for number in loading_order_nos:
                        remaining.pop(number, None)

            assignment_seconds += perf_counter() - started
            if not finalized:
                continue

            prediction["explanation"] = {
                **prediction["explanation"],
                "capacity_iteration": {
                    "tier_compartments": tier,
                    "tier_capacity_kl": tier * 8,
                    "iteration_order": list(CAPACITY_ITERATION_TIERS),
                    "only_successful_larger_groups_consume_loading_orders": True,
                    "mt_capacity_policy": "EXACT_CAPACITY_FULL_LOAD_REQUIRED",
                    "spbu_cluster_evidence_required_for_multi_lo": True,
                    "spbu_mt_master_tag_compatibility_required": True,
                },
            }
            plan.append(
                {
                    "prediction": prediction,
                    "candidates": candidates,
                    "selected_candidate": candidate,
                    "estimate": estimate,
                    "assignment_status": status,
                    "unassigned_reason": unassigned_reason,
                    "departure": departure,
                    "delay_minutes": delay_minutes,
                    "trip_number": trip_numbers[candidate["vehicle_id"]] if candidate else None,
                }
            )

        iteration_summary.append(
            {
                "tier_compartments": tier,
                "tier_capacity_kl": tier * 8,
                "loading_orders_considered": considered_count,
                "candidate_shipments": len(tier_predictions),
                "assigned_shipments": assigned_shipments,
                "assigned_loading_orders": assigned_loading_orders,
                "carried_forward_loading_orders": len(remaining),
            }
        )

    if remaining:
        raise RuntimeError(f"Iterative capacity planner left {len(remaining)} Loading Orders without a final shipment.")

    for sequence, item in enumerate(plan, start=1):
        item["prediction"]["predicted_shipment_id"] = f"PRED-SHIP-{sequence:04d}"

    return {
        "plan": plan,
        "iteration_summary": iteration_summary,
        "routing_metrics": routing_metrics,
        "durations_ms": {
            "shipment_prediction": round(shipment_prediction_seconds * 1000),
            "mt_prediction": round(mt_prediction_seconds * 1000),
            "assignment_optimization": round(assignment_seconds * 1000),
        },
    }
