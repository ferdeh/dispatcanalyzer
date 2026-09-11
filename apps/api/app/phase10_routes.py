from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from .database import get_db
from .phase10_auth import (
    IntegrationPrincipal,
    Phase10Actor,
    require_integration_client,
    require_phase10_permission,
)
from .phase10_schemas import (
    AvailabilityAfterResponse,
    ErrorResponse,
    HealthResponse,
    HistoricalOperationListResponse,
    MetadataResponse,
    RegeneratedCredentialResponse,
    RouteDetail,
    RouteListResponse,
    RouteVehicleListResponse,
    ShiftAvailabilityResponse,
    ShiftResponse,
    TerminalListResponse,
    TerminalRecord,
    VehicleListResponse,
    VehicleRecord,
)
from .phase10_service import (
    api_log_detail,
    availability_after,
    console_access,
    console_overview,
    get_route,
    get_terminal,
    get_vehicle,
    list_api_logs,
    list_historical_operations,
    list_routes,
    list_terminals,
    list_vehicles,
    mark_record_count,
    metadata,
    regenerate_credential,
    route_vehicles,
    shift_availability,
    shift_records,
    timezone_for_name,
    utc_now,
)


BASE_PATH = "/api/v1/integration/amt-scheduler"
ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Invalid request or date range"},
    401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"},
    403: {"model": ErrorResponse, "description": "Client or console actor lacks permission"},
    404: {"model": ErrorResponse, "description": "Canonical resource not found"},
    422: {"model": ErrorResponse, "description": "Validation or source-data quality error"},
    429: {"model": ErrorResponse, "description": "Per-client request rate exceeded"},
    500: {"model": ErrorResponse, "description": "Sanitized internal connector failure"},
}
router = APIRouter(prefix=BASE_PATH, tags=["AMT Scheduler Connector"], responses=ERROR_RESPONSES)
console_router = APIRouter(prefix=f"{BASE_PATH}/console", tags=["AMT Scheduler Connector Console"], responses=ERROR_RESPONSES)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Test AMT Scheduler connector reachability",
    description="Unauthenticated liveness check. No operational data is returned.",
)
def health(request: Request) -> dict:
    mark_record_count(request, 1)
    return {
        "status": "ok",
        "service": "amt-scheduler-connector",
        "api_version": "v1",
        "server_time": utc_now().astimezone(timezone_for_name(None)),
    }


