from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from time import perf_counter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from .compatibility import evaluate_compatibility_entities
from .google_routes import get_google_routes_configuration
from .models import (
    ActualBayState,
    ActualVehicleEvent,
    BridgeMTTag,
    BridgeSPBUTag,
    FactSPBUMTPair,
    FactSPBUPair,
    LOOperationalState,
    LoadingBayProductCompatibility,
    MasterDepot,
    MasterLoadingBay,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    MasterTag,
    OperationalStateSnapshot,
    OptimizationBayAssignment,
    OptimizationBayOperation,
    OptimizationInitialQueue,
    OptimizationJob,
    OptimizationParameterProfile,
    OptimizationParameterSnapshot,
    OptimizationParameterValue,
    OptimizationRun,
    OptimizationVehicleCostRule,
    PredictionAssignment,
    PredictionRun,
    PredictionShipment,
    PredictionShipmentLine,
    ProductCompartmentLoadingDuration,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionStop,
    RouteVersionTrip,
    RouteVersionVehicleAssignment,
    VehicleOperationalState,
)
from .phase6_capacity import mt_compartment_profile
from .phase7_constants import (
    DEFAULT_PARAMETER_PROFILES,
    DEFAULT_PHASE7_PARAMETERS,
    JOB_STATUSES,
    LO_STATUSES,
    MT_STATUSES,
    effective_parameters,
)
from .phase7_matrix import RouteMatrixService
from .phase7_optimization import OptimizationCoordinatorService


logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(value: datetime | date | time | None) -> str | None:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    return value.isoformat() if value else None


def _timezone(depot: MasterDepot | None) -> ZoneInfo:
    try:
        return ZoneInfo(depot.timezone if depot and depot.timezone else "Asia/Jakarta")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Jakarta")


def _job_day(job: OptimizationJob, depot: MasterDepot) -> tuple[datetime, datetime, datetime]:
    zone = _timezone(depot)
    day_start = datetime.combine(job.operating_date, time.min, tzinfo=zone).astimezone(timezone.utc)
    operational_start = datetime.combine(job.operating_date, job.depot_operational_start, tzinfo=zone).astimezone(timezone.utc)
    operational_end = datetime.combine(job.operating_date, job.depot_operational_end, tzinfo=zone).astimezone(timezone.utc)
    if operational_end <= operational_start:
        operational_end += timedelta(days=1)
    return day_start, operational_start, operational_end


def _job_number(operating_date: date) -> str:
    return f"P7-JOB-{operating_date:%Y%m%d}-{uuid.uuid4().hex[:5].upper()}"


def _require_job(db: Session, job_id: str) -> OptimizationJob:
    job = db.get(OptimizationJob, job_id) or db.scalar(select(OptimizationJob).where(OptimizationJob.job_no == job_id))
    if not job:
        raise HTTPException(status_code=404, detail={"code": "PHASE7_JOB_NOT_FOUND", "message": "Phase 7 Job was not found."})
    return job


def _require_version(db: Session, job: OptimizationJob, version_id: str | None = None) -> RouteVersion:
    route_version = None
    if version_id:
        route_version = db.get(RouteVersion, version_id)
        if not route_version and str(version_id).upper().startswith("V"):
            try:
                version_number = int(str(version_id)[1:])
            except ValueError:
                version_number = -1
            route_version = db.scalar(select(RouteVersion).where(RouteVersion.job_id == job.job_id, RouteVersion.version_number == version_number))
    elif job.current_route_version_id:
        route_version = db.get(RouteVersion, job.current_route_version_id)
    if not route_version or route_version.job_id != job.job_id:
        raise HTTPException(status_code=404, detail={"code": "ROUTE_VERSION_NOT_FOUND", "message": "Route version was not found for this Job."})
    return route_version


def _date_from_prediction_row(row: dict, depot: MasterDepot | None) -> date | None:
    value = row.get("shipment_start_datetime_local") or row.get("shipment_start_datetime")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(_timezone(depot))
    return parsed.date()


def ensure_default_parameter_profiles(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(OptimizationParameterProfile)):
        return
    for index, (name, description, overrides) in enumerate(DEFAULT_PARAMETER_PROFILES):
        profile_id = uuid.uuid4().hex
        db.add(
            OptimizationParameterProfile(
                profile_id=profile_id,
                profile_name=name,
                description=description,
                version=1,
                is_default=index == 0,
                is_active=True,
                created_by="system",
            )
        )
        parameters = effective_parameters(overrides)
        db.add(
            OptimizationParameterValue(
                parameter_value_id=uuid.uuid4().hex,
                profile_id=profile_id,
                parameter_key="parameters",
                parameter_value=parameters,
            )
        )
        for rule in parameters["vehicle_activation_cost_rules"]:
            db.add(
                OptimizationVehicleCostRule(
                    cost_rule_id=uuid.uuid4().hex,
                    profile_id=profile_id,
                    vehicle_class=rule.get("vehicle_class"),
                    vehicle_tag=rule.get("vehicle_tag"),
                    activation_cost=float(rule.get("activation_cost") or 0),
                    priority=int(rule.get("priority") or 0),
                )
            )
    db.commit()


def _profile_payload(db: Session, profile: OptimizationParameterProfile) -> dict:
    row = db.scalar(select(OptimizationParameterValue).where(OptimizationParameterValue.profile_id == profile.profile_id, OptimizationParameterValue.parameter_key == "parameters"))
    rules = db.scalars(select(OptimizationVehicleCostRule).where(OptimizationVehicleCostRule.profile_id == profile.profile_id).order_by(desc(OptimizationVehicleCostRule.priority))).all()
    parameters = effective_parameters(row.parameter_value if row else {})
    parameters["vehicle_activation_cost_rules"] = [
        {"vehicle_class": rule.vehicle_class, "vehicle_tag": rule.vehicle_tag, "activation_cost": rule.activation_cost, "priority": rule.priority}
        for rule in rules
    ] or parameters["vehicle_activation_cost_rules"]
    return {
        "profile_id": profile.profile_id,
        "profile_name": profile.profile_name,
        "description": profile.description,
        "version": profile.version,
        "created_by": profile.created_by,
        "created_at": _iso(profile.created_at),
        "updated_at": _iso(profile.updated_at),
        "is_default": profile.is_default,
        "is_active": profile.is_active,
        "parameters": parameters,
    }


def list_parameter_profiles(db: Session, *, include_inactive: bool = False) -> list[dict]:
    ensure_default_parameter_profiles(db)
    statement = select(OptimizationParameterProfile)
    if not include_inactive:
        statement = statement.where(OptimizationParameterProfile.is_active.is_(True))
    profiles = db.scalars(statement.order_by(OptimizationParameterProfile.profile_name, desc(OptimizationParameterProfile.version))).all()
    return [_profile_payload(db, profile) for profile in profiles]


def get_parameter_profile(db: Session, profile_id: str) -> dict:
    ensure_default_parameter_profiles(db)
    profile = db.get(OptimizationParameterProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail={"code": "PARAMETER_PROFILE_NOT_FOUND", "message": "Optimization parameter profile was not found."})
    return _profile_payload(db, profile)


def save_parameter_profile(db: Session, payload: dict, *, actor: str = "local-user", source_profile_id: str | None = None, save_as: bool = False) -> dict:
    ensure_default_parameter_profiles(db)
    parameters = effective_parameters(payload.get("parameters") or {})
    profile_name = str(payload.get("profile_name") or "").strip()
    description = str(payload.get("description") or "").strip() or None
    if source_profile_id and not save_as:
        source = db.get(OptimizationParameterProfile, source_profile_id)
        if not source:
            raise HTTPException(status_code=404, detail={"code": "PARAMETER_PROFILE_NOT_FOUND", "message": "Source profile was not found."})
        profile_name = profile_name or source.profile_name
        description = description or source.description
        version = max(db.scalars(select(OptimizationParameterProfile.version).where(OptimizationParameterProfile.profile_name == profile_name)).all() or [0]) + 1
        source.is_active = False
    else:
        if not profile_name:
            raise HTTPException(status_code=400, detail={"code": "PROFILE_NAME_REQUIRED", "message": "profile_name is required."})
        version = 1
        existing_versions = db.scalars(select(OptimizationParameterProfile.version).where(OptimizationParameterProfile.profile_name == profile_name)).all()
        if existing_versions:
            version = max(existing_versions) + 1
    profile = OptimizationParameterProfile(
        profile_id=uuid.uuid4().hex,
        profile_name=profile_name,
        description=description,
        version=version,
        created_by=actor,
        is_default=bool(payload.get("is_default", False)),
        is_active=True,
    )
    if profile.is_default:
        for old in db.scalars(select(OptimizationParameterProfile).where(OptimizationParameterProfile.is_default.is_(True))).all():
            old.is_default = False
    db.add(profile)
    db.flush()
    db.add(OptimizationParameterValue(parameter_value_id=uuid.uuid4().hex, profile_id=profile.profile_id, parameter_key="parameters", parameter_value=parameters))
    for rule in parameters.get("vehicle_activation_cost_rules") or []:
        db.add(
            OptimizationVehicleCostRule(
                cost_rule_id=uuid.uuid4().hex,
                profile_id=profile.profile_id,
                vehicle_class=rule.get("vehicle_class"),
                vehicle_tag=rule.get("vehicle_tag"),
                activation_cost=float(rule.get("activation_cost") or 0),
                priority=int(rule.get("priority") or 0),
            )
        )
    db.commit()
    return _profile_payload(db, profile)


def create_job(db: Session, payload: dict, *, actor: str = "local-user") -> dict:
    depot = db.get(MasterDepot, payload.get("depot_id"))
    if not depot:
        raise HTTPException(status_code=404, detail={"code": "DEPOT_NOT_FOUND", "message": "Selected depot was not found."})
    try:
        operating_date = date.fromisoformat(str(payload.get("operating_date")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_OPERATING_DATE", "message": "operating_date must be YYYY-MM-DD."}) from exc
    name = str(payload.get("job_name") or "").strip() or f"Optimization {depot.depot_name} {operating_date.isoformat()}"
    job = OptimizationJob(
        job_id=uuid.uuid4().hex,
        job_no=_job_number(operating_date),
        job_name=name,
        depot_id=depot.depot_id,
        operating_date=operating_date,
        status="DRAFT",
        created_by=actor,
    )
    db.add(job)
    db.commit()
    return get_job(db, job.job_id)


def list_jobs(db: Session, depot_id: str) -> list[dict]:
    if not depot_id:
        raise HTTPException(status_code=400, detail={"code": "DEPOT_REQUIRED", "message": "Select a depot before listing Phase 7 Jobs."})
    jobs = db.scalars(select(OptimizationJob).where(OptimizationJob.depot_id == depot_id).order_by(desc(OptimizationJob.updated_at))).all()
    return [_job_summary(db, job) for job in jobs]


def _job_summary(db: Session, job: OptimizationJob) -> dict:
    depot = db.get(MasterDepot, job.depot_id)
    lo_rows = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id)).all()
    mt_count = db.scalar(select(func.count()).select_from(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id)) or 0
    version = db.get(RouteVersion, job.current_route_version_id) if job.current_route_version_id else None
    return {
        "job_id": job.job_id,
        "job_no": job.job_no,
        "job_name": job.job_name,
        "operating_date": job.operating_date.isoformat(),
        "depot_id": job.depot_id,
        "depot": depot.depot_name if depot else job.depot_id,
        "total_lo": len(lo_rows),
        "total_mt": int(mt_count),
        "current_route_version_id": job.current_route_version_id,
        "current_route_version": version.version_label if version else None,
        "status": job.status,
        "last_updated": _iso(job.updated_at),
        "source_prediction_run_id": job.source_prediction_run_id,
    }


def _job_kpis(db: Session, job: OptimizationJob) -> dict:
    rows = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id)).all()
    statuses = Counter(row.status for row in rows)
    version = db.get(RouteVersion, job.current_route_version_id) if job.current_route_version_id else None
    summary = version.summary_snapshot if version else {}
    total_volume = sum(float(row.volume_kl or 0) for row in rows)
    delivered = sum(float(row.volume_kl or 0) for row in rows if row.status == "DONE")
    dropped = int(summary.get("dropped_lo") or 0)
    return {
        "total_lo": len(rows),
        "done_lo": statuses["DONE"],
        "ongoing_lo": statuses["ONGOING"],
        "planned_lo": statuses["PLANNED"],
        "dropped_lo": dropped,
        "used_mt": int(summary.get("used_mt") or 0),
        "total_trips": int(summary.get("total_trips") or 0),
        "delivered_kl": round(delivered, 3),
        "remaining_kl": round(max(0, total_volume - delivered), 3),
        "completion_pct": round(statuses["DONE"] / len(rows) * 100, 2) if rows else 0,
    }


