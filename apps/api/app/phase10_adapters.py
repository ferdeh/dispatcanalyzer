from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    ManualDispatchJob,
    ManualDispatchLoadingOrder,
    ManualDispatchTrip,
    ManualDispatchTripLO,
    ManualDispatchVehicle,
    MasterDepot,
    MasterMT,
    OperationalStateSnapshot,
    OptimizationJob,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionTrip,
    RouteVersionVehicleAssignment,
)
from .phase10_auth import IntegrationAPIError


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return aware_utc(value)
    if isinstance(value, str) and value:
        try:
            return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def depot_timezone(depot: MasterDepot | None) -> ZoneInfo:
    default_name = get_settings().phase10_default_timezone
    try:
        return ZoneInfo(depot.timezone if depot and depot.timezone else default_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Jakarta")


def local_datetime(value: datetime | None, depot: MasterDepot | None) -> datetime | None:
    aware = aware_utc(value)
    return aware.astimezone(depot_timezone(depot)) if aware else None


def local_day_start(job_date, depot: MasterDepot | None) -> datetime:
    return datetime.combine(job_date, time.min, tzinfo=depot_timezone(depot))


def canonical_status(source: str, value: str | None) -> str:
    status = (value or "UNKNOWN").upper()
    if source == "PHASE_7" and status in {"OPTIMAL", "FEASIBLE", "PARTIAL"}:
        return "COMPLETED"
    if source == "PHASE_8" and status == "FINALIZED":
        return "COMPLETED"
    return status


class Phase7RouteAdapter:
    source = "PHASE_7"
    prefix = "P7:"

    @classmethod
    def canonical_id(cls, internal_id: str) -> str:
        return f"{cls.prefix}{internal_id}"

    @classmethod
    def internal_id(cls, route_id: str) -> str | None:
        return route_id[len(cls.prefix):] if route_id.startswith(cls.prefix) else None

    @classmethod
    def summary(cls, db: Session, internal_id: str) -> dict:
        row = db.execute(
            select(RouteVersion, OptimizationJob, MasterDepot)
            .join(OptimizationJob, OptimizationJob.job_id == RouteVersion.job_id)
            .outerjoin(MasterDepot, MasterDepot.depot_id == OptimizationJob.depot_id)
            .where(RouteVersion.route_version_id == internal_id)
        ).first()
        if not row:
            raise IntegrationAPIError(404, "NOT_FOUND", "The selected Phase 7 route was not found.")
        version, job, depot = row
        vehicle_count = len(
            db.scalars(
                select(RouteVersionVehicleAssignment.vehicle_id).where(
                    RouteVersionVehicleAssignment.route_version_id == version.route_version_id,
                    RouteVersionVehicleAssignment.used.is_(True),
                )
            ).all()
        )
        if vehicle_count == 0:
            vehicle_count = len(set(db.scalars(select(RouteVersionTrip.vehicle_id).where(RouteVersionTrip.route_version_id == version.route_version_id)).all()))
        trip_count = len(db.scalars(select(RouteVersionTrip.route_version_trip_id).where(RouteVersionTrip.route_version_id == version.route_version_id)).all())
        created = local_datetime(version.created_at, depot) or local_day_start(job.operating_date, depot)
        return {
            "route_id": cls.canonical_id(version.route_version_id),
            "route_source": cls.source,
            "route_version": version.version_label,
            "operation_date": job.operating_date,
            "terminal_id": job.depot_id,
            "job_id": job.job_id,
            "route_name": f"{job.job_name} · {version.version_label}",
            "status": canonical_status(cls.source, version.solver_status),
            "vehicle_count": vehicle_count,
            "trip_count": trip_count,
            "created_at": created,
            "updated_at": created,
        }

    @classmethod
    def detail(cls, db: Session, internal_id: str) -> dict:
        summary = cls.summary(db, internal_id)
        version = db.get(RouteVersion, internal_id)
        job = db.get(OptimizationJob, version.job_id)
        depot = db.get(MasterDepot, job.depot_id)
        trips = db.scalars(
            select(RouteVersionTrip)
            .where(RouteVersionTrip.route_version_id == internal_id)
            .order_by(RouteVersionTrip.vehicle_id, RouteVersionTrip.trip_number, RouteVersionTrip.gate_out)
        ).all()
        assignments = db.scalars(
            select(RouteVersionLOAssignment)
            .where(
                RouteVersionLOAssignment.route_version_id == internal_id,
                RouteVersionLOAssignment.route_version_trip_id.is_not(None),
            )
            .order_by(RouteVersionLOAssignment.stop_sequence, RouteVersionLOAssignment.loading_order_id)
        ).all()
        assignments_by_trip: dict[str, list[RouteVersionLOAssignment]] = defaultdict(list)
        for assignment in assignments:
            assignments_by_trip[str(assignment.route_version_trip_id)].append(assignment)

        vehicle_assignments = db.scalars(
            select(RouteVersionVehicleAssignment)
            .where(RouteVersionVehicleAssignment.route_version_id == internal_id)
            .order_by(RouteVersionVehicleAssignment.vehicle_id)
        ).all()
        vehicle_ids = {row.vehicle_id for row in vehicle_assignments}
        vehicle_ids.update(row.vehicle_id for row in trips if row.vehicle_id)
        masters = {
            row.mt_id: row
            for row in db.scalars(select(MasterMT).where(MasterMT.mt_id.in_(vehicle_ids))).all()
        } if vehicle_ids else {}
        snapshot = db.get(OperationalStateSnapshot, version.state_snapshot_id)
        snapshot_by_vehicle = {
            str(row.get("mt_id")): row
            for row in ((snapshot.vehicle_state_snapshot or []) if snapshot else [])
            if isinstance(row, dict) and row.get("mt_id")
        }
        trips_by_vehicle: dict[str, list[dict]] = defaultdict(list)
        initial_by_vehicle: dict[str, datetime] = {}
        for trip in trips:
            departure = local_datetime(trip.gate_out, depot)
            returned = local_datetime(trip.estimated_return_depot, depot)
            if departure is None or returned is None:
                timeline_status = "INCOMPLETE"
            elif returned < departure:
                timeline_status = "INVALID"
            else:
                timeline_status = "VALID"
            lo_rows = assignments_by_trip[trip.route_version_trip_id]
            spbus: list[str] = []
            for row in lo_rows:
                if row.spbu_id and row.spbu_id not in spbus:
                    spbus.append(row.spbu_id)
            trips_by_vehicle[trip.vehicle_id].append({
                "trip_number": trip.trip_number,
                "trip_id": trip.route_version_trip_id,
                "departure_at": departure,
                "return_to_depot_at": returned,
                "loading_orders": [row.loading_order_id for row in lo_rows],
                "spbu_ids": spbus,
                "total_volume_kl": round(sum(float(row.volume_kl or 0) for row in lo_rows), 3),
                "timeline_status": timeline_status,
            })
            ready = local_datetime(trip.vehicle_ready_at_depot, depot)
            if ready and (trip.vehicle_id not in initial_by_vehicle or ready < initial_by_vehicle[trip.vehicle_id]):
                initial_by_vehicle[trip.vehicle_id] = ready

        vehicles: list[dict] = []
        for mt_id in sorted(vehicle_ids, key=lambda key: ((masters.get(key).vehicle_registration or "") if masters.get(key) else "", key)):
            master = masters.get(mt_id)
            snapshot_row = snapshot_by_vehicle.get(mt_id, {})
            initial = initial_by_vehicle.get(mt_id)
            if initial is None:
                initial = local_datetime(
                    parse_datetime(snapshot_row.get("effective_eta_depot") or snapshot_row.get("planned_eta_depot")),
                    depot,
                )
            capacity = float(master.vehicle_type_tag) if master and master.vehicle_type_tag is not None else None
            if capacity is None and isinstance(snapshot_row.get("capacity_kl"), (int, float)):
                capacity = float(snapshot_row["capacity_kl"])
            vehicles.append({
                "mt_id": mt_id,
                "vehicle_number": (master.vehicle_registration if master else None) or snapshot_row.get("registration"),
                "capacity_kl": capacity,
                "initial_available_at": initial,
                "operational_status": snapshot_row.get("operational_status"),
                "trip_count": len(trips_by_vehicle[mt_id]),
                "trips": trips_by_vehicle[mt_id],
            })
        return {**summary, "vehicles": vehicles}


class Phase8RouteAdapter:
    source = "PHASE_8"
    prefix = "P8:"

    @classmethod
    def canonical_id(cls, internal_id: str) -> str:
        return f"{cls.prefix}{internal_id}"

    @classmethod
    def internal_id(cls, route_id: str) -> str | None:
        return route_id[len(cls.prefix):] if route_id.startswith(cls.prefix) else None

    @classmethod
    def _job(cls, db: Session, internal_id: str) -> ManualDispatchJob:
        job = db.get(ManualDispatchJob, internal_id)
        if not job:
            job = db.scalar(select(ManualDispatchJob).where(ManualDispatchJob.job_id == internal_id))
        if not job:
            raise IntegrationAPIError(404, "NOT_FOUND", "The selected Phase 8 route was not found.")
        return job

    @classmethod
    def summary(cls, db: Session, internal_id: str) -> dict:
        job = cls._job(db, internal_id)
        depot = db.get(MasterDepot, job.depot_id)
        vehicle_ids = db.scalars(select(ManualDispatchVehicle.id).where(ManualDispatchVehicle.dispatch_job_id == job.id)).all()
        trip_count = len(
            db.scalars(select(ManualDispatchTrip.id).where(ManualDispatchTrip.dispatch_vehicle_id.in_(vehicle_ids))).all()
        ) if vehicle_ids else 0
        created = local_datetime(job.created_at, depot) or local_day_start(job.operational_date, depot)
        updated = local_datetime(job.updated_at, depot) or created
        return {
            "route_id": cls.canonical_id(job.id),
            "route_source": cls.source,
            "route_version": f"R{job.dispatch_version}",
            "operation_date": job.operational_date,
            "terminal_id": job.depot_id,
            "job_id": job.job_id,
            "route_name": job.job_name,
            "status": canonical_status(cls.source, job.status),
            "vehicle_count": len(vehicle_ids),
            "trip_count": trip_count,
            "created_at": created,
            "updated_at": updated,
        }

    @classmethod
    def detail(cls, db: Session, internal_id: str) -> dict:
        job = cls._job(db, internal_id)
        summary = cls.summary(db, job.id)
        depot = db.get(MasterDepot, job.depot_id)
        vehicles = db.scalars(
            select(ManualDispatchVehicle)
            .where(ManualDispatchVehicle.dispatch_job_id == job.id)
            .order_by(ManualDispatchVehicle.vehicle_registration, ManualDispatchVehicle.mt_id)
        ).all()
        vehicle_ids = [row.id for row in vehicles]
        trips = db.scalars(
            select(ManualDispatchTrip)
            .where(ManualDispatchTrip.dispatch_vehicle_id.in_(vehicle_ids))
            .order_by(ManualDispatchTrip.dispatch_vehicle_id, ManualDispatchTrip.trip_sequence)
        ).all() if vehicle_ids else []
        trip_ids = [row.id for row in trips]
        joins = db.scalars(
            select(ManualDispatchTripLO)
            .where(ManualDispatchTripLO.trip_id.in_(trip_ids))
            .order_by(ManualDispatchTripLO.trip_id, ManualDispatchTripLO.stop_sequence)
        ).all() if trip_ids else []
        lo_ids = [row.manual_dispatch_lo_id for row in joins]
        loading_orders = {
            row.id: row
            for row in db.scalars(select(ManualDispatchLoadingOrder).where(ManualDispatchLoadingOrder.id.in_(lo_ids))).all()
        } if lo_ids else {}
        joins_by_trip: dict[str, list[ManualDispatchTripLO]] = defaultdict(list)
        for row in joins:
            joins_by_trip[row.trip_id].append(row)
        trips_by_vehicle: dict[str, list[dict]] = defaultdict(list)
        for trip in trips:
            departure = local_datetime(trip.departure_datetime, depot)
            returned = local_datetime(trip.estimated_return_datetime, depot)
            if departure is None or returned is None:
                timeline_status = "INCOMPLETE"
            elif returned < departure:
                timeline_status = "INVALID"
            else:
                timeline_status = "VALID"
            trip_los = [loading_orders[row.manual_dispatch_lo_id] for row in joins_by_trip[trip.id] if row.manual_dispatch_lo_id in loading_orders]
            spbus: list[str] = []
            for row in trip_los:
                if row.spbu_id and row.spbu_id not in spbus:
                    spbus.append(row.spbu_id)
            trips_by_vehicle[trip.dispatch_vehicle_id].append({
                "trip_number": trip.trip_sequence,
                "trip_id": trip.id,
                "departure_at": departure,
                "return_to_depot_at": returned,
                "loading_orders": [row.lo_number for row in trip_los],
                "spbu_ids": spbus,
                "total_volume_kl": round(float(trip.total_volume_kl or sum(float(row.volume_kl or 0) for row in trip_los)), 3),
                "timeline_status": timeline_status,
            })
        result = []
        for vehicle in vehicles:
            result.append({
                "mt_id": vehicle.mt_id,
                "vehicle_number": vehicle.vehicle_registration,
                "capacity_kl": float(vehicle.capacity_kl) if vehicle.capacity_kl is not None else None,
                "initial_available_at": local_datetime(vehicle.initial_available_datetime, depot),
                "operational_status": vehicle.status,
                "trip_count": len(trips_by_vehicle[vehicle.id]),
                "trips": trips_by_vehicle[vehicle.id],
            })
        return {**summary, "vehicles": result}


def route_adapter(route_id: str):
    if route_id.startswith(Phase7RouteAdapter.prefix):
        return Phase7RouteAdapter, Phase7RouteAdapter.internal_id(route_id)
    if route_id.startswith(Phase8RouteAdapter.prefix):
        return Phase8RouteAdapter, Phase8RouteAdapter.internal_id(route_id)
    raise IntegrationAPIError(404, "NOT_FOUND", "Route IDs must use a P7: or P8: canonical prefix.")


def route_detail(db: Session, route_id: str) -> dict:
    adapter, internal_id = route_adapter(route_id)
    return adapter.detail(db, str(internal_id))
