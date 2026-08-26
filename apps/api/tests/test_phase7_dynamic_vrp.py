from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    LOOperationalState,
    MasterDepot,
    MasterMT,
    MasterSPBU,
    OperationalStateSnapshot,
    OptimizationJob,
    OptimizationParameterSnapshot,
    RouteVersion,
    RouteVersionLOAssignment,
    VehicleOperationalState,
)
from app.phase7_optimization import BayQueueOptimizationService, CompartmentAssignmentService, VRPOptimizationService
from app.phase7_matrix import RouteMatrixService
from app.phase7_service import _comparison, _trip_cost_breakdowns, apply_freeze_rules, update_vehicle_states


DAY_START = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)


def parameters(**overrides) -> dict:
    return {
        "objective": "MIN_TOTAL_DISTANCE",
        "default_spbu_service_minutes": 10,
        "optimization_time_limit": 1,
        "unserved_penalty": 10_000_000,
        "maximum_trips_per_mt": 4,
        "cost_per_km": 10_000,
        "cost_per_operating_hour": 50_000,
        "phase6_vehicle_change_penalty": 0,
        "vehicle_activation_cost_rules": [],
        "gate_process_time": 5,
        "loading_mode": "SEQUENTIAL",
        "max_coordination_iterations": 3,
        "departure_time_tolerance_minutes": 5,
        **overrides,
    }


def vehicle(mt_id: str, *, eta_hour: int = 5, capacity: float = 8, compartments: int = 1) -> dict:
    return {
        "mt_id": mt_id,
        "vehicle_class": int(capacity),
        "tags": [],
        "capacity_kl": capacity,
        "compartments": [
            {"compartment_id": f"C{index}", "capacity_kl": capacity / compartments}
            for index in range(1, compartments + 1)
        ],
        "effective_eta_depot": DAY_START + timedelta(hours=eta_hour),
        "operational_status": "READY",
        "working_time_remaining_minutes": 600,
        "working_time_used_minutes": 0,
    }


def loading_order(lo_id: str, spbu_id: str, *, product_id: str = "P1", allowed=None, preferred="M1") -> dict:
    return {
        "loading_order_id": lo_id,
        "spbu_id": spbu_id,
        "product_id": product_id,
        "volume_kl": 8,
        "allowed_vehicle_ids": allowed if allowed is not None else ["M1"],
        "phase6_predicted_vehicle_id": preferred,
        "phase6_predicted_shipment_id": "P6-SHIP-1",
        "mandatory": True,
    }