def get_job(db: Session, job_id: str) -> dict:
    job = _require_job(db, job_id)
    depot = db.get(MasterDepot, job.depot_id)
    current = db.get(RouteVersion, job.current_route_version_id) if job.current_route_version_id else None
    return {
        **_job_summary(db, job),
        "header": {
            "job_id": job.job_no,
            "depot": depot.depot_name if depot else job.depot_id,
            "operating_date": job.operating_date.isoformat(),
            "source_phase6_run_id": _prediction_run_no(db, job.source_prediction_run_id),
            "current_route_version": current.version_label if current else None,
            "job_status": job.status,
        },
        "kpis": _job_kpis(db, job),
        "depot_operational_start": _iso(job.depot_operational_start),
        "depot_operational_end": _iso(job.depot_operational_end),
        "error_message": job.error_message,
    }


def _prediction_run_no(db: Session, run_id: str | None) -> str | None:
    run = db.get(PredictionRun, run_id) if run_id else None
    return run.prediction_run_no if run else None


def list_prediction_runs_for_job(db: Session, job_id: str) -> list[dict]:
    job = _require_job(db, job_id)
    depot = db.get(MasterDepot, job.depot_id)
    runs = db.scalars(
        select(PredictionRun).where(PredictionRun.depot_id == job.depot_id, PredictionRun.status == "COMPLETED").order_by(desc(PredictionRun.completed_at))
    ).all()
    result = []
    for run in runs:
        dates = {_date_from_prediction_row(row, depot) for row in run.input_loading_order_snapshot or []}
        dates.discard(None)
        if dates and job.operating_date not in dates:
            continue
        shipment_count = db.scalar(select(func.count()).select_from(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id)) or 0
        assignments = db.scalars(
            select(PredictionAssignment).join(PredictionShipment, PredictionShipment.id == PredictionAssignment.prediction_shipment_id).where(PredictionShipment.prediction_run_id == run.id)
        ).all()
        result.append(
            {
                "id": run.id,
                "run_id": run.prediction_run_no,
                "date": job.operating_date.isoformat(),
                "depot_id": run.depot_id,
                "depot": depot.depot_name if depot else run.depot_id,
                "total_lo": len(run.input_loading_order_snapshot or []),
                "predicted_shipment_count": int(shipment_count),
                "predicted_mt_count": len({row.final_vehicle_id for row in assignments if row.final_vehicle_id}),
                "model_id": run.model_id,
                "model_name": (run.model_snapshot or {}).get("model_name") or run.model_id,
                "saved_at": _iso(run.completed_at or run.created_at),
                "status": run.status,
            }
        )
    return result


def load_prediction_run(db: Session, job_id: str, run_id: str, *, actor: str = "local-user") -> dict:
    job = _require_job(db, job_id)
    run = db.get(PredictionRun, run_id) or db.scalar(select(PredictionRun).where(PredictionRun.prediction_run_no == run_id))
    if not run or run.status != "COMPLETED":
        raise HTTPException(status_code=422, detail={"code": "INVALID_PHASE6_RUN", "message": "Select a completed/saved Phase 6 Prediction Run."})
    if run.depot_id != job.depot_id:
        raise HTTPException(status_code=422, detail={"code": "PHASE6_DEPOT_MISMATCH", "message": "Prediction Run depot does not match the Phase 7 Job depot."})
    depot = db.get(MasterDepot, job.depot_id)
    dates = {_date_from_prediction_row(row, depot) for row in run.input_loading_order_snapshot or []}
    dates.discard(None)
    if dates and job.operating_date not in dates:
        raise HTTPException(status_code=422, detail={"code": "PHASE6_DATE_MISMATCH", "message": "Prediction Run operating date does not match the Phase 7 Job."})
    existing = db.scalar(select(func.count()).select_from(LOOperationalState).where(LOOperationalState.job_id == job.job_id)) or 0
    if existing:
        if job.source_prediction_run_id == run.id:
            return get_job(db, job.job_id)
        raise HTTPException(status_code=409, detail={"code": "PHASE6_ALREADY_LOADED", "message": "This Job already has an immutable Phase 6 source. Create another Job to use a different run."})
    shipments = db.scalars(select(PredictionShipment).where(PredictionShipment.prediction_run_id == run.id)).all()
    shipment_ids = [row.id for row in shipments]
    lines = db.scalars(select(PredictionShipmentLine).where(PredictionShipmentLine.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    assignments = db.scalars(select(PredictionAssignment).where(PredictionAssignment.prediction_shipment_id.in_(shipment_ids))).all() if shipment_ids else []
    shipment_by_id = {row.id: row for row in shipments}
    assignment_by_shipment = {row.prediction_shipment_id: row for row in assignments}
    lines_by_shipment: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
    for line in lines:
        lines_by_shipment[line.prediction_shipment_id].append(line)
    spbu_ids = {line.spbu_id for line in lines}
    spbus = {row.spbu_id: row for row in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()} if spbu_ids else {}
    for line in lines:
        shipment = shipment_by_id[line.prediction_shipment_id]
        assignment = assignment_by_shipment.get(shipment.id)
        pairing = sorted({row.spbu_id for row in lines_by_shipment[shipment.id]})
        db.add(
            LOOperationalState(
                lo_state_id=uuid.uuid4().hex,
                job_id=job.job_id,
                loading_order_id=line.loading_order_no,
                spbu_id=line.spbu_id,
                spbu_name_snapshot=spbus[line.spbu_id].spbu_name if line.spbu_id in spbus else line.spbu_no,
                product_id=line.product_id,
                product_name_snapshot=line.product_name,
                volume_kl=float(line.order_quantity_kl or 0),
                depot_id=job.depot_id,
                operating_date=job.operating_date,
                source_prediction_run_id=run.id,
                phase6_predicted_shipment_id=shipment.predicted_shipment_id,
                phase6_predicted_vehicle_id=assignment.final_vehicle_id if assignment else None,
                phase6_predicted_spbu_pairing=pairing,
                phase6_shipment_confidence=shipment.shipment_prediction_score,
                phase6_vehicle_assignment_confidence=assignment.final_assignment_score if assignment else None,
                phase6_model_id=run.model_id,
                status="PLANNED",
                frozen=False,
                updated_by=actor,
            )
        )
    job.source_prediction_run_id = run.id
    job.status = "DRAFT"
    db.commit()
    return get_job(db, job.job_id)


def list_job_los(db: Session, job_id: str) -> list[dict]:
    job = _require_job(db, job_id)
    rows = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id).order_by(LOOperationalState.loading_order_id)).all()
    mt_ids = {row.phase6_predicted_vehicle_id for row in rows if row.phase6_predicted_vehicle_id} | {row.current_vehicle_id for row in rows if row.current_vehicle_id}
    mts = {row.mt_id: row for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(mt_ids))).all()} if mt_ids else {}
    return [
        {
            "lo_state_id": row.lo_state_id,
            "loading_order_id": row.loading_order_id,
            "spbu_id": row.spbu_id,
            "spbu_name": row.spbu_name_snapshot,
            "product_id": row.product_id,
            "product_name": row.product_name_snapshot,
            "volume_kl": row.volume_kl,
            "phase6_shipment": row.phase6_predicted_shipment_id,
            "phase6_mt_id": row.phase6_predicted_vehicle_id,
            "phase6_mt": mts[row.phase6_predicted_vehicle_id].vehicle_registration if row.phase6_predicted_vehicle_id in mts else row.phase6_predicted_vehicle_id,
            "phase6_pairing": row.phase6_predicted_spbu_pairing,
            "shipment_confidence": row.phase6_shipment_confidence,
            "vehicle_assignment_confidence": row.phase6_vehicle_assignment_confidence,
            "current_mt_id": row.current_vehicle_id,
            "current_mt": mts[row.current_vehicle_id].vehicle_registration if row.current_vehicle_id in mts else row.current_vehicle_id,
            "current_trip": row.current_trip_number,
            "current_shipment": row.current_shipment_id,
            "current_compartment": row.current_compartment_id,
            "planned_gate_out": _iso(row.planned_gate_out),
            "status": row.status,
            "frozen": row.frozen,
            "frozen_reason": row.frozen_reason,
        }
        for row in rows
    ]


def update_lo_statuses(db: Session, job_id: str, updates: list[dict], *, actor: str = "local-user") -> dict:
    job = _require_job(db, job_id)
    rows = {row.loading_order_id: row for row in db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id)).all()}
    changed = []
    for update in updates:
        row = rows.get(str(update.get("loading_order_id")))
        if not row:
            raise HTTPException(status_code=404, detail={"code": "LO_NOT_FOUND", "message": f"LO {update.get('loading_order_id')} was not found in this Job."})
        status = str(update.get("status") or "").upper()
        if status not in LO_STATUSES:
            raise HTTPException(status_code=400, detail={"code": "INVALID_LO_STATUS", "message": f"Unsupported LO status: {status}."})
        before = row.status
        row.status = status
        row.updated_by = actor
        if status == "ONGOING":
            row.frozen = True
            row.frozen_reason = "ONGOING"
            if update.get("actual_gate_out"):
                row.actual_gate_out = datetime.fromisoformat(str(update["actual_gate_out"]).replace("Z", "+00:00"))
        elif status == "DONE":
            row.frozen = True
            row.frozen_reason = "DONE"
            if update.get("actual_delivered_at"):
                row.actual_delivered_at = datetime.fromisoformat(str(update["actual_delivered_at"]).replace("Z", "+00:00"))
        else:
            row.frozen = False
            row.frozen_reason = None
        changed.append({"loading_order_id": row.loading_order_id, "before": before, "after": status})
    db.commit()
    return {"status": "APPLIED", "changed": changed, "job": get_job(db, job.job_id)}


def load_mt_from_master(db: Session, job_id: str, *, actor: str = "local-user") -> dict:
    job = _require_job(db, job_id)
    mts = db.scalars(select(MasterMT).where(MasterMT.depot_id == job.depot_id, MasterMT.active_status == "ACTIVE").order_by(MasterMT.vehicle_registration)).all()
    existing = {row.mt_id: row for row in db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id)).all()}
    mt_ids = [row.mt_id for row in mts]
    tags_by_mt: dict[str, list[str]] = defaultdict(list)
    if mt_ids:
        for mt_id, tag in db.execute(select(BridgeMTTag.mt_id, MasterTag.tag_value).join(MasterTag, MasterTag.tag_id == BridgeMTTag.tag_id).where(BridgeMTTag.mt_id.in_(mt_ids))).all():
            tags_by_mt[mt_id].append(tag)
    added = 0
    for mt in mts:
        if mt.mt_id in existing:
            continue
        profile = mt_compartment_profile(mt)
        compartment_count = int(profile["effective_compartments"] or 0)
        capacity = float(profile["capacity_kl"] or 0)
        compartment_capacity = capacity / compartment_count if compartment_count else 0
        compartments = [{"compartment_id": f"C{index}", "capacity_kl": compartment_capacity} for index in range(1, compartment_count + 1)]
        db.add(
            VehicleOperationalState(
                vehicle_state_id=uuid.uuid4().hex,
                job_id=job.job_id,
                mt_id=mt.mt_id,
                registration_snapshot=mt.vehicle_registration,
                vehicle_class=mt.vehicle_type_tag,
                tag_snapshot=sorted(tags_by_mt[mt.mt_id]),
                capacity_kl=capacity,
                number_of_compartments=compartment_count,
                compartment_configuration=compartments,
                operational_status="READY",
                working_time_limit_minutes=720,
                working_time_remaining_minutes=720,
                updated_by=actor,
            )
        )
        added += 1
    db.commit()
    return {"loaded": added, "total": len(mts), "vehicles": list_job_vehicles(db, job.job_id)}


def list_job_vehicles(db: Session, job_id: str) -> list[dict]:
    job = _require_job(db, job_id)
    rows = db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id).order_by(VehicleOperationalState.registration_snapshot)).all()
    return [
        {
            "vehicle_state_id": row.vehicle_state_id,
            "mt_id": row.mt_id,
            "registration": row.registration_snapshot,
            "vehicle_class": row.vehicle_class,
            "tags": row.tag_snapshot,
            "capacity_kl": row.capacity_kl,
            "number_of_compartments": row.number_of_compartments,
            "compartments": row.compartment_configuration,
            "planned_eta_depot": _iso(row.planned_eta_depot),
            "system_eta_depot": _iso(row.system_eta_depot),
            "user_eta_override": _iso(row.user_eta_override),
            "effective_eta_depot": _iso(row.effective_eta_depot),
            "operational_status": row.operational_status,
            "working_time_used": row.working_time_used_minutes,
            "working_time_remaining": row.working_time_remaining_minutes,
            "working_time_limit": row.working_time_limit_minutes,
        }
        for row in rows
    ]


