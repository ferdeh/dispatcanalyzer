from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.manual_dispatch_service import (
    DispatchEligibilityService,
    DispatchGeographicMapService,
    DispatchRouteService,
    DispatchSimulationService,
    DispatchTimelineService,
    DispatchTripService,
    DispatchValidationService,
    DispatchVersionService,
    ManualDispatchFinalizeService,
    ManualDispatchJobService,
    ManualDispatchSnapshotService,
)
from app.models import (
    Base,
    BridgeMTTag,
    BridgeSPBUTag,
    LOOperationalState,
    ManualDispatchAuditLog,
    ManualDispatchLoadingOrder,
    ManualDispatchTrip,
    ManualDispatchTripLO,
    ManualDispatchVehicle,
    MasterDepot,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    MasterTag,
    MasterTagType,
    OptimizationJob,
    PredictionRun,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionTrip,
    VehicleOperationalState,
)


DAY = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def seed_source(db) -> None:
    db.add(MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot A", latitude=3.59, longitude=98.67, timezone="UTC"))
    db.add(MasterTagType(tag_type_id="TT", code="PROJECT", name="Project"))
    db.add_all([
        MasterTag(tag_id="ALL", tag_type_id="TT", tag_value="All-In", normalized_tag="ALL-IN"),
        MasterTag(tag_id="GUNUNG", tag_type_id="TT", tag_value="Gunung", normalized_tag="GUNUNG"),
    ])
    db.add_all([
        MasterMT(mt_id="MT1", vehicle_name_raw="MT 1", vehicle_registration="B 9123 XX", vehicle_type_tag=24, depot_id="D1", active_status="ACTIVE", number_of_compartments=3),
        MasterMT(mt_id="MT2", vehicle_name_raw="MT 2", vehicle_registration="B 9002 XX", vehicle_type_tag=24, depot_id="D1", active_status="ACTIVE", number_of_compartments=3),
        MasterSPBU(spbu_id="S1", spbu_code="31.123.09", spbu_name="SPBU Gunung", latitude=3.65, longitude=98.71, vehicle_type_tag=24, primary_depot_id="D1", active_status="ACTIVE"),
        MasterSPBU(spbu_id="S2", spbu_code="31.123.10", spbu_name="SPBU Kota", latitude=3.62, longitude=98.70, vehicle_type_tag=24, primary_depot_id="D1", active_status="ACTIVE"),
        MasterProduct(product_id="P1", product_name="BIOSOLAR", normalized_product="BIOSOLAR"),
    ])
    db.add_all([
        BridgeMTTag(mt_id="MT1", tag_id="ALL"), BridgeMTTag(mt_id="MT1", tag_id="GUNUNG"),
        BridgeMTTag(mt_id="MT2", tag_id="ALL"),
        BridgeSPBUTag(spbu_id="S1", tag_id="ALL"), BridgeSPBUTag(spbu_id="S1", tag_id="GUNUNG"),
        BridgeSPBUTag(spbu_id="S2", tag_id="ALL"),
    ])
    db.add(
        PredictionRun(
            id="P6", prediction_run_no="P6-001", depot_id="D1", model_id="MODEL", model_version=1,
            status="COMPLETED", input_loading_order_filename="lo.xlsx", input_mt_availability_filename="mt.xlsx",
            algorithm_version="phase6.test", created_by="tester",
        )
    )
    db.add(OptimizationJob(job_id="P7", job_no="P7-001", job_name="Phase 7", depot_id="D1", operating_date=date(2026, 8, 31), source_prediction_run_id="P6", current_route_version_id="RV2", status="READY"))
    db.add_all([
        VehicleOperationalState(vehicle_state_id="VS1", job_id="P7", mt_id="MT1", registration_snapshot="B 9123 XX", vehicle_class=24, tag_snapshot=["All-In", "Gunung"], capacity_kl=24, number_of_compartments=3, effective_eta_depot=DAY),
        VehicleOperationalState(vehicle_state_id="VS2", job_id="P7", mt_id="MT2", registration_snapshot="B 9002 XX", vehicle_class=24, tag_snapshot=["All-In"], capacity_kl=24, number_of_compartments=3, effective_eta_depot=DAY),
    ])
    for index, spbu in enumerate(["S1", "S2", "S1"], start=1):
        db.add(
            LOOperationalState(
                lo_state_id=f"LOS{index}", job_id="P7", loading_order_id=f"LO-{index}", spbu_id=spbu,
                spbu_name_snapshot=f"SPBU {spbu}", product_id="P1", product_name_snapshot="BIOSOLAR", volume_kl=8,
                depot_id="D1", operating_date=date(2026, 8, 31), source_prediction_run_id="P6",
            )
        )
    db.add(RouteVersion(route_version_id="RV2", job_id="P7", version_number=2, version_label="V2", reason="test", state_snapshot_id="SNAP", parameter_snapshot_id="PARAM", objective="MIN_DISTANCE", solver_status="FEASIBLE", created_by="tester"))
    db.add(RouteVersionTrip(route_version_trip_id="P7T1", route_version_id="RV2", vehicle_id="MT1", trip_number=1, shipment_id="SHIP-1", vehicle_ready_at_depot=DAY, gate_out=DAY, estimated_return_depot=DAY + timedelta(hours=3, minutes=45), distance_meters=10000, driving_seconds=7200, service_seconds=3600))
    db.add_all([
        RouteVersionLOAssignment(route_version_lo_assignment_id="RVA1", route_version_id="RV2", route_version_trip_id="P7T1", loading_order_id="LO-1", vehicle_id="MT1", trip_number=1, shipment_id="SHIP-1", spbu_id="S1", product_id="P1", volume_kl=8, stop_sequence=1),
        RouteVersionLOAssignment(route_version_lo_assignment_id="RVA2", route_version_id="RV2", route_version_trip_id="P7T1", loading_order_id="LO-2", vehicle_id="MT1", trip_number=1, shipment_id="SHIP-1", spbu_id="S2", product_id="P1", volume_kl=8, stop_sequence=2),
    ])
    db.commit()


def create_job(db) -> dict:
    return ManualDispatchSnapshotService.create(
        db,
        {"depot_id": "D1", "operational_date": "2026-08-31", "source_job_id": "P7", "source_route_id": "RV2", "job_name": "Manual V1"},
        actor="dispatcher",
    )


def route_stub(origin, destination, departure):
    assert origin != destination
    return {"distance_meters": 1000, "duration_seconds": 600, "static_duration_seconds": 540, "route_geometry": [], "provider": "MOCK_GOOGLE_ROUTES"}


def full_route_stub(origin, destination, departure, intermediates):
    assert origin == destination
    assert departure is not None
    assert len(intermediates) == 2
    return {
        "distance_meters": 12000,
        "duration_seconds": 3600,
        "route_geometry": [
            {"latitude": origin[0], "longitude": origin[1]},
            {"latitude": intermediates[0][0], "longitude": intermediates[0][1]},
            {"latitude": intermediates[1][0], "longitude": intermediates[1][1]},
            {"latitude": destination[0], "longitude": destination[1]},
        ],
        "route_geometry_source": "GOOGLE_ROUTES_GEOJSON",
    }


def test_create_job_snapshots_v2_without_mutating_phase7(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        assert workspace["job"]["source_route_version"] == "V2"
        assert workspace["job"]["dispatch_version"] == 1
        assert len(workspace["vehicles"]) == 2
        assert workspace["unassigned"]["count"] == 1
        assert db.get(RouteVersionLOAssignment, "RVA1").vehicle_id == "MT1"
        assert db.scalar(select(ManualDispatchAuditLog).where(ManualDispatchAuditLog.action == "SOURCE_ROUTE_LOADED"))


def test_geographic_map_hydrates_selected_mt_with_read_only_google_road_geometry(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        mt1 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT1")
        trip = db.get(ManualDispatchTrip, mt1["trips"][0]["id"])
        assert trip.route_geometry == []

        payload = DispatchGeographicMapService.build(db, workspace["job"]["id"], mt1["id"], route_provider=full_route_stub)

        assert payload["status"] == "READY"
        assert payload["vehicle"]["vehicle_registration"] == "B 9123 XX"
        assert payload["trip_count"] == 1
        assert payload["road_geometry_trip_count"] == 1
        assert payload["live_google_requests"] == 1
        assert payload["trips"][0]["geometry_status"] == "LIVE_GOOGLE_ROUTES"
        assert [row["spbu_id"] for row in payload["trips"][0]["stops"]] == ["S1", "S2"]
        assert len(payload["trips"][0]["route_geometry"]) == 4
        db.refresh(trip)
        assert trip.route_geometry == []


def test_eligibility_reuses_master_tags_and_hides_ineligible(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        mt1 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT1")
        mt2 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT2")
        eligible = DispatchEligibilityService.eligible_los(db, workspace["job"]["id"], mt1["id"])
        assert any(row["lo_number"] == "LO-3" for row in eligible["rows"])
        ineligible = DispatchEligibilityService.eligible_los(db, workspace["job"]["id"], mt2["id"], include_ineligible=True)
        lo3 = next(row for row in ineligible["rows"] if row["lo_number"] == "LO-3")
        assert lo3["eligible"] is False
        assert "Gunung" in lo3["rejection_reasons"][0]


def test_add_trip_uses_previous_availability_and_apply_cascades(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        mt1 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT1")
        expected = mt1["trips"][0]["available_after_trip_datetime"]
        workspace = DispatchTripService.create_trip(db, workspace["job"]["id"], mt1["id"], actor="dispatcher", expected_job_version=workspace["job"]["job_version"])
        mt1 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT1")
        assert mt1["trips"][1]["available_before_trip_datetime"] == expected
        assert mt1["trips"][1]["status"] == "DRAFT"
        with pytest.raises(HTTPException, match="successfully applied"):
            DispatchTripService.create_trip(db, workspace["job"]["id"], mt1["id"], actor="dispatcher")

        trip1 = db.get(ManualDispatchTrip, mt1["trips"][0]["id"])
        trip2 = db.get(ManualDispatchTrip, mt1["trips"][1]["id"])
        trip2.status = "VALID"
        trip2.estimated_return_datetime = trip2.departure_datetime + timedelta(hours=2)
        trip2.available_after_trip_datetime = trip2.estimated_return_datetime + timedelta(minutes=30)
        trip3 = ManualDispatchTrip(id="TRIP3", dispatch_vehicle_id=mt1["id"], trip_sequence=3, available_before_trip_datetime=trip2.available_after_trip_datetime, departure_datetime=trip2.available_after_trip_datetime, estimated_return_datetime=trip2.available_after_trip_datetime + timedelta(hours=2), available_after_trip_datetime=trip2.available_after_trip_datetime + timedelta(hours=2, minutes=30), status="VALID")
        db.add(trip3)
        trip1.status = "MODIFIED"
        db.commit()
        result = DispatchRouteService.apply_trip(db, workspace["job"]["id"], trip1.id, actor="dispatcher", route_provider=route_stub)
        assert result["applied"] is True
        db.refresh(trip2)
        db.refresh(trip3)
        assert trip2.status == "NEEDS_RECALCULATION"
        assert trip3.status == "NEEDS_RECALCULATION"
        assert trip2.available_before_trip_datetime.replace(tzinfo=timezone.utc) == db.get(ManualDispatchTrip, trip1.id).available_after_trip_datetime
        assert trip2.estimated_return_datetime is None


def test_duplicate_lo_rejected_and_delete_trip_returns_lo_to_unassigned(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        mt1 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT1")
        mt2 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT2")
        lo3 = next(row for row in workspace["unassigned"]["rows"] if row["lo_number"] == "LO-3")
        workspace = DispatchTripService.create_trip(db, workspace["job"]["id"], mt2["id"], actor="dispatcher", expected_job_version=workspace["job"]["job_version"])
        mt2 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT2")
        # MT2 is incompatible with LO-3, so move it to a fresh MT1 trip instead.
        workspace = DispatchTripService.create_trip(db, workspace["job"]["id"], mt1["id"], actor="dispatcher", expected_job_version=workspace["job"]["job_version"])
        mt1 = next(row for row in workspace["vehicles"] if row["mt_id"] == "MT1")
        target = mt1["trips"][-1]
        workspace = DispatchTripService.add_lo(db, workspace["job"]["id"], target["id"], lo3["id"], actor="dispatcher", expected_job_version=workspace["job"]["job_version"])
        with pytest.raises(HTTPException) as raised:
            DispatchTripService.add_lo(db, workspace["job"]["id"], mt2["trips"][0]["id"], lo3["id"], actor="dispatcher")
        assert raised.value.status_code == 409
        workspace = DispatchTripService.delete_trip(db, workspace["job"]["id"], target["id"], actor="dispatcher", expected_job_version=workspace["job"]["job_version"])
        assert any(row["lo_number"] == "LO-3" for row in workspace["unassigned"]["rows"])
        assert db.scalar(select(ManualDispatchAuditLog).where(ManualDispatchAuditLog.action == "TRIP_DELETED"))


def test_simulation_uses_kl_and_reports_negative_capacity_gap(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        job_id = workspace["job"]["id"]
        # Force exact acceptance example at 08:00: 120 KL demand, 96 KL available.
        db.query(ManualDispatchTripLO).delete()
        db.query(ManualDispatchLoadingOrder).delete()
        vehicles = db.scalars(select(ManualDispatchVehicle).where(ManualDispatchVehicle.dispatch_job_id == job_id)).all()
        for vehicle in vehicles:
            vehicle.capacity_kl = 48
        trip = db.scalar(select(ManualDispatchTrip).where(ManualDispatchTrip.dispatch_vehicle_id == vehicles[0].id))
        trip.departure_datetime = datetime(2026, 8, 31, 8, 15, tzinfo=timezone.utc)
        for index in range(15):
            lo = ManualDispatchLoadingOrder(id=f"SIMLO{index}", dispatch_job_id=job_id, lo_id=f"SIM-{index}", lo_number=f"SIM-{index}", spbu_id="S2", product_id="P1", volume_kl=8, assignment_status="ASSIGNED")
            db.add(lo)
            db.flush()
            db.add(ManualDispatchTripLO(id=f"SIMAS{index}", dispatch_job_id=job_id, trip_id=trip.id, manual_dispatch_lo_id=lo.id, stop_sequence=index + 1))
        db.commit()
        payload = DispatchSimulationService.build(db, job_id)
        bucket = next(row for row in payload["buckets"] if row["label"].startswith("08:00"))
        assert bucket["demand_kl"] == 120
        assert bucket["available_capacity_kl"] == 96
        assert bucket["capacity_gap_kl"] == -24


def test_finalization_blocks_stale_trip_warns_unassigned_and_version_is_editable(session_factory) -> None:
    with session_factory() as db:
        seed_source(db)
        workspace = create_job(db)
        job_id = workspace["job"]["id"]
        trip = db.scalar(select(ManualDispatchTrip).join(ManualDispatchVehicle).where(ManualDispatchVehicle.dispatch_job_id == job_id))
        trip.status = "NEEDS_RECALCULATION"
        db.commit()
        validation = DispatchValidationService.validate_dispatch(db, job_id)
        assert validation["valid"] is False
        with pytest.raises(HTTPException) as raised:
            ManualDispatchFinalizeService.finalize(db, job_id, actor="dispatcher", acknowledge_unassigned=True)
        assert raised.value.status_code == 422

        result = DispatchRouteService.apply_trip(db, job_id, trip.id, actor="dispatcher", route_provider=route_stub)
        assert result["applied"] is True
        with pytest.raises(HTTPException) as warning:
            ManualDispatchFinalizeService.finalize(db, job_id, actor="dispatcher")
        assert warning.value.status_code == 409
        finalized = ManualDispatchFinalizeService.finalize(db, job_id, actor="dispatcher", acknowledge_unassigned=True)
        assert finalized["workspace"]["job"]["status"] == "FINALIZED"
        with pytest.raises(HTTPException, match="read-only"):
            DispatchTripService.update_trip(db, job_id, trip.id, {"departure_datetime": DAY}, actor="dispatcher")
        copied = DispatchVersionService.create_version(db, job_id, actor="dispatcher")
        assert copied["job"]["dispatch_version"] == 2
        assert copied["job"]["status"] == "DRAFT"
        assert copied["job"]["parent_dispatch_job_id"] == job_id
