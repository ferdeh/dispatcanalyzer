from __future__ import annotations

import json
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.google_routes import (
    GoogleRoutesClient,
    GoogleRoutesError,
    get_google_routes_configuration,
    large_vehicle_profile,
    public_google_routes_configuration,
    save_google_routes_configuration,
)
from app.models import Base, MLBehavioralModel, MLSPBUClusterAssignment, MasterDepot, MasterMT, MasterSPBU
from app.phase6_demo import generate_demo_loading_orders
from app.phase6_routing import Phase6RouteEstimationService
from app.phase6_service import create_prediction_run, override_assignment
from app.phase6_validation import validate_loading_orders, validate_mt_availability


def make_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def workbook(headers: list[str], rows: list[list]) -> bytes:
    result = Workbook()
    sheet = result.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    result.save(buffer)
    return buffer.getvalue()


def seed(session) -> MLBehavioralModel:
    session.add_all(
        [
            MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot One", latitude=-6.20, longitude=106.84, timezone="Asia/Jakarta"),
            MasterDepot(depot_id="D2", depot_code="D2", depot_name="Depot Two", timezone="Asia/Jakarta"),
            MasterMT(mt_id="T1", vehicle_name_raw="Truck 1", vehicle_registration="B1001AA", vehicle_type_tag=8, depot_id="D1", active_status="ACTIVE"),
            MasterMT(mt_id="T2", vehicle_name_raw="Truck 2", vehicle_registration="B1002AA", vehicle_type_tag=8, depot_id="D1", active_status="ACTIVE"),
            MasterMT(mt_id="T3", vehicle_name_raw="Truck 3", vehicle_registration="B1003AA", vehicle_type_tag=16, depot_id="D1", active_status="ACTIVE"),
            MasterMT(mt_id="INACTIVE", vehicle_name_raw="Inactive", vehicle_registration="B1999AA", vehicle_type_tag=8, depot_id="D1", active_status="INACTIVE"),
            MasterMT(mt_id="OTHER-MT", vehicle_name_raw="Other", vehicle_registration="B2001BB", vehicle_type_tag=8, depot_id="D2", active_status="ACTIVE"),
            MasterSPBU(spbu_id="A", spbu_code="SPBU-A", spbu_name="A", latitude=-6.21, longitude=106.85, master_travel_time_min=10, vehicle_type_tag=8, primary_depot_id="D1"),
            MasterSPBU(spbu_id="B", spbu_code="SPBU-B", spbu_name="B", latitude=-6.22, longitude=106.86, master_travel_time_min=10, vehicle_type_tag=8, primary_depot_id="D1"),
            MasterSPBU(spbu_id="C", spbu_code="SPBU-C", spbu_name="C", latitude=-6.23, longitude=106.87, master_travel_time_min=10, vehicle_type_tag=8, primary_depot_id="D1"),
            MasterSPBU(spbu_id="LIMITED", spbu_code="SPBU-LIMITED", spbu_name="Limited", latitude=-6.24, longitude=106.88, master_travel_time_min=10, vehicle_type_tag=16, primary_depot_id="D1"),
            MasterSPBU(spbu_id="OTHER", spbu_code="SPBU-OTHER", spbu_name="Other", vehicle_type_tag=8, primary_depot_id="D2"),
        ]
    )
    model = MLBehavioralModel(
        model_id="M1",
        model_name="Ready Model",
        model_version=1,
        depot_id="D1",
        training_start_date=date(2026, 1, 1),
        training_end_date=date(2026, 6, 30),
        training_shipment_count=100,
        training_spbu_count=4,
        cluster_count=3,
        average_membership_probability=0.9,
        feature_weights={"tag": 0.4, "shift": 0.25, "pairing": 0.35},
        shift_definition_snapshot=[
            {"shift_id": "s1", "name": "Shift 1", "start_time": "00:00", "end_time": "05:59"},
            {"shift_id": "s2", "name": "Shift 2", "start_time": "06:00", "end_time": "11:59"},
            {"shift_id": "s3", "name": "Shift 3", "start_time": "12:00", "end_time": "17:59"},
            {"shift_id": "s4", "name": "Shift 4", "start_time": "18:00", "end_time": "23:59"},
        ],
        model_status="ACTIVE",
    )
    session.add(model)
    for spbu_id, cluster, shift in (("A", 0, "Shift 1"), ("B", 0, "Shift 1"), ("C", 1, "Shift 2"), ("LIMITED", 1, "Shift 2")):
        session.add(
            MLSPBUClusterAssignment(
                assignment_id=f"AS-{spbu_id}", model_id="M1", depot_id="D1", spbu_id=spbu_id,
                cluster_id=cluster, cluster_label=f"Cluster {cluster + 1}", membership_probability=0.9,
                is_noise=False, dominant_shift=shift,
            )
        )
    session.commit()
    return model


