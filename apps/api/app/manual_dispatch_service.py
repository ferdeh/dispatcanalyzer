from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .compatibility import evaluate_mt_spbu_compatibility
from .google_routes import (
    GoogleRoutesClient,
    GoogleRoutesError,
    configuration_snapshot,
    decrypt_api_key,
    get_google_routes_configuration,
)
from .models import (
    BridgeMTTag,
    BridgeSPBUTag,
    MLSPBUClusterAssignment,
    ManualDispatchAuditLog,
    ManualDispatchJob,
    ManualDispatchLoadingOrder,
    ManualDispatchRouteLeg,
    ManualDispatchTrip,
    ManualDispatchTripLO,
    ManualDispatchVehicle,
    MasterDepot,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    MasterTag,
    OptimizationJob,
    PredictionAssignment,
    PredictionRun,
    PredictionShipment,
    PredictionShipmentLine,
    PredictionTrip,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionStop,
    RouteVersionTrip,
    RouteMatrixCache,
    VehicleOperationalState,
    LOOperationalState,
)


JOB_STATUSES = {"DRAFT", "IN_PROGRESS", "READY", "FINALIZED"}
TRIP_STATUSES = {"DRAFT", "MODIFIED", "CALCULATING", "VALID", "WARNING", "CONFLICT", "NEEDS_RECALCULATION"}
HARD_FINALIZATION_TRIP_STATUSES = {"DRAFT", "MODIFIED", "CALCULATING", "WARNING", "CONFLICT", "NEEDS_RECALCULATION"}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex.upper()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _capacity(mt: MasterMT | None, fallback: float | int | None = None) -> float:
    if fallback is not None and float(fallback or 0) > 0:
        return float(fallback)
    return float((mt.vehicle_type_tag if mt else 0) or 0)


def _job_or_404(db: Session, dispatch_job_id: str) -> ManualDispatchJob:
    job = db.get(ManualDispatchJob, dispatch_job_id)
    if not job:
        job = db.scalar(select(ManualDispatchJob).where(ManualDispatchJob.job_id == dispatch_job_id))
    if not job:
        raise HTTPException(status_code=404, detail={"code": "MANUAL_DISPATCH_JOB_NOT_FOUND", "message": "Manual Dispatch Job was not found."})
    return job


def _vehicle_or_404(db: Session, job: ManualDispatchJob, vehicle_id: str) -> ManualDispatchVehicle:
    vehicle = db.get(ManualDispatchVehicle, vehicle_id)
    if not vehicle or vehicle.dispatch_job_id != job.id:
        raise HTTPException(status_code=404, detail={"code": "DISPATCH_VEHICLE_NOT_FOUND", "message": "MT is not part of this Manual Dispatch Job."})
    return vehicle


def _trip_context(db: Session, job: ManualDispatchJob, trip_id: str) -> tuple[ManualDispatchTrip, ManualDispatchVehicle]:
    trip = db.get(ManualDispatchTrip, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail={"code": "DISPATCH_TRIP_NOT_FOUND", "message": "Trip was not found."})
    vehicle = db.get(ManualDispatchVehicle, trip.dispatch_vehicle_id)
    if not vehicle or vehicle.dispatch_job_id != job.id:
        raise HTTPException(status_code=404, detail={"code": "DISPATCH_TRIP_NOT_FOUND", "message": "Trip is not part of this Manual Dispatch Job."})
    return trip, vehicle


def _assert_editable(job: ManualDispatchJob) -> None:
    if job.status == "FINALIZED":
        raise HTTPException(
            status_code=409,
            detail={"code": "FINALIZED_DISPATCH_READ_ONLY", "message": "Finalized dispatch is read-only. Create a new version to edit it."},
        )


def _assert_version(job: ManualDispatchJob, expected_job_version: int | None) -> None:
    if expected_job_version is not None and expected_job_version != job.row_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MANUAL_DISPATCH_VERSION_CONFLICT",
                "message": "The Manual Dispatch Job changed after this workspace was loaded. Refresh before saving.",
                "current_job_version": job.row_version,
            },
        )


def _touch(job: ManualDispatchJob) -> None:
    job.row_version = int(job.row_version or 0) + 1
    job.updated_at = _now()


class DispatchAuditService:
    @staticmethod
    def log(
        db: Session,
        job: ManualDispatchJob,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        *,
        old: dict | None = None,
        new: dict | None = None,
        metadata: dict | None = None,
        reason: str | None = None,
    ) -> None:
        db.add(
            ManualDispatchAuditLog(
                id=_id("MDAUD"),
                dispatch_job_id=job.id,
                user_id=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                old_value_json=old or {},
                new_value_json=new or {},
                metadata_json=metadata or {},
                reason=reason,
            )
        )

    @staticmethod
    def list(db: Session, dispatch_job_id: str, *, limit: int = 250, offset: int = 0) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        base = select(ManualDispatchAuditLog).where(ManualDispatchAuditLog.dispatch_job_id == job.id)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(base.order_by(ManualDispatchAuditLog.created_at.desc()).offset(offset).limit(limit)).all()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "rows": [
                {
                    "id": row.id,
                    "timestamp": _iso(row.created_at),
                    "user": row.user_id,
                    "action": row.action,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "previous_value": row.old_value_json or {},
                    "new_value": row.new_value_json or {},
                    "metadata": row.metadata_json or {},
                    "reason": row.reason,
                    "summary": DispatchAuditService._summary(row),
                }
                for row in rows
            ],
        }

    @staticmethod
    def _summary(row: ManualDispatchAuditLog) -> str:
        meta = row.metadata_json or {}
        lo = meta.get("lo_number") or meta.get("lo_id")
        mt = meta.get("vehicle_registration") or meta.get("mt_id")
        trip = meta.get("trip_sequence")
        if row.action == "LO_MOVED":
            return f"{row.user_id} moved {lo or row.entity_id} from {meta.get('source_label', 'unassigned')} to {meta.get('destination_label', 'trip')}."
        if row.action == "TRIP_RECALCULATED":
            return f"{mt or 'MT'} Trip {trip or '-'} recalculated; return changed from {meta.get('old_return') or '-'} to {meta.get('new_return') or '-'} ."
        return f"{row.user_id} {row.action.replace('_', ' ').lower()} {row.entity_type.lower()} {row.entity_id}."


class ManualDispatchJobService:
    @staticmethod
    def source_options(db: Session, depot_id: str, operational_date: date | None = None) -> dict:
        query = select(OptimizationJob).where(OptimizationJob.depot_id == depot_id)
        if operational_date:
            query = query.where(OptimizationJob.operating_date == operational_date)
        jobs = db.scalars(query.order_by(OptimizationJob.created_at.desc())).all()
        rows = []
        for job in jobs:
            versions = db.scalars(
                select(RouteVersion).where(RouteVersion.job_id == job.job_id).order_by(RouteVersion.version_number)
            ).all()
            routes = []
            if job.source_prediction_run_id:
                run = db.get(PredictionRun, job.source_prediction_run_id)
                if run:
                    routes.append(
                        {
                            "source_phase": "PHASE_6",
                            "source_route_id": "PHASE6",
                            "source_route_version": "Phase 6 Prediction / Warm Start",
                            "source_run_id": run.id,
                            "source_created_at": _iso(run.created_at),
                        }
                    )
            routes.extend(
                {
                    "source_phase": "PHASE_7",
                    "source_route_id": version.route_version_id,
                    "source_route_version": version.version_label,
                    "source_run_id": job.source_prediction_run_id,
                    "source_created_at": _iso(version.created_at),
                }
                for version in versions
            )
            rows.append(
                {
                    "job_id": job.job_id,
                    "job_no": job.job_no,
                    "job_name": job.job_name,
                    "depot_id": job.depot_id,
                    "operational_date": job.operating_date.isoformat(),
                    "status": job.status,
                    "routes": routes,
                }
            )
        return {"depot_id": depot_id, "jobs": rows}

    @staticmethod
    def list(
        db: Session,
        *,
        depot_id: str | None = None,
        operational_date: date | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict:
        query = select(ManualDispatchJob)
        if depot_id:
            query = query.where(ManualDispatchJob.depot_id == depot_id)
        if operational_date:
            query = query.where(ManualDispatchJob.operational_date == operational_date)
        if status:
            query = query.where(ManualDispatchJob.status == status)
        if search:
            token = f"%{search.strip()}%"
            query = query.where(or_(ManualDispatchJob.job_id.ilike(token), ManualDispatchJob.job_name.ilike(token)))
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        jobs = db.scalars(query.order_by(ManualDispatchJob.updated_at.desc()).offset(offset).limit(limit)).all()
        depots = {row.depot_id: row for row in db.scalars(select(MasterDepot).where(MasterDepot.depot_id.in_([j.depot_id for j in jobs]))).all()} if jobs else {}
        rows = []
        for job in jobs:
            vehicle_ids = db.scalars(select(ManualDispatchVehicle.id).where(ManualDispatchVehicle.dispatch_job_id == job.id)).all()
            trips = db.scalars(select(ManualDispatchTrip).where(ManualDispatchTrip.dispatch_vehicle_id.in_(vehicle_ids))).all() if vehicle_ids else []
            scope = db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.dispatch_job_id == job.id)).all()
            rows.append(
                {
                    **ManualDispatchJobService._metadata(job, depots.get(job.depot_id)),
                    "assigned_lo": sum(1 for lo in scope if lo.assignment_status == "ASSIGNED"),
                    "unassigned_lo": sum(1 for lo in scope if lo.assignment_status != "ASSIGNED"),
                    "mt_used": len({trip.dispatch_vehicle_id for trip in trips if trip.total_volume_kl > 0}),
                    "total_trips": len(trips),
                }
            )
        return {"total": total, "limit": limit, "offset": offset, "rows": rows}

    @staticmethod
    def _metadata(job: ManualDispatchJob, depot: MasterDepot | None = None) -> dict:
        return {
            "id": job.id,
            "job_id": job.job_id,
            "job_name": job.job_name,
            "depot_id": job.depot_id,
            "depot_name": depot.depot_name if depot else None,
            "operational_date": job.operational_date.isoformat(),
            "source_phase": job.source_phase,
            "source_job_id": job.source_job_id,
            "source_run_id": job.source_run_id,
            "source_route_id": job.source_route_id,
            "source_route_version": job.source_route_version,
            "source_created_at": _iso(job.source_created_at),
            "dispatch_version": job.dispatch_version,
            "dispatch_version_label": f"Dispatch V{job.dispatch_version}",
            "parent_dispatch_job_id": job.parent_dispatch_job_id,
            "status": job.status,
            "job_version": job.row_version,
            "created_by": job.created_by,
            "created_at": _iso(job.created_at),
            "last_saved": _iso(job.updated_at),
            "finalized_by": job.finalized_by,
            "finalized_at": _iso(job.finalized_at),
        }

    @staticmethod
    def detail(db: Session, dispatch_job_id: str) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        depot = db.get(MasterDepot, job.depot_id)
        vehicles = db.scalars(
            select(ManualDispatchVehicle)
            .where(ManualDispatchVehicle.dispatch_job_id == job.id)
            .order_by(ManualDispatchVehicle.vehicle_registration, ManualDispatchVehicle.mt_id)
        ).all()
        vehicle_ids = [vehicle.id for vehicle in vehicles]
        trips = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id.in_(vehicle_ids))
            .order_by(ManualDispatchTrip.dispatch_vehicle_id, ManualDispatchTrip.trip_sequence)
        ).all() if vehicle_ids else []
        trip_ids = [trip.id for trip in trips]
        joins = db.scalars(
            select(ManualDispatchTripLO)
            .where(ManualDispatchTripLO.trip_id.in_(trip_ids))
            .order_by(ManualDispatchTripLO.trip_id, ManualDispatchTripLO.stop_sequence)
        ).all() if trip_ids else []
        scope = db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.dispatch_job_id == job.id)).all()
        lo_by_id = {lo.id: lo for lo in scope}
        joins_by_trip: dict[str, list[ManualDispatchTripLO]] = defaultdict(list)
        for row in joins:
            joins_by_trip[row.trip_id].append(row)
        trips_by_vehicle: dict[str, list[dict]] = defaultdict(list)
        for trip in trips:
            trips_by_vehicle[trip.dispatch_vehicle_id].append(ManualDispatchJobService._trip_payload(trip, joins_by_trip[trip.id], lo_by_id))
        vehicle_rows = []
        for vehicle in vehicles:
            vehicle_trips = trips_by_vehicle[vehicle.id]
            last_return = next((row["estimated_return_datetime"] for row in reversed(vehicle_trips) if row["estimated_return_datetime"]), None)
            vehicle_rows.append(
                {
                    "id": vehicle.id,
                    "mt_id": vehicle.mt_id,
                    "vehicle_registration": vehicle.vehicle_registration,
                    "vehicle_class": vehicle.vehicle_class,
                    "capacity_kl": vehicle.capacity_kl,
                    "mt_tags": vehicle.mt_tags or [],
                    "number_of_compartments": vehicle.number_of_compartments,
                    "initial_available_datetime": _iso(vehicle.initial_available_datetime),
                    "last_estimated_return_datetime": last_return,
                    "last_available_datetime": _iso(vehicle.last_available_datetime),
                    "trip_count": len(vehicle_trips),
                    "total_assigned_volume_kl": round(sum(row["total_volume_kl"] for row in vehicle_trips), 3),
                    "status": vehicle.status,
                    "can_add_trip": not vehicle_trips or (
                        vehicle_trips[-1]["status"] == "VALID" and bool(vehicle_trips[-1]["available_after_trip_datetime"])
                    ),
                    "trips": vehicle_trips,
                }
            )
        unassigned = [ManualDispatchJobService._lo_payload(lo) for lo in scope if lo.assignment_status != "ASSIGNED"]
        return {
            "job": ManualDispatchJobService._metadata(job, depot),
            "configuration": job.configuration_snapshot or {},
            "vehicles": vehicle_rows,
            "unassigned": {
                "count": len(unassigned),
                "volume_kl": round(sum(row["volume_kl"] for row in unassigned), 3),
                "spbu_count": len({row["spbu_id"] for row in unassigned}),
                "rows": unassigned,
            },
        }

    @staticmethod
    def _lo_payload(lo: ManualDispatchLoadingOrder) -> dict:
        return {
            "id": lo.id,
            "lo_id": lo.lo_id,
            "lo_number": lo.lo_number,
            "spbu_id": lo.spbu_id,
            "spbu_number": lo.spbu_number,
            "spbu_name": lo.spbu_name,
            "product_id": lo.product_id,
            "product_name": lo.product_name,
            "volume_kl": lo.volume_kl,
            "cluster_id": lo.cluster_id,
            "cluster_name": lo.cluster_name or "Unknown / Unclustered",
            "shift_id": lo.shift_id,
            "shift_name": lo.shift_name or "Unknown",
            "spbu_tags": lo.spbu_tags or [],
            "assignment_status": lo.assignment_status,
            "status_reason": lo.status_reason,
        }

    @staticmethod
    def _trip_payload(trip: ManualDispatchTrip, joins: list[ManualDispatchTripLO], lo_by_id: dict[str, ManualDispatchLoadingOrder]) -> dict:
        loading_orders = []
        for join in joins:
            lo = lo_by_id.get(join.manual_dispatch_lo_id)
            if not lo:
                continue
            loading_orders.append(
                {
                    **ManualDispatchJobService._lo_payload(lo),
                    "assignment_id": join.id,
                    "stop_sequence": join.stop_sequence,
                    "estimated_arrival_datetime": _iso(join.estimated_arrival_datetime),
                }
            )
        return {
            "id": trip.id,
            "trip_id": trip.id,
            "trip_sequence": trip.trip_sequence,
            "available_before_trip_datetime": _iso(trip.available_before_trip_datetime),
            "departure_datetime": _iso(trip.departure_datetime),
            "estimated_return_datetime": _iso(trip.estimated_return_datetime),
            "turnaround_duration_seconds": trip.turnaround_duration_seconds,
            "available_after_trip_datetime": _iso(trip.available_after_trip_datetime),
            "distance_meter": trip.distance_meter,
            "travel_duration_seconds": trip.travel_duration_seconds,
            "service_duration_seconds": trip.service_duration_seconds,
            "operational_buffer_seconds": trip.operational_buffer_seconds,
            "total_duration_seconds": trip.total_duration_seconds,
            "total_volume_kl": trip.total_volume_kl,
            "status": trip.status,
            "route_provider": trip.route_provider,
            "route_response_status": trip.route_response_status,
            "route_error_message": trip.route_error_message,
            "route_calculated_at": _iso(trip.route_calculated_at),
            "trip_version": trip.row_version,
            "total_lo": len(loading_orders),
            "total_spbu_stops": len({row["spbu_id"] for row in loading_orders}),
            "loading_orders": loading_orders,
        }


