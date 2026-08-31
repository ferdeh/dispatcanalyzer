from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import get_db
from .manual_dispatch_service import (
    DispatchAuditService,
    DispatchDashboardService,
    DispatchEligibilityService,
    DispatchGeographicMapService,
    DispatchRouteService,
    DispatchSimulationService,
    DispatchTripService,
    DispatchValidationService,
    DispatchVersionService,
    ManualDispatchFinalizeService,
    ManualDispatchJobService,
    ManualDispatchSnapshotService,
)
from .phase8_auth import Phase8Actor, require_phase8_permission


router = APIRouter(prefix="/api/v1/phase8/manual-dispatch", tags=["Phase 8 - Manual Dispatching and Operational Simulation"])


class ManualDispatchCreateRequest(BaseModel):
    depot_id: str
    operational_date: date
    source_job_id: str
    source_route_id: str
    job_name: str = Field(min_length=1, max_length=255)
    operational_buffer_minutes: int = Field(default=0, ge=0, le=1440)


class VersionCreateRequest(BaseModel):
    expected_job_version: int | None = None
    job_name: str | None = Field(default=None, min_length=1, max_length=255)


class TripCreateRequest(BaseModel):
    vehicle_id: str
    expected_job_version: int | None = None


class TripUpdateRequest(BaseModel):
    departure_datetime: datetime | None = None
    expected_job_version: int | None = None
    expected_trip_version: int | None = None


class TripLORequest(BaseModel):
    lo_scope_id: str
    expected_job_version: int | None = None


class MoveLORequest(BaseModel):
    destination_trip_id: str
    expected_job_version: int | None = None
    reason: str | None = Field(default=None, max_length=500)


class StopOrderRequest(BaseModel):
    lo_scope_ids: list[str] = Field(min_length=1)
    expected_job_version: int | None = None


class ApplyTripRequest(BaseModel):
    expected_job_version: int | None = None
    expected_trip_version: int | None = None


class FinalizeRequest(BaseModel):
    acknowledge_unassigned: bool = False
    expected_job_version: int | None = None