def run_prediction(session, lo_rows: list[list], mt_rows: list[list], parameters: dict | None = None) -> dict:
    return create_prediction_run(
        session,
        depot_id="D1",
        model_id="M1",
        loading_order_content=workbook(["loading_order_no", "shipment_start_datetime", "spbu_no"], lo_rows),
        loading_order_filename="lo.xlsx",
        availability_content=workbook(["vehicle_registration_no", "initial_available_datetime"], mt_rows),
        availability_filename="mt.xlsx",
        parameters=parameters,
        created_by="tester",
    )


def test_timestamp_validation_derives_shift_and_rejects_bad_inputs() -> None:
    Session = make_session()
    with Session() as session:
        model = seed(session)
        result = validate_loading_orders(
            session, depot_id="D1", model=model,
            content=workbook(
                ["loading_order_no", "shipment_start_datetime", "spbu_no"],
                [["LO1", "2026-08-22 05:30:00", "SPBU-A"], ["LO1", "bad", "UNKNOWN"], ["LO3", "2026-08-22 07:00:00", "SPBU-OTHER"]],
            ),
            file_name="lo.xlsx",
        )
        codes = {issue["error_code"] for issue in result["issues"]}
        assert {"DUPLICATE_LOADING_ORDER", "INVALID_DATETIME", "SPBU_NOT_FOUND", "SPBU_DEPOT_MISMATCH"} <= codes

        valid = validate_loading_orders(
            session, depot_id="D1", model=model,
            content=workbook(["loading_order_no", "shipment_start_datetime", "spbu_no"], [["LO4", "2026-08-22 07:00:00", "SPBU-A"]]),
            file_name="lo.xlsx",
        )
        assert valid["status"] == "PASS"
        assert valid["normalized_rows"][0]["shift_id"] == "s2"
        assert valid["normalized_rows"][0]["shipment_start_datetime"].endswith("+00:00")


def test_mt_timestamp_validation_duplicate_inactive_and_depot_errors() -> None:
    Session = make_session()
    with Session() as session:
        model = seed(session)
        result = validate_mt_availability(
            session, depot_id="D1", model=model,
            content=workbook(
                ["vehicle_registration_no", "initial_available_datetime"],
                [["B1001AA", "2026-08-22 05:00:00"], ["B1001AA", "bad"], ["B1999AA", "2026-08-22 05:00:00"], ["B2001BB", "2026-08-22 05:00:00"]],
            ),
            file_name="mt.xlsx",
        )
        codes = {issue["error_code"] for issue in result["issues"]}
        assert {"DUPLICATE_VEHICLE_AVAILABILITY", "INVALID_AVAILABLE_DATETIME", "VEHICLE_INACTIVE", "VEHICLE_DEPOT_MISMATCH"} <= codes


def test_strict_start_multi_trip_reuses_vehicle_without_overlap() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        result = run_prediction(
            session,
            [["LO1", "2026-08-22 05:30:00", "SPBU-A"], ["LO2", "2026-08-22 10:00:00", "SPBU-C"]],
            [["B1001AA", "2026-08-22 05:00:00"]],
            {"assignment_mode": "STRICT_START"},
        )
        assert [trip["vehicle_id"] for trip in result["trips"]] == ["T1", "T1"]
        assert [trip["trip_number"] for trip in result["trips"]] == [1, 2]
        first, second = result["trips"]
        assert datetime.fromisoformat(first["next_available_datetime"]) <= datetime.fromisoformat(second["predicted_departure_datetime"])
        assert result["summary"]["multi_trip_mt"] == 1


def test_strict_start_rejects_not_yet_available_vehicle() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        result = run_prediction(
            session,
            [["LO1", "2026-08-22 09:00:00", "SPBU-A"]],
            [["B1001AA", "2026-08-22 09:30:00"]],
            {"assignment_mode": "STRICT_START"},
        )
        assert result["trips"][0]["assignment_status"] == "UNASSIGNED"
        assert result["trips"][0]["unassigned_reason"] == "NO_MT_AVAILABLE_AT_REQUIRED_TIME"