class ManualDispatchSnapshotService:
    @staticmethod
    def create(db: Session, payload: dict, *, actor: str = "local-user") -> dict:
        source_job = db.get(OptimizationJob, payload["source_job_id"])
        if not source_job:
            raise HTTPException(status_code=404, detail={"code": "PHASE7_JOB_NOT_FOUND", "message": "Selected Phase 7 Job was not found."})
        operational_date = payload["operational_date"]
        if isinstance(operational_date, str):
            operational_date = date.fromisoformat(operational_date)
        if source_job.depot_id != payload["depot_id"] or source_job.operating_date != operational_date:
            raise HTTPException(status_code=422, detail={"code": "SOURCE_SCOPE_MISMATCH", "message": "Selected Phase 7 Job does not belong to the depot and operational date."})
        route_id = payload["source_route_id"]
        route_version = None if route_id == "PHASE6" else db.get(RouteVersion, route_id)
        if route_id != "PHASE6" and (not route_version or route_version.job_id != source_job.job_id):
            raise HTTPException(status_code=404, detail={"code": "SOURCE_ROUTE_NOT_FOUND", "message": "Selected source route was not found for the Phase 7 Job."})
        source_run = db.get(PredictionRun, source_job.source_prediction_run_id) if source_job.source_prediction_run_id else None
        if route_id == "PHASE6" and not source_run:
            raise HTTPException(status_code=422, detail={"code": "PHASE6_SOURCE_NOT_AVAILABLE", "message": "The Phase 7 Job has no loaded Phase 6 Prediction Run."})
        depot = db.get(MasterDepot, source_job.depot_id)
        config = configuration_snapshot(get_google_routes_configuration(db))
        job = ManualDispatchJob(
            id=_id("MDJ"),
            job_id=ManualDispatchSnapshotService._next_job_no(db),
            job_name=str(payload["job_name"]).strip(),
            depot_id=source_job.depot_id,
            operational_date=operational_date,
            source_phase="PHASE_6" if route_id == "PHASE6" else "PHASE_7",
            source_job_id=source_job.job_id,
            source_run_id=source_run.id if source_run else None,
            source_route_id=route_id,
            source_route_version="Phase 6 Prediction / Warm Start" if route_id == "PHASE6" else route_version.version_label,
            source_created_at=source_run.created_at if route_id == "PHASE6" else route_version.created_at,
            dispatch_version=1,
            status="DRAFT",
            configuration_snapshot={
                **config,
                "depot_timezone": depot.timezone if depot and depot.timezone else "Asia/Jakarta",
                "depot_operational_start": (depot.depot_operational_start if depot else time(0, 0)).isoformat(),
                "depot_operational_end": (depot.depot_operational_end if depot else time(23, 59)).isoformat(),
                "operational_buffer_minutes": int(payload.get("operational_buffer_minutes") or 0),
                "unassigned_lo_blocks_finalization": False,
            },
            source_lineage_snapshot={
                "phase6_run_id": source_job.source_prediction_run_id,
                "phase7_job_id": source_job.job_id,
                "phase7_job_no": source_job.job_no,
                "selected_source_route_id": route_id,
                "selected_source_route_version": "PHASE6" if route_id == "PHASE6" else route_version.version_label,
            },
            created_by=actor,
        )
        db.add(job)
        db.flush()
        ManualDispatchSnapshotService._copy_scope(db, job, source_job, source_run, route_version)
        job.status = "IN_PROGRESS"
        DispatchAuditService.log(db, job, actor, "JOB_CREATED", "JOB", job.id, new=ManualDispatchJobService._metadata(job, depot))
        DispatchAuditService.log(
            db,
            job,
            actor,
            "SOURCE_ROUTE_LOADED",
            "SOURCE_ROUTE",
            route_id,
            metadata=job.source_lineage_snapshot,
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail={"code": "MANUAL_DISPATCH_CREATE_CONFLICT", "message": "Manual Dispatch snapshot could not be created atomically."}) from exc
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def _next_job_no(db: Session) -> str:
        prefix = f"MD-{_now().strftime('%Y%m%d')}"
        count = db.scalar(select(func.count()).select_from(ManualDispatchJob).where(ManualDispatchJob.job_id.like(f"{prefix}%"))) or 0
        return f"{prefix}-{count + 1:04d}"

    @staticmethod
    def _local_start(job: ManualDispatchJob, depot: MasterDepot | None) -> datetime:
        tz = ZoneInfo((depot.timezone if depot and depot.timezone else "Asia/Jakarta"))
        return datetime.combine(job.operational_date, depot.depot_operational_start if depot else time(0, 0), tzinfo=tz)

    @staticmethod
    def _copy_scope(
        db: Session,
        job: ManualDispatchJob,
        source_job: OptimizationJob,
        source_run: PredictionRun | None,
        route_version: RouteVersion | None,
    ) -> None:
        depot = db.get(MasterDepot, job.depot_id)
        default_start = ManualDispatchSnapshotService._local_start(job, depot)
        vehicle_states = db.scalars(select(VehicleOperationalState).where(VehicleOperationalState.job_id == source_job.job_id)).all()
        route_trips = db.scalars(
            select(RouteVersionTrip).where(RouteVersionTrip.route_version_id == route_version.route_version_id)
        ).all() if route_version else []
        p6_trips = db.scalars(select(PredictionTrip).where(PredictionTrip.prediction_run_id == source_run.id)).all() if source_run else []
        source_vehicle_ids = {row.mt_id for row in vehicle_states}
        source_vehicle_ids.update(row.vehicle_id for row in route_trips if row.vehicle_id)
        source_vehicle_ids.update(row.vehicle_id for row in p6_trips if row.vehicle_id)
        master_mts = {row.mt_id: row for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(source_vehicle_ids))).all()} if source_vehicle_ids else {}
        state_by_mt = {row.mt_id: row for row in vehicle_states}
        min_ready: dict[str, datetime] = {}
        for trip in route_trips:
            if trip.vehicle_id and (trip.vehicle_id not in min_ready or _aware(trip.vehicle_ready_at_depot) < _aware(min_ready[trip.vehicle_id])):
                min_ready[trip.vehicle_id] = trip.vehicle_ready_at_depot
        for trip in p6_trips:
            if trip.vehicle_id and (trip.vehicle_id not in min_ready or _aware(trip.planned_start_datetime) < _aware(min_ready[trip.vehicle_id])):
                min_ready[trip.vehicle_id] = trip.planned_start_datetime
        vehicle_map: dict[str, ManualDispatchVehicle] = {}
        for mt_id in sorted(source_vehicle_ids):
            mt = master_mts.get(mt_id)
            state = state_by_mt.get(mt_id)
            initial = min_ready.get(mt_id) or (state.effective_eta_depot if state else None) or default_start
            vehicle = ManualDispatchVehicle(
                id=_id("MDV"),
                dispatch_job_id=job.id,
                mt_id=mt_id,
                vehicle_registration=(state.registration_snapshot if state else None) or (mt.vehicle_registration if mt else mt_id),
                vehicle_class=(state.vehicle_class if state else None) or (mt.vehicle_type_tag if mt else None),
                capacity_kl=_capacity(mt, state.capacity_kl if state else None),
                mt_tags=(state.tag_snapshot if state else []) or ManualDispatchSnapshotService._tag_labels(db, "MT", mt_id),
                number_of_compartments=(state.number_of_compartments if state else 0) or (mt.number_of_compartments if mt else 0) or 0,
                compartment_configuration=(state.compartment_configuration if state else []) or [],
                initial_available_datetime=initial,
                last_available_datetime=initial,
                status="IDLE",
            )
            db.add(vehicle)
            vehicle_map[mt_id] = vehicle
        db.flush()

        lo_states = db.scalars(select(LOOperationalState).where(LOOperationalState.job_id == source_job.job_id)).all()
        p6_lines = db.execute(
            select(PredictionShipmentLine, PredictionShipment)
            .join(PredictionShipment, PredictionShipment.id == PredictionShipmentLine.prediction_shipment_id)
            .where(PredictionShipmentLine.prediction_run_id == source_run.id)
        ).all() if source_run else []
        line_context = {line.loading_order_no: (line, shipment) for line, shipment in p6_lines}
        state_by_lo = {row.loading_order_id: row for row in lo_states}
        all_lo_ids = set(state_by_lo) | set(line_context)
        model_id = source_run.model_id if source_run else None
        clusters = {
            row.spbu_id: row
            for row in db.scalars(select(MLSPBUClusterAssignment).where(MLSPBUClusterAssignment.model_id == model_id)).all()
        } if model_id else {}
        product_ids = {line.product_id for line, _ in line_context.values() if line.product_id}
        products = {row.product_id: row for row in db.scalars(select(MasterProduct).where(MasterProduct.product_id.in_(product_ids))).all()} if product_ids else {}
        scope_map: dict[str, ManualDispatchLoadingOrder] = {}
        for lo_id in sorted(all_lo_ids):
            state = state_by_lo.get(lo_id)
            line, shipment = line_context.get(lo_id, (None, None))
            spbu_id = (state.spbu_id if state else None) or (line.spbu_id if line else None)
            if not spbu_id:
                continue
            spbu = db.get(MasterSPBU, spbu_id)
            product_id = (state.product_id if state else None) or (line.product_id if line else None)
            cluster = clusters.get(spbu_id)
            lo = ManualDispatchLoadingOrder(
                id=_id("MDLO"),
                dispatch_job_id=job.id,
                lo_id=lo_id,
                lo_number=lo_id,
                spbu_id=spbu_id,
                spbu_number=(spbu.spbu_code if spbu else None) or (line.spbu_no if line else None),
                spbu_name=(state.spbu_name_snapshot if state else None) or (spbu.spbu_name if spbu else None),
                product_id=product_id,
                product_name=(state.product_name_snapshot if state else None) or (line.product_name if line else None) or (products.get(product_id).product_name if product_id in products else None),
                volume_kl=float((state.volume_kl if state else None) or (line.order_quantity_kl if line else None) or 0),
                cluster_id=str(cluster.cluster_id) if cluster and cluster.cluster_id is not None else None,
                cluster_name=cluster.cluster_label if cluster else None,
                shift_id=shipment.shift_id if shipment else None,
                shift_name=shipment.shift_name if shipment else None,
                spbu_tags=ManualDispatchSnapshotService._tag_labels(db, "SPBU", spbu_id),
                assignment_status="UNASSIGNED",
                source_snapshot={
                    "phase6_predicted_shipment_id": state.phase6_predicted_shipment_id if state else (shipment.predicted_shipment_id if shipment else None),
                    "phase6_model_id": model_id,
                    "source_phase": job.source_phase,
                },
            )
            db.add(lo)
            scope_map[lo_id] = lo
        db.flush()
        if route_version:
            ManualDispatchSnapshotService._copy_phase7_trips(db, job, route_version, route_trips, vehicle_map, scope_map)
        elif source_run:
            ManualDispatchSnapshotService._copy_phase6_trips(db, job, source_run, p6_trips, vehicle_map, scope_map, p6_lines)

    @staticmethod
    def _tag_labels(db: Session, entity_type: str, entity_id: str) -> list[str]:
        bridge = BridgeMTTag if entity_type == "MT" else BridgeSPBUTag
        id_column = bridge.mt_id if entity_type == "MT" else bridge.spbu_id
        tag_ids = db.scalars(select(bridge.tag_id).where(id_column == entity_id)).all()
        if not tag_ids:
            return []
        return [row.tag_value for row in db.scalars(select(MasterTag).where(MasterTag.tag_id.in_(tag_ids)).order_by(MasterTag.tag_value)).all()]

    @staticmethod
    def _copy_phase7_trips(
        db: Session,
        job: ManualDispatchJob,
        version: RouteVersion,
        source_trips: list[RouteVersionTrip],
        vehicle_map: dict[str, ManualDispatchVehicle],
        scope_map: dict[str, ManualDispatchLoadingOrder],
    ) -> None:
        source_trips = sorted(source_trips, key=lambda row: (row.vehicle_id, row.trip_number))
        trip_map: dict[str, ManualDispatchTrip] = {}
        turn_seconds = int((job.configuration_snapshot or {}).get("default_turnaround_buffer_minutes", 0)) * 60
        for source in source_trips:
            vehicle = vehicle_map.get(source.vehicle_id)
            if not vehicle:
                continue
            available_after = _aware(source.estimated_return_depot) + timedelta(seconds=turn_seconds)
            trip = ManualDispatchTrip(
                id=_id("MDT"),
                dispatch_vehicle_id=vehicle.id,
                trip_sequence=source.trip_number,
                available_before_trip_datetime=source.vehicle_ready_at_depot,
                departure_datetime=source.gate_out,
                estimated_return_datetime=source.estimated_return_depot,
                turnaround_duration_seconds=turn_seconds,
                available_after_trip_datetime=available_after,
                distance_meter=source.distance_meters,
                travel_duration_seconds=source.driving_seconds,
                service_duration_seconds=source.service_seconds,
                total_duration_seconds=max(0, round((_aware(source.estimated_return_depot) - _aware(source.gate_out)).total_seconds())),
                status="VALID",
                route_provider=source.route_geometry_source or "PHASE_7_SNAPSHOT",
                route_response_status="SOURCE_SNAPSHOT",
                route_geometry=source.route_geometry or [],
                route_calculated_at=version.created_at,
            )
            db.add(trip)
            trip_map[source.route_version_trip_id] = trip
            vehicle.last_available_datetime = max(_aware(vehicle.last_available_datetime), available_after)
        db.flush()
        assignments = db.scalars(
            select(RouteVersionLOAssignment)
            .where(RouteVersionLOAssignment.route_version_id == version.route_version_id)
            .order_by(RouteVersionLOAssignment.route_version_trip_id, RouteVersionLOAssignment.stop_sequence)
        ).all()
        for source in assignments:
            trip = trip_map.get(source.route_version_trip_id or "")
            lo = scope_map.get(source.loading_order_id)
            if not trip or not lo:
                continue
            db.add(
                ManualDispatchTripLO(
                    id=_id("MDTL"),
                    dispatch_job_id=job.id,
                    trip_id=trip.id,
                    manual_dispatch_lo_id=lo.id,
                    stop_sequence=int(source.stop_sequence or 1),
                    estimated_arrival_datetime=source.eta,
                )
            )
            lo.assignment_status = "ASSIGNED"
            trip.total_volume_kl += lo.volume_kl
        for vehicle in vehicle_map.values():
            vehicle.status = "DISPATCHED" if any(t.dispatch_vehicle_id == vehicle.id for t in trip_map.values()) else "IDLE"

    @staticmethod
    def _copy_phase6_trips(
        db: Session,
        job: ManualDispatchJob,
        run: PredictionRun,
        source_trips: list[PredictionTrip],
        vehicle_map: dict[str, ManualDispatchVehicle],
        scope_map: dict[str, ManualDispatchLoadingOrder],
        p6_lines: list[tuple[PredictionShipmentLine, PredictionShipment]],
    ) -> None:
        lines_by_shipment: dict[str, list[PredictionShipmentLine]] = defaultdict(list)
        for line, shipment in p6_lines:
            lines_by_shipment[shipment.id].append(line)
        turn_default = int((job.configuration_snapshot or {}).get("default_turnaround_buffer_minutes", 0)) * 60
        for source in sorted(source_trips, key=lambda row: (row.vehicle_id or "", row.trip_number or 0)):
            vehicle = vehicle_map.get(source.vehicle_id or "")
            if not vehicle or source.assignment_status != "ASSIGNED":
                continue
            return_at = source.estimated_return_datetime
            turn_seconds = int(source.turnaround_buffer_seconds if source.turnaround_buffer_seconds is not None else turn_default)
            available_after = source.next_available_datetime or (_aware(return_at) + timedelta(seconds=turn_seconds) if return_at else None)
            trip = ManualDispatchTrip(
                id=_id("MDT"),
                dispatch_vehicle_id=vehicle.id,
                trip_sequence=int(source.trip_number or 1),
                available_before_trip_datetime=source.planned_start_datetime,
                departure_datetime=source.predicted_departure_datetime or source.planned_start_datetime,
                estimated_return_datetime=return_at,
                turnaround_duration_seconds=turn_seconds,
                available_after_trip_datetime=available_after,
                distance_meter=source.route_distance_meters,
                travel_duration_seconds=source.route_duration_seconds,
                service_duration_seconds=source.service_duration_seconds or 0,
                total_duration_seconds=source.total_cycle_duration_seconds,
                status="VALID" if return_at and available_after else "WARNING",
                route_provider=source.routing_provider or source.route_estimation_source or "PHASE_6_SNAPSHOT",
                route_response_status="SOURCE_SNAPSHOT" if return_at else "SOURCE_ROUTE_UNCALCULATED",
                route_geometry=source.route_geometry or [],
                route_calculated_at=run.completed_at,
            )
            db.add(trip)
            db.flush()
            stop_by_spbu = {spbu_id: index + 1 for index, spbu_id in enumerate(source.estimated_visit_sequence or [])}
            for line in lines_by_shipment.get(source.prediction_shipment_id, []):
                lo = scope_map.get(line.loading_order_no)
                if not lo:
                    continue
                db.add(
                    ManualDispatchTripLO(
                        id=_id("MDTL"),
                        dispatch_job_id=job.id,
                        trip_id=trip.id,
                        manual_dispatch_lo_id=lo.id,
                        stop_sequence=stop_by_spbu.get(line.spbu_id, len(stop_by_spbu) + 1),
                    )
                )
                lo.assignment_status = "ASSIGNED"
                trip.total_volume_kl += lo.volume_kl
            vehicle.last_available_datetime = available_after or vehicle.last_available_datetime
            vehicle.status = "DISPATCHED"