@router.get(
    "/metadata",
    response_model=MetadataResponse,
    summary="Read logical dataset versions before synchronization",
)
def connector_metadata(
    request: Request,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = metadata(db)
    mark_record_count(request, len(payload["datasets"]))
    return payload


@router.get("/terminals", response_model=TerminalListResponse, summary="List canonical terminal master data")
def terminals(
    request: Request,
    active: bool | None = None,
    updated_since: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=5000),
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = list_terminals(db, active=active, updated_since=updated_since, page=page, page_size=page_size)
    mark_record_count(request, len(payload["records"]))
    return payload


@router.get("/terminals/{terminal_id}", response_model=TerminalRecord, summary="Read one terminal by stable ID")
def terminal_detail(
    terminal_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = get_terminal(db, terminal_id)
    mark_record_count(request, 1)
    return payload


@router.get("/vehicles", response_model=VehicleListResponse, summary="List canonical Mobil Tangki master data")
def vehicles(
    request: Request,
    terminal_id: str | None = None,
    active: bool | None = None,
    vehicle_status: str | None = None,
    updated_since: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=5000),
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = list_vehicles(
        db,
        terminal_id=terminal_id,
        active=active,
        vehicle_status=vehicle_status,
        updated_since=updated_since,
        page=page,
        page_size=page_size,
    )
    mark_record_count(request, len(payload["records"]))
    return payload


@router.get("/vehicles/{mt_id}", response_model=VehicleRecord, summary="Read one Mobil Tangki by stable ID")
def vehicle_detail(
    mt_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = get_vehicle(db, mt_id)
    mark_record_count(request, 1)
    return payload


@router.get(
    "/historical-operations",
    response_model=HistoricalOperationListResponse,
    summary="List historical distribution operations at trip grain",
    description="One shipment/trip is returned once even when it owns multiple loading orders.",
)
def historical_operations(
    request: Request,
    terminal_id: str,
    date_from: date,
    date_to: date,
    mt_id: str | None = None,
    updated_since: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = list_historical_operations(
        db,
        terminal_id=terminal_id,
        date_from=date_from,
        date_to=date_to,
        mt_id=mt_id,
        updated_since=updated_since,
        page=page,
        page_size=page_size,
    )
    mark_record_count(request, len(payload["records"]))
    return payload


@router.get(
    "/routes",
    response_model=RouteListResponse,
    summary="List every Phase 7 and Phase 8 route result for an operation date",
    description="Returns all valid versions, not only a job's current/latest route.",
)
def routes(
    request: Request,
    operation_date: date,
    terminal_id: str | None = None,
    route_source: str | None = Query(default=None, pattern="^(PHASE_7|PHASE_8)$"),
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=5000),
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = list_routes(
        db,
        operation_date=operation_date,
        terminal_id=terminal_id,
        route_source=route_source,
        status=status,
        page=page,
        page_size=page_size,
    )
    mark_record_count(request, len(payload["records"]))
    return payload


@router.get("/routes/{route_id}", response_model=RouteDetail, summary="Read a canonical Phase 7 or Phase 8 route timeline")
def route_result_detail(
    route_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = get_route(db, route_id)
    mark_record_count(request, len(payload["vehicles"]))
    return payload


@router.get("/routes/{route_id}/vehicles", response_model=RouteVehicleListResponse, summary="List vehicles included in a selected route")
def selected_route_vehicles(
    route_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=5000),
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = route_vehicles(db, route_id, page, page_size)
    mark_record_count(request, len(payload["records"]))
    return payload


@router.get(
    "/routes/{route_id}/shift-availability",
    response_model=ShiftAvailabilityResponse,
    summary="Calculate route-specific MT availability at authoritative shift boundaries",
    description="Departure is inclusive and return is exclusive: departure <= shift start < return means ON_TRIP.",
)
def selected_route_shift_availability(
    route_id: str,
    request: Request,
    shift_id: str | None = None,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = shift_availability(db, route_id, shift_id)
    mark_record_count(request, sum(len(row["vehicles"]) for row in payload["shifts"]))
    return payload


@router.get(
    "/routes/{route_id}/availability-after",
    response_model=AvailabilityAfterResponse,
    summary="Calculate when every MT is available on or after a timestamp",
    description="The result is scoped strictly to the selected route. It does not recommend AMT arrival or perform scheduling.",
)
def selected_route_availability_after(
    route_id: str,
    request: Request,
    timestamp: datetime,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = availability_after(db, route_id, timestamp)
    mark_record_count(request, len(payload["records"]))
    return payload


@router.get("/shifts", response_model=ShiftResponse, summary="Read the authoritative shift definition for a terminal and date")
def shifts(
    request: Request,
    terminal_id: str,
    operation_date: date,
    db: Session = Depends(get_db),
    _client: IntegrationPrincipal = Depends(require_integration_client),
) -> dict:
    payload = shift_records(db, terminal_id, operation_date)
    mark_record_count(request, len(payload["records"]))
    return payload


@console_router.get("/overview", summary="Read internal connector health and usage KPIs")
def internal_overview(
    request: Request,
    db: Session = Depends(get_db),
    _actor: Phase10Actor = Depends(require_phase10_permission("view")),
) -> dict:
    return console_overview(db, f"{str(request.base_url).rstrip('/')}{BASE_PATH}")


@console_router.get("/access", summary="Read masked integration credential metadata")
def internal_access(
    request: Request,
    db: Session = Depends(get_db),
    _actor: Phase10Actor = Depends(require_phase10_permission("view")),
) -> dict:
    return console_access(db, f"{str(request.base_url).rstrip('/')}{BASE_PATH}")


@console_router.post("/access/regenerate", response_model=RegeneratedCredentialResponse, summary="Rotate the AMT Scheduler bearer token")
def internal_regenerate_access(
    db: Session = Depends(get_db),
    actor: Phase10Actor = Depends(require_phase10_permission("manage_credentials")),
) -> dict:
    return regenerate_credential(db, actor=actor.user_id)


@console_router.get("/try-permission", summary="Authorize the internal Try API console")
def internal_try_permission(
    actor: Phase10Actor = Depends(require_phase10_permission("try_api")),
) -> dict:
    return {"allowed": True, "actor": actor.user_id}


@console_router.get("/logs", summary="List sanitized AMT Scheduler API request logs")
def internal_logs(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    response_status: int | None = None,
    endpoint: str | None = None,
    client_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _actor: Phase10Actor = Depends(require_phase10_permission("view_logs")),
) -> dict:
    return list_api_logs(
        db,
        date_from=date_from,
        date_to=date_to,
        response_status=response_status,
        endpoint=endpoint,
        client_id=client_id,
        page=page,
        page_size=page_size,
    )


@console_router.get("/logs/{log_id}", summary="Read one sanitized API request log")
def internal_log_detail(
    log_id: str,
    db: Session = Depends(get_db),
    _actor: Phase10Actor = Depends(require_phase10_permission("view_logs")),
) -> dict:
    return api_log_detail(db, log_id)