def test_allow_delay_assigns_with_expected_departure() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        result = run_prediction(
            session,
            [["LO1", "2026-08-22 09:00:00", "SPBU-A"]],
            [["B1001AA", "2026-08-22 09:20:00"]],
            {"assignment_mode": "ALLOW_DELAY", "maximum_allowed_delay_minutes": 30},
        )
        trip = result["trips"][0]
        assert trip["assignment_status"] == "ASSIGNED_WITH_DELAY"
        assert trip["delay_minutes"] == 20
        assert datetime.fromisoformat(trip["predicted_departure_datetime"]).astimezone(timezone.utc).hour == 2


def test_multi_spbu_master_compatibility_is_intersection() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        # C and LIMITED are deliberately paired by allowing a zero confidence threshold.
        result = run_prediction(
            session,
            [["LO1", "2026-08-22 09:00:00", "SPBU-C"], ["LO2", "2026-08-22 09:00:00", "SPBU-LIMITED"]],
            [["B1003AA", "2026-08-22 08:00:00"]],
            {"minimum_prediction_confidence": 0},
        )
        assert result["shipments"][0]["candidates"][0]["compatibility_status"] == "FAIL"
        assert result["trips"][0]["unassigned_reason"] == "NO_COMPATIBLE_MT"


def test_demo_loading_order_uses_timestamps_and_requested_total() -> None:
    Session = make_session()
    with Session() as session:
        model = seed(session)
        content, filename = generate_demo_loading_orders(session, depot_id="D1", model=model, total_order_kl=18, random_seed=42)
        sheet = load_workbook(BytesIO(content), read_only=True, data_only=True).active
        assert [cell.value for cell in next(sheet.iter_rows(max_row=1))][:3] == ["loading_order_no", "shipment_start_datetime", "spbu_no"]
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        assert filename.startswith("phase6-demo-loading-order-")
        assert [row[4] for row in rows] == [8, 8, 2]
        validated = validate_loading_orders(session, depot_id="D1", model=model, content=content, file_name=filename)
        assert validated["status"] == "PASS"
        assert sum(row["order_quantity_kl"] for row in validated["normalized_rows"]) == 18


