from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(StrictResponse):
    code: str
    message: str
    request_id: str


class ErrorResponse(StrictResponse):
    error: ErrorDetail


class HealthResponse(StrictResponse):
    status: Literal["ok"]
    service: Literal["amt-scheduler-connector"]
    api_version: Literal["v1"]
    server_time: datetime


class DatasetVersion(StrictResponse):
    data_version: str
    last_updated_at: datetime | None
    data_until: date | None = None


class MetadataResponse(StrictResponse):
    system: Literal["dispatcher-optimizer"]
    integration: Literal["amt-scheduler"]
    api_version: Literal["v1"]
    server_time: datetime
    datasets: dict[str, DatasetVersion]


class TerminalRecord(StrictResponse):
    terminal_id: str
    terminal_code: str | None
    terminal_name: str
    terminal_type: str | None
    address: str | None
    latitude: float | None
    longitude: float | None
    timezone: str
    active: bool
    updated_at: datetime


class CompartmentRecord(StrictResponse):
    compartment_no: int
    capacity_kl: float | None


class VehicleRecord(StrictResponse):
    mt_id: str
    terminal_id: str | None
    vehicle_code: str | None
    vehicle_number: str | None
    vehicle_class: str | None
    capacity_kl: float | None
    compartment_count: int | None
    compartments: list[CompartmentRecord]
    vehicle_tags: list[str]
    vehicle_status: str
    operational_status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
    active: bool
    updated_at: datetime


class HistoricalOperationRecord(StrictResponse):
    operation_date: date
    terminal_id: str
    mt_id: str | None
    vehicle_number: str | None
    shipment_id: str
    trip_id: str
    trip_number: int
    gate_out_at: datetime | None
    returned_to_depot_at: datetime | None
    trip_duration_minutes: int | None
    total_volume_kl: float
    spbu_count: int
    spbu_ids: list[str]
    loading_orders: list[str]
    amt1_id: None = None
    amt2_id: None = None
    updated_at: datetime


class RouteSummary(StrictResponse):
    route_id: str
    route_source: Literal["PHASE_7", "PHASE_8"]
    route_version: str
    operation_date: date
    terminal_id: str
    job_id: str
    route_name: str
    status: str
    vehicle_count: int
    trip_count: int
    created_at: datetime
    updated_at: datetime


class CanonicalTrip(StrictResponse):
    trip_number: int
    trip_id: str
    departure_at: datetime | None
    return_to_depot_at: datetime | None
    loading_orders: list[str]
    spbu_ids: list[str]
    total_volume_kl: float
    timeline_status: Literal["VALID", "INCOMPLETE", "INVALID"]


class CanonicalRouteVehicle(StrictResponse):
    mt_id: str
    vehicle_number: str | None
    capacity_kl: float | None
    initial_available_at: datetime | None
    operational_status: str | None
    trip_count: int
    trips: list[CanonicalTrip]


class RouteDetail(RouteSummary):
    vehicles: list[CanonicalRouteVehicle]


class AvailabilityRecord(StrictResponse):
    mt_id: str
    vehicle_number: str | None
    capacity_kl: float | None
    availability_status: Literal["AVAILABLE", "ON_TRIP", "UNAVAILABLE", "UNKNOWN"]
    availability_reason: str
    active_trip_id: str | None
    available_at: datetime | None
    next_available_at: datetime | None
    next_trip_id: str | None
    next_departure_at: datetime | None
    handover_window_minutes: int | None


class PaginatedResponse(StrictResponse):
    data_version: str | None = None
    page: int
    page_size: int
    total_records: int
    total_pages: int
    records: list[Any]


class TerminalListResponse(PaginatedResponse):
    records: list[TerminalRecord]


class VehicleListResponse(PaginatedResponse):
    records: list[VehicleRecord]


class HistoricalOperationListResponse(PaginatedResponse):
    records: list[HistoricalOperationRecord]


class RouteListResponse(PaginatedResponse):
    records: list[RouteSummary]


class RouteVehicleRecord(StrictResponse):
    mt_id: str
    vehicle_number: str | None
    capacity_kl: float | None
    initial_available_at: datetime | None
    trip_count: int


class RouteVehicleListResponse(PaginatedResponse):
    route_id: str
    records: list[RouteVehicleRecord]


class ShiftRecord(StrictResponse):
    shift_id: str
    shift_name: str
    start_at: datetime
    end_at: datetime


class ShiftResponse(StrictResponse):
    operation_date: date
    terminal_id: str
    data_version: str
    source: str | None
    records: list[ShiftRecord]


class AvailabilityAfterResponse(StrictResponse):
    route_id: str
    route_source: Literal["PHASE_7", "PHASE_8"]
    operation_date: date
    terminal_id: str
    reference_at: datetime
    records: list[AvailabilityRecord]


class ShiftAvailabilityGroup(StrictResponse):
    shift_id: str
    shift_name: str
    shift_start_at: datetime
    available_mt_count: int
    available_capacity_kl: float
    vehicles: list[AvailabilityRecord]


class ShiftAvailabilityResponse(StrictResponse):
    route_id: str
    route_source: Literal["PHASE_7", "PHASE_8"]
    operation_date: date
    terminal_id: str
    shifts: list[ShiftAvailabilityGroup]


class RegeneratedCredentialResponse(StrictResponse):
    client_id: str
    client_name: str
    client_code: str
    bearer_token: str = Field(description="Returned once. Only its SHA-256 hash is persisted.")
    token_hint: str
    created_at: datetime
