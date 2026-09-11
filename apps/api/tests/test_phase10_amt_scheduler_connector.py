from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    DepartureShiftAnalysisConfig,
    FactLoadingOrderLine,
    FactShipment,
    IntegrationAPILog,
    IntegrationClient,
    MLBehavioralModel,
    ManualDispatchJob,
    ManualDispatchLoadingOrder,
    ManualDispatchTrip,
    ManualDispatchTripLO,
    ManualDispatchVehicle,
    MasterDepot,
    MasterMT,
    OptimizationJob,
    OperationalStateSnapshot,
    PredictionRun,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionTrip,
    RouteVersionVehicleAssignment,
)
from app.phase10_auth import hash_integration_token


TOKEN = "amt_test-token-that-is-never-written-to-the-api-log"
UTC = timezone.utc
DAY = date(2026, 9, 7)
START = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


@pytest.fixture()
def phase10_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        _seed(db)

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.state.phase10_session_factory = Session
    try:
        yield TestClient(app), Session
    finally:
        app.dependency_overrides.clear()
        del app.state.phase10_session_factory


def _seed(db) -> None:
    db.add(MasterDepot(depot_id="D1", depot_code="MEDAN", depot_name="Integrated Terminal Medan", timezone="UTC", active_status="ACTIVE"))
    db.add_all([
        MasterMT(mt_id="MT1", source_mt_id="MT001", vehicle_name_raw="MT 1", vehicle_registration="BK 1001 AA", vehicle_type_tag=24, number_of_compartments=3, depot_id="D1", active_status="ACTIVE"),
        MasterMT(mt_id="MT2", source_mt_id="MT002", vehicle_name_raw="MT 2", vehicle_registration="BK 1002 AA", vehicle_type_tag=16, number_of_compartments=2, depot_id="D1", active_status="INACTIVE"),
    ])
    db.add(
        MLBehavioralModel(
            model_id="MODEL1",
            model_name="Shift Model",
            model_version=1,
            depot_id="D1",
            training_start_date=date(2026, 7, 1),
            training_end_date=date(2026, 8, 31),
            shift_definition_snapshot=[
                {"shift_id": "SHIFT-1", "name": "Shift 1", "start_time": "00:00", "end_time": "07:59"},
                {"shift_id": "SHIFT-2", "name": "Shift 2", "start_time": "08:00", "end_time": "15:59"},
                {"shift_id": "SHIFT-3", "name": "Shift 3", "start_time": "16:00", "end_time": "23:59"},
            ],
            model_status="SAVED",
        )
    )
    db.add(PredictionRun(id="RUN1", prediction_run_no="RUN-1", depot_id="D1", model_id="MODEL1", model_version=1, status="COMPLETED", input_loading_order_filename="lo.xlsx", input_mt_availability_filename="mt.xlsx", algorithm_version="test"))
    db.add(OptimizationJob(job_id="JOB1", job_no="P7-001", job_name="Optimization", depot_id="D1", operating_date=DAY, source_prediction_run_id="RUN1", current_route_version_id="RV2", status="READY"))
    db.add_all([
        OperationalStateSnapshot(state_snapshot_id="S1", job_id="JOB1", snapshot_reason="test", vehicle_state_snapshot=[{"mt_id": "MT1", "effective_eta_depot": START.isoformat(), "capacity_kl": 24}, {"mt_id": "MT2", "effective_eta_depot": START.isoformat(), "capacity_kl": 16}]),
        OperationalStateSnapshot(state_snapshot_id="S2", job_id="JOB1", snapshot_reason="test", vehicle_state_snapshot=[{"mt_id": "MT1", "effective_eta_depot": START.isoformat(), "capacity_kl": 24}, {"mt_id": "MT2", "effective_eta_depot": START.isoformat(), "capacity_kl": 16}]),
    ])
    db.add_all([
        RouteVersion(route_version_id="RV1", job_id="JOB1", version_number=1, version_label="V1", reason="initial", state_snapshot_id="S1", parameter_snapshot_id="P1", objective="MIN_DISTANCE", solver_status="FEASIBLE"),
        RouteVersion(route_version_id="RV2", job_id="JOB1", version_number=2, version_label="V2", reason="reroute", state_snapshot_id="S2", parameter_snapshot_id="P2", objective="MIN_DISTANCE", solver_status="FEASIBLE"),
    ])
    db.add_all([
        RouteVersionVehicleAssignment(route_version_vehicle_assignment_id="VA11", route_version_id="RV1", vehicle_id="MT1", used=True, trip_count=1),
        RouteVersionVehicleAssignment(route_version_vehicle_assignment_id="VA12", route_version_id="RV1", vehicle_id="MT2", used=False, trip_count=0),
        RouteVersionVehicleAssignment(route_version_vehicle_assignment_id="VA21", route_version_id="RV2", vehicle_id="MT1", used=True, trip_count=1),
        RouteVersionVehicleAssignment(route_version_vehicle_assignment_id="VA22", route_version_id="RV2", vehicle_id="MT2", used=False, trip_count=0),
    ])
    db.add_all([
        RouteVersionTrip(route_version_trip_id="P7T1", route_version_id="RV1", vehicle_id="MT1", trip_number=1, shipment_id="P7S1", vehicle_ready_at_depot=START, gate_out=START + timedelta(hours=7), estimated_return_depot=START + timedelta(hours=8, minutes=45)),
        RouteVersionTrip(route_version_trip_id="P7T2", route_version_id="RV2", vehicle_id="MT1", trip_number=1, shipment_id="P7S2", vehicle_ready_at_depot=START, gate_out=START + timedelta(hours=7), estimated_return_depot=START + timedelta(hours=9, minutes=20)),
    ])
    db.add_all([
        RouteVersionLOAssignment(route_version_lo_assignment_id="A1", route_version_id="RV1", route_version_trip_id="P7T1", loading_order_id="LO-P7-1", vehicle_id="MT1", trip_number=1, shipment_id="P7S1", spbu_id="SPBU1", volume_kl=24),
        RouteVersionLOAssignment(route_version_lo_assignment_id="A2", route_version_id="RV2", route_version_trip_id="P7T2", loading_order_id="LO-P7-2", vehicle_id="MT1", trip_number=1, shipment_id="P7S2", spbu_id="SPBU1", volume_kl=24),
    ])
    manual = ManualDispatchJob(id="MD1", job_id="MD-001", job_name="Manual Dispatch Final", depot_id="D1", operational_date=DAY, source_phase="PHASE_7", source_job_id="JOB1", source_run_id="RUN1", source_route_id="RV2", source_route_version="V2", dispatch_version=1, status="FINALIZED")
    db.add(manual)
    db.add_all([
        ManualDispatchVehicle(id="MDV1", dispatch_job_id="MD1", mt_id="MT1", vehicle_registration="BK 1001 AA", capacity_kl=24, number_of_compartments=3, initial_available_datetime=START),
        ManualDispatchVehicle(id="MDV2", dispatch_job_id="MD1", mt_id="MT2", vehicle_registration="BK 1002 AA", capacity_kl=16, number_of_compartments=2, initial_available_datetime=START + timedelta(hours=8, minutes=30)),
    ])
    db.add(ManualDispatchTrip(id="MDT1", dispatch_vehicle_id="MDV1", trip_sequence=1, available_before_trip_datetime=START, departure_datetime=START + timedelta(hours=7), estimated_return_datetime=START + timedelta(hours=10, minutes=15), total_volume_kl=24, status="VALID"))
    db.add(ManualDispatchLoadingOrder(id="MDLO1", dispatch_job_id="MD1", lo_id="LO-MD-1", lo_number="LO-MD-1", spbu_id="SPBU1", volume_kl=24, assignment_status="ASSIGNED"))
    db.add(ManualDispatchTripLO(id="MDTL1", dispatch_job_id="MD1", trip_id="MDT1", manual_dispatch_lo_id="MDLO1", stop_sequence=1))
    historical = FactShipment(shipment_id="SHIP-H1", source_shipment_id="SRC-H1", operating_date=date(2026, 9, 6), depot_id="D1", mt_id="MT1", vehicle_registration="BK 1001 AA", gate_out_datetime=START - timedelta(days=1) + timedelta(hours=1), shipment_end_datetime=START - timedelta(days=1) + timedelta(hours=4))
    db.add(historical)
    db.add_all([
        FactLoadingOrderLine(loading_order_number="LO-H1", source_depot_name="MEDAN", shipment_id="SHIP-H1", spbu_id="SPBU1", quantity=8),
        FactLoadingOrderLine(loading_order_number="LO-H2", source_depot_name="MEDAN", shipment_id="SHIP-H1", spbu_id="SPBU2", quantity=16),
    ])
    db.add(IntegrationClient(id="CLIENT1", integration_name="amt-scheduler", client_name="AMT Scheduler", client_code="AMT-SCHEDULER", token_hash=hash_integration_token(TOKEN), token_hint="amt_test...-log", active=True, permissions=["read"]))
    db.commit()


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_auth_and_master_data_contracts(phase10_client) -> None:
    client, _ = phase10_client
    health = client.get("/api/v1/integration/amt-scheduler/health")
    assert health.status_code == 200
    assert health.headers["x-request-id"].startswith("REQ-")

    unauthorized = client.get("/api/v1/integration/amt-scheduler/vehicles")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"

    terminals = client.get("/api/v1/integration/amt-scheduler/terminals", headers=auth()).json()
    assert terminals["data_version"]
    assert terminals["page"] == 1
    assert terminals["records"][0]["terminal_id"] == "D1"

    active = client.get("/api/v1/integration/amt-scheduler/vehicles", params={"terminal_id": "D1", "active": "true"}, headers=auth()).json()
    assert [row["mt_id"] for row in active["records"]] == ["MT1"]
    inactive = client.get("/api/v1/integration/amt-scheduler/vehicles/MT2", headers=auth())
    assert inactive.status_code == 200
    assert inactive.json()["active"] is False