def test_google_client_truck_request_uses_vehicle_specific_supported_fields() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "secret-test-key"
        return httpx.Response(200, json={"routes": [{"distanceMeters": 1000, "duration": "600s", "staticDuration": "550s"}]})

    client = GoogleRoutesClient("secret-test-key", transport=httpx.MockTransport(handler))
    response = client.compute_route(
        origin=(-6.2, 106.8), destination=(-6.3, 106.9), departure_datetime=datetime(2099, 1, 1, tzinfo=timezone.utc),
        routing_mode="TRUCK", routing_preference="TRAFFIC_AWARE",
        vehicle_info={"totalHeightMm": 3500, "totalWidthMm": 2500, "totalLengthMm": 9000, "totalWeightKg": 16000, "totalAxleCount": 3},
    )
    assert captured["travelMode"] == "TRUCK"
    assert captured["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    assert captured["routeModifiers"]["vehicleInfo"]["totalHeightMm"] == 3500
    assert response["duration_seconds"] == 600


class FakeRoutesClient:
    def __init__(self):
        self.calls = 0

    def compute_route(self, **_kwargs):
        self.calls += 1
        return {"distance_meters": 1000, "duration_seconds": 600, "static_duration_seconds": 550, "restrictions_partially_ignored": False, "warnings": []}


def test_route_cache_separates_vehicle_profiles_and_reuses_exact_profile() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        mt1, mt2 = session.get(MasterMT, "T1"), session.get(MasterMT, "T2")
        for mt, height in ((mt1, 3500), (mt2, 3800)):
            mt.vehicle_height_mm = height
            mt.vehicle_width_mm = 2500
            mt.vehicle_length_mm = 9000
            mt.vehicle_weight_kg = 16000
            mt.vehicle_axle_count = 3
        configuration = get_google_routes_configuration(session)
        configuration.routing_mode = "TRUCK"
        configuration.truck_routing_status = "AVAILABLE"
        fake = FakeRoutesClient()
        metrics = {}
        service = Phase6RouteEstimationService(session, configuration=configuration, model_id="M1", metrics=metrics, google_client=fake)
        depot, spbu = session.get(MasterDepot, "D1"), session.get(MasterSPBU, "A")
        departure = datetime(2099, 1, 1, 1, tzinfo=timezone.utc)
        service.estimate_trip(depot=depot, spbus=[spbu], mt=mt1, predicted_departure_datetime=departure, max_exact_sequence_stops=4)
        session.flush()
        service.estimate_trip(depot=depot, spbus=[spbu], mt=mt1, predicted_departure_datetime=departure, max_exact_sequence_stops=4)
        service.estimate_trip(depot=depot, spbus=[spbu], mt=mt2, predicted_departure_datetime=departure, max_exact_sequence_stops=4)
        assert fake.calls == 4  # outbound + return for each distinct profile; exact repeat uses cache
        assert metrics["google_routes_cache_hit_count"] == 2


def test_drive_fallback_visible_and_block_policy_errors() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        configuration = get_google_routes_configuration(session)
        configuration.routing_mode = "TRUCK"
        configuration.truck_routing_status = "NOT_AVAILABLE"
        configuration.fallback_policy = "ALLOW_DRIVE_FALLBACK"
        service = Phase6RouteEstimationService(session, configuration=configuration, model_id="M1")
        estimate = service.estimate_trip(
            depot=session.get(MasterDepot, "D1"), spbus=[session.get(MasterSPBU, "A")], mt=session.get(MasterMT, "T1"),
            predicted_departure_datetime=datetime(2099, 1, 1, tzinfo=timezone.utc), max_exact_sequence_stops=4,
        )
        assert estimate["routing_mode"] == "DRIVE_FALLBACK"
        assert estimate["fallback_used"] is True
        assert "DRIVE_FALLBACK" in estimate["warning_codes"]

        configuration.fallback_policy = "BLOCK_IF_TRUCK_UNAVAILABLE"
        with pytest.raises(GoogleRoutesError) as raised:
            service.estimate_trip(
                depot=session.get(MasterDepot, "D1"), spbus=[session.get(MasterSPBU, "A")], mt=session.get(MasterMT, "T1"),
                predicted_departure_datetime=datetime(2099, 1, 1, tzinfo=timezone.utc), max_exact_sequence_stops=4,
            )
        assert raised.value.code == "INVALID_VEHICLE_PROFILE"


def test_api_key_is_encrypted_masked_and_never_returned(monkeypatch, caplog) -> None:
    monkeypatch.setenv("GOOGLE_ROUTES_ENCRYPTION_KEY", "test-only-encryption-key-at-least-16")
    get_settings.cache_clear()
    Session = make_session()
    raw_key = "AIzaSyTEST-RAW-KEY-1234567890"
    try:
        with Session() as session:
            seed(session)
            payload = save_google_routes_configuration(session, {"api_key": raw_key}, updated_by="tester")
            configuration = get_google_routes_configuration(session)
            assert configuration.encrypted_api_key != raw_key
            assert raw_key not in configuration.encrypted_api_key
            assert payload["masked_api_key"].endswith("7890")
            assert raw_key not in json.dumps(payload)
            assert raw_key not in caplog.text
            public = public_google_routes_configuration(session)
            assert raw_key not in json.dumps(public)
    finally:
        get_settings.cache_clear()


def test_manual_override_preserves_original_and_recalculates_route() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        result = run_prediction(
            session,
            [["LO1", "2026-08-22 09:00:00", "SPBU-A"]],
            [["B1001AA", "2026-08-22 08:00:00"], ["B1002AA", "2026-08-22 08:00:00"]],
        )
        original = result["original_model_prediction"]
        shipment = result["shipments"][0]
        replacement = next(row for row in shipment["candidates"] if row["vehicle_id"] != shipment["assignment"]["assigned_vehicle_id"] and row["compatibility_status"] == "PASS")
        updated = override_assignment(session, result["id"], shipment["assignment"]["id"], replacement["vehicle_id"], "dispatcher", "user")
        assert updated["original_model_prediction"] == original
        assert updated["trips"][0]["vehicle_id"] == replacement["vehicle_id"]
        assert updated["trips"][0]["assignment_status"] == "MANUAL_OVERRIDE"


def test_phase6_has_no_route_optimization_or_full_vrp_client() -> None:
    source = "\n".join(
        Path(path).read_text()
        for path in ("app/google_routes.py", "app/phase6_routing.py", "app/phase6_service.py")
    )
    assert "routeOptimization" not in source
    assert "optimization.googleapis.com" not in source
    assert "def optimize_tours" not in source.lower()
    assert "class VehicleRoutingProblemOptimizer" not in source