def update_vehicle_states(db: Session, job_id: str, updates: list[dict], *, actor: str = "local-user") -> dict:
    job = _require_job(db, job_id)
    rows = {row.mt_id: row for row in db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id)).all()}
    changed = []
    for update in updates:
        row = rows.get(str(update.get("mt_id")))
        if not row:
            raise HTTPException(status_code=404, detail={"code": "MT_NOT_LOADED", "message": f"MT {update.get('mt_id')} is not loaded in this Job."})
        before = {"effective_eta_depot": _iso(row.effective_eta_depot), "operational_status": row.operational_status}
        if "planned_eta_depot" in update:
            row.planned_eta_depot = datetime.fromisoformat(str(update["planned_eta_depot"]).replace("Z", "+00:00")) if update["planned_eta_depot"] else None
        if "user_eta_override" in update:
            row.user_eta_override = datetime.fromisoformat(str(update["user_eta_override"]).replace("Z", "+00:00")) if update["user_eta_override"] else None
        if "operational_status" in update:
            status = str(update["operational_status"]).upper()
            if status not in MT_STATUSES:
                raise HTTPException(status_code=400, detail={"code": "INVALID_MT_STATUS", "message": f"Unsupported MT status: {status}."})
            row.operational_status = status
        if "working_time_limit_minutes" in update:
            row.working_time_limit_minutes = max(1, int(update["working_time_limit_minutes"]))
            row.working_time_remaining_minutes = max(0, row.working_time_limit_minutes - row.working_time_used_minutes)
        # Actual/user override outranks previous prediction; planned ETA is the
        # required initial Trip-1 input before a system ETA exists.
        row.effective_eta_depot = row.user_eta_override or row.system_eta_depot or row.planned_eta_depot
        row.updated_by = actor
        after = {"effective_eta_depot": _iso(row.effective_eta_depot), "operational_status": row.operational_status}
        if before != after:
            changed.append({"mt_id": row.mt_id, "before": before, "after": after})
            if row.effective_eta_depot:
                db.add(ActualVehicleEvent(vehicle_event_id=uuid.uuid4().hex, job_id=job.job_id, mt_id=row.mt_id, event_type="ETA_DEPOT_UPDATE", event_time=row.effective_eta_depot, source="USER", details={"before": before, "after": after, "updated_by": actor}))
    db.commit()
    return {"status": "APPLIED", "changed": changed, "vehicles": list_job_vehicles(db, job.job_id)}


def upsert_bay_configuration(db: Session, depot_id: str, bays: list[dict], durations: list[dict], *, actor: str = "local-user") -> dict:
    if not db.get(MasterDepot, depot_id):
        raise HTTPException(status_code=404, detail={"code": "DEPOT_NOT_FOUND", "message": "Depot was not found."})
    products = {row.product_id: row for row in db.scalars(select(MasterProduct)).all()}
    for payload in bays:
        bay_id = str(payload.get("bay_id") or "").strip()
        if not bay_id:
            raise HTTPException(status_code=400, detail={"code": "BAY_ID_REQUIRED", "message": "bay_id is required."})
        bay = db.scalar(select(MasterLoadingBay).where(MasterLoadingBay.depot_id == depot_id, MasterLoadingBay.bay_id == bay_id))
        if not bay:
            bay = MasterLoadingBay(master_bay_id=uuid.uuid4().hex, depot_id=depot_id, bay_id=bay_id, bay_name=str(payload.get("bay_name") or bay_id))
            db.add(bay)
            db.flush()
        bay.bay_name = str(payload.get("bay_name") or bay_id)
        bay.all_products_allowed = bool(payload.get("all_products_allowed", False))
        bay.operational_start = time.fromisoformat(str(payload.get("operational_start") or "05:00"))
        bay.operational_end = time.fromisoformat(str(payload.get("operational_end") or "22:00"))
        bay.number_of_loading_arms = max(1, int(payload.get("number_of_loading_arms") or 1))
        bay.loading_mode = str(payload.get("loading_mode") or "SEQUENTIAL").upper()
        bay.active_status = str(payload.get("active_status") or "ACTIVE").upper()
        db.execute(delete(LoadingBayProductCompatibility).where(LoadingBayProductCompatibility.master_bay_id == bay.master_bay_id))
        if not bay.all_products_allowed:
            for product_id in payload.get("allowed_products") or []:
                if product_id not in products:
                    raise HTTPException(status_code=422, detail={"code": "PRODUCT_NOT_FOUND", "message": f"Product {product_id} was not found."})
                db.add(LoadingBayProductCompatibility(master_bay_id=bay.master_bay_id, product_id=product_id))
    for payload in durations:
        product_id = str(payload.get("product_id") or "")
        if product_id not in products:
            raise HTTPException(status_code=422, detail={"code": "PRODUCT_NOT_FOUND", "message": f"Product {product_id} was not found."})
        row = db.scalar(select(ProductCompartmentLoadingDuration).where(ProductCompartmentLoadingDuration.depot_id == depot_id, ProductCompartmentLoadingDuration.product_id == product_id))
        if not row:
            row = ProductCompartmentLoadingDuration(loading_duration_id=uuid.uuid4().hex, depot_id=depot_id, product_id=product_id)
            db.add(row)
        row.duration_minutes_per_compartment = max(1, int(payload.get("duration_minutes_per_compartment") or 0))
        row.active_status = "ACTIVE"
    db.commit()
    return get_bay_configuration(db, depot_id)


def get_bay_configuration(db: Session, depot_id: str) -> dict:
    bays = db.scalars(select(MasterLoadingBay).where(MasterLoadingBay.depot_id == depot_id).order_by(MasterLoadingBay.bay_id)).all()
    bay_ids = [row.master_bay_id for row in bays]
    compat: dict[str, list[str]] = defaultdict(list)
    if bay_ids:
        for master_bay_id, product_id in db.execute(select(LoadingBayProductCompatibility.master_bay_id, LoadingBayProductCompatibility.product_id).where(LoadingBayProductCompatibility.master_bay_id.in_(bay_ids))).all():
            compat[master_bay_id].append(product_id)
    durations = db.scalars(select(ProductCompartmentLoadingDuration).where(ProductCompartmentLoadingDuration.depot_id == depot_id).order_by(ProductCompartmentLoadingDuration.product_id)).all()
    products = {row.product_id: row.product_name for row in db.scalars(select(MasterProduct)).all()}
    return {
        "depot_id": depot_id,
        "number_of_bays": len([row for row in bays if row.active_status == "ACTIVE"]),
        "bays": [
            {
                "master_bay_id": row.master_bay_id,
                "bay_id": row.bay_id,
                "bay_name": row.bay_name,
                "all_products_allowed": row.all_products_allowed,
                "allowed_products": compat[row.master_bay_id],
                "allowed_product_names": [products.get(product_id, product_id) for product_id in compat[row.master_bay_id]],
                "operational_start": _iso(row.operational_start),
                "operational_end": _iso(row.operational_end),
                "number_of_loading_arms": row.number_of_loading_arms,
                "loading_mode": row.loading_mode,
                "active_status": row.active_status,
            }
            for row in bays
        ],
        "loading_durations": [
            {"loading_duration_id": row.loading_duration_id, "product_id": row.product_id, "product_name": products.get(row.product_id), "duration_minutes_per_compartment": row.duration_minutes_per_compartment, "active_status": row.active_status}
            for row in durations
        ],
    }


def update_actual_bay_state(db: Session, job_id: str, states: list[dict], queue: list[dict], *, actor: str = "local-user") -> dict:
    job = _require_job(db, job_id)
    allowed_bays = {row.master_bay_id: row for row in db.scalars(select(MasterLoadingBay).where(MasterLoadingBay.depot_id == job.depot_id)).all()}
    for payload in states:
        bay_id = str(payload.get("master_bay_id") or "")
        if bay_id not in allowed_bays:
            raise HTTPException(status_code=422, detail={"code": "BAY_DEPOT_MISMATCH", "message": "Bay does not belong to the Job depot."})
        state = db.scalar(select(ActualBayState).where(ActualBayState.job_id == job.job_id, ActualBayState.master_bay_id == bay_id))
        if not state:
            state = ActualBayState(actual_bay_state_id=uuid.uuid4().hex, job_id=job.job_id, master_bay_id=bay_id, state_effective_at=datetime.now(timezone.utc))
            db.add(state)
        state.current_vehicle_id = payload.get("current_vehicle_id") or None
        state.current_compartment_id = payload.get("current_compartment_id") or None
        state.current_product_id = payload.get("current_product_id") or None
        state.remaining_loading_minutes = max(0, int(payload.get("remaining_loading_minutes") or 0))
        state.actual_queue_length = max(0, int(payload.get("actual_queue_length") or 0))
        state.state_effective_at = datetime.fromisoformat(str(payload["state_effective_at"]).replace("Z", "+00:00")) if payload.get("state_effective_at") else datetime.now(timezone.utc)
        state.source = "USER"
        state.updated_by = actor
    # Queue replacement is an explicit actual-state update scoped to this Job.
    db.execute(delete(OptimizationInitialQueue).where(OptimizationInitialQueue.job_id == job.job_id))
    positions: set[tuple[str, int]] = set()
    for payload in queue:
        bay_id = str(payload.get("master_bay_id") or "")
        position = int(payload.get("queue_position") or 0)
        if bay_id not in allowed_bays or position < 1 or (bay_id, position) in positions:
            raise HTTPException(status_code=422, detail={"code": "INVALID_BAY_QUEUE", "message": "Queue bay/position is invalid or duplicated."})
        positions.add((bay_id, position))
        db.add(
            OptimizationInitialQueue(
                initial_queue_id=uuid.uuid4().hex,
                job_id=job.job_id,
                master_bay_id=bay_id,
                queue_position=position,
                vehicle_id=str(payload["vehicle_id"]),
                compartment_id=payload.get("compartment_id") or None,
                product_id=payload.get("product_id") or None,
                estimated_loading_duration_minutes=max(1, int(payload.get("estimated_loading_duration_minutes") or 1)),
                state_effective_at=datetime.fromisoformat(str(payload["state_effective_at"]).replace("Z", "+00:00")) if payload.get("state_effective_at") else datetime.now(timezone.utc),
            )
        )
    db.commit()
    return get_actual_bay_state(db, job.job_id)


def get_actual_bay_state(db: Session, job_id: str) -> dict:
    job = _require_job(db, job_id)
    config = get_bay_configuration(db, job.depot_id)
    states = db.scalars(select(ActualBayState).where(ActualBayState.job_id == job.job_id)).all()
    queues = db.scalars(select(OptimizationInitialQueue).where(OptimizationInitialQueue.job_id == job.job_id).order_by(OptimizationInitialQueue.master_bay_id, OptimizationInitialQueue.queue_position)).all()
    return {
        "configuration": config,
        "states": [
            {"actual_bay_state_id": row.actual_bay_state_id, "master_bay_id": row.master_bay_id, "current_vehicle_id": row.current_vehicle_id, "current_compartment_id": row.current_compartment_id, "current_product_id": row.current_product_id, "remaining_loading_minutes": row.remaining_loading_minutes, "actual_queue_length": row.actual_queue_length, "state_effective_at": _iso(row.state_effective_at), "source": row.source}
            for row in states
        ],
        "queue": [
            {"initial_queue_id": row.initial_queue_id, "master_bay_id": row.master_bay_id, "queue_position": row.queue_position, "vehicle_id": row.vehicle_id, "compartment_id": row.compartment_id, "product_id": row.product_id, "estimated_loading_duration_minutes": row.estimated_loading_duration_minutes, "state_effective_at": _iso(row.state_effective_at)}
            for row in queues
        ],
    }


def apply_freeze_rules(rows: list[LOOperationalState], *, current_time: datetime, freeze_window_minutes: int) -> list[LOOperationalState]:
    horizon = _utc(current_time) + timedelta(minutes=max(0, freeze_window_minutes))
    frozen_groups: set[tuple[str, int]] = set()
    for row in rows:
        if row.status == "DONE":
            row.frozen, row.frozen_reason = True, "DONE"
        elif row.status == "ONGOING":
            row.frozen, row.frozen_reason = True, "ONGOING"
        elif row.status == "PLANNED" and row.planned_gate_out and _utc(row.planned_gate_out) <= horizon:
            row.frozen, row.frozen_reason = True, "FREEZE_WINDOW"
        else:
            row.frozen, row.frozen_reason = False, None
        if row.frozen and row.current_vehicle_id and row.current_trip_number is not None:
            frozen_groups.add((row.current_vehicle_id, row.current_trip_number))
    # A physical trip is an indivisible execution unit; when one LO in it is
    # frozen, retain every LO in the same current trip to avoid changing its load.
    for row in rows:
        if (row.current_vehicle_id, row.current_trip_number) in frozen_groups and not row.frozen:
            row.frozen, row.frozen_reason = True, "FROZEN_TRIP_DEPENDENCY"
    return rows