def test_historical_trip_grain_incremental_sync_and_invalid_range(phase10_client) -> None:
    client, Session = phase10_client
    response = client.get(
        "/api/v1/integration/amt-scheduler/historical-operations",
        params={"terminal_id": "D1", "date_from": "2026-09-06", "date_to": "2026-09-06"},
        headers=auth(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_records"] == 1
    assert payload["records"][0]["loading_orders"] == ["LO-H1", "LO-H2"]
    assert payload["records"][0]["total_volume_kl"] == 24

    future = client.get(
        "/api/v1/integration/amt-scheduler/historical-operations",
        params={"terminal_id": "D1", "date_from": "2026-09-06", "date_to": "2026-09-06", "updated_since": "2099-01-01T00:00:00+00:00"},
        headers=auth(),
    ).json()
    assert future["records"] == []

    invalid = client.get(
        "/api/v1/integration/amt-scheduler/historical-operations",
        params={"terminal_id": "D1", "date_from": "2026-09-07", "date_to": "2026-09-06"},
        headers=auth(),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_DATE_RANGE"

    with Session() as db:
        logs = db.scalars(select(IntegrationAPILog)).all()
        assert logs
        assert all(TOKEN not in str(row.query_params) for row in logs)


def test_route_versions_sources_and_route_specific_availability(phase10_client) -> None:
    client, _ = phase10_client
    routes = client.get(
        "/api/v1/integration/amt-scheduler/routes",
        params={"operation_date": DAY.isoformat(), "terminal_id": "D1"},
        headers=auth(),
    )
    assert routes.status_code == 200, routes.text
    route_rows = routes.json()["records"]
    assert {row["route_id"] for row in route_rows} == {"P7:RV1", "P7:RV2", "P8:MD1"}
    assert {row["route_version"] for row in route_rows if row["route_source"] == "PHASE_7"} == {"V1", "V2"}
    route_vehicles = client.get(
        "/api/v1/integration/amt-scheduler/routes/P7:RV1/vehicles",
        headers=auth(),
    )
    assert route_vehicles.status_code == 200
    assert route_vehicles.json()["route_id"] == "P7:RV1"

    p7 = client.get(
        "/api/v1/integration/amt-scheduler/routes/P7:RV1/availability-after",
        params={"timestamp": "2026-09-07T08:00:00+00:00"},
        headers=auth(),
    )
    p8 = client.get(
        "/api/v1/integration/amt-scheduler/routes/P8:MD1/availability-after",
        params={"timestamp": "2026-09-07T08:00:00+00:00"},
        headers=auth(),
    )
    assert p7.status_code == 200, p7.text
    assert p8.status_code == 200, p8.text
    p7_mt1 = next(row for row in p7.json()["records"] if row["mt_id"] == "MT1")
    p8_mt1 = next(row for row in p8.json()["records"] if row["mt_id"] == "MT1")
    assert p7_mt1["availability_status"] == "ON_TRIP"
    assert datetime.fromisoformat(p7_mt1["next_available_at"].replace("Z", "+00:00")).time().isoformat() == "08:45:00"
    assert datetime.fromisoformat(p8_mt1["next_available_at"].replace("Z", "+00:00")).time().isoformat() == "10:15:00"
    p8_mt2 = next(row for row in p8.json()["records"] if row["mt_id"] == "MT2")
    assert p8_mt2["availability_status"] == "UNAVAILABLE"
    assert datetime.fromisoformat(p8_mt2["next_available_at"].replace("Z", "+00:00")).time().isoformat() == "08:30:00"

    exact_return = client.get(
        "/api/v1/integration/amt-scheduler/routes/P7:RV1/availability-after",
        params={"timestamp": "2026-09-07T08:45:00+00:00"},
        headers=auth(),
    ).json()
    assert next(row for row in exact_return["records"] if row["mt_id"] == "MT1")["availability_status"] == "AVAILABLE"


def test_shift_availability_and_credential_rotation(phase10_client) -> None:
    client, _ = phase10_client
    shifts = client.get(
        "/api/v1/integration/amt-scheduler/shifts",
        params={"terminal_id": "D1", "operation_date": DAY.isoformat()},
        headers=auth(),
    )
    assert shifts.status_code == 200
    assert [row["shift_id"] for row in shifts.json()["records"]] == ["SHIFT-1", "SHIFT-2", "SHIFT-3"]
    availability = client.get(
        "/api/v1/integration/amt-scheduler/routes/P7:RV1/shift-availability",
        params={"shift_id": "SHIFT-2"},
        headers=auth(),
    )
    assert availability.status_code == 200, availability.text
    assert availability.json()["shifts"][0]["available_mt_count"] == 1

    denied = client.post(
        "/api/v1/integration/amt-scheduler/console/access/regenerate",
        headers={"X-Permissions": "phase10.view"},
    )
    assert denied.status_code == 403
    assert client.get(
        "/api/v1/integration/amt-scheduler/console/try-permission",
        headers={"X-Permissions": "phase10.view"},
    ).status_code == 403
    assert client.get(
        "/api/v1/integration/amt-scheduler/console/try-permission",
        headers={"X-Permissions": "phase10.try_api"},
    ).status_code == 200
    rotated = client.post("/api/v1/integration/amt-scheduler/console/access/regenerate")
    assert rotated.status_code == 200
    new_token = rotated.json()["bearer_token"]
    assert new_token.startswith("amt_")
    assert client.get("/api/v1/integration/amt-scheduler/metadata", headers=auth()).status_code == 401
    assert client.get("/api/v1/integration/amt-scheduler/metadata", headers=auth(new_token)).status_code == 200