class DispatchEligibilityService:
    @staticmethod
    def compatibility(db: Session, job: ManualDispatchJob, vehicle: ManualDispatchVehicle, lo: ManualDispatchLoadingOrder) -> dict:
        mt = db.get(MasterMT, vehicle.mt_id)
        reasons: list[str] = []
        if lo.dispatch_job_id != job.id:
            reasons.append("LO does not belong to this Manual Dispatch Job.")
        if not mt or mt.active_status != "ACTIVE":
            reasons.append("Selected MT is not active in canonical master data.")
        if mt and mt.depot_id != job.depot_id:
            reasons.append("Selected MT does not belong to this depot.")
        result = evaluate_mt_spbu_compatibility(db, vehicle.mt_id, lo.spbu_id, product_id=lo.product_id)
        if not result["compatible"]:
            missing_tags = sorted(set(lo.spbu_tags or []) - set(vehicle.mt_tags or []))
            if "PROJECT_TAGS" in result.get("failed_rules", []) and missing_tags:
                reasons.append(
                    f"SPBU {lo.spbu_number or lo.spbu_id} requires tag {', '.join(missing_tags)}, but MT {vehicle.vehicle_registration or vehicle.mt_id} does not contain it."
                )
            if "VEHICLE_TYPE" in result.get("failed_rules", []):
                reasons.append("MT vehicle class exceeds the SPBU vehicle-class limit.")
            if "DEPOT" in result.get("failed_rules", []):
                reasons.append("MT and SPBU do not share the same depot context.")
            if not reasons:
                reasons.append(result.get("explanation") or "SPBU is not compatible with the selected MT.")
        return {"eligible": not reasons, "reasons": reasons, "compatibility": result}

    @staticmethod
    def eligible_los(
        db: Session,
        dispatch_job_id: str,
        vehicle_id: str,
        *,
        trip_id: str | None = None,
        include_ineligible: bool = False,
        search: str | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        vehicle = _vehicle_or_404(db, job, vehicle_id)
        trip = None
        current_volume = 0.0
        if trip_id:
            trip, trip_vehicle = _trip_context(db, job, trip_id)
            if trip_vehicle.id != vehicle.id:
                raise HTTPException(status_code=422, detail={"code": "TRIP_MT_MISMATCH", "message": "Trip does not belong to the selected MT."})
            current_volume = float(trip.total_volume_kl or 0)
        query = select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.dispatch_job_id == job.id)
        if search:
            token = f"%{search.strip()}%"
            query = query.where(
                or_(
                    ManualDispatchLoadingOrder.lo_number.ilike(token),
                    ManualDispatchLoadingOrder.spbu_number.ilike(token),
                    ManualDispatchLoadingOrder.spbu_name.ilike(token),
                    ManualDispatchLoadingOrder.product_name.ilike(token),
                )
            )
        rows = []
        for lo in db.scalars(query.order_by(ManualDispatchLoadingOrder.lo_number)).all():
            evaluation = DispatchEligibilityService.compatibility(db, job, vehicle, lo)
            reasons = list(evaluation["reasons"])
            existing = db.scalar(
                select(ManualDispatchTripLO).where(
                    ManualDispatchTripLO.dispatch_job_id == job.id,
                    ManualDispatchTripLO.manual_dispatch_lo_id == lo.id,
                )
            )
            if existing and existing.trip_id != trip_id:
                existing_trip = db.get(ManualDispatchTrip, existing.trip_id)
                existing_vehicle = db.get(ManualDispatchVehicle, existing_trip.dispatch_vehicle_id) if existing_trip else None
                reasons.append(
                    f"LO is already assigned to MT {existing_vehicle.vehicle_registration if existing_vehicle else '-'} Trip {existing_trip.trip_sequence if existing_trip else '-'} ."
                )
            if trip and current_volume + lo.volume_kl > vehicle.capacity_kl + 1e-6 and not existing:
                reasons.append(
                    f"Adding {lo.volume_kl:g} KL would exceed MT capacity {vehicle.capacity_kl:g} KL."
                )
            eligible = not reasons
            if eligible or include_ineligible:
                rows.append({**ManualDispatchJobService._lo_payload(lo), "eligible": eligible, "rejection_reasons": reasons})
        return {"vehicle_id": vehicle.id, "trip_id": trip_id, "eligible_count": sum(1 for row in rows if row["eligible"]), "rows": rows}


class DispatchTimelineService:
    @staticmethod
    def invalidate_downstream(
        db: Session,
        job: ManualDispatchJob,
        vehicle: ManualDispatchVehicle,
        changed_sequence: int,
        *,
        next_available: datetime | None,
        actor: str,
    ) -> int:
        downstream = db.scalars(
            select(ManualDispatchTrip)
            .where(
                ManualDispatchTrip.dispatch_vehicle_id == vehicle.id,
                ManualDispatchTrip.trip_sequence > changed_sequence,
            )
            .order_by(ManualDispatchTrip.trip_sequence)
        ).all()
        for index, trip in enumerate(downstream):
            old = {
                "available_before_trip_datetime": _iso(trip.available_before_trip_datetime),
                "status": trip.status,
                "estimated_return_datetime": _iso(trip.estimated_return_datetime),
            }
            if index == 0 and next_available:
                trip.available_before_trip_datetime = next_available
            trip.status = "NEEDS_RECALCULATION"
            trip.estimated_return_datetime = None
            trip.available_after_trip_datetime = None
            trip.route_calculated_at = None
            trip.route_response_status = "UPSTREAM_TIMELINE_CHANGED"
            trip.route_error_message = f"Trip {trip.trip_sequence} must be recalculated because an earlier trip timing changed."
            trip.row_version += 1
            DispatchAuditService.log(
                db,
                job,
                actor,
                "AVAILABILITY_CHANGED",
                "TRIP",
                trip.id,
                old=old,
                new={"available_before_trip_datetime": _iso(trip.available_before_trip_datetime), "status": trip.status},
                metadata={"mt_id": vehicle.mt_id, "vehicle_registration": vehicle.vehicle_registration, "trip_sequence": trip.trip_sequence},
            )
        return len(downstream)

    @staticmethod
    def refresh_vehicle(db: Session, vehicle: ManualDispatchVehicle) -> None:
        trips = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id == vehicle.id)
            .order_by(ManualDispatchTrip.trip_sequence)
        ).all()
        valid_available = [trip.available_after_trip_datetime for trip in trips if trip.available_after_trip_datetime]
        vehicle.last_available_datetime = valid_available[-1] if valid_available else vehicle.initial_available_datetime
        vehicle.status = "DISPATCHED" if any((trip.total_volume_kl or 0) > 0 for trip in trips) else "IDLE"