def validate_job(db: Session, job_id: str) -> dict:
    job = _require_job(db, job_id)
    ensure_default_parameter_profiles(db)
    messages: list[dict] = []
    def add(code: str, level: str, message: str) -> None:
        messages.append({"code": code, "level": level, "message": message})
    lo_rows = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id)).all()
    vehicles = db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id)).all()
    bays = db.scalars(select(MasterLoadingBay).where(MasterLoadingBay.depot_id == job.depot_id, MasterLoadingBay.active_status == "ACTIVE")).all()
    durations = {row.product_id for row in db.scalars(select(ProductCompartmentLoadingDuration).where(ProductCompartmentLoadingDuration.depot_id == job.depot_id, ProductCompartmentLoadingDuration.active_status == "ACTIVE")).all()}
    if not job.source_prediction_run_id:
        add("PREDICTION_RUN_REQUIRED", "BLOCKED", "Load a completed Phase 6 Prediction Run by Run ID.")
    if not lo_rows:
        add("LO_REQUIRED", "BLOCKED", "No Phase 7 LO has been imported.")
    if not vehicles:
        add("MT_REQUIRED", "BLOCKED", "Load MT from the selected depot master data.")
    missing_eta = [row.registration_snapshot or row.mt_id for row in vehicles if row.operational_status != "UNAVAILABLE" and not (row.user_eta_override or row.system_eta_depot or row.planned_eta_depot)]
    if missing_eta:
        add("MT_ETA_REQUIRED", "BLOCKED", f"Planned ETA Depot is missing for {len(missing_eta)} available MT.")
    if any(not row.spbu_id or not db.get(MasterSPBU, row.spbu_id) for row in lo_rows):
        add("SPBU_MAPPING_REQUIRED", "BLOCKED", "One or more LO does not map to canonical SPBU master data.")
    if any(not row.product_id or not db.get(MasterProduct, row.product_id) for row in lo_rows):
        add("PRODUCT_MAPPING_REQUIRED", "BLOCKED", "One or more LO does not map to canonical product master data.")
    if any(row.vehicle_class is None for row in vehicles):
        add("VEHICLE_CLASS_REQUIRED", "BLOCKED", "Vehicle class is missing on one or more loaded MT.")
    if lo_rows and vehicles:
        spbu_ids = sorted({row.spbu_id for row in lo_rows if row.spbu_id})
        mt_ids = [row.mt_id for row in vehicles]
        mt_tags, spbu_tags, tag_labels = _tags(db, mt_ids, spbu_ids)
        master_mt = {mt_id: db.get(MasterMT, mt_id) for mt_id in mt_ids}
        master_spbu = {spbu_id: db.get(MasterSPBU, spbu_id) for spbu_id in spbu_ids}
        incompatible_los = []
        for lo in lo_rows:
            spbu = master_spbu.get(lo.spbu_id)
            if not spbu:
                continue
            compatible = any(
                vehicle.operational_status != "UNAVAILABLE"
                and master_mt.get(vehicle.mt_id) is not None
                and evaluate_compatibility_entities(
                    master_mt[vehicle.mt_id],
                    spbu,
                    mt_tag_ids=mt_tags[vehicle.mt_id],
                    spbu_tag_ids=spbu_tags[lo.spbu_id],
                    tag_labels=tag_labels,
                    product_id=lo.product_id,
                    vehicle_mode="MT_CAPACITY_LE_SPBU_LIMIT",
                )["compatible"]
                for vehicle in vehicles
            )
            if not compatible:
                incompatible_los.append(lo.loading_order_id)
        if incompatible_los:
            add("NO_COMPATIBLE_MT", "BLOCKED", f"No loaded active MT passes canonical vehicle-class/tag compatibility for {len(incompatible_los)} LO.")
    if any(not row.compartment_configuration or sum(float(item.get("capacity_kl") or 0) for item in row.compartment_configuration) + 1e-9 < row.capacity_kl for row in vehicles):
        add("COMPARTMENT_CONFIGURATION_INVALID", "BLOCKED", "MT compartment configuration is missing or does not cover total capacity.")
    if not bays:
        add("BAY_CONFIGURATION_REQUIRED", "BLOCKED", "Configure at least one active loading bay for the depot.")
    if bays and any(not bay.all_products_allowed and not db.scalar(select(func.count()).select_from(LoadingBayProductCompatibility).where(LoadingBayProductCompatibility.master_bay_id == bay.master_bay_id)) for bay in bays):
        add("BAY_PRODUCT_COMPATIBILITY_REQUIRED", "BLOCKED", "Every restricted bay needs at least one allowed product.")
    required_products = {row.product_id for row in lo_rows if row.product_id}
    missing_durations = required_products - durations
    if missing_durations:
        add("LOADING_DURATION_REQUIRED", "BLOCKED", f"Per-compartment loading duration is missing for {len(missing_durations)} product(s).")
    active_profiles = db.scalars(select(OptimizationParameterProfile).where(OptimizationParameterProfile.is_active.is_(True))).all()
    if not active_profiles:
        add("PARAMETER_PROFILE_REQUIRED", "BLOCKED", "At least one valid active optimization parameter profile is required.")
    else:
        try:
            for profile in active_profiles:
                _profile_payload(db, profile)
        except (TypeError, ValueError) as exc:
            add("PARAMETER_PROFILE_INVALID", "BLOCKED", f"An active parameter profile is invalid: {exc}")
    routes_config = get_google_routes_configuration(db)
    if not routes_config or not routes_config.encrypted_api_key:
        add("GOOGLE_ROUTES_NOT_CONFIGURED", "WARNING", "Google Routes API is not configured; cached/master Haversine fallback will be used and marked in the audit.")
    if not messages:
        add("VALIDATION_READY", "READY", "All pre-optimization requirements are complete.")
    status = "BLOCKED" if any(row["level"] == "BLOCKED" for row in messages) else "WARNING" if any(row["level"] == "WARNING" for row in messages) else "READY"
    job.status = "READY" if status in {"READY", "WARNING"} and job.status == "DRAFT" else job.status
    db.commit()
    return {"status": status, "messages": messages, "checked_at": datetime.now(timezone.utc).isoformat()}


def _tags(db: Session, mt_ids: list[str], spbu_ids: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, str]]:
    mt_tags: dict[str, set[str]] = defaultdict(set)
    spbu_tags: dict[str, set[str]] = defaultdict(set)
    tag_ids: set[str] = set()
    if mt_ids:
        for entity_id, tag_id in db.execute(select(BridgeMTTag.mt_id, BridgeMTTag.tag_id).where(BridgeMTTag.mt_id.in_(mt_ids))).all():
            mt_tags[entity_id].add(tag_id)
            tag_ids.add(tag_id)
    if spbu_ids:
        for entity_id, tag_id in db.execute(select(BridgeSPBUTag.spbu_id, BridgeSPBUTag.tag_id).where(BridgeSPBUTag.spbu_id.in_(spbu_ids))).all():
            spbu_tags[entity_id].add(tag_id)
            tag_ids.add(tag_id)
    labels = {row.tag_id: row.tag_value for row in db.scalars(select(MasterTag).where(MasterTag.tag_id.in_(tag_ids))).all()} if tag_ids else {}
    return mt_tags, spbu_tags, labels


def _solver_inputs(db: Session, job: OptimizationJob, parameters: dict, *, current_time: datetime, reroute: bool) -> dict:
    depot = db.get(MasterDepot, job.depot_id)
    assert depot is not None
    day_start, operational_start, operational_end = _job_day(job, depot)
    rows = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id).order_by(LOOperationalState.loading_order_id)).all()
    if reroute:
        apply_freeze_rules(rows, current_time=current_time, freeze_window_minutes=int(parameters["freeze_window_minutes"]))
    else:
        for row in rows:
            row.frozen = row.status in {"ONGOING", "DONE"}
            row.frozen_reason = row.status if row.frozen else None
    optimizable = [row for row in rows if row.status == "PLANNED" and not row.frozen and not row.user_cancelled]
    vehicle_rows = db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id)).all()
    for row in vehicle_rows:
        row.effective_eta_depot = row.user_eta_override or row.system_eta_depot or row.planned_eta_depot
    mts = {row.mt_id: db.get(MasterMT, row.mt_id) for row in vehicle_rows}
    spbu_ids = sorted({row.spbu_id for row in optimizable})
    spbus = {spbu_id: db.get(MasterSPBU, spbu_id) for spbu_id in spbu_ids}
    mt_tags, spbu_tags, labels = _tags(db, list(mts), spbu_ids)
    historical_affinity: dict[tuple[str, str], tuple[datetime, float]] = {}
    if spbu_ids and mts:
        for row in db.scalars(
            select(FactSPBUMTPair).where(
                FactSPBUMTPair.depot_id == job.depot_id,
                FactSPBUMTPair.spbu_id.in_(spbu_ids),
                FactSPBUMTPair.mt_id.in_(list(mts)),
                FactSPBUMTPair.analysis_end_date < job.operating_date,
            )
        ).all():
            key = (row.spbu_id, row.mt_id)
            calculated = _utc(row.calculated_at) if row.calculated_at else datetime.min.replace(tzinfo=timezone.utc)
            if key not in historical_affinity or calculated > historical_affinity[key][0]:
                historical_affinity[key] = (calculated, float(row.probability_mt_given_spbu or 0))
    historical_pairing: dict[tuple[str, str], tuple[datetime, float]] = {}
    if spbu_ids:
        for row in db.scalars(
            select(FactSPBUPair).where(
                FactSPBUPair.depot_id == job.depot_id,
                FactSPBUPair.spbu_a_id.in_(spbu_ids),
                FactSPBUPair.spbu_b_id.in_(spbu_ids),
                FactSPBUPair.analysis_end_date < job.operating_date,
            )
        ).all():
            key = tuple(sorted((row.spbu_a_id, row.spbu_b_id)))
            calculated = _utc(row.calculated_at) if row.calculated_at else datetime.min.replace(tzinfo=timezone.utc)
            if key not in historical_pairing or calculated > historical_pairing[key][0]:
                historical_pairing[key] = (calculated, float(row.support or 0))
    latest_assignment_by_lo: dict[str, RouteVersionLOAssignment] = {}
    if job.current_route_version_id:
        latest_assignment_by_lo = {
            row.loading_order_id: row
            for row in db.scalars(
                select(RouteVersionLOAssignment).where(RouteVersionLOAssignment.route_version_id == job.current_route_version_id)
            ).all()
        }
    # A frozen trip is copied into the next immutable version. New solver
    # rounds for the same physical MT must therefore continue after its highest
    # frozen trip number, otherwise the new version would contain duplicate
    # (vehicle, trip_number) identities.
    completed_trip_count_by_mt: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.frozen and row.current_vehicle_id and row.current_trip_number is not None:
            completed_trip_count_by_mt[row.current_vehicle_id] = max(
                completed_trip_count_by_mt[row.current_vehicle_id],
                int(row.current_trip_number),
            )
    lo_payload = []
    for matrix_node_index, row in enumerate(optimizable, start=1):
        spbu = spbus[row.spbu_id]
        allowed = []
        for vehicle in vehicle_rows:
            mt = mts[vehicle.mt_id]
            compatibility = evaluate_compatibility_entities(
                mt,
                spbu,
                mt_tag_ids=mt_tags[vehicle.mt_id],
                spbu_tag_ids=spbu_tags[row.spbu_id],
                tag_labels=labels,
                product_id=row.product_id,
                vehicle_mode="MT_CAPACITY_LE_SPBU_LIMIT",
            )
            if compatibility["compatible"] and float(vehicle.capacity_kl) + 1e-9 >= float(row.volume_kl):
                allowed.append(vehicle.mt_id)
        start_minutes, end_minutes = None, None
        if spbu and spbu.official_window_start and spbu.official_window_end:
            zone = _timezone(depot)
            start = datetime.combine(job.operating_date, spbu.official_window_start, tzinfo=zone).astimezone(timezone.utc)
            end = datetime.combine(job.operating_date, spbu.official_window_end, tzinfo=zone).astimezone(timezone.utc)
            if end <= start:
                end += timedelta(days=1)
            start_minutes = max(0, round((start - day_start).total_seconds() / 60))
            end_minutes = max(start_minutes, round((end - day_start).total_seconds() / 60))
        lo_payload.append(
            {
                "matrix_node_index": matrix_node_index,
                "loading_order_id": row.loading_order_id,
                "spbu_id": row.spbu_id,
                "product_id": row.product_id,
                "product_name": row.product_name_snapshot,
                "volume_kl": row.volume_kl,
                "phase6_predicted_shipment_id": row.phase6_predicted_shipment_id,
                "phase6_predicted_vehicle_id": row.phase6_predicted_vehicle_id,
                "phase6_pairing": row.phase6_predicted_spbu_pairing,
                "historical_mt_affinity": {
                    mt_id: historical_affinity.get((row.spbu_id, mt_id), (datetime.min.replace(tzinfo=timezone.utc), 0))[1]
                    for mt_id in allowed
                },
                "historical_pairing_scores": {
                    other_spbu_id: historical_pairing.get(tuple(sorted((row.spbu_id, other_spbu_id))), (datetime.min.replace(tzinfo=timezone.utc), 0))[1]
                    for other_spbu_id in spbu_ids if other_spbu_id != row.spbu_id
                },
                "current_vehicle_id": row.current_vehicle_id,
                "current_shipment_id": row.current_shipment_id,
                "current_stop_sequence": latest_assignment_by_lo[row.loading_order_id].stop_sequence if row.loading_order_id in latest_assignment_by_lo else None,
                "current_planned_gate_out_minutes": (
                    round((_utc(row.planned_gate_out) - day_start).total_seconds() / 60)
                    if row.planned_gate_out else None
                ),
                "allowed_vehicle_ids": allowed,
                "time_window_start_minutes": start_minutes,
                "time_window_end_minutes": end_minutes,
                "mandatory": True,
                "user_cancelled": row.user_cancelled,
            }
        )
    vehicles = [
        {
            "mt_id": row.mt_id,
            "registration": row.registration_snapshot,
            "vehicle_class": row.vehicle_class,
            "tags": row.tag_snapshot,
            "capacity_kl": row.capacity_kl,
            "compartments": row.compartment_configuration,
            "effective_eta_depot": max(_utc(row.effective_eta_depot), operational_start, _utc(current_time)) if row.effective_eta_depot else max(operational_start, _utc(current_time)),
            "operational_status": row.operational_status,
            "working_time_limit_minutes": row.working_time_limit_minutes,
            "working_time_used_minutes": row.working_time_used_minutes,
            "working_time_remaining_minutes": row.working_time_remaining_minutes,
            "completed_trip_count": completed_trip_count_by_mt[row.mt_id],
        }
        for row in vehicle_rows
    ]
    bay_config = get_bay_configuration(db, job.depot_id)
    bays = [
        {"master_bay_id": row["master_bay_id"], "all_products_allowed": row["all_products_allowed"], "allowed_product_ids": row["allowed_products"], "number_of_loading_arms": row["number_of_loading_arms"], "loading_mode": row["loading_mode"]}
        for row in bay_config["bays"] if row["active_status"] == "ACTIVE"
    ]
    actual = db.scalars(select(ActualBayState).where(ActualBayState.job_id == job.job_id)).all()
    queue = db.scalars(select(OptimizationInitialQueue).where(OptimizationInitialQueue.job_id == job.job_id)).all()
    return {
        "depot": depot,
        "day_start": day_start,
        "operational_start": operational_start,
        "operational_end": operational_end,
        "all_lo_rows": rows,
        "loading_orders": lo_payload,
        "vehicles": vehicles,
        "spbus": spbus,
        "bays": bays,
        "actual_bay_states": [{"master_bay_id": row.master_bay_id, "state_effective_at": row.state_effective_at, "remaining_loading_minutes": row.remaining_loading_minutes, "actual_queue_length": row.actual_queue_length, "current_vehicle_id": row.current_vehicle_id} for row in actual],
        "initial_queue": [{"master_bay_id": row.master_bay_id, "queue_position": row.queue_position, "vehicle_id": row.vehicle_id, "compartment_id": row.compartment_id, "product_id": row.product_id, "estimated_loading_duration_minutes": row.estimated_loading_duration_minutes, "state_effective_at": row.state_effective_at} for row in queue],
        "loading_durations": {row["product_id"]: row["duration_minutes_per_compartment"] for row in bay_config["loading_durations"] if row["active_status"] == "ACTIVE"},
    }