def test_a_phase6_warm_start_is_soft_and_can_reassign_when_preferred_mt_is_too_late() -> None:
    lo = loading_order("LO-1", "S1", allowed=["M1", "M2"], preferred="M1")
    lo.update({"time_window_start_minutes": 300, "time_window_end_minutes": 420})
    result = VRPOptimizationService().solve(
        loading_orders=[lo],
        vehicles=[vehicle("M1", eta_hour=10), vehicle("M2", eta_hour=5)],
        distance_matrix=[[0, 10_000], [10_000, 0]],
        time_matrix=[[0, 1_200], [1_200, 0]],
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert result["trips"][0]["vehicle_id"] == "M2"
    assert result["trips"][0]["lo_assignments"][0]["phase6_predicted_vehicle_id"] == "M1"


def test_b_one_compartment_one_product_rejects_mixed_product() -> None:
    result = CompartmentAssignmentService().assign(
        [
            {"loading_order_id": "LO-1", "product_id": "PERTALITE", "volume_kl": 4},
            {"loading_order_id": "LO-2", "product_id": "BIOSOLAR", "volume_kl": 4},
        ],
        [{"compartment_id": "C1", "capacity_kl": 8}],
    )
    assert result["feasible"] is False
    assert result["reason"] == "COMPARTMENT_INFEASIBLE"


def test_c_multi_trip_assigns_same_mt_again_after_return() -> None:
    result = VRPOptimizationService().solve(
        loading_orders=[loading_order("LO-1", "S1"), loading_order("LO-2", "S2")],
        vehicles=[vehicle("M1")],
        distance_matrix=[[0, 10_000, 12_000], [10_000, 0, 5_000], [12_000, 5_000, 0]],
        time_matrix=[[0, 1_200, 1_500], [1_200, 0, 600], [1_500, 600, 0]],
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert [trip["trip_number"] for trip in result["trips"]] == [1, 2]
    assert result["trips"][1]["vehicle_ready_at_depot"] >= result["trips"][0]["estimated_return_depot"]


def test_d_vehicle_availability_prevents_early_departure() -> None:
    result = VRPOptimizationService().solve(
        loading_orders=[loading_order("LO-1", "S1")],
        vehicles=[vehicle("M1", eta_hour=10)],
        distance_matrix=[[0, 10_000], [10_000, 0]],
        time_matrix=[[0, 1_200], [1_200, 0]],
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert result["trips"][0]["gate_out"] >= DAY_START + timedelta(hours=10)


def lo_state(lo_id: str, *, status: str, gate_out: datetime | None = None) -> LOOperationalState:
    return LOOperationalState(
        lo_state_id=lo_id,
        job_id="JOB-1",
        loading_order_id=lo_id,
        spbu_id="S1",
        volume_kl=8,
        depot_id="D1",
        operating_date=date(2026, 8, 26),
        source_prediction_run_id="P6-1",
        status=status,
        planned_gate_out=gate_out,
    )


def test_e_freeze_window_locks_near_term_planned_lo() -> None:
    now = DAY_START + timedelta(hours=5)
    row = lo_state("LO-1", status="PLANNED", gate_out=now + timedelta(minutes=59))
    apply_freeze_rules([row], current_time=now, freeze_window_minutes=60)
    assert row.frozen is True
    assert row.frozen_reason == "FREEZE_WINDOW"


def test_f_ongoing_lo_is_frozen() -> None:
    row = lo_state("LO-1", status="ONGOING")
    apply_freeze_rules([row], current_time=DAY_START, freeze_window_minutes=60)
    assert row.frozen is True
    assert row.frozen_reason == "ONGOING"


def test_g_done_lo_is_permanently_excluded_from_reoptimizable_set() -> None:
    row = lo_state("LO-1", status="DONE")
    rows = apply_freeze_rules([row], current_time=DAY_START, freeze_window_minutes=60)
    reoptimizable = [item for item in rows if item.status == "PLANNED" and not item.frozen]
    assert row.frozen is True
    assert reoptimizable == []


def test_h_bay_product_compatibility_is_hard() -> None:
    trip = {
        "vehicle_ready_at_depot": DAY_START + timedelta(hours=5),
        "lo_assignments": [{"loading_order_id": "LO-1", "compartment_id": "C1", "product_id": "BIOSOLAR", "volume_kl": 8}],
    }
    result = BayQueueOptimizationService().schedule(
        trips=[trip],
        bays=[{"master_bay_id": "B1", "all_products_allowed": False, "allowed_product_ids": ["PERTALITE"], "number_of_loading_arms": 1, "loading_mode": "SEQUENTIAL"}],
        actual_states=[],
        initial_queue=[],
        loading_durations={"BIOSOLAR": 10},
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert result["dropped_trip_indexes"] == [0]


def test_i_initial_queue_delays_future_gate_out() -> None:
    ready = DAY_START + timedelta(hours=5)
    trip = {"vehicle_ready_at_depot": ready, "lo_assignments": [{"loading_order_id": "LO-1", "compartment_id": "C1", "product_id": "P1", "volume_kl": 8}]}
    result = BayQueueOptimizationService().schedule(
        trips=[trip],
        bays=[{"master_bay_id": "B1", "all_products_allowed": True, "allowed_product_ids": [], "number_of_loading_arms": 1, "loading_mode": "SEQUENTIAL"}],
        actual_states=[],
        initial_queue=[{"master_bay_id": "B1", "queue_position": 1, "estimated_loading_duration_minutes": 20}],
        loading_durations={"P1": 8},
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert result["assignments"][0]["loading_start"] >= ready + timedelta(minutes=20)
    assert result["assignments"][0]["gate_out"] >= ready + timedelta(minutes=33)


def test_j_loading_duration_is_summed_per_compartment_in_sequential_mode() -> None:
    trip = {
        "lo_assignments": [
            {"compartment_id": "C1", "product_id": "PERTALITE"},
            {"compartment_id": "C2", "product_id": "PERTALITE"},
            {"compartment_id": "C3", "product_id": "BIOSOLAR"},
        ]
    }
    assert BayQueueOptimizationService.loading_minutes(trip, {"PERTALITE": 8, "BIOSOLAR": 10}, loading_mode="SEQUENTIAL") == 26


def test_k_user_eta_override_has_priority_over_system_and_planned_eta() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(MasterDepot(depot_id="D1", depot_name="Depot 1"))
        db.add(MasterMT(mt_id="M1", vehicle_name_raw="MT 1", vehicle_registration="BL 1", depot_id="D1"))
        db.add(OptimizationJob(job_id="JOB-1", job_no="P7-JOB-1", job_name="Job", depot_id="D1", operating_date=date(2026, 8, 26), depot_operational_start=time(5), depot_operational_end=time(22)))
        db.add(VehicleOperationalState(vehicle_state_id="VS-1", job_id="JOB-1", mt_id="M1", capacity_kl=8, number_of_compartments=1, compartment_configuration=[{"compartment_id": "C1", "capacity_kl": 8}], planned_eta_depot=DAY_START + timedelta(hours=5), system_eta_depot=DAY_START + timedelta(hours=7), effective_eta_depot=DAY_START + timedelta(hours=7)))
        db.commit()
        override = DAY_START + timedelta(hours=8)
        update_vehicle_states(db, "JOB-1", [{"mt_id": "M1", "user_eta_override": override.isoformat()}])
        state = db.scalar(select(VehicleOperationalState).where(VehicleOperationalState.mt_id == "M1"))
        assert state.effective_eta_depot.replace(tzinfo=timezone.utc) == override


def test_l_route_versioning_keeps_v1_immutable_when_v2_becomes_current() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(MasterDepot(depot_id="D1", depot_name="Depot 1"))
        job = OptimizationJob(job_id="JOB-1", job_no="P7-JOB-1", job_name="Job", depot_id="D1", operating_date=date(2026, 8, 26), depot_operational_start=time(5), depot_operational_end=time(22))
        db.add(job)
        state_1 = OperationalStateSnapshot(state_snapshot_id="S1", job_id="JOB-1", snapshot_reason="Initial")
        state_2 = OperationalStateSnapshot(state_snapshot_id="S2", job_id="JOB-1", snapshot_reason="Reroute")
        parameter_1 = OptimizationParameterSnapshot(parameter_snapshot_id="P1", job_id="JOB-1", effective_parameters={"objective": "MIN_TOTAL_COST"}, parameter_checksum="a")
        parameter_2 = OptimizationParameterSnapshot(parameter_snapshot_id="P2", job_id="JOB-1", effective_parameters={"objective": "MIN_TOTAL_DISTANCE"}, parameter_checksum="b")
        db.add_all([state_1, state_2, parameter_1, parameter_2])
        db.flush()
        v1 = RouteVersion(route_version_id="V1", job_id="JOB-1", version_number=1, version_label="V1", reason="Initial", state_snapshot_id="S1", parameter_snapshot_id="P1", objective="MIN_TOTAL_COST", solver_status="FEASIBLE", summary_snapshot={"total_trips": 1})
        v2 = RouteVersion(route_version_id="V2", job_id="JOB-1", version_number=2, version_label="V2", reason="Reroute", state_snapshot_id="S2", parameter_snapshot_id="P2", objective="MIN_TOTAL_DISTANCE", solver_status="FEASIBLE", summary_snapshot={"total_trips": 2})
        db.add_all([v1, v2])
        job.current_route_version_id = "V2"
        db.commit()
        assert db.get(RouteVersion, "V1").summary_snapshot == {"total_trips": 1}
        assert db.get(OptimizationJob, "JOB-1").current_route_version_id == "V2"


def test_m_infeasible_lo_is_returned_with_reason_instead_of_disappearing() -> None:
    result = VRPOptimizationService().solve(
        loading_orders=[loading_order("LO-117", "S1", allowed=[])],
        vehicles=[vehicle("M1")],
        distance_matrix=[[0, 10_000], [10_000, 0]],
        time_matrix=[[0, 1_200], [1_200, 0]],
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert result["trips"] == []
    assert result["dropped"][0]["loading_order_id"] == "LO-117"
    assert result["dropped"][0]["reason_code"] == "NO_COMPATIBLE_MT"
    assert result["dropped"][0]["reason_description"]


def test_n_final_route_geometry_materializes_depot_stop_depot_without_callback_error() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        depot = MasterDepot(depot_id="D1", depot_name="Depot", latitude=3.59, longitude=98.67)
        spbu = MasterSPBU(spbu_id="S1", spbu_code="S1", spbu_name="SPBU", latitude=3.65, longitude=98.72)
        db.add_all([depot, spbu])
        db.commit()
        result = RouteMatrixService(db).build_route_geometry(
            depot=depot,
            ordered_spbus=[spbu],
            departure=DAY_START + timedelta(hours=5),
            parameters={"route_matrix_cache_enabled": True, "route_matrix_cache_ttl_minutes": 60, "route_vehicle_mode": "GENERAL_VEHICLE", "traffic_aware": False},
        )
        assert len(result["route_geometry"]) == 3
        assert result["route_geometry"][0] == result["route_geometry"][-1]
        assert result["route_geometry_source"] == "MIXED_OR_MASTER_FALLBACK"


def test_o_new_trip_numbers_continue_after_copied_frozen_trip_number() -> None:
    mt = vehicle("M1")
    mt["completed_trip_count"] = 2
    result = VRPOptimizationService().solve(
        loading_orders=[loading_order("LO-3", "S1")],
        vehicles=[mt],
        distance_matrix=[[0, 10_000], [10_000, 0]],
        time_matrix=[[0, 1_200], [1_200, 0]],
        day_start=DAY_START,
        depot_close=DAY_START + timedelta(hours=18),
        parameters=parameters(),
    )
    assert result["trips"][0]["trip_number"] == 3


def test_p_explicit_truck_mode_never_masquerades_as_general_drive_geometry() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        depot = MasterDepot(depot_id="D1", depot_name="Depot", latitude=3.59, longitude=98.67)
        spbu = MasterSPBU(spbu_id="S1", spbu_code="S1", spbu_name="SPBU", latitude=3.65, longitude=98.72)
        db.add_all([depot, spbu])
        db.commit()
        result = RouteMatrixService(db).build_route_geometry(
            depot=depot,
            ordered_spbus=[spbu],
            departure=DAY_START + timedelta(hours=5),
            parameters={"route_matrix_cache_enabled": True, "route_matrix_cache_ttl_minutes": 60, "route_vehicle_mode": "TRUCK", "traffic_aware": True},
        )
        assert result["google_request_count"] == 0
        assert result["leg_sources"] == [
            "TRUCK_MODE_UNAVAILABLE_MASTER_HAVERSINE_FALLBACK",
            "TRUCK_MODE_UNAVAILABLE_MASTER_HAVERSINE_FALLBACK",
        ]


def test_q_trip_cost_drilldown_activates_each_mt_only_once() -> None:
    mt = vehicle("M1")
    result = {
        "trips": [
            {"vehicle_id": "M1", "trip_number": number, "distance_meters": 10_000, "operating_minutes": 60, "queue_minutes": 5, "loading_minutes": 8, "lo_assignments": [{"phase6_predicted_vehicle_id": "M1"}]}
            for number in (1, 2)
        ]
    }
    costs = _trip_cost_breakdowns(
        result,
        [mt],
        parameters(
            vehicle_activation_cost_rules=[{"vehicle_class": 8, "vehicle_tag": None, "activation_cost": 500_000, "priority": 10}],
            queue_cost=1_000,
            loading_cost=1_000,
            overtime_cost=5_000,
        ),
    )
    assert costs[("M1", 1)]["vehicle_activation_cost"] == 500_000
    assert costs[("M1", 2)]["vehicle_activation_cost"] == 0
    assert costs[("M1", 1)]["total_cost"] == 663_000
    assert costs[("M1", 2)]["total_cost"] == 163_000


def test_r_plan_stability_counts_material_gate_out_changes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(MasterDepot(depot_id="D1", depot_name="Depot 1"))
        job = OptimizationJob(job_id="JOB-1", job_no="P7-JOB-1", job_name="Job", depot_id="D1", operating_date=date(2026, 8, 26), depot_operational_start=time(5), depot_operational_end=time(22), current_route_version_id=None)
        db.add(job)
        db.add_all([
            OperationalStateSnapshot(state_snapshot_id="STATE-1", job_id="JOB-1", snapshot_reason="Initial"),
            OptimizationParameterSnapshot(parameter_snapshot_id="PARAM-1", job_id="JOB-1", effective_parameters={}, parameter_checksum="a"),
        ])
        db.flush()
        version = RouteVersion(route_version_id="V1", job_id="JOB-1", version_number=1, version_label="V1", reason="Initial", state_snapshot_id="STATE-1", parameter_snapshot_id="PARAM-1", objective="MIN_TOTAL_COST", solver_status="FEASIBLE")
        db.add(version)
        db.flush()
        db.add(RouteVersionLOAssignment(route_version_lo_assignment_id="A1", route_version_id="V1", loading_order_id="LO-1", vehicle_id="M1", shipment_id="S1", spbu_id="SPBU-1", volume_kl=8, stop_sequence=1, planned_gate_out=DAY_START + timedelta(hours=5), assignment_status="PLANNED"))
        job.current_route_version_id = "V1"
        db.commit()
        comparison = _comparison(db, job, {"trips": [{"vehicle_id": "M2", "shipment_id": "S2", "gate_out": DAY_START + timedelta(hours=5, minutes=20), "stops": [{"sequence": 2, "loading_order": {"loading_order_id": "LO-1"}}], "lo_assignments": [{"loading_order_id": "LO-1"}]}]}, gate_out_tolerance_minutes=5)
        assert comparison["vehicle_assignment_changes"] == 1
        assert comparison["shipment_changes"] == 1
        assert comparison["route_sequence_changes"] == 1
        assert comparison["gate_out_changes"] == 1
        assert comparison["plan_adherence_pct"] == 0