class DispatchTripService:
    @staticmethod
    def create_trip(
        db: Session,
        dispatch_job_id: str,
        vehicle_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        vehicle = _vehicle_or_404(db, job, vehicle_id)
        trips = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id == vehicle.id)
            .order_by(ManualDispatchTrip.trip_sequence)
        ).all()
        previous = trips[-1] if trips else None
        if previous and (previous.status != "VALID" or not previous.available_after_trip_datetime):
            raise HTTPException(
                status_code=409,
                detail={"code": "PREVIOUS_TRIP_NOT_VALID", "message": f"Trip {previous.trip_sequence} must be successfully applied before adding another trip."},
            )
        available = previous.available_after_trip_datetime if previous else vehicle.initial_available_datetime
        trip = ManualDispatchTrip(
            id=_id("MDT"),
            dispatch_vehicle_id=vehicle.id,
            trip_sequence=len(trips) + 1,
            available_before_trip_datetime=available,
            departure_datetime=available,
            turnaround_duration_seconds=int((job.configuration_snapshot or {}).get("default_turnaround_buffer_minutes", 0)) * 60,
            operational_buffer_seconds=int((job.configuration_snapshot or {}).get("operational_buffer_minutes", 0)) * 60,
            status="DRAFT",
        )
        db.add(trip)
        _touch(job)
        DispatchAuditService.log(
            db,
            job,
            actor,
            "TRIP_CREATED",
            "TRIP",
            trip.id,
            new={"trip_sequence": trip.trip_sequence, "available_before_trip_datetime": _iso(available)},
            metadata={"mt_id": vehicle.mt_id, "vehicle_registration": vehicle.vehicle_registration, "trip_sequence": trip.trip_sequence},
        )
        db.commit()
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def update_trip(
        db: Session,
        dispatch_job_id: str,
        trip_id: str,
        payload: dict,
        *,
        actor: str,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, payload.get("expected_job_version"))
        trip, vehicle = _trip_context(db, job, trip_id)
        if payload.get("expected_trip_version") is not None and payload["expected_trip_version"] != trip.row_version:
            raise HTTPException(status_code=409, detail={"code": "TRIP_VERSION_CONFLICT", "message": "Trip changed after it was loaded. Refresh before editing."})
        old = {"departure_datetime": _iso(trip.departure_datetime), "status": trip.status}
        if "departure_datetime" in payload:
            departure = payload["departure_datetime"]
            if isinstance(departure, str):
                departure = datetime.fromisoformat(departure.replace("Z", "+00:00"))
            if _aware(departure) < _aware(trip.available_before_trip_datetime):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "TRIP_BEFORE_MT_AVAILABLE",
                        "message": f"Trip {trip.trip_sequence} cannot start at {_iso(departure)} because the MT is only available at {_iso(trip.available_before_trip_datetime)}.",
                    },
                )
            trip.departure_datetime = departure
        trip.status = "MODIFIED"
        trip.row_version += 1
        DispatchTimelineService.invalidate_downstream(db, job, vehicle, trip.trip_sequence, next_available=None, actor=actor)
        _touch(job)
        DispatchAuditService.log(db, job, actor, "TRIP_MODIFIED", "TRIP", trip.id, old=old, new={"departure_datetime": _iso(trip.departure_datetime), "status": trip.status})
        db.commit()
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def add_lo(
        db: Session,
        dispatch_job_id: str,
        trip_id: str,
        lo_scope_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        trip, vehicle = _trip_context(db, job, trip_id)
        lo = db.get(ManualDispatchLoadingOrder, lo_scope_id)
        if not lo or lo.dispatch_job_id != job.id:
            raise HTTPException(status_code=404, detail={"code": "LO_NOT_IN_SCOPE", "message": "LO does not exist in this Manual Dispatch Job planning scope."})
        existing = db.scalar(select(ManualDispatchTripLO).where(ManualDispatchTripLO.dispatch_job_id == job.id, ManualDispatchTripLO.manual_dispatch_lo_id == lo.id))
        if existing:
            current_trip = db.get(ManualDispatchTrip, existing.trip_id)
            current_vehicle = db.get(ManualDispatchVehicle, current_trip.dispatch_vehicle_id) if current_trip else None
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LO_ALREADY_ASSIGNED",
                    "message": f"LO is already assigned to MT {current_vehicle.vehicle_registration if current_vehicle else '-'} Trip {current_trip.trip_sequence if current_trip else '-'}.",
                },
            )
        evaluation = DispatchEligibilityService.compatibility(db, job, vehicle, lo)
        if not evaluation["eligible"]:
            raise HTTPException(status_code=422, detail={"code": "LO_MT_INELIGIBLE", "message": evaluation["reasons"][0], "reasons": evaluation["reasons"]})
        current_volume = db.scalar(
            select(func.coalesce(func.sum(ManualDispatchLoadingOrder.volume_kl), 0.0))
            .select_from(ManualDispatchTripLO)
            .join(ManualDispatchLoadingOrder, ManualDispatchLoadingOrder.id == ManualDispatchTripLO.manual_dispatch_lo_id)
            .where(ManualDispatchTripLO.trip_id == trip.id)
        ) or 0.0
        if float(current_volume) + lo.volume_kl > vehicle.capacity_kl + 1e-6:
            raise HTTPException(status_code=422, detail={"code": "TRIP_CAPACITY_EXCEEDED", "message": f"Trip volume would exceed MT capacity {vehicle.capacity_kl:g} KL."})
        max_stop = db.scalar(select(func.max(ManualDispatchTripLO.stop_sequence)).where(ManualDispatchTripLO.trip_id == trip.id)) or 0
        db.add(ManualDispatchTripLO(id=_id("MDTL"), dispatch_job_id=job.id, trip_id=trip.id, manual_dispatch_lo_id=lo.id, stop_sequence=max_stop + 1))
        lo.assignment_status = "ASSIGNED"
        lo.status_reason = None
        trip.total_volume_kl = float(current_volume) + lo.volume_kl
        trip.status = "MODIFIED"
        trip.row_version += 1
        DispatchTimelineService.invalidate_downstream(db, job, vehicle, trip.trip_sequence, next_available=None, actor=actor)
        _touch(job)
        DispatchAuditService.log(
            db,
            job,
            actor,
            "LO_ADDED",
            "LO",
            lo.lo_id,
            new={"trip_id": trip.id, "mt_id": vehicle.mt_id, "stop_sequence": max_stop + 1},
            metadata={"lo_id": lo.lo_id, "lo_number": lo.lo_number, "mt_id": vehicle.mt_id, "vehicle_registration": vehicle.vehicle_registration, "trip_sequence": trip.trip_sequence},
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail={"code": "LO_ALREADY_ASSIGNED", "message": "LO was assigned by another dispatcher. Refresh the workspace."}) from exc
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def remove_lo(
        db: Session,
        dispatch_job_id: str,
        trip_id: str,
        lo_scope_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
        reason: str | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        trip, vehicle = _trip_context(db, job, trip_id)
        assignment = db.scalar(
            select(ManualDispatchTripLO).where(
                ManualDispatchTripLO.trip_id == trip.id,
                ManualDispatchTripLO.manual_dispatch_lo_id == lo_scope_id,
            )
        )
        lo = db.get(ManualDispatchLoadingOrder, lo_scope_id)
        if not assignment or not lo:
            raise HTTPException(status_code=404, detail={"code": "LO_ASSIGNMENT_NOT_FOUND", "message": "LO is not assigned to this trip."})
        db.delete(assignment)
        db.flush()
        lo.assignment_status = "UNASSIGNED"
        lo.status_reason = reason or "Removed from trip by dispatcher"
        DispatchTripService._refresh_trip_volume(db, trip)
        DispatchTripService._resequence_stops(db, trip.id)
        trip.status = "MODIFIED"
        trip.row_version += 1
        DispatchTimelineService.invalidate_downstream(db, job, vehicle, trip.trip_sequence, next_available=None, actor=actor)
        _touch(job)
        DispatchAuditService.log(
            db,
            job,
            actor,
            "LO_REMOVED",
            "LO",
            lo.lo_id,
            old={"trip_id": trip.id, "mt_id": vehicle.mt_id},
            new={"assignment_status": "UNASSIGNED"},
            metadata={"lo_id": lo.lo_id, "lo_number": lo.lo_number, "mt_id": vehicle.mt_id, "vehicle_registration": vehicle.vehicle_registration, "trip_sequence": trip.trip_sequence},
            reason=reason,
        )
        db.commit()
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def move_lo(
        db: Session,
        dispatch_job_id: str,
        lo_scope_id: str,
        destination_trip_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
        reason: str | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        lo = db.get(ManualDispatchLoadingOrder, lo_scope_id)
        if not lo or lo.dispatch_job_id != job.id:
            raise HTTPException(status_code=404, detail={"code": "LO_NOT_IN_SCOPE", "message": "LO is not in this job."})
        destination, destination_vehicle = _trip_context(db, job, destination_trip_id)
        evaluation = DispatchEligibilityService.compatibility(db, job, destination_vehicle, lo)
        if not evaluation["eligible"]:
            raise HTTPException(status_code=422, detail={"code": "LO_MT_INELIGIBLE", "message": evaluation["reasons"][0], "reasons": evaluation["reasons"]})
        assignment = db.scalar(select(ManualDispatchTripLO).where(ManualDispatchTripLO.dispatch_job_id == job.id, ManualDispatchTripLO.manual_dispatch_lo_id == lo.id))
        source = db.get(ManualDispatchTrip, assignment.trip_id) if assignment else None
        source_vehicle = db.get(ManualDispatchVehicle, source.dispatch_vehicle_id) if source else None
        if source and source.id == destination.id:
            return ManualDispatchJobService.detail(db, job.id)
        destination_volume = db.scalar(
            select(func.coalesce(func.sum(ManualDispatchLoadingOrder.volume_kl), 0.0))
            .select_from(ManualDispatchTripLO)
            .join(ManualDispatchLoadingOrder, ManualDispatchLoadingOrder.id == ManualDispatchTripLO.manual_dispatch_lo_id)
            .where(ManualDispatchTripLO.trip_id == destination.id)
        ) or 0.0
        if float(destination_volume) + lo.volume_kl > destination_vehicle.capacity_kl + 1e-6:
            raise HTTPException(status_code=422, detail={"code": "TRIP_CAPACITY_EXCEEDED", "message": f"Moving LO would exceed MT capacity {destination_vehicle.capacity_kl:g} KL."})
        if assignment:
            assignment.trip_id = destination.id
            assignment.stop_sequence = (db.scalar(select(func.max(ManualDispatchTripLO.stop_sequence)).where(ManualDispatchTripLO.trip_id == destination.id)) or 0) + 1
            assignment.estimated_arrival_datetime = None
        else:
            assignment = ManualDispatchTripLO(
                id=_id("MDTL"), dispatch_job_id=job.id, trip_id=destination.id, manual_dispatch_lo_id=lo.id,
                stop_sequence=(db.scalar(select(func.max(ManualDispatchTripLO.stop_sequence)).where(ManualDispatchTripLO.trip_id == destination.id)) or 0) + 1,
            )
            db.add(assignment)
        lo.assignment_status = "ASSIGNED"
        lo.status_reason = None
        db.flush()
        if source:
            DispatchTripService._refresh_trip_volume(db, source)
            DispatchTripService._resequence_stops(db, source.id)
            source.status = "MODIFIED"
            source.row_version += 1
            DispatchTimelineService.invalidate_downstream(db, job, source_vehicle, source.trip_sequence, next_available=None, actor=actor)
        DispatchTripService._refresh_trip_volume(db, destination)
        destination.status = "MODIFIED"
        destination.row_version += 1
        DispatchTimelineService.invalidate_downstream(db, job, destination_vehicle, destination.trip_sequence, next_available=None, actor=actor)
        _touch(job)
        source_label = f"{source_vehicle.vehicle_registration} Trip {source.trip_sequence}" if source and source_vehicle else "UNASSIGNED"
        destination_label = f"{destination_vehicle.vehicle_registration} Trip {destination.trip_sequence}"
        DispatchAuditService.log(
            db,
            job,
            actor,
            "LO_MOVED",
            "LO",
            lo.lo_id,
            old={"trip_id": source.id if source else None, "mt_id": source_vehicle.mt_id if source_vehicle else None},
            new={"trip_id": destination.id, "mt_id": destination_vehicle.mt_id},
            metadata={"lo_id": lo.lo_id, "lo_number": lo.lo_number, "source_label": source_label, "destination_label": destination_label},
            reason=reason,
        )
        db.commit()
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def reorder_los(
        db: Session,
        dispatch_job_id: str,
        trip_id: str,
        lo_scope_ids: list[str],
        *,
        actor: str,
        expected_job_version: int | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        trip, vehicle = _trip_context(db, job, trip_id)
        assignments = db.scalars(select(ManualDispatchTripLO).where(ManualDispatchTripLO.trip_id == trip.id)).all()
        by_lo = {row.manual_dispatch_lo_id: row for row in assignments}
        if set(lo_scope_ids) != set(by_lo):
            raise HTTPException(status_code=422, detail={"code": "INVALID_STOP_SEQUENCE", "message": "Stop order must contain every LO assigned to the trip exactly once."})
        old = {row.manual_dispatch_lo_id: row.stop_sequence for row in assignments}
        # Temporary negative values keep any future unique stop constraint safe.
        for index, lo_id in enumerate(lo_scope_ids, start=1):
            by_lo[lo_id].stop_sequence = -index
        db.flush()
        for index, lo_id in enumerate(lo_scope_ids, start=1):
            by_lo[lo_id].stop_sequence = index
            by_lo[lo_id].estimated_arrival_datetime = None
        trip.status = "MODIFIED"
        trip.row_version += 1
        DispatchTimelineService.invalidate_downstream(db, job, vehicle, trip.trip_sequence, next_available=None, actor=actor)
        _touch(job)
        DispatchAuditService.log(db, job, actor, "TRIP_SEQUENCE_CHANGED", "TRIP", trip.id, old=old, new={lo_id: index + 1 for index, lo_id in enumerate(lo_scope_ids)})
        db.commit()
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def delete_trip(
        db: Session,
        dispatch_job_id: str,
        trip_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
        reason: str | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        trip, vehicle = _trip_context(db, job, trip_id)
        removed_sequence = trip.trip_sequence
        assignments = db.scalars(select(ManualDispatchTripLO).where(ManualDispatchTripLO.trip_id == trip.id)).all()
        lo_ids = [row.manual_dispatch_lo_id for row in assignments]
        los = db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.id.in_(lo_ids))).all() if lo_ids else []
        lo_numbers = [lo.lo_number for lo in los]
        for lo in los:
            lo.assignment_status = "UNASSIGNED"
            lo.status_reason = f"Trip {removed_sequence} deleted"
        db.delete(trip)
        db.flush()
        remaining = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id == vehicle.id)
            .order_by(ManualDispatchTrip.trip_sequence)
        ).all()
        for index, row in enumerate(remaining, start=1):
            row.trip_sequence = -index
        db.flush()
        for index, row in enumerate(remaining, start=1):
            row.trip_sequence = index
            if index >= removed_sequence:
                row.status = "NEEDS_RECALCULATION"
                row.estimated_return_datetime = None
                row.available_after_trip_datetime = None
                row.route_calculated_at = None
                row.route_response_status = "UPSTREAM_TRIP_DELETED"
                row.row_version += 1
                previous = remaining[index - 2] if index > 1 else None
                if previous and previous.available_after_trip_datetime:
                    row.available_before_trip_datetime = previous.available_after_trip_datetime
        DispatchTimelineService.refresh_vehicle(db, vehicle)
        _touch(job)
        DispatchAuditService.log(
            db,
            job,
            actor,
            "TRIP_DELETED",
            "TRIP",
            trip_id,
            old={"trip_sequence": removed_sequence, "lo_numbers": lo_numbers},
            metadata={"mt_id": vehicle.mt_id, "vehicle_registration": vehicle.vehicle_registration, "trip_sequence": removed_sequence, "unassigned_lo": lo_numbers},
            reason=reason,
        )
        db.commit()
        return ManualDispatchJobService.detail(db, job.id)

    @staticmethod
    def _refresh_trip_volume(db: Session, trip: ManualDispatchTrip) -> None:
        trip.total_volume_kl = float(
            db.scalar(
                select(func.coalesce(func.sum(ManualDispatchLoadingOrder.volume_kl), 0.0))
                .select_from(ManualDispatchTripLO)
                .join(ManualDispatchLoadingOrder, ManualDispatchLoadingOrder.id == ManualDispatchTripLO.manual_dispatch_lo_id)
                .where(ManualDispatchTripLO.trip_id == trip.id)
            )
            or 0
        )

    @staticmethod
    def _resequence_stops(db: Session, trip_id: str) -> None:
        rows = db.scalars(
            select(ManualDispatchTripLO).where(ManualDispatchTripLO.trip_id == trip_id).order_by(ManualDispatchTripLO.stop_sequence, ManualDispatchTripLO.id)
        ).all()
        for index, row in enumerate(rows, start=1):
            row.stop_sequence = index
            row.estimated_arrival_datetime = None