def _state_snapshot(db: Session, job: OptimizationJob, *, reason: str, actor: str) -> OperationalStateSnapshot:
    lo_rows = list_job_los(db, job.job_id)
    vehicle_rows = list_job_vehicles(db, job.job_id)
    bay = get_actual_bay_state(db, job.job_id)
    current_version = db.get(RouteVersion, job.current_route_version_id) if job.current_route_version_id else None
    audit_events = []
    if current_version:
        updated_lo = [row for row in lo_rows if (db.get(LOOperationalState, row["lo_state_id"]).updated_at or current_version.created_at) > current_version.created_at]
        updated_vehicle = db.scalars(select(ActualVehicleEvent).where(ActualVehicleEvent.job_id == job.job_id, ActualVehicleEvent.created_at > current_version.created_at)).all()
        if updated_lo:
            counts = Counter(row["status"] for row in updated_lo)
            audit_events.extend({"event": "LO_STATUS_UPDATE", "status": status, "count": count} for status, count in counts.items())
        audit_events.extend({"event": row.event_type, "mt_id": row.mt_id, "details": row.details, "event_time": _iso(row.event_time)} for row in updated_vehicle)
    snapshot = OperationalStateSnapshot(
        state_snapshot_id=uuid.uuid4().hex,
        job_id=job.job_id,
        snapshot_reason=reason,
        captured_by=actor,
        lo_state_snapshot=lo_rows,
        vehicle_state_snapshot=vehicle_rows,
        bay_state_snapshot=bay["states"],
        queue_snapshot=bay["queue"],
        audit_events=audit_events,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _parameter_snapshot(db: Session, job: OptimizationJob, parameters: dict, profile_id: str | None, *, actor: str) -> OptimizationParameterSnapshot:
    profile = db.get(OptimizationParameterProfile, profile_id) if profile_id else None
    serialized = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    snapshot = OptimizationParameterSnapshot(
        parameter_snapshot_id=uuid.uuid4().hex,
        job_id=job.job_id,
        source_profile_id=profile.profile_id if profile else None,
        source_profile_version=profile.version if profile else None,
        effective_parameters=parameters,
        parameter_checksum=hashlib.sha256(serialized.encode()).hexdigest(),
        created_by=actor,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _cost_breakdown(result: dict, vehicles: list[dict], parameters: dict) -> dict:
    used_ids = {trip["vehicle_id"] for trip in result["trips"]}
    vehicle_map = {row["mt_id"]: row for row in vehicles}
    activation = sum(OptimizationCoordinatorService().vrp._vehicle_cost(parameters, vehicle_map[mt_id]) for mt_id in used_ids)
    distance = sum(trip["distance_meters"] for trip in result["trips"]) / 1000 * float(parameters["cost_per_km"])
    operating_hours = sum(trip["operating_minutes"] for trip in result["trips"]) / 60
    operating = operating_hours * float(parameters["cost_per_operating_hour"])
    queue = sum(int(trip.get("queue_minutes") or 0) for trip in result["trips"]) * float(parameters["queue_cost"])
    loading = sum(int(trip.get("loading_minutes") or 0) for trip in result["trips"]) * float(parameters["loading_cost"])
    overtime_minutes = sum(max(0, int(trip["operating_minutes"]) - int(vehicle_map[trip["vehicle_id"]]["working_time_remaining_minutes"])) for trip in result["trips"])
    overtime = overtime_minutes * float(parameters["overtime_cost"])
    unserved = len(result["dropped"]) * float(parameters["unserved_penalty"])
    phase6_changes = sum(assignment.get("phase6_predicted_vehicle_id") and assignment.get("phase6_predicted_vehicle_id") != trip["vehicle_id"] for trip in result["trips"] for assignment in trip["lo_assignments"])
    penalties = unserved + phase6_changes * float(parameters["phase6_vehicle_change_penalty"])
    total = activation + distance + operating + queue + loading + overtime + penalties
    total_kl = sum(float(row["volume_kl"]) for trip in result["trips"] for row in trip["lo_assignments"])
    total_distance_km = sum(trip["distance_meters"] for trip in result["trips"]) / 1000
    return {
        "vehicle_activation_cost": round(activation, 2),
        "distance_cost": round(distance, 2),
        "operating_time_cost": round(operating, 2),
        "queue_cost": round(queue, 2),
        "loading_cost": round(loading, 2),
        "overtime_cost": round(overtime, 2),
        "penalty_cost": round(penalties, 2),
        "total_cost": round(total, 2),
        "cost_per_mt": round(total / len(used_ids), 2) if used_ids else 0,
        "cost_per_trip": round(total / len(result["trips"]), 2) if result["trips"] else 0,
        "cost_per_km": round(total / total_distance_km, 2) if total_distance_km else 0,
        "cost_per_kl": round(total / total_kl, 2) if total_kl else 0,
        "cost_per_lo": round(total / sum(len(trip["lo_assignments"]) for trip in result["trips"]), 2) if result["trips"] else 0,
    }


def _trip_cost_breakdowns(result: dict, vehicles: list[dict], parameters: dict) -> dict[tuple[str, int], dict]:
    """Return auditable trip-level costs; dropped-LO penalties remain plan-level."""
    vehicle_map = {row["mt_id"]: row for row in vehicles}
    activated: set[str] = set()
    breakdowns: dict[tuple[str, int], dict] = {}
    for trip in sorted(result["trips"], key=lambda row: (row["vehicle_id"], row["trip_number"])):
        vehicle = vehicle_map[trip["vehicle_id"]]
        activation = 0.0
        if trip["vehicle_id"] not in activated:
            activation = float(OptimizationCoordinatorService().vrp._vehicle_cost(parameters, vehicle))
            activated.add(trip["vehicle_id"])
        distance = trip["distance_meters"] / 1000 * float(parameters["cost_per_km"])
        operating = trip["operating_minutes"] / 60 * float(parameters["cost_per_operating_hour"])
        queue = int(trip.get("queue_minutes") or 0) * float(parameters["queue_cost"])
        loading = int(trip.get("loading_minutes") or 0) * float(parameters["loading_cost"])
        overtime_minutes = max(0, int(trip["operating_minutes"]) - int(vehicle["working_time_remaining_minutes"]))
        overtime = overtime_minutes * float(parameters["overtime_cost"])
        phase6_changes = sum(
            bool(row.get("phase6_predicted_vehicle_id") and row["phase6_predicted_vehicle_id"] != trip["vehicle_id"])
            for row in trip["lo_assignments"]
        )
        penalty = phase6_changes * float(parameters["phase6_vehicle_change_penalty"])
        total = activation + distance + operating + queue + loading + overtime + penalty
        breakdowns[(trip["vehicle_id"], trip["trip_number"])] = {
            "vehicle_activation_cost": round(activation, 2),
            "distance_cost": round(distance, 2),
            "operating_time_cost": round(operating, 2),
            "queue_cost": round(queue, 2),
            "loading_cost": round(loading, 2),
            "overtime_cost": round(overtime, 2),
            "penalty_cost": round(penalty, 2),
            "total_cost": round(total, 2),
        }
    return breakdowns


def _comparison(db: Session, job: OptimizationJob, result: dict, *, gate_out_tolerance_minutes: int = 5) -> dict:
    previous = db.get(RouteVersion, job.current_route_version_id) if job.current_route_version_id else None
    if not previous:
        return {"baseline": True, "plan_adherence_pct": 100.0, "vehicle_assignment_changes": 0, "shipment_changes": 0, "gate_out_variance_minutes": 0, "gate_out_changes": 0, "route_sequence_changes": 0}
    old = {row.loading_order_id: row for row in db.scalars(select(RouteVersionLOAssignment).where(RouteVersionLOAssignment.route_version_id == previous.route_version_id)).all()}
    changes = 0
    shipment_changes = 0
    route_sequence_changes = 0
    gate_variance = []
    new_count = 0
    for trip in result["trips"]:
        sequence_by_lo = {
            row["loading_order_id"]: stop["sequence"]
            for stop in trip.get("stops") or []
            for row in stop.get("loading_orders") or [stop["loading_order"]]
        }
        for row in trip["lo_assignments"]:
            new_count += 1
            prior = old.get(row["loading_order_id"])
            changes += int(bool(prior and prior.vehicle_id != trip["vehicle_id"]))
            shipment_changes += int(bool(prior and prior.shipment_id != trip["shipment_id"]))
            route_sequence_changes += int(bool(prior and prior.stop_sequence != sequence_by_lo.get(row["loading_order_id"])))
            if prior and prior.planned_gate_out:
                gate_variance.append(abs((_utc(trip["gate_out"]) - _utc(prior.planned_gate_out)).total_seconds()) / 60)
    gate_out_changes = sum(value > gate_out_tolerance_minutes for value in gate_variance)
    change_units = changes + shipment_changes + route_sequence_changes + gate_out_changes
    return {
        "baseline": False,
        "compared_to": previous.version_label,
        "plan_adherence_pct": round(max(0, 1 - change_units / max(1, new_count * 4)) * 100, 2),
        "vehicle_assignment_changes": changes,
        "shipment_changes": shipment_changes,
        "gate_out_variance_minutes": round(sum(gate_variance) / len(gate_variance), 2) if gate_variance else 0,
        "gate_out_changes": gate_out_changes,
        "route_sequence_changes": route_sequence_changes,
    }


def _copy_frozen_plan(db: Session, job: OptimizationJob, version: RouteVersion, frozen_rows: list[LOOperationalState]) -> tuple[list[RouteVersionTrip], set[str]]:
    if not job.current_route_version_id or not frozen_rows:
        return [], set()
    previous_assignments = {row.loading_order_id: row for row in db.scalars(select(RouteVersionLOAssignment).where(RouteVersionLOAssignment.route_version_id == job.current_route_version_id)).all()}
    previous_trip_ids = {row.route_version_trip_id for lo in frozen_rows if (row := previous_assignments.get(lo.loading_order_id)) and row.route_version_trip_id}
    previous_trips = db.scalars(select(RouteVersionTrip).where(RouteVersionTrip.route_version_trip_id.in_(previous_trip_ids))).all() if previous_trip_ids else []
    trip_map: dict[str, RouteVersionTrip] = {}
    for old in previous_trips:
        new = RouteVersionTrip(
            route_version_trip_id=uuid.uuid4().hex, route_version_id=version.route_version_id, vehicle_id=old.vehicle_id,
            trip_number=old.trip_number, shipment_id=old.shipment_id, vehicle_ready_at_depot=old.vehicle_ready_at_depot,
            queue_start=old.queue_start, loading_start=old.loading_start, loading_finish=old.loading_finish, gate_out=old.gate_out,
            estimated_return_depot=old.estimated_return_depot, distance_meters=old.distance_meters, driving_seconds=old.driving_seconds,
            service_seconds=old.service_seconds, queue_minutes=old.queue_minutes, loading_minutes=old.loading_minutes,
            operating_minutes=old.operating_minutes, assignment_status="FROZEN", route_geometry=old.route_geometry,
            route_geometry_source=old.route_geometry_source, cost_breakdown=old.cost_breakdown,
        )
        db.add(new)
        trip_map[old.route_version_trip_id] = new
        for stop in db.scalars(select(RouteVersionStop).where(RouteVersionStop.route_version_trip_id == old.route_version_trip_id)).all():
            db.add(RouteVersionStop(route_version_stop_id=uuid.uuid4().hex, route_version_trip_id=new.route_version_trip_id, sequence_number=stop.sequence_number, stop_type=stop.stop_type, spbu_id=stop.spbu_id, arrival_time=stop.arrival_time, departure_time=stop.departure_time, service_minutes=stop.service_minutes, distance_from_previous_meters=stop.distance_from_previous_meters, travel_from_previous_seconds=stop.travel_from_previous_seconds, loading_order_ids=stop.loading_order_ids, products=stop.products, volume_kl=stop.volume_kl))
        old_bay = db.scalar(
            select(OptimizationBayAssignment).where(
                OptimizationBayAssignment.route_version_trip_id == old.route_version_trip_id
            )
        )
        if old_bay:
            new_bay = OptimizationBayAssignment(
                bay_assignment_id=uuid.uuid4().hex,
                route_version_id=version.route_version_id,
                route_version_trip_id=new.route_version_trip_id,
                master_bay_id=old_bay.master_bay_id,
                vehicle_ready_at_depot=old_bay.vehicle_ready_at_depot,
                queue_start=old_bay.queue_start,
                loading_start=old_bay.loading_start,
                loading_finish=old_bay.loading_finish,
                gate_out=old_bay.gate_out,
                queue_minutes=old_bay.queue_minutes,
                loading_minutes=old_bay.loading_minutes,
            )
            db.add(new_bay)
            for operation in db.scalars(
                select(OptimizationBayOperation).where(
                    OptimizationBayOperation.bay_assignment_id == old_bay.bay_assignment_id
                )
            ).all():
                db.add(
                    OptimizationBayOperation(
                        bay_operation_id=uuid.uuid4().hex,
                        bay_assignment_id=new_bay.bay_assignment_id,
                        master_bay_id=operation.master_bay_id,
                        compartment_id=operation.compartment_id,
                        product_id=operation.product_id,
                        loading_start=operation.loading_start,
                        loading_finish=operation.loading_finish,
                        duration_minutes=operation.duration_minutes,
                        loading_mode=operation.loading_mode,
                    )
                )
    copied_ids = set()
    for lo in frozen_rows:
        old = previous_assignments.get(lo.loading_order_id)
        if not old:
            continue
        copied_ids.add(lo.loading_order_id)
        new_trip = trip_map.get(old.route_version_trip_id)
        db.add(RouteVersionLOAssignment(route_version_lo_assignment_id=uuid.uuid4().hex, route_version_id=version.route_version_id, route_version_trip_id=new_trip.route_version_trip_id if new_trip else None, loading_order_id=old.loading_order_id, vehicle_id=old.vehicle_id, trip_number=old.trip_number, shipment_id=old.shipment_id, compartment_id=old.compartment_id, spbu_id=old.spbu_id, product_id=old.product_id, volume_kl=old.volume_kl, stop_sequence=old.stop_sequence, planned_gate_out=old.planned_gate_out, eta=old.eta, frozen=True, assignment_status=lo.status, dropped_reason_code=old.dropped_reason_code, dropped_reason_description=old.dropped_reason_description, phase6_deviation=old.phase6_deviation))
    return list(trip_map.values()), copied_ids


def _summary(result: dict, all_rows: list[LOOperationalState], frozen_trips: list[RouteVersionTrip], cost: dict) -> dict:
    trips = result["trips"]
    all_trips_count = len(trips) + len(frozen_trips)
    used = {trip["vehicle_id"] for trip in trips} | {trip.vehicle_id for trip in frozen_trips}
    trip_counts = Counter([trip["vehicle_id"] for trip in trips] + [trip.vehicle_id for trip in frozen_trips])
    queue_minutes = [int(trip.get("queue_minutes") or 0) for trip in trips] + [trip.queue_minutes for trip in frozen_trips]
    total_distance = sum(int(trip["distance_meters"]) for trip in trips) + sum(trip.distance_meters for trip in frozen_trips)
    total_operating = sum(int(trip["operating_minutes"]) for trip in trips) + sum(trip.operating_minutes for trip in frozen_trips)
    total_kl = sum(float(row.volume_kl) for row in all_rows)
    planned_kl = sum(float(row["volume_kl"]) for trip in trips for row in trip["lo_assignments"])
    return {
        "total_lo": len(all_rows),
        "done_lo": sum(row.status == "DONE" for row in all_rows),
        "ongoing_lo": sum(row.status == "ONGOING" for row in all_rows),
        "planned_lo": sum(row.status == "PLANNED" for row in all_rows),
        "dropped_lo": len(result["dropped"]),
        "delivered_kl": round(sum(float(row.volume_kl) for row in all_rows if row.status == "DONE"), 3),
        "remaining_kl": round(sum(float(row.volume_kl) for row in all_rows if row.status != "DONE"), 3),
        "planned_kl": round(planned_kl, 3),
        "used_mt": len(used),
        "total_trips": all_trips_count,
        "average_trips_per_mt": round(all_trips_count / len(used), 2) if used else 0,
        "max_trips_per_mt": max(trip_counts.values(), default=0),
        "mt_with_1_trip": sum(value == 1 for value in trip_counts.values()),
        "mt_with_2_trips": sum(value == 2 for value in trip_counts.values()),
        "mt_with_3_plus_trips": sum(value >= 3 for value in trip_counts.values()),
        "fleet_utilization_pct": 0,
        "average_queue_minutes": round(sum(queue_minutes) / len(queue_minutes), 2) if queue_minutes else 0,
        "maximum_queue_minutes": max(queue_minutes, default=0),
        "total_distance_meters": total_distance,
        "total_travel_time_seconds": sum(int(trip["driving_seconds"]) for trip in trips) + sum(trip.driving_seconds for trip in frozen_trips),
        "total_operating_minutes": total_operating,
        "average_trip_duration_minutes": round(total_operating / all_trips_count, 2) if all_trips_count else 0,
        "total_cost": cost["total_cost"],
        "cost_per_kl": cost["cost_per_kl"],
        "cost_per_trip": cost["cost_per_trip"],
        "total_volume_kl": round(total_kl, 3),
    }


def run_optimization(db: Session, job_id: str, payload: dict, *, reroute: bool, actor: str = "local-user") -> dict:
    job = _require_job(db, job_id)
    if job.status == "CALCULATING":
        raise HTTPException(status_code=409, detail={"code": "OPTIMIZATION_ALREADY_RUNNING", "message": "This Job is already calculating."})
    if reroute and not job.current_route_version_id:
        raise HTTPException(status_code=409, detail={"code": "INITIAL_PLAN_REQUIRED", "message": "Run initial optimization before rerouting."})
    current_lo_rows = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == job.job_id)).all()
    if current_lo_rows and all(row.status == "DONE" for row in current_lo_rows):
        job.status = "CLOSED"
        job.closed_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={"code": "ALL_LO_DONE", "message": "All LO are DONE. The Job is closed and no new operational plan was created."},
        )
    validation = validate_job(db, job.job_id)
    if validation["status"] == "BLOCKED":
        raise HTTPException(status_code=422, detail={"code": "JOB_VALIDATION_BLOCKED", "message": "Phase 7 Job validation is blocked.", "validation": validation})
    profile_id = payload.get("profile_id")
    profile_parameters = get_parameter_profile(db, profile_id)["parameters"] if profile_id else DEFAULT_PHASE7_PARAMETERS
    try:
        parameters = effective_parameters({**profile_parameters, **(payload.get("parameters") or {})})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PHASE7_PARAMETER", "message": str(exc)}) from exc
    current_time = datetime.fromisoformat(str(payload["current_time"]).replace("Z", "+00:00")) if payload.get("current_time") else datetime.now(timezone.utc)
    reason = str(payload.get("reason") or ("Reroute" if reroute else "Initial Plan"))
    job.status = "CALCULATING"
    job.error_message = None
    db.commit()
    started = perf_counter()
    optimization_run = None
    try:
        inputs = _solver_inputs(db, job, parameters, current_time=current_time, reroute=reroute)
        state_snapshot = _state_snapshot(db, job, reason=reason, actor=actor)
        parameter_snapshot = _parameter_snapshot(db, job, parameters, profile_id, actor=actor)
        optimization_run = OptimizationRun(
            optimization_run_id=uuid.uuid4().hex,
            job_id=job.job_id,
            run_type="REROUTE" if reroute else "INITIAL",
            status="RUNNING",
            state_snapshot_id=state_snapshot.state_snapshot_id,
            parameter_snapshot_id=parameter_snapshot.parameter_snapshot_id,
            start_time=datetime.now(timezone.utc),
            solver_status="PENDING",
            objective=parameters["objective"],
        )
        db.add(optimization_run)
        db.flush()
        # Persist the audit snapshots and RUNNING row before any external route
        # call or solver work. A later rollback can then record a durable FAILED
        # outcome instead of losing the run that failed.
        db.commit()
        matrix_service = RouteMatrixService(db, job_id=job.job_id)
        matrix = matrix_service.build(
            depot=inputs["depot"], loading_orders=inputs["loading_orders"], spbus=inputs["spbus"], departure=max(current_time, inputs["operational_start"]), parameters=parameters
        ) if inputs["loading_orders"] else {"distance_matrix": [[0]], "time_matrix": [[0]], "geometry": {}, "metadata": {"location_count": 1, "pair_count": 0, "cache_hit_count": 0, "google_request_count": 0}}
        result = OptimizationCoordinatorService().optimize(
            loading_orders=inputs["loading_orders"], vehicles=inputs["vehicles"], distance_matrix=matrix["distance_matrix"],
            time_matrix=matrix["time_matrix"], bays=inputs["bays"], actual_bay_states=inputs["actual_bay_states"],
            initial_queue=inputs["initial_queue"], loading_durations=inputs["loading_durations"], day_start=inputs["day_start"],
            depot_close=inputs["operational_end"], parameters=parameters,
        )
        end_of_day = _utc(current_time) >= _utc(inputs["operational_end"])
        if end_of_day:
            for row in result["dropped"]:
                row["reason_code"] = "UNSERVED_END_OF_DAY"
                row["reason_description"] = "LO remains unserved because the depot operating time has ended; classify it as DROPPED or CARRY_OVER in the next operational workflow."
        if not inputs["loading_orders"] and any(row.frozen for row in inputs["all_lo_rows"]):
            result["solver_status"] = "FEASIBLE"
        version_number = (db.scalar(select(func.max(RouteVersion.version_number)).where(RouteVersion.job_id == job.job_id)) or 0) + 1
        comparison = _comparison(
            db,
            job,
            result,
            gate_out_tolerance_minutes=int(parameters.get("departure_time_tolerance_minutes", 5)),
        )
        costs = _cost_breakdown(result, inputs["vehicles"], parameters)
        trip_costs = _trip_cost_breakdowns(result, inputs["vehicles"], parameters)
        route_version = RouteVersion(
            route_version_id=uuid.uuid4().hex,
            job_id=job.job_id,
            version_number=version_number,
            version_label=f"V{version_number}",
            created_by=actor,
            reason=reason,
            state_snapshot_id=state_snapshot.state_snapshot_id,
            parameter_snapshot_id=parameter_snapshot.parameter_snapshot_id,
            objective=parameters["objective"],
            solver_status=result["solver_status"],
            objective_value=result["objective_value"],
            cost_snapshot=costs,
            comparison_snapshot=comparison,
        )
        db.add(route_version)
        db.flush()
        frozen_rows = [row for row in inputs["all_lo_rows"] if row.frozen]
        frozen_trips, copied_frozen_ids = _copy_frozen_plan(db, job, route_version, frozen_rows)
        persisted_trips: list[RouteVersionTrip] = []
        for trip in result["trips"]:
            ordered_spbus = [
                inputs["spbus"][stop["loading_order"]["spbu_id"]]
                for stop in trip["stops"]
                if stop["loading_order"]["spbu_id"] in inputs["spbus"]
            ]
            geometry = matrix_service.build_route_geometry(
                depot=inputs["depot"],
                ordered_spbus=ordered_spbus,
                departure=trip["gate_out"],
                parameters=parameters,
            )
            entity = RouteVersionTrip(
                route_version_trip_id=uuid.uuid4().hex, route_version_id=route_version.route_version_id, vehicle_id=trip["vehicle_id"], trip_number=trip["trip_number"], shipment_id=trip["shipment_id"],
                vehicle_ready_at_depot=trip["vehicle_ready_at_depot"], queue_start=trip.get("queue_start"), loading_start=trip.get("loading_start"), loading_finish=trip.get("loading_finish"), gate_out=trip["gate_out"],
                estimated_return_depot=trip["estimated_return_depot"], distance_meters=trip["distance_meters"], driving_seconds=trip["driving_seconds"], service_seconds=trip["service_seconds"],
                queue_minutes=int(trip.get("queue_minutes") or 0), loading_minutes=int(trip.get("loading_minutes") or 0), operating_minutes=trip["operating_minutes"], assignment_status="PLANNED",
                route_geometry=geometry["route_geometry"], route_geometry_source=geometry["route_geometry_source"], cost_breakdown=trip_costs[(trip["vehicle_id"], trip["trip_number"])],
            )
            db.add(entity)
            db.flush()
            persisted_trips.append(entity)
            lo_by_id = {row["loading_order_id"]: row for row in trip["lo_assignments"]}
            for stop in trip["stops"]:
                stop_los = stop.get("loading_orders") or [stop["loading_order"]]
                db.add(RouteVersionStop(route_version_stop_id=uuid.uuid4().hex, route_version_trip_id=entity.route_version_trip_id, sequence_number=stop["sequence"], stop_type="SPBU", spbu_id=stop["loading_order"]["spbu_id"], arrival_time=stop["arrival"] + (_utc(trip["gate_out"]) - _utc(trip["preliminary_gate_out"])), departure_time=stop["departure"] + (_utc(trip["gate_out"]) - _utc(trip["preliminary_gate_out"])), service_minutes=round((stop["departure"] - stop["arrival"]).total_seconds() / 60), distance_from_previous_meters=stop["leg_distance_meters"], travel_from_previous_seconds=stop["leg_seconds"], loading_order_ids=[lo["loading_order_id"] for lo in stop_los], products=sorted({lo.get("product_id") for lo in stop_los if lo.get("product_id")}), volume_kl=sum(float(lo["volume_kl"]) for lo in stop_los)))
                for lo in stop_los:
                    assignment = lo_by_id[lo["loading_order_id"]]
                    phase6_deviation = {"vehicle_changed": bool(lo.get("phase6_predicted_vehicle_id") and lo["phase6_predicted_vehicle_id"] != trip["vehicle_id"]), "shipment_changed": bool(lo.get("phase6_predicted_shipment_id") and lo["phase6_predicted_shipment_id"] != trip["shipment_id"])}
                    db.add(RouteVersionLOAssignment(route_version_lo_assignment_id=uuid.uuid4().hex, route_version_id=route_version.route_version_id, route_version_trip_id=entity.route_version_trip_id, loading_order_id=lo["loading_order_id"], vehicle_id=trip["vehicle_id"], trip_number=trip["trip_number"], shipment_id=trip["shipment_id"], compartment_id=assignment["compartment_id"], spbu_id=lo["spbu_id"], product_id=lo.get("product_id"), volume_kl=lo["volume_kl"], stop_sequence=stop["sequence"], planned_gate_out=trip["gate_out"], eta=stop["arrival"] + (_utc(trip["gate_out"]) - _utc(trip["preliminary_gate_out"])), frozen=False, assignment_status="PLANNED", phase6_deviation=phase6_deviation))
                    state = next(row for row in inputs["all_lo_rows"] if row.loading_order_id == lo["loading_order_id"])
                    state.current_vehicle_id = trip["vehicle_id"]
                    state.current_trip_number = trip["trip_number"]
                    state.current_shipment_id = trip["shipment_id"]
                    state.current_compartment_id = assignment["compartment_id"]
                    state.planned_gate_out = trip["gate_out"]
            if trip.get("master_bay_id"):
                bay_assignment = OptimizationBayAssignment(bay_assignment_id=uuid.uuid4().hex, route_version_id=route_version.route_version_id, route_version_trip_id=entity.route_version_trip_id, master_bay_id=trip["master_bay_id"], vehicle_ready_at_depot=trip["vehicle_ready_at_depot"], queue_start=trip["queue_start"], loading_start=trip["loading_start"], loading_finish=trip["loading_finish"], gate_out=trip["gate_out"], queue_minutes=trip["queue_minutes"], loading_minutes=trip["loading_minutes"])
                db.add(bay_assignment)
                product_by_compartment = {row["compartment_id"]: row.get("product_id") for row in trip["lo_assignments"]}
                operation_start = trip["loading_start"]
                for compartment_id, product_id in product_by_compartment.items():
                    duration = int(inputs["loading_durations"].get(product_id, 0))
                    operation_finish = operation_start + timedelta(minutes=duration)
                    db.add(OptimizationBayOperation(bay_operation_id=uuid.uuid4().hex, bay_assignment_id=bay_assignment.bay_assignment_id, master_bay_id=trip["master_bay_id"], compartment_id=compartment_id, product_id=product_id, loading_start=operation_start, loading_finish=operation_finish, duration_minutes=duration, loading_mode=parameters["loading_mode"]))
                    if parameters["loading_mode"] == "SEQUENTIAL":
                        operation_start = operation_finish
        for row in result["dropped"]:
            db.add(RouteVersionLOAssignment(route_version_lo_assignment_id=uuid.uuid4().hex, route_version_id=route_version.route_version_id, loading_order_id=row["loading_order_id"], vehicle_id=None, trip_number=None, shipment_id=None, compartment_id=None, spbu_id=row["spbu_id"], product_id=row.get("product_id"), volume_kl=row["volume_kl"], frozen=False, assignment_status="DROPPED", dropped_reason_code=row["reason_code"], dropped_reason_description=row["reason_description"], phase6_deviation={}))
            state = next((item for item in inputs["all_lo_rows"] if item.loading_order_id == row["loading_order_id"]), None)
            if state:
                state.current_vehicle_id = None
                state.current_trip_number = None
                state.current_shipment_id = None
                state.current_compartment_id = None
                state.planned_gate_out = None
        all_version_trips = [*frozen_trips, *persisted_trips]
        trips_by_vehicle: dict[str, list[RouteVersionTrip]] = defaultdict(list)
        for trip in all_version_trips:
            trips_by_vehicle[trip.vehicle_id].append(trip)
        vehicle_rows = {row.mt_id: row for row in db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == job.job_id)).all()}
        for mt_id, state in vehicle_rows.items():
            mt_trips = trips_by_vehicle[mt_id]
            delivered = sum(assignment.volume_kl for assignment in db.scalars(select(RouteVersionLOAssignment).where(RouteVersionLOAssignment.route_version_id == route_version.route_version_id, RouteVersionLOAssignment.vehicle_id == mt_id, RouteVersionLOAssignment.assignment_status != "DROPPED")).all())
            last_return = max((trip.estimated_return_depot for trip in mt_trips), default=state.effective_eta_depot)
            state.system_eta_depot = last_return
            state.effective_eta_depot = state.user_eta_override or state.system_eta_depot or state.planned_eta_depot
            used_minutes = sum(trip.operating_minutes for trip in mt_trips)
            state.working_time_used_minutes = max(state.working_time_used_minutes, used_minutes)
            state.working_time_remaining_minutes = max(0, state.working_time_limit_minutes - state.working_time_used_minutes)
            db.add(RouteVersionVehicleAssignment(route_version_vehicle_assignment_id=uuid.uuid4().hex, route_version_id=route_version.route_version_id, vehicle_id=mt_id, used=bool(mt_trips), trip_count=len(mt_trips), delivered_kl=delivered, total_distance_meters=sum(trip.distance_meters for trip in mt_trips), total_operating_minutes=used_minutes, working_time_remaining_minutes=state.working_time_remaining_minutes, activation_cost=OptimizationCoordinatorService().vrp._vehicle_cost(parameters, next((row for row in inputs["vehicles"] if row["mt_id"] == mt_id), {"vehicle_class": state.vehicle_class, "tags": state.tag_snapshot})), system_eta_depot=last_return))
        gateouts = [trip.gate_out for trip in all_version_trips]
        route_version.first_gate_out = min(gateouts) if gateouts else None
        route_version.last_gate_out = max(gateouts) if gateouts else None
        route_version.depot_dispatch_span_minutes = round((route_version.last_gate_out - route_version.first_gate_out).total_seconds() / 60) if route_version.first_gate_out and route_version.last_gate_out else 0
        route_version.summary_snapshot = _summary(result, inputs["all_lo_rows"], frozen_trips, costs)
        bay_assignments = db.scalars(
            select(OptimizationBayAssignment).where(
                OptimizationBayAssignment.route_version_id == route_version.route_version_id
            )
        ).all()
        bay_load = Counter()
        for assignment in bay_assignments:
            bay_load[assignment.master_bay_id] += assignment.loading_minutes + assignment.queue_minutes
        operating_span_minutes = max(1, round((_utc(inputs["operational_end"]) - _utc(inputs["operational_start"])).total_seconds() / 60))
        bay_capacity_minutes = len(inputs["bays"]) * operating_span_minutes
        total_loading_minutes = sum(assignment.loading_minutes for assignment in bay_assignments)
        queue_length = max(
            len(inputs["initial_queue"]),
            sum(int(row.get("actual_queue_length") or 0) for row in inputs["actual_bay_states"]),
        )
        total_mt = len(inputs["vehicles"])
        available_mt = sum(row.get("operational_status") != "UNAVAILABLE" for row in inputs["vehicles"])
        total_lo = len(inputs["all_lo_rows"])
        route_version.summary_snapshot.update(
            {
                "completion_pct": round(route_version.summary_snapshot["done_lo"] / total_lo * 100, 2) if total_lo else 0,
                "available_mt": available_mt,
                "unused_mt": max(0, total_mt - route_version.summary_snapshot["used_mt"]),
                "fleet_utilization_pct": round(route_version.summary_snapshot["used_mt"] / total_mt * 100, 2) if total_mt else 0,
                "working_time_used_minutes": sum(state.working_time_used_minutes for state in vehicle_rows.values()),
                "working_time_remaining_minutes": sum(state.working_time_remaining_minutes for state in vehicle_rows.values()),
                "average_turnaround_minutes": route_version.summary_snapshot["average_trip_duration_minutes"],
                "queue_length": queue_length,
                "bay_utilization_pct": round(total_loading_minutes / bay_capacity_minutes * 100, 2) if bay_capacity_minutes else 0,
                "bay_idle_minutes": max(0, bay_capacity_minutes - total_loading_minutes),
                "loading_throughput_kl_per_hour": round(route_version.summary_snapshot["planned_kl"] / (total_loading_minutes / 60), 2) if total_loading_minutes else 0,
                "bay_bottleneck": max(bay_load, key=bay_load.get) if bay_load else None,
                "activation_cost": costs["vehicle_activation_cost"],
                "distance_cost": costs["distance_cost"],
                "reroute_number": version_number - 1,
                "lo_reassigned": comparison["vehicle_assignment_changes"],
                "shipment_regrouped": comparison["shipment_changes"],
                "mt_assignment_changes": comparison["vehicle_assignment_changes"],
                "gate_out_changes": comparison["gate_out_changes"],
                "plan_stability_pct": comparison["plan_adherence_pct"],
            }
        )
        optimization_run.route_version_id = route_version.route_version_id
        optimization_run.status = "COMPLETED"
        optimization_run.solver_status = result["solver_status"]
        optimization_run.objective_value = result["objective_value"]
        optimization_run.coordination_iterations = result["coordination_iterations"]
        optimization_run.solver_metadata = {**result["solver_metadata"], "route_matrix": matrix["metadata"]}
        optimization_run.end_time = datetime.now(timezone.utc)
        optimization_run.solve_duration_ms = round((perf_counter() - started) * 1000)
        job.current_route_version_id = route_version.route_version_id
        job.status = "CLOSED" if end_of_day else "ACTIVE"
        job.closed_at = datetime.now(timezone.utc) if end_of_day else None
        db.commit()
        return get_route_version(db, job.job_id, route_version.route_version_id)
    except Exception as exc:
        db.rollback()
        job = _require_job(db, job_id)
        job.status = "FAILED"
        job.error_message = str(exc)
        if optimization_run:
            run = db.get(OptimizationRun, optimization_run.optimization_run_id)
            if run:
                run.status = "FAILED"
                run.solver_status = "FAILED"
                run.end_time = datetime.now(timezone.utc)
                run.solve_duration_ms = round((perf_counter() - started) * 1000)
                run.error_code = "PHASE7_OPTIMIZATION_FAILED"
                run.error_message = str(exc)
        db.commit()
        logger.exception("Phase 7 optimization failed for job %s", job_id)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail={"code": "PHASE7_OPTIMIZATION_FAILED", "message": str(exc)}) from exc