@router.get("/sources")
def manual_dispatch_sources(
    depot_id: str,
    operational_date: date | None = None,
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return ManualDispatchJobService.source_options(db, depot_id, operational_date)


@router.get("/jobs")
def manual_dispatch_jobs(
    depot_id: str | None = None,
    operational_date: date | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return ManualDispatchJobService.list(
        db,
        depot_id=depot_id,
        operational_date=operational_date,
        status=status_filter,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_manual_dispatch_job(
    request: ManualDispatchCreateRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return ManualDispatchSnapshotService.create(db, request.model_dump(mode="json"), actor=actor.user_id)


@router.get("/jobs/{job_id}")
def manual_dispatch_job(
    job_id: str,
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return ManualDispatchJobService.detail(db, job_id)


@router.post("/jobs/{job_id}/versions", status_code=status.HTTP_201_CREATED)
def create_manual_dispatch_version(
    job_id: str,
    request: VersionCreateRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchVersionService.create_version(
        db,
        job_id,
        actor=actor.user_id,
        expected_job_version=request.expected_job_version,
        job_name=request.job_name,
    )


@router.get("/jobs/{job_id}/vehicles/{vehicle_id}/eligible-loading-orders")
def eligible_loading_orders(
    job_id: str,
    vehicle_id: str,
    trip_id: str | None = None,
    include_ineligible: bool = False,
    search: str | None = None,
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return DispatchEligibilityService.eligible_los(
        db,
        job_id,
        vehicle_id,
        trip_id=trip_id,
        include_ineligible=include_ineligible,
        search=search,
    )


@router.post("/jobs/{job_id}/trips", status_code=status.HTTP_201_CREATED)
def create_manual_dispatch_trip(
    job_id: str,
    request: TripCreateRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.create_trip(db, job_id, request.vehicle_id, actor=actor.user_id, expected_job_version=request.expected_job_version)


@router.patch("/jobs/{job_id}/trips/{trip_id}")
def update_manual_dispatch_trip(
    job_id: str,
    trip_id: str,
    request: TripUpdateRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.update_trip(db, job_id, trip_id, request.model_dump(mode="json", exclude_unset=True), actor=actor.user_id)


@router.delete("/jobs/{job_id}/trips/{trip_id}")
def delete_manual_dispatch_trip(
    job_id: str,
    trip_id: str,
    expected_job_version: int | None = None,
    reason: str | None = None,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.delete_trip(db, job_id, trip_id, actor=actor.user_id, expected_job_version=expected_job_version, reason=reason)


@router.post("/jobs/{job_id}/trips/{trip_id}/loading-orders")
def add_manual_dispatch_lo(
    job_id: str,
    trip_id: str,
    request: TripLORequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.add_lo(db, job_id, trip_id, request.lo_scope_id, actor=actor.user_id, expected_job_version=request.expected_job_version)


@router.delete("/jobs/{job_id}/trips/{trip_id}/loading-orders/{lo_scope_id}")
def remove_manual_dispatch_lo(
    job_id: str,
    trip_id: str,
    lo_scope_id: str,
    expected_job_version: int | None = None,
    reason: str | None = None,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.remove_lo(db, job_id, trip_id, lo_scope_id, actor=actor.user_id, expected_job_version=expected_job_version, reason=reason)


@router.post("/jobs/{job_id}/loading-orders/{lo_scope_id}/move")
def move_manual_dispatch_lo(
    job_id: str,
    lo_scope_id: str,
    request: MoveLORequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.move_lo(
        db, job_id, lo_scope_id, request.destination_trip_id,
        actor=actor.user_id, expected_job_version=request.expected_job_version, reason=request.reason,
    )


@router.put("/jobs/{job_id}/trips/{trip_id}/stop-order")
def reorder_manual_dispatch_los(
    job_id: str,
    trip_id: str,
    request: StopOrderRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchTripService.reorder_los(db, job_id, trip_id, request.lo_scope_ids, actor=actor.user_id, expected_job_version=request.expected_job_version)


@router.post("/jobs/{job_id}/trips/{trip_id}/apply")
def apply_manual_dispatch_trip(
    job_id: str,
    trip_id: str,
    request: ApplyTripRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("edit")),
) -> dict:
    return DispatchRouteService.apply_trip(
        db, job_id, trip_id, actor=actor.user_id,
        expected_job_version=request.expected_job_version, expected_trip_version=request.expected_trip_version,
    )


@router.get("/jobs/{job_id}/simulation")
def manual_dispatch_simulation(
    job_id: str,
    bucket_minutes: int = Query(default=60),
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return DispatchSimulationService.build(db, job_id, bucket_minutes=bucket_minutes)


@router.get("/jobs/{job_id}/map")
def manual_dispatch_geographic_map(
    job_id: str,
    vehicle_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return DispatchGeographicMapService.build(db, job_id, vehicle_id)


@router.get("/jobs/{job_id}/dashboard")
def manual_dispatch_dashboard(
    job_id: str,
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return DispatchDashboardService.build(db, job_id)


@router.get("/jobs/{job_id}/audit")
def manual_dispatch_audit(
    job_id: str,
    limit: int = Query(default=250, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return DispatchAuditService.list(db, job_id, limit=limit, offset=offset)


@router.get("/jobs/{job_id}/validation")
def validate_manual_dispatch(
    job_id: str,
    db: Session = Depends(get_db),
    _actor: Phase8Actor = Depends(require_phase8_permission("view")),
) -> dict:
    return DispatchValidationService.validate_dispatch(db, job_id)


@router.post("/jobs/{job_id}/finalize")
def finalize_manual_dispatch(
    job_id: str,
    request: FinalizeRequest,
    db: Session = Depends(get_db),
    actor: Phase8Actor = Depends(require_phase8_permission("finalize")),
) -> dict:
    return ManualDispatchFinalizeService.finalize(
        db,
        job_id,
        actor=actor.user_id,
        acknowledge_unassigned=request.acknowledge_unassigned,
        expected_job_version=request.expected_job_version,
    )