class DispatchValidationService:
    @staticmethod
    def validate_trip(db: Session, job: ManualDispatchJob, trip: ManualDispatchTrip, vehicle: ManualDispatchVehicle) -> dict:
        assignments = db.scalars(
            select(ManualDispatchTripLO).where(ManualDispatchTripLO.trip_id == trip.id).order_by(ManualDispatchTripLO.stop_sequence)
        ).all()
        los = [db.get(ManualDispatchLoadingOrder, row.manual_dispatch_lo_id) for row in assignments]
        los = [lo for lo in los if lo]
        errors: list[dict] = []
        if not los:
            errors.append({"code": "EMPTY_TRIP", "message": "Trip must contain at least one Loading Order."})
        total_volume = sum(lo.volume_kl for lo in los)
        if total_volume > vehicle.capacity_kl + 1e-6:
            errors.append({"code": "TRIP_CAPACITY_EXCEEDED", "message": f"Trip volume {total_volume:g} KL exceeds MT capacity {vehicle.capacity_kl:g} KL."})
        if vehicle.number_of_compartments and len({lo.product_id for lo in los if lo.product_id}) > vehicle.number_of_compartments:
            errors.append({"code": "COMPARTMENT_PRODUCT_CONFLICT", "message": "Distinct product requirements exceed the MT compartment count."})
        if trip.trip_sequence < 1:
            errors.append({"code": "INVALID_TRIP_SEQUENCE", "message": "Trip sequence must start at 1."})
        if trip.departure_datetime and _aware(trip.departure_datetime) < _aware(trip.available_before_trip_datetime):
            errors.append(
                {
                    "code": "TRIP_BEFORE_MT_AVAILABLE",
                    "message": f"Trip {trip.trip_sequence} cannot start at {_iso(trip.departure_datetime)} because the MT is only available at {_iso(trip.available_before_trip_datetime)}.",
                }
            )
        for lo in los:
            evaluation = DispatchEligibilityService.compatibility(db, job, vehicle, lo)
            errors.extend({"code": "MT_SPBU_INCOMPATIBLE", "message": reason, "lo_id": lo.lo_id} for reason in evaluation["reasons"])
        if len({lo.id for lo in los}) != len(los):
            errors.append({"code": "DUPLICATE_LO", "message": "A Loading Order appears more than once in the trip."})
        sequences = [row.stop_sequence for row in assignments]
        if sequences != list(range(1, len(sequences) + 1)):
            errors.append({"code": "INVALID_STOP_SEQUENCE", "message": "LO stop sequence must be consecutive and start at 1."})
        return {"valid": not errors, "errors": errors, "total_volume_kl": total_volume, "lo_count": len(los), "assignments": assignments, "loading_orders": los}

    @staticmethod
    def validate_dispatch(db: Session, dispatch_job_id: str) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        vehicles = db.scalars(select(ManualDispatchVehicle).where(ManualDispatchVehicle.dispatch_job_id == job.id)).all()
        trips = []
        hard_errors: list[dict] = []
        compatibility_errors = 0
        for vehicle in vehicles:
            vehicle_trips = db.scalars(
                select(ManualDispatchTrip).where(ManualDispatchTrip.dispatch_vehicle_id == vehicle.id).order_by(ManualDispatchTrip.trip_sequence)
            ).all()
            trips.extend(vehicle_trips)
            if [row.trip_sequence for row in vehicle_trips] != list(range(1, len(vehicle_trips) + 1)):
                hard_errors.append({"code": "INVALID_TRIP_SEQUENCE", "message": f"{vehicle.vehicle_registration}: trip sequence is not consecutive."})
            previous = None
            for trip in vehicle_trips:
                result = DispatchValidationService.validate_trip(db, job, trip, vehicle)
                for error in result["errors"]:
                    hard_errors.append({**error, "trip_id": trip.id, "mt_id": vehicle.mt_id})
                    compatibility_errors += int(error["code"] == "MT_SPBU_INCOMPATIBLE")
                if trip.status in HARD_FINALIZATION_TRIP_STATUSES:
                    hard_errors.append({"code": "TRIP_NOT_CALCULATED", "message": f"{vehicle.vehicle_registration} Trip {trip.trip_sequence} has status {trip.status}.", "trip_id": trip.id})
                if not trip.departure_datetime or not trip.estimated_return_datetime or not trip.available_after_trip_datetime:
                    hard_errors.append({"code": "INVALID_TIMELINE", "message": f"{vehicle.vehicle_registration} Trip {trip.trip_sequence} has incomplete timestamps.", "trip_id": trip.id})
                if previous and previous.estimated_return_datetime and trip.departure_datetime:
                    if _aware(trip.departure_datetime) < _aware(previous.available_after_trip_datetime or previous.estimated_return_datetime):
                        hard_errors.append({"code": "OVERLAPPING_TRIPS", "message": f"{vehicle.vehicle_registration} Trip {trip.trip_sequence} starts before the MT is available.", "trip_id": trip.id})
                previous = trip
        duplicate_rows = db.execute(
            select(ManualDispatchTripLO.manual_dispatch_lo_id, func.count())
            .where(ManualDispatchTripLO.dispatch_job_id == job.id)
            .group_by(ManualDispatchTripLO.manual_dispatch_lo_id)
            .having(func.count() > 1)
        ).all()
        for lo_id, count in duplicate_rows:
            hard_errors.append({"code": "DUPLICATE_LO", "message": f"LO scope {lo_id} is assigned {count} times."})
        scope = db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.dispatch_job_id == job.id)).all()
        assigned = [lo for lo in scope if lo.assignment_status == "ASSIGNED"]
        unassigned = [lo for lo in scope if lo.assignment_status != "ASSIGNED"]
        warnings = []
        if unassigned:
            warnings.append({"code": "UNASSIGNED_LO", "message": f"{len(unassigned)} Loading Orders ({sum(lo.volume_kl for lo in unassigned):g} KL) remain unassigned.", "requires_acknowledgement": True})
        return {
            "valid": not hard_errors,
            "hard_errors": hard_errors,
            "warnings": warnings,
            "summary": {
                "assigned_lo": len(assigned),
                "assigned_volume_kl": round(sum(lo.volume_kl for lo in assigned), 3),
                "unassigned_lo": len(unassigned),
                "unassigned_volume_kl": round(sum(lo.volume_kl for lo in unassigned), 3),
                "mt_used": len({trip.dispatch_vehicle_id for trip in trips if trip.total_volume_kl > 0}),
                "trips": len(trips),
                "compatibility_errors": compatibility_errors,
                "uncalculated_trips": sum(1 for trip in trips if trip.status != "VALID"),
                "timeline_conflicts": sum(1 for row in hard_errors if row["code"] in {"INVALID_TIMELINE", "OVERLAPPING_TRIPS", "TRIP_BEFORE_MT_AVAILABLE"}),
            },
        }