def list_route_versions(db: Session, job_id: str) -> list[dict]:
    job = _require_job(db, job_id)
    rows = db.scalars(select(RouteVersion).where(RouteVersion.job_id == job.job_id).order_by(desc(RouteVersion.version_number))).all()
    return [{"route_version_id": row.route_version_id, "version_number": row.version_number, "version_label": row.version_label, "created_at": _iso(row.created_at), "created_by": row.created_by, "reason": row.reason, "objective": row.objective, "solver_status": row.solver_status, "objective_value": row.objective_value, "state_snapshot_id": row.state_snapshot_id, "parameter_snapshot_id": row.parameter_snapshot_id, "summary": row.summary_snapshot, "comparison": row.comparison_snapshot} for row in rows]


def get_route_version(db: Session, job_id: str, version_id: str | None = None) -> dict:
    job = _require_job(db, job_id)
    version = _require_version(db, job, version_id)
    trips = db.scalars(select(RouteVersionTrip).where(RouteVersionTrip.route_version_id == version.route_version_id).order_by(RouteVersionTrip.gate_out, RouteVersionTrip.vehicle_id, RouteVersionTrip.trip_number)).all()
    trip_ids = [row.route_version_trip_id for row in trips]
    stops = db.scalars(select(RouteVersionStop).where(RouteVersionStop.route_version_trip_id.in_(trip_ids)).order_by(RouteVersionStop.route_version_trip_id, RouteVersionStop.sequence_number)).all() if trip_ids else []
    assignments = db.scalars(select(RouteVersionLOAssignment).where(RouteVersionLOAssignment.route_version_id == version.route_version_id).order_by(RouteVersionLOAssignment.loading_order_id)).all()
    bays = db.scalars(select(OptimizationBayAssignment).where(OptimizationBayAssignment.route_version_id == version.route_version_id)).all()
    vehicles = {row.mt_id: row for row in db.scalars(select(MasterMT)).all()}
    spbu_ids = {row.spbu_id for row in assignments}
    spbus = {row.spbu_id: row for row in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()} if spbu_ids else {}
    stops_by_trip: dict[str, list[RouteVersionStop]] = defaultdict(list)
    assignments_by_trip: dict[str, list[RouteVersionLOAssignment]] = defaultdict(list)
    for row in stops:
        stops_by_trip[row.route_version_trip_id].append(row)
    for row in assignments:
        if row.route_version_trip_id:
            assignments_by_trip[row.route_version_trip_id].append(row)
    bay_by_trip = {row.route_version_trip_id: row for row in bays}
    trip_payload = []
    for trip in trips:
        trip_payload.append({"route_version_trip_id": trip.route_version_trip_id, "vehicle_id": trip.vehicle_id, "registration": vehicles[trip.vehicle_id].vehicle_registration if trip.vehicle_id in vehicles else trip.vehicle_id, "trip_number": trip.trip_number, "shipment_id": trip.shipment_id, "vehicle_ready_at_depot": _iso(trip.vehicle_ready_at_depot), "queue_start": _iso(trip.queue_start), "loading_start": _iso(trip.loading_start), "loading_finish": _iso(trip.loading_finish), "gate_out": _iso(trip.gate_out), "return_depot": _iso(trip.estimated_return_depot), "distance_meters": trip.distance_meters, "travel_time_seconds": trip.driving_seconds, "service_seconds": trip.service_seconds, "queue_minutes": trip.queue_minutes, "loading_minutes": trip.loading_minutes, "operating_minutes": trip.operating_minutes, "assignment_status": trip.assignment_status, "route_geometry": trip.route_geometry, "route_geometry_source": trip.route_geometry_source, "cost_breakdown": trip.cost_breakdown or {}, "bay_assignment": {"master_bay_id": bay_by_trip[trip.route_version_trip_id].master_bay_id, "queue_minutes": bay_by_trip[trip.route_version_trip_id].queue_minutes} if trip.route_version_trip_id in bay_by_trip else None, "stops": [{"sequence": row.sequence_number, "spbu_id": row.spbu_id, "spbu_name": spbus[row.spbu_id].spbu_name if row.spbu_id in spbus else row.spbu_id, "latitude": float(spbus[row.spbu_id].latitude) if row.spbu_id in spbus and spbus[row.spbu_id].latitude is not None else None, "longitude": float(spbus[row.spbu_id].longitude) if row.spbu_id in spbus and spbus[row.spbu_id].longitude is not None else None, "arrival_time": _iso(row.arrival_time), "departure_time": _iso(row.departure_time), "service_minutes": row.service_minutes, "distance_from_previous_meters": row.distance_from_previous_meters, "travel_from_previous_seconds": row.travel_from_previous_seconds, "loading_order_ids": row.loading_order_ids, "products": row.products, "volume_kl": row.volume_kl} for row in stops_by_trip[trip.route_version_trip_id]], "loading_orders": [{"loading_order_id": row.loading_order_id, "spbu_id": row.spbu_id, "spbu_name": spbus[row.spbu_id].spbu_name if row.spbu_id in spbus else row.spbu_id, "product_id": row.product_id, "volume_kl": row.volume_kl, "compartment_id": row.compartment_id, "stop_sequence": row.stop_sequence, "eta": _iso(row.eta), "frozen": row.frozen, "status": row.assignment_status, "phase6_deviation": row.phase6_deviation} for row in assignments_by_trip[trip.route_version_trip_id]]})
    dropped = [{"loading_order_id": row.loading_order_id, "spbu_id": row.spbu_id, "spbu": spbus[row.spbu_id].spbu_name if row.spbu_id in spbus else row.spbu_id, "product_id": row.product_id, "volume_kl": row.volume_kl, "reason_code": row.dropped_reason_code, "reason_description": row.dropped_reason_description, "route_version": version.version_label} for row in assignments if row.assignment_status == "DROPPED"]
    snapshot = db.get(OperationalStateSnapshot, version.state_snapshot_id)
    parameters = db.get(OptimizationParameterSnapshot, version.parameter_snapshot_id)
    return {"route_version_id": version.route_version_id, "version_number": version.version_number, "version_label": version.version_label, "created_at": _iso(version.created_at), "created_by": version.created_by, "reason": version.reason, "objective": version.objective, "solver_status": version.solver_status, "objective_value": version.objective_value, "first_gate_out": _iso(version.first_gate_out), "last_gate_out": _iso(version.last_gate_out), "depot_dispatch_span_minutes": version.depot_dispatch_span_minutes, "summary": version.summary_snapshot, "cost": version.cost_snapshot, "comparison": version.comparison_snapshot, "audit_events": snapshot.audit_events if snapshot else [], "parameter_snapshot": parameters.effective_parameters if parameters else {}, "parameter_checksum": parameters.parameter_checksum if parameters else None, "trips": trip_payload, "dropped_lo": dropped}