class DispatchRouteService:
    @staticmethod
    def _provider(db: Session) -> Callable[[tuple[float, float], tuple[float, float], datetime], dict]:
        config = get_google_routes_configuration(db)
        if not config or not config.encrypted_api_key:
            raise GoogleRoutesError("GOOGLE_ROUTES_NOT_CONFIGURED", "Google Routes API key is not configured.", status_code=503)
        client = GoogleRoutesClient(decrypt_api_key(config.encrypted_api_key))

        def calculate(origin: tuple[float, float], destination: tuple[float, float], departure: datetime) -> dict:
            return client.compute_route(
                origin=origin,
                destination=destination,
                departure_datetime=departure,
                routing_mode="DRIVE",
                routing_preference=config.routing_preference,
            )

        return calculate

    @staticmethod
    def apply_trip(
        db: Session,
        dispatch_job_id: str,
        trip_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
        expected_trip_version: int | None = None,
        route_provider: Callable[[tuple[float, float], tuple[float, float], datetime], dict] | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        trip, vehicle = _trip_context(db, job, trip_id)
        if expected_trip_version is not None and expected_trip_version != trip.row_version:
            raise HTTPException(status_code=409, detail={"code": "TRIP_VERSION_CONFLICT", "message": "Trip changed after it was loaded. Refresh before Apply."})
        validation = DispatchValidationService.validate_trip(db, job, trip, vehicle)
        old_return = trip.estimated_return_datetime
        if not validation["valid"]:
            trip.status = "CONFLICT"
            trip.route_response_status = "VALIDATION_FAILED"
            trip.route_error_message = "; ".join(error["message"] for error in validation["errors"])
            trip.row_version += 1
            _touch(job)
            DispatchAuditService.log(db, job, actor, "TRIP_VALIDATION_FAILED", "TRIP", trip.id, new={"status": trip.status, "errors": validation["errors"]})
            db.commit()
            return {"applied": False, "validation": validation, "workspace": ManualDispatchJobService.detail(db, job.id)}
        depot = db.get(MasterDepot, job.depot_id)
        if not depot or depot.latitude is None or depot.longitude is None:
            validation["errors"].append({"code": "INVALID_DEPOT_COORDINATES", "message": "Depot coordinates are required for route calculation."})
            trip.status = "CONFLICT"
            trip.route_response_status = "INVALID_COORDINATES"
            trip.route_error_message = validation["errors"][-1]["message"]
            trip.row_version += 1
            _touch(job)
            db.commit()
            return {"applied": False, "validation": validation, "workspace": ManualDispatchJobService.detail(db, job.id)}
        spbus: dict[str, MasterSPBU] = {}
        ordered_spbu_ids: list[str] = []
        for lo in validation["loading_orders"]:
            if lo.spbu_id not in spbus:
                spbu = db.get(MasterSPBU, lo.spbu_id)
                if not spbu or spbu.latitude is None or spbu.longitude is None:
                    validation["errors"].append({"code": "INVALID_SPBU_COORDINATES", "message": f"SPBU {lo.spbu_number or lo.spbu_id} has invalid coordinates."})
                    continue
                spbus[lo.spbu_id] = spbu
                ordered_spbu_ids.append(lo.spbu_id)
        if validation["errors"]:
            trip.status = "CONFLICT"
            trip.route_response_status = "INVALID_COORDINATES"
            trip.route_error_message = "; ".join(error["message"] for error in validation["errors"])
            trip.row_version += 1
            _touch(job)
            db.commit()
            return {"applied": False, "validation": validation, "workspace": ManualDispatchJobService.detail(db, job.id)}
        trip.status = "CALCULATING"
        db.flush()
        provider = route_provider
        try:
            provider = provider or DispatchRouteService._provider(db)
            db.execute(delete(ManualDispatchRouteLeg).where(ManualDispatchRouteLeg.trip_id == trip.id))
            departure = _aware(trip.departure_datetime or trip.available_before_trip_datetime)
            service_per_stop = int((job.configuration_snapshot or {}).get("default_spbu_service_minutes", 45)) * 60
            route_points = [("DEPOT", depot.depot_id, float(depot.latitude), float(depot.longitude))]
            route_points.extend(("SPBU", spbu_id, float(spbus[spbu_id].latitude), float(spbus[spbu_id].longitude)) for spbu_id in ordered_spbu_ids)
            route_points.append(("DEPOT", depot.depot_id, float(depot.latitude), float(depot.longitude)))
            total_distance = 0
            total_travel = 0
            geometry: list[dict] = []
            arrival_by_spbu: dict[str, datetime] = {}
            cursor = departure
            for index in range(len(route_points) - 1):
                origin = route_points[index]
                destination = route_points[index + 1]
                requested_at = _now()
                result = provider((origin[2], origin[3]), (destination[2], destination[3]), cursor)
                distance = int(result.get("distance_meters") or 0)
                traffic = int(result.get("duration_seconds") or 0)
                static = int(result.get("static_duration_seconds") or traffic)
                if distance <= 0 or traffic <= 0:
                    raise GoogleRoutesError("GOOGLE_ROUTE_NOT_FOUND", "Google Routes returned an empty route.", status_code=404)
                db.add(
                    ManualDispatchRouteLeg(
                        id=_id("MDLEG"), trip_id=trip.id, leg_sequence=index + 1,
                        origin_type=origin[0], origin_id=origin[1], destination_type=destination[0], destination_id=destination[1],
                        origin_lat=origin[2], origin_lng=origin[3], destination_lat=destination[2], destination_lng=destination[3],
                        distance_meter=distance, duration_seconds=static, traffic_duration_seconds=traffic,
                        route_provider=str(result.get("provider") or "GOOGLE_ROUTES"), request_timestamp=requested_at, response_status="SUCCESS",
                    )
                )
                total_distance += distance
                total_travel += traffic
                cursor += timedelta(seconds=traffic)
                if destination[0] == "SPBU":
                    arrival_by_spbu[destination[1]] = cursor
                    cursor += timedelta(seconds=service_per_stop)
                geometry.extend(result.get("route_geometry") or [])
            service_seconds = service_per_stop * len(ordered_spbu_ids)
            operational_buffer = int(trip.operational_buffer_seconds or 0)
            total_seconds = total_travel + service_seconds + operational_buffer
            estimated_return = departure + timedelta(seconds=total_seconds)
            available_after = estimated_return + timedelta(seconds=int(trip.turnaround_duration_seconds or 0))
            for assignment, lo in zip(validation["assignments"], validation["loading_orders"]):
                assignment.estimated_arrival_datetime = arrival_by_spbu.get(lo.spbu_id)
            trip.departure_datetime = departure
            trip.distance_meter = total_distance
            trip.travel_duration_seconds = total_travel
            trip.service_duration_seconds = service_seconds
            trip.total_duration_seconds = total_seconds
            trip.total_volume_kl = validation["total_volume_kl"]
            trip.estimated_return_datetime = estimated_return
            trip.available_after_trip_datetime = available_after
            trip.route_provider = "GOOGLE_ROUTES" if route_provider is None else "MOCK_OR_INJECTED_ROUTE_PROVIDER"
            trip.route_response_status = "SUCCESS"
            trip.route_error_message = None
            trip.route_geometry = geometry
            trip.route_calculated_at = _now()
            trip.status = "VALID"
            trip.row_version += 1
            invalidated = DispatchTimelineService.invalidate_downstream(db, job, vehicle, trip.trip_sequence, next_available=available_after, actor=actor)
            DispatchTimelineService.refresh_vehicle(db, vehicle)
            _touch(job)
            all_trips = db.scalars(
                select(ManualDispatchTrip).join(ManualDispatchVehicle, ManualDispatchVehicle.id == ManualDispatchTrip.dispatch_vehicle_id).where(ManualDispatchVehicle.dispatch_job_id == job.id)
            ).all()
            job.status = "READY" if all_trips and all(row.status == "VALID" for row in all_trips) else "IN_PROGRESS"
            DispatchAuditService.log(
                db,
                job,
                actor,
                "TRIP_RECALCULATED",
                "TRIP",
                trip.id,
                old={"estimated_return_datetime": _iso(old_return)},
                new={"estimated_return_datetime": _iso(estimated_return), "available_after_trip_datetime": _iso(available_after), "status": "VALID"},
                metadata={
                    "mt_id": vehicle.mt_id, "vehicle_registration": vehicle.vehicle_registration, "trip_sequence": trip.trip_sequence,
                    "old_return": _iso(old_return), "new_return": _iso(estimated_return), "downstream_invalidated": invalidated,
                },
            )
            db.commit()
            return {"applied": True, "validation": validation, "workspace": ManualDispatchJobService.detail(db, job.id)}
        except GoogleRoutesError as exc:
            trip.status = "WARNING"
            trip.route_provider = "GOOGLE_ROUTES"
            trip.route_response_status = exc.code
            trip.route_error_message = exc.message
            trip.route_calculated_at = None
            trip.row_version += 1
            _touch(job)
            job.status = "IN_PROGRESS"
            DispatchAuditService.log(db, job, actor, "ROUTE_CALCULATION_FAILED", "TRIP", trip.id, new={"status": trip.status, "code": exc.code, "message": exc.message})
            db.commit()
            return {
                "applied": False,
                "route_error": {"code": exc.code, "message": exc.message},
                "validation": validation,
                "workspace": ManualDispatchJobService.detail(db, job.id),
            }


class DispatchGeographicMapService:
    """Read-only, one-MT map projection backed exclusively by Google road geometry."""

    @staticmethod
    def _valid_geometry(points: list | None) -> list[dict[str, float]]:
        geometry: list[dict[str, float]] = []
        for point in points or []:
            try:
                latitude = float(point["latitude"])
                longitude = float(point["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                geometry.append({"latitude": latitude, "longitude": longitude})
        return geometry

    @staticmethod
    def _provider(db: Session) -> Callable[[tuple[float, float], tuple[float, float], datetime | None, list[tuple[float, float]]], dict]:
        config = get_google_routes_configuration(db)
        if not config or not config.encrypted_api_key:
            raise GoogleRoutesError("GOOGLE_ROUTES_NOT_CONFIGURED", "Google Routes API key is not configured.", status_code=503)
        client = GoogleRoutesClient(decrypt_api_key(config.encrypted_api_key))

        def calculate(
            origin: tuple[float, float],
            destination: tuple[float, float],
            departure: datetime | None,
            intermediates: list[tuple[float, float]],
        ) -> dict:
            return client.compute_route(
                origin=origin,
                destination=destination,
                departure_datetime=departure,
                routing_mode="DRIVE",
                routing_preference=config.routing_preference,
                intermediates=intermediates,
            )

        return calculate

    @staticmethod
    def _historical_google_geometry(db: Session, depot_id: str, spbu_ids: tuple[str, ...]) -> dict | None:
        if not spbu_ids:
            return None
        cache_rows = db.scalars(
            select(RouteMatrixCache)
            .where(
                RouteMatrixCache.route_vehicle_mode == "GEOMETRY_ONLY",
                RouteMatrixCache.provider.contains("GOOGLE"),
            )
            .order_by(RouteMatrixCache.calculated_at.desc())
        ).all()
        for row in cache_rows:
            metadata = row.response_metadata or {}
            if tuple(str(value) for value in metadata.get("stop_ids") or []) == spbu_ids:
                geometry = DispatchGeographicMapService._valid_geometry(row.route_geometry)
                if len(geometry) >= 2 and row.origin_location_id == depot_id and row.destination_location_id == depot_id:
                    return {"route_geometry": geometry, "route_geometry_source": "HISTORICAL_GOOGLE_ROUTES_CACHE"}

        route_rows = db.execute(
            select(RouteVersionTrip.route_version_trip_id, RouteVersionTrip.route_geometry, RouteVersionTrip.route_geometry_source)
            .join(RouteVersion, RouteVersion.route_version_id == RouteVersionTrip.route_version_id)
            .join(OptimizationJob, OptimizationJob.job_id == RouteVersion.job_id)
            .where(
                OptimizationJob.depot_id == depot_id,
                RouteVersionTrip.route_geometry_source.contains("GOOGLE"),
            )
            .order_by(RouteVersionTrip.gate_out.desc())
        ).all()
        route_ids = [row[0] for row in route_rows]
        stops_by_trip: dict[str, list[str]] = defaultdict(list)
        for offset in range(0, len(route_ids), 500):
            for trip_id, spbu_id in db.execute(
                select(RouteVersionStop.route_version_trip_id, RouteVersionStop.spbu_id)
                .where(RouteVersionStop.route_version_trip_id.in_(route_ids[offset : offset + 500]))
                .order_by(RouteVersionStop.route_version_trip_id, RouteVersionStop.sequence_number)
            ).all():
                if spbu_id:
                    stops_by_trip[trip_id].append(spbu_id)
        for trip_id, route_geometry, source in route_rows:
            if tuple(stops_by_trip.get(trip_id) or []) != spbu_ids:
                continue
            geometry = DispatchGeographicMapService._valid_geometry(route_geometry)
            if len(geometry) >= 2:
                return {"route_geometry": geometry, "route_geometry_source": f"HISTORICAL_{source}"}

        prediction_rows = db.execute(
            select(PredictionTrip.route_geometry, PredictionTrip.route_geometry_source, PredictionTrip.estimated_visit_sequence)
            .join(PredictionRun, PredictionRun.id == PredictionTrip.prediction_run_id)
            .where(
                PredictionRun.depot_id == depot_id,
                PredictionTrip.route_geometry_source.contains("GOOGLE"),
            )
            .order_by(PredictionTrip.created_at.desc())
        ).all()
        for route_geometry, source, sequence in prediction_rows:
            if tuple(str(value) for value in sequence or []) != spbu_ids:
                continue
            geometry = DispatchGeographicMapService._valid_geometry(route_geometry)
            if len(geometry) >= 2:
                return {"route_geometry": geometry, "route_geometry_source": f"HISTORICAL_{source}"}
        return None

    @staticmethod
    def build(
        db: Session,
        dispatch_job_id: str,
        vehicle_id: str,
        *,
        route_provider: Callable[[tuple[float, float], tuple[float, float], datetime | None, list[tuple[float, float]]], dict] | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        vehicle = _vehicle_or_404(db, job, vehicle_id)
        depot = db.get(MasterDepot, job.depot_id)
        if not depot or depot.latitude is None or depot.longitude is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_DEPOT_COORDINATES", "message": "Master Depot coordinates are required for the Geographic Map."},
            )
        trips = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id == vehicle.id)
            .order_by(ManualDispatchTrip.trip_sequence)
        ).all()
        trip_ids = [trip.id for trip in trips]
        assignments = db.scalars(
            select(ManualDispatchTripLO)
            .where(ManualDispatchTripLO.trip_id.in_(trip_ids))
            .order_by(ManualDispatchTripLO.trip_id, ManualDispatchTripLO.stop_sequence)
        ).all() if trip_ids else []
        lo_ids = {row.manual_dispatch_lo_id for row in assignments}
        los = {
            row.id: row
            for row in db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.id.in_(lo_ids))).all()
        } if lo_ids else {}
        assignments_by_trip: dict[str, list[ManualDispatchTripLO]] = defaultdict(list)
        for assignment in assignments:
            assignments_by_trip[assignment.trip_id].append(assignment)

        provider = route_provider
        provider_error: GoogleRoutesError | None = None
        response_trips: list[dict] = []
        live_requests = 0
        reused_geometry = 0
        historical_geometry = 0
        errors: list[dict] = []
        depot_point = (float(depot.latitude), float(depot.longitude))
        for trip in trips:
            ordered_stops: list[dict] = []
            seen_spbu: set[str] = set()
            for assignment in assignments_by_trip[trip.id]:
                lo = los.get(assignment.manual_dispatch_lo_id)
                if not lo or lo.spbu_id in seen_spbu:
                    continue
                spbu = db.get(MasterSPBU, lo.spbu_id)
                if not spbu or spbu.latitude is None or spbu.longitude is None:
                    errors.append({"trip_id": trip.id, "code": "INVALID_SPBU_COORDINATES", "message": f"SPBU {lo.spbu_number or lo.spbu_id} has no valid coordinates."})
                    continue
                seen_spbu.add(lo.spbu_id)
                ordered_stops.append(
                    {
                        "sequence": len(ordered_stops) + 1,
                        "spbu_id": lo.spbu_id,
                        "spbu_number": lo.spbu_number,
                        "spbu_name": lo.spbu_name,
                        "latitude": float(spbu.latitude),
                        "longitude": float(spbu.longitude),
                        "estimated_arrival_datetime": _iso(assignment.estimated_arrival_datetime),
                        "loading_order_ids": [
                            candidate_lo.lo_number
                            for candidate in assignments_by_trip[trip.id]
                            if (candidate_lo := los.get(candidate.manual_dispatch_lo_id)) and candidate_lo.spbu_id == lo.spbu_id
                        ],
                    }
                )

            geometry = DispatchGeographicMapService._valid_geometry(trip.route_geometry)
            geometry_source = str(trip.route_provider or "")
            geometry_status = "STORED_GOOGLE_ROUTES" if geometry and "GOOGLE" in geometry_source.upper() else ""
            if geometry_status:
                reused_geometry += 1
            elif ordered_stops:
                history = DispatchGeographicMapService._historical_google_geometry(
                    db,
                    depot.depot_id,
                    tuple(stop["spbu_id"] for stop in ordered_stops),
                )
                if history:
                    geometry = history["route_geometry"]
                    geometry_source = history["route_geometry_source"]
                    geometry_status = "HISTORICAL_GOOGLE_ROUTES"
                    historical_geometry += 1
                else:
                    geometry_status = ""
            if not ordered_stops:
                geometry = []
                geometry_status = "NO_STOPS"
            elif not geometry_status:
                if provider is None and provider_error is None:
                    try:
                        provider = DispatchGeographicMapService._provider(db)
                    except GoogleRoutesError as exc:
                        provider_error = exc
                if provider_error:
                    geometry = []
                    geometry_status = "GOOGLE_ERROR"
                    errors.append({"trip_id": trip.id, "code": provider_error.code, "message": provider_error.message})
                elif provider:
                    try:
                        result = provider(
                            depot_point,
                            depot_point,
                            _aware(trip.departure_datetime or trip.available_before_trip_datetime),
                            [(stop["latitude"], stop["longitude"]) for stop in ordered_stops],
                        )
                        geometry = DispatchGeographicMapService._valid_geometry(result.get("route_geometry"))
                        if len(geometry) < 2:
                            raise GoogleRoutesError("GOOGLE_ROUTE_NOT_FOUND", "Google Routes returned an empty road geometry.", status_code=404)
                        geometry_source = str(result.get("route_geometry_source") or "GOOGLE_ROUTES_GEOJSON")
                        geometry_status = "LIVE_GOOGLE_ROUTES"
                        live_requests += 1
                    except GoogleRoutesError as exc:
                        geometry = []
                        geometry_status = "GOOGLE_ERROR"
                        errors.append({"trip_id": trip.id, "code": exc.code, "message": exc.message})
            response_trips.append(
                {
                    "trip_id": trip.id,
                    "trip_sequence": trip.trip_sequence,
                    "status": trip.status,
                    "departure_datetime": _iso(trip.departure_datetime),
                    "estimated_return_datetime": _iso(trip.estimated_return_datetime),
                    "distance_meter": trip.distance_meter,
                    "route_geometry": geometry,
                    "route_geometry_source": geometry_source or None,
                    "geometry_status": geometry_status,
                    "stops": ordered_stops,
                }
            )
        road_trip_count = sum(1 for trip in response_trips if len(trip["route_geometry"]) >= 2)
        return {
            "job_id": job.id,
            "vehicle": {
                "id": vehicle.id,
                "mt_id": vehicle.mt_id,
                "vehicle_registration": vehicle.vehicle_registration,
                "capacity_kl": vehicle.capacity_kl,
            },
            "depot": {
                "depot_id": depot.depot_id,
                "depot_name": depot.depot_name,
                "latitude": float(depot.latitude),
                "longitude": float(depot.longitude),
            },
            "status": "READY" if road_trip_count == len(response_trips) else "PARTIAL" if road_trip_count else "FAILED",
            "trip_count": len(response_trips),
            "road_geometry_trip_count": road_trip_count,
            "live_google_requests": live_requests,
            "stored_google_geometry_count": reused_geometry,
            "historical_google_geometry_count": historical_geometry,
            "errors": errors,
            "trips": response_trips,
        }


class DispatchSimulationService:
    @staticmethod
    def build(db: Session, dispatch_job_id: str, *, bucket_minutes: int = 60) -> dict:
        if bucket_minutes not in {15, 30, 60}:
            raise HTTPException(status_code=422, detail={"code": "INVALID_BUCKET_GRANULARITY", "message": "Supported bucket sizes are 15, 30, or 60 minutes."})
        job = _job_or_404(db, dispatch_job_id)
        depot = db.get(MasterDepot, job.depot_id)
        tz = ZoneInfo((job.configuration_snapshot or {}).get("depot_timezone") or (depot.timezone if depot else None) or "Asia/Jakarta")
        local_start = datetime.combine(job.operational_date, time(0, 0), tzinfo=tz)
        local_end = local_start + timedelta(days=1)
        vehicles = db.scalars(select(ManualDispatchVehicle).where(ManualDispatchVehicle.dispatch_job_id == job.id)).all()
        vehicle_ids = [vehicle.id for vehicle in vehicles]
        trips = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id.in_(vehicle_ids))
            .order_by(ManualDispatchTrip.dispatch_vehicle_id, ManualDispatchTrip.trip_sequence)
        ).all() if vehicle_ids else []
        trip_ids = [trip.id for trip in trips]
        assignment_rows = db.execute(
            select(ManualDispatchTripLO.trip_id, ManualDispatchLoadingOrder)
            .join(ManualDispatchLoadingOrder, ManualDispatchLoadingOrder.id == ManualDispatchTripLO.manual_dispatch_lo_id)
            .where(ManualDispatchTripLO.trip_id.in_(trip_ids))
        ).all() if trip_ids else []
        los_by_trip: dict[str, list[ManualDispatchLoadingOrder]] = defaultdict(list)
        for trip_id, lo in assignment_rows:
            los_by_trip[trip_id].append(lo)
        trips_by_vehicle: dict[str, list[ManualDispatchTrip]] = defaultdict(list)
        for trip in trips:
            trips_by_vehicle[trip.dispatch_vehicle_id].append(trip)

        buckets = []
        cursor = local_start
        while cursor < local_end:
            bucket_end = cursor + timedelta(minutes=bucket_minutes)
            bucket_trips = [
                trip for trip in trips
                if trip.departure_datetime and cursor <= _aware(trip.departure_datetime).astimezone(tz) < bucket_end
            ]
            demand_los = [lo for trip in bucket_trips for lo in los_by_trip.get(trip.id, [])]
            available_vehicles = []
            for vehicle in vehicles:
                point = cursor
                initial = _aware(vehicle.initial_available_datetime).astimezone(tz)
                if initial > point:
                    continue
                away = False
                uncertain = False
                for trip in trips_by_vehicle.get(vehicle.id, []):
                    if not trip.departure_datetime:
                        continue
                    departure = _aware(trip.departure_datetime).astimezone(tz)
                    if trip.available_after_trip_datetime:
                        available_after = _aware(trip.available_after_trip_datetime).astimezone(tz)
                        if departure <= point < available_after:
                            away = True
                            break
                    elif departure <= point and trip.status != "DRAFT":
                        uncertain = True
                if not away and not uncertain:
                    available_vehicles.append(vehicle)
            demand_kl = round(sum(lo.volume_kl for lo in demand_los), 3)
            capacity_kl = round(sum(vehicle.capacity_kl for vehicle in available_vehicles), 3)
            gap = round(capacity_kl - demand_kl, 3)
            buckets.append(
                {
                    "bucket_start": cursor.isoformat(),
                    "bucket_end": bucket_end.isoformat(),
                    "label": f"{cursor:%H:%M}–{(bucket_end - timedelta(minutes=1)):%H:%M}",
                    "demand_kl": demand_kl,
                    "lo_count": len(demand_los),
                    "trip_count": len(bucket_trips),
                    "departing_mt_count": len({trip.dispatch_vehicle_id for trip in bucket_trips}),
                    "available_capacity_kl": capacity_kl,
                    "available_mt_count": len(available_vehicles),
                    "capacity_gap_kl": gap,
                    "gap_label": "Capacity Shortage Indicator" if gap < 0 else "Capacity Slack",
                }
            )
            cursor = bucket_end

        vehicle_by_id = {vehicle.id: vehicle for vehicle in vehicles}
        gantt_rows = []
        for vehicle in vehicles:
            vehicle_trips = trips_by_vehicle.get(vehicle.id, [])
            segments = []
            available_cursor = max(local_start, _aware(vehicle.initial_available_datetime).astimezone(tz))
            for trip in vehicle_trips:
                if not trip.departure_datetime:
                    continue
                departure = _aware(trip.departure_datetime).astimezone(tz)
                if departure > available_cursor:
                    segments.append({"type": "AVAILABLE_AT_DEPOT", "start": available_cursor.isoformat(), "end": min(departure, local_end).isoformat()})
                if trip.estimated_return_datetime:
                    trip_end = _aware(trip.estimated_return_datetime).astimezone(tz)
                    segments.append(
                        {
                            "type": "TRIP",
                            "start": departure.isoformat(),
                            "end": trip_end.isoformat(),
                            "trip_id": trip.id,
                            "trip_sequence": trip.trip_sequence,
                            "lo_count": len(los_by_trip.get(trip.id, [])),
                            "spbu_count": len({lo.spbu_id for lo in los_by_trip.get(trip.id, [])}),
                            "volume_kl": trip.total_volume_kl,
                            "distance_meter": trip.distance_meter,
                            "duration_seconds": trip.total_duration_seconds,
                            "status": trip.status,
                        }
                    )
                    available_cursor = _aware(trip.available_after_trip_datetime or trip.estimated_return_datetime).astimezone(tz)
                else:
                    segments.append({"type": "TRIP", "start": departure.isoformat(), "end": None, "trip_id": trip.id, "trip_sequence": trip.trip_sequence, "status": trip.status})
            if available_cursor < local_end:
                segments.append({"type": "AVAILABLE_AT_DEPOT", "start": available_cursor.isoformat(), "end": local_end.isoformat()})
            gantt_rows.append(
                {
                    "vehicle_id": vehicle.id,
                    "mt_id": vehicle.mt_id,
                    "vehicle_registration": vehicle.vehicle_registration,
                    "capacity_kl": vehicle.capacity_kl,
                    "active": bool(vehicle_trips),
                    "segments": segments,
                }
            )
        negative = [row for row in buckets if row["capacity_gap_kl"] < 0]
        peak = max(buckets, key=lambda row: row["demand_kl"], default=None)
        active_ids = {trip.dispatch_vehicle_id for trip in trips if trip.total_volume_kl > 0}
        return {
            "job_id": job.id,
            "generated_at": _now().isoformat(),
            "bucket_minutes": bucket_minutes,
            "raw_timestamps_preserved": True,
            "buckets": buckets,
            "gantt": {"window_start": local_start.isoformat(), "window_end": local_end.isoformat(), "rows": gantt_rows},
            "summary": {
                "peak_lo_gate_out_kl_per_hour": peak["demand_kl"] if peak else 0,
                "minimum_available_capacity_kl": min((row["available_capacity_kl"] for row in buckets), default=0),
                "maximum_negative_capacity_gap_kl": min((row["capacity_gap_kl"] for row in buckets), default=0),
                "peak_gate_out_hour": peak["label"] if peak else None,
                "capacity_shortage_bucket_count": len(negative),
                "mt_active": len(active_ids),
                "mt_idle": len(vehicles) - len(active_ids),
                "total_trips": len(trips),
            },
        }