def get_trip_details(db: Session, job_id: str, version_id: str, trip_id: str) -> dict:
    payload = get_route_version(db, job_id, version_id)
    for trip in payload["trips"]:
        if trip["route_version_trip_id"] == trip_id:
            return trip
    raise HTTPException(status_code=404, detail={"code": "TRIP_NOT_FOUND", "message": "Trip was not found in this route version."})


def get_simulation_data(db: Session, job_id: str, version_id: str | None = None) -> dict:
    version = get_route_version(db, job_id, version_id)
    hourly: dict[str, dict] = defaultdict(lambda: {"gate_out_kl": 0.0, "returning_mt": 0, "returning_capacity_kl": 0.0})
    vehicle_states = {row["mt_id"]: row for row in list_job_vehicles(db, job_id)}
    for trip in version["trips"]:
        gate_hour = _utc(datetime.fromisoformat(trip["gate_out"])).replace(minute=0, second=0, microsecond=0).isoformat()
        hourly[gate_hour]["gate_out_kl"] += sum(float(row["volume_kl"]) for row in trip["loading_orders"])
        return_hour = _utc(datetime.fromisoformat(trip["return_depot"])).replace(minute=0, second=0, microsecond=0).isoformat()
        hourly[return_hour]["returning_mt"] += 1
        hourly[return_hour]["returning_capacity_kl"] += float(vehicle_states.get(trip["vehicle_id"], {}).get("capacity_kl") or 0)
    cumulative = 0.0
    rows = []
    for hour in sorted(hourly):
        cumulative += hourly[hour]["gate_out_kl"]
        rows.append({"hour": hour, **hourly[hour], "cumulative_gate_out_kl": round(cumulative, 3)})
    return {"route_version_id": version["route_version_id"], "version_label": version["version_label"], "hourly": rows, "kpis": version["summary"], "first_gate_out": version["first_gate_out"], "last_gate_out": version["last_gate_out"], "depot_dispatch_span_minutes": version["depot_dispatch_span_minutes"]}