class DispatchDashboardService:
    @staticmethod
    def build(db: Session, dispatch_job_id: str) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        simulation = DispatchSimulationService.build(db, job.id, bucket_minutes=60)
        depot = db.get(MasterDepot, job.depot_id)
        tz = ZoneInfo((job.configuration_snapshot or {}).get("depot_timezone") or (depot.timezone if depot else None) or "Asia/Jakarta")
        vehicles = db.scalars(select(ManualDispatchVehicle).where(ManualDispatchVehicle.dispatch_job_id == job.id)).all()
        vehicle_ids = [vehicle.id for vehicle in vehicles]
        trips = db.scalars(select(ManualDispatchTrip).where(ManualDispatchTrip.dispatch_vehicle_id.in_(vehicle_ids))).all() if vehicle_ids else []
        scope = db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.dispatch_job_id == job.id)).all()
        assigned = [lo for lo in scope if lo.assignment_status == "ASSIGNED"]
        unassigned = [lo for lo in scope if lo.assignment_status != "ASSIGNED"]
        assigned_lo_ids = {lo.id for lo in assigned}
        assignments = db.scalars(select(ManualDispatchTripLO).where(ManualDispatchTripLO.dispatch_job_id == job.id)).all()
        trip_by_id = {trip.id: trip for trip in trips}
        assigned_trip_by_lo = {row.manual_dispatch_lo_id: trip_by_id.get(row.trip_id) for row in assignments}
        used_vehicle_ids = {trip.dispatch_vehicle_id for trip in trips if trip.total_volume_kl > 0}
        active_seconds = sum(int(trip.total_duration_seconds or 0) for trip in trips if trip.status == "VALID")
        average_duration = round(active_seconds / len(trips), 2) if trips else 0
        operational_start = datetime.combine(job.operational_date, depot.depot_operational_start if depot else time(0, 0), tzinfo=tz)
        operational_end = datetime.combine(job.operational_date, depot.depot_operational_end if depot else time(23, 59), tzinfo=tz)
        if operational_end <= operational_start:
            operational_end += timedelta(days=1)
        window_seconds = max(1, int((operational_end - operational_start).total_seconds()))

        cumulative = []
        running = 0.0
        for bucket in simulation["buckets"]:
            running += bucket["demand_kl"]
            cumulative.append({"bucket_start": bucket["bucket_start"], "label": bucket["label"], "cumulative_assigned_kl": round(running, 3), "total_demand_kl": round(sum(lo.volume_kl for lo in scope), 3)})

        def distribution(key_fn: Callable[[ManualDispatchLoadingOrder], tuple[str, str]]) -> list[dict]:
            groups: dict[tuple[str, str], list[ManualDispatchLoadingOrder]] = defaultdict(list)
            for lo in scope:
                groups[key_fn(lo)].append(lo)
            rows = []
            for (group_id, group_name), los in sorted(groups.items(), key=lambda item: item[0][1]):
                assigned_rows = [lo for lo in los if lo.id in assigned_lo_ids]
                unassigned_rows = [lo for lo in los if lo.id not in assigned_lo_ids]
                involved_trips = {assigned_trip_by_lo.get(lo.id).id for lo in assigned_rows if assigned_trip_by_lo.get(lo.id)}
                rows.append(
                    {
                        "id": group_id,
                        "name": group_name,
                        "required_volume_kl": round(sum(lo.volume_kl for lo in los), 3),
                        "total_volume_kl": round(sum(lo.volume_kl for lo in los), 3),
                        "assigned_volume_kl": round(sum(lo.volume_kl for lo in assigned_rows), 3),
                        "unassigned_volume_kl": round(sum(lo.volume_kl for lo in unassigned_rows), 3),
                        "gap_kl": round(sum(lo.volume_kl for lo in assigned_rows) - sum(lo.volume_kl for lo in los), 3),
                        "lo_count": len(los),
                        "spbu_count": len({lo.spbu_id for lo in los}),
                        "trips": len(involved_trips),
                    }
                )
            return rows

        shift_rows = distribution(lambda lo: (lo.shift_id or "UNKNOWN", lo.shift_name or "Unknown"))
        cluster_rows = distribution(lambda lo: (lo.cluster_id or "UNKNOWN", lo.cluster_name or "Unknown / Unclustered"))
        fleet = []
        for vehicle in vehicles:
            vehicle_trips = [trip for trip in trips if trip.dispatch_vehicle_id == vehicle.id]
            vehicle_los = [lo for lo in assigned if (assigned_trip_by_lo.get(lo.id) and assigned_trip_by_lo[lo.id].dispatch_vehicle_id == vehicle.id)]
            active = sum(int(trip.total_duration_seconds or 0) for trip in vehicle_trips if trip.status == "VALID")
            departures = [_aware(trip.departure_datetime) for trip in vehicle_trips if trip.departure_datetime]
            returns = [_aware(trip.estimated_return_datetime) for trip in vehicle_trips if trip.estimated_return_datetime]
            capacity_denominator = sum(vehicle.capacity_kl for _ in vehicle_trips)
            fleet.append(
                {
                    "vehicle_id": vehicle.id,
                    "mt_id": vehicle.mt_id,
                    "vehicle_registration": vehicle.vehicle_registration,
                    "vehicle_class": vehicle.vehicle_class,
                    "capacity_kl": vehicle.capacity_kl,
                    "trips": len(vehicle_trips),
                    "assigned_volume_kl": round(sum(lo.volume_kl for lo in vehicle_los), 3),
                    "first_departure": _iso(min(departures)) if departures else None,
                    "last_return": _iso(max(returns)) if returns else None,
                    "active_time_seconds": active,
                    "idle_time_seconds": max(0, window_seconds - active),
                    "utilization_time_pct": round(100 * active / window_seconds, 2),
                    "volume_capacity_utilization_pct": round(100 * sum(lo.volume_kl for lo in vehicle_los) / capacity_denominator, 2) if capacity_denominator else 0,
                }
            )

        def remaining_breakdown(key_fn: Callable[[ManualDispatchLoadingOrder], str]) -> list[dict]:
            groups: dict[str, list[ManualDispatchLoadingOrder]] = defaultdict(list)
            for lo in unassigned:
                groups[key_fn(lo)].append(lo)
            return [
                {"name": name, "lo_count": len(los), "volume_kl": round(sum(lo.volume_kl for lo in los), 3), "spbu_count": len({lo.spbu_id for lo in los})}
                for name, los in sorted(groups.items())
            ]

        total_distance = sum(int(trip.distance_meter or 0) for trip in trips)
        return {
            "job_id": job.id,
            "generated_at": _now().isoformat(),
            "kpis": {
                "total_lo": len(scope),
                "total_planned_volume_kl": round(sum(lo.volume_kl for lo in scope), 3),
                "assigned_lo": len(assigned),
                "assigned_volume_kl": round(sum(lo.volume_kl for lo in assigned), 3),
                "unassigned_lo": len(unassigned),
                "unassigned_volume_kl": round(sum(lo.volume_kl for lo in unassigned), 3),
                "total_mt_in_scope": len(vehicles),
                "mt_used": len(used_vehicle_ids),
                "total_trips": len(trips),
                "fleet_utilization_time_pct": round(100 * active_seconds / (window_seconds * len(vehicles)), 2) if vehicles else 0,
                "average_trips_per_used_mt": round(len(trips) / len(used_vehicle_ids), 2) if used_vehicle_ids else 0,
                "average_trip_duration_seconds": average_duration,
                "total_estimated_distance_meter": total_distance,
            },
            "hourly_gate_out": simulation["buckets"],
            "cumulative_distribution": cumulative,
            "distribution_by_shift": shift_rows,
            "distribution_by_cluster": cluster_rows,
            "fleet_utilization": {
                "definition": "active trip time / available operating window",
                "volume_definition": "assigned volume / sum of trip vehicle capacity",
                "rows": fleet,
            },
            "remaining_demand": {
                "unassigned_lo": len(unassigned),
                "unassigned_volume_kl": round(sum(lo.volume_kl for lo in unassigned), 3),
                "affected_spbu": len({lo.spbu_id for lo in unassigned}),
                "by_shift": remaining_breakdown(lambda lo: lo.shift_name or "Unknown"),
                "by_cluster": remaining_breakdown(lambda lo: lo.cluster_name or "Unknown / Unclustered"),
                "by_product": remaining_breakdown(lambda lo: lo.product_name or "Unknown"),
                "by_spbu": remaining_breakdown(lambda lo: f"{lo.spbu_number or lo.spbu_id} · {lo.spbu_name or '-'}"),
            },
        }


class DispatchVersionService:
    @staticmethod
    def create_version(
        db: Session,
        dispatch_job_id: str,
        *,
        actor: str,
        expected_job_version: int | None = None,
        job_name: str | None = None,
    ) -> dict:
        source = _job_or_404(db, dispatch_job_id)
        _assert_version(source, expected_job_version)
        target = ManualDispatchJob(
            id=_id("MDJ"),
            job_id=ManualDispatchSnapshotService._next_job_no(db),
            job_name=(job_name or source.job_name).strip(),
            depot_id=source.depot_id,
            operational_date=source.operational_date,
            source_phase="MANUAL_DISPATCH",
            source_job_id=source.id,
            source_run_id=source.source_run_id,
            source_route_id=source.id,
            source_route_version=f"Dispatch V{source.dispatch_version}",
            source_created_at=source.updated_at,
            dispatch_version=source.dispatch_version + 1,
            parent_dispatch_job_id=source.id,
            status="DRAFT",
            configuration_snapshot=dict(source.configuration_snapshot or {}),
            source_lineage_snapshot={**(source.source_lineage_snapshot or {}), "parent_manual_dispatch_job_id": source.id, "parent_dispatch_version": source.dispatch_version},
            created_by=actor,
        )
        db.add(target)
        db.flush()
        source_los = db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.dispatch_job_id == source.id)).all()
        lo_map: dict[str, ManualDispatchLoadingOrder] = {}
        for lo in source_los:
            copied = ManualDispatchLoadingOrder(
                id=_id("MDLO"), dispatch_job_id=target.id, lo_id=lo.lo_id, lo_number=lo.lo_number,
                spbu_id=lo.spbu_id, spbu_number=lo.spbu_number, spbu_name=lo.spbu_name,
                product_id=lo.product_id, product_name=lo.product_name, volume_kl=lo.volume_kl,
                cluster_id=lo.cluster_id, cluster_name=lo.cluster_name, shift_id=lo.shift_id, shift_name=lo.shift_name,
                spbu_tags=list(lo.spbu_tags or []), assignment_status=lo.assignment_status, status_reason=lo.status_reason,
                source_snapshot=dict(lo.source_snapshot or {}),
            )
            db.add(copied)
            lo_map[lo.id] = copied
        source_vehicles = db.scalars(select(ManualDispatchVehicle).where(ManualDispatchVehicle.dispatch_job_id == source.id)).all()
        vehicle_map: dict[str, ManualDispatchVehicle] = {}
        for vehicle in source_vehicles:
            copied = ManualDispatchVehicle(
                id=_id("MDV"), dispatch_job_id=target.id, mt_id=vehicle.mt_id, vehicle_registration=vehicle.vehicle_registration,
                vehicle_class=vehicle.vehicle_class, capacity_kl=vehicle.capacity_kl, mt_tags=list(vehicle.mt_tags or []),
                number_of_compartments=vehicle.number_of_compartments, compartment_configuration=list(vehicle.compartment_configuration or []),
                initial_available_datetime=vehicle.initial_available_datetime, last_available_datetime=vehicle.last_available_datetime, status=vehicle.status,
            )
            db.add(copied)
            vehicle_map[vehicle.id] = copied
        db.flush()
        source_trips = db.scalars(select(ManualDispatchTrip).where(ManualDispatchTrip.dispatch_vehicle_id.in_(list(vehicle_map)))).all() if vehicle_map else []
        trip_map: dict[str, ManualDispatchTrip] = {}
        for trip in source_trips:
            copied = ManualDispatchTrip(
                id=_id("MDT"), dispatch_vehicle_id=vehicle_map[trip.dispatch_vehicle_id].id, trip_sequence=trip.trip_sequence,
                available_before_trip_datetime=trip.available_before_trip_datetime, departure_datetime=trip.departure_datetime,
                estimated_return_datetime=trip.estimated_return_datetime, turnaround_duration_seconds=trip.turnaround_duration_seconds,
                available_after_trip_datetime=trip.available_after_trip_datetime, distance_meter=trip.distance_meter,
                travel_duration_seconds=trip.travel_duration_seconds, service_duration_seconds=trip.service_duration_seconds,
                operational_buffer_seconds=trip.operational_buffer_seconds, total_duration_seconds=trip.total_duration_seconds,
                total_volume_kl=trip.total_volume_kl, status=trip.status, route_provider=trip.route_provider,
                route_response_status=trip.route_response_status, route_error_message=trip.route_error_message,
                route_geometry=list(trip.route_geometry or []), route_calculated_at=trip.route_calculated_at,
            )
            db.add(copied)
            trip_map[trip.id] = copied
        db.flush()
        source_assignments = db.scalars(select(ManualDispatchTripLO).where(ManualDispatchTripLO.dispatch_job_id == source.id)).all()
        for assignment in source_assignments:
            db.add(
                ManualDispatchTripLO(
                    id=_id("MDTL"), dispatch_job_id=target.id, trip_id=trip_map[assignment.trip_id].id,
                    manual_dispatch_lo_id=lo_map[assignment.manual_dispatch_lo_id].id, stop_sequence=assignment.stop_sequence,
                    estimated_arrival_datetime=assignment.estimated_arrival_datetime,
                )
            )
        source_legs = db.scalars(select(ManualDispatchRouteLeg).where(ManualDispatchRouteLeg.trip_id.in_(list(trip_map)))).all() if trip_map else []
        for leg in source_legs:
            db.add(
                ManualDispatchRouteLeg(
                    id=_id("MDLEG"), trip_id=trip_map[leg.trip_id].id, leg_sequence=leg.leg_sequence,
                    origin_type=leg.origin_type, origin_id=leg.origin_id, destination_type=leg.destination_type, destination_id=leg.destination_id,
                    origin_lat=leg.origin_lat, origin_lng=leg.origin_lng, destination_lat=leg.destination_lat, destination_lng=leg.destination_lng,
                    distance_meter=leg.distance_meter, duration_seconds=leg.duration_seconds, traffic_duration_seconds=leg.traffic_duration_seconds,
                    route_provider=leg.route_provider, request_timestamp=leg.request_timestamp, response_status=leg.response_status,
                )
            )
        DispatchAuditService.log(db, target, actor, "VERSION_CREATED", "JOB", target.id, old={"parent_job_id": source.id, "dispatch_version": source.dispatch_version}, new={"dispatch_version": target.dispatch_version})
        db.commit()
        return ManualDispatchJobService.detail(db, target.id)


class ManualDispatchFinalizeService:
    @staticmethod
    def finalize(
        db: Session,
        dispatch_job_id: str,
        *,
        actor: str,
        acknowledge_unassigned: bool = False,
        expected_job_version: int | None = None,
    ) -> dict:
        job = _job_or_404(db, dispatch_job_id)
        _assert_editable(job)
        _assert_version(job, expected_job_version)
        validation = DispatchValidationService.validate_dispatch(db, job.id)
        if not validation["valid"]:
            raise HTTPException(status_code=422, detail={"code": "FINALIZATION_VALIDATION_FAILED", "message": "Dispatch has unresolved hard validation errors.", "validation": validation})
        if validation["warnings"] and not acknowledge_unassigned:
            raise HTTPException(status_code=409, detail={"code": "UNASSIGNED_ACKNOWLEDGEMENT_REQUIRED", "message": "Unassigned Loading Orders require explicit acknowledgement before finalization.", "validation": validation})
        job.status = "FINALIZED"
        job.finalized_by = actor
        job.finalized_at = _now()
        _touch(job)
        DispatchAuditService.log(db, job, actor, "JOB_FINALIZED", "JOB", job.id, new={"status": job.status, "finalized_at": _iso(job.finalized_at)}, metadata=validation["summary"])
        db.commit()
        return {"finalized": True, "validation": validation, "workspace": ManualDispatchJobService.detail(db, job.id)}