def get_map_route(db: Session, job_id: str, version_id: str | None = None, *, vehicle_id: str | None = None, trip_number: int | None = None) -> dict:
    job = _require_job(db, job_id)
    depot = db.get(MasterDepot, job.depot_id)
    version = get_route_version(db, job_id, version_id)
    trips = [trip for trip in version["trips"] if (not vehicle_id or trip["vehicle_id"] == vehicle_id) and (trip_number is None or trip["trip_number"] == trip_number)]
    return {"route_version_id": version["route_version_id"], "version_label": version["version_label"], "depot": {"depot_id": depot.depot_id if depot else None, "name": depot.depot_name if depot else None, "latitude": depot.latitude if depot else None, "longitude": depot.longitude if depot else None}, "trips": trips, "provider_role": "Google Routes supplies road matrix/geometry only; OR-Tools produces the optimized plan."}


def get_cost_analysis(db: Session, job_id: str, version_id: str | None = None) -> dict:
    version = get_route_version(db, job_id, version_id)
    by_mt: dict[str, dict] = defaultdict(lambda: {"trips": 0, "distance_meters": 0, "operating_minutes": 0, "volume_kl": 0.0})
    by_trip = []
    for trip in version["trips"]:
        volume = sum(float(row["volume_kl"]) for row in trip["loading_orders"])
        row = by_mt[trip["vehicle_id"]]
        row["trips"] += 1
        row["distance_meters"] += trip["distance_meters"]
        row["operating_minutes"] += trip["operating_minutes"]
        row["volume_kl"] += volume
        by_trip.append({"vehicle_id": trip["vehicle_id"], "trip_number": trip["trip_number"], "distance_meters": trip["distance_meters"], "operating_minutes": trip["operating_minutes"], "volume_kl": volume})
    return {"route_version_id": version["route_version_id"], "version_label": version["version_label"], "summary": version["cost"], "per_mt": [{"vehicle_id": key, **value} for key, value in by_mt.items()], "per_trip": by_trip}


def get_dropped_lo(db: Session, job_id: str, version_id: str | None = None) -> dict:
    version = get_route_version(db, job_id, version_id)
    return {"route_version_id": version["route_version_id"], "version_label": version["version_label"], "total": len(version["dropped_lo"]), "rows": version["dropped_lo"]}
