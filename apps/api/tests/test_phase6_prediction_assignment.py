from __future__ import annotations

from datetime import date
from io import BytesIO

from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    MLBehavioralModel,
    MLSPBUClusterAssignment,
    MasterDepot,
    MasterMT,
    MasterSPBU,
)
from app.phase6_assignment import optimize_global_assignment
from app.phase6_service import adjust_shipment, create_prediction_run, override_assignment
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
            MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot One"),
            MasterDepot(depot_id="D2", depot_code="D2", depot_name="Depot Two"),
            MasterMT(mt_id="T1", vehicle_name_raw="Truck 1", vehicle_registration="B1001AA", vehicle_type_tag=8, depot_id="D1"),
            MasterMT(mt_id="T2", vehicle_name_raw="Truck 2", vehicle_registration="B1002AA", vehicle_type_tag=8, depot_id="D1"),
            MasterMT(mt_id="T3", vehicle_name_raw="Truck 3", vehicle_registration="B1003AA", vehicle_type_tag=16, depot_id="D1"),
            MasterMT(mt_id="OTHER-MT", vehicle_name_raw="Other", vehicle_registration="B2001BB", vehicle_type_tag=8, depot_id="D2"),
            MasterSPBU(spbu_id="A", spbu_code="SPBU-A", spbu_name="A", vehicle_type_tag=8, primary_depot_id="D1"),
            MasterSPBU(spbu_id="B", spbu_code="SPBU-B", spbu_name="B", vehicle_type_tag=8, primary_depot_id="D1"),
            MasterSPBU(spbu_id="C", spbu_code="SPBU-C", spbu_name="C", vehicle_type_tag=8, primary_depot_id="D1"),
            MasterSPBU(spbu_id="LIMITED", spbu_code="SPBU-LIMITED", spbu_name="Limited", vehicle_type_tag=16, primary_depot_id="D1"),
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
        cluster_count=2,
        average_membership_probability=0.9,
        feature_weights={"tag": 0.4, "shift": 0.25, "pairing": 0.35},
        shift_definition_snapshot=[
            {"shift_id": "morning", "name": "Morning", "start_time": "06:00", "end_time": "13:59"},
            {"shift_id": "evening", "name": "Evening", "start_time": "14:00", "end_time": "21:59"},
        ],
        model_status="ACTIVE",
    )
    session.add(model)
    for spbu_id, cluster, shift in (("A", 0, "Morning"), ("B", 0, "Morning"), ("C", 1, "Evening"), ("LIMITED", 1, "Evening")):
        session.add(
            MLSPBUClusterAssignment(
                assignment_id=f"AS-{spbu_id}",
                model_id="M1",
                depot_id="D1",
                spbu_id=spbu_id,
                cluster_id=cluster,
                cluster_label=f"Cluster {cluster + 1}",
                membership_probability=0.9,
                is_noise=False,
                dominant_shift=shift,
            )
        )
    session.commit()
    return model


def test_global_optimizer_finds_non_greedy_optimum() -> None:
    result = optimize_global_assignment(
        ["A", "B"],
        ["MT01", "MT02", "MT03"],
        {("A", "MT01"): 0.95, ("A", "MT02"): 0.90, ("B", "MT01"): 0.93, ("B", "MT03"): 0.70},
    )
    assert result == {"A": ("MT02", 0.9), "B": ("MT01", 0.93)}


def test_file_validation_detects_unknown_master_depot_shift_and_duplicates() -> None:
    Session = make_session()
    with Session() as session:
        model = seed(session)
        lo = workbook(
            ["loading_order_no", "shift_gate_out", "spbu_no"],
            [["LO1", "Morning", "UNKNOWN"], ["LO1", "Bad shift", "SPBU-OTHER"]],
        )
        result = validate_loading_orders(session, depot_id="D1", model=model, content=lo, file_name="lo.xlsx")
        codes = {issue["error_code"] for issue in result["issues"]}
        assert {"SPBU_NOT_FOUND", "SPBU_DEPOT_MISMATCH", "SHIFT_NOT_FOUND", "DUPLICATE_LOADING_ORDER"} <= codes
        assert result["status"] == "ERROR"

        mt = workbook(["shift", "vehicle_registration_no"], [["Morning", "UNKNOWN"], ["Morning", "B2001BB"]])
        mt_result = validate_mt_availability(session, depot_id="D1", model=model, content=mt, file_name="mt.xlsx")
        mt_codes = {issue["error_code"] for issue in mt_result["issues"]}
        assert {"VEHICLE_NOT_FOUND", "VEHICLE_DEPOT_MISMATCH"} <= mt_codes


def test_prediction_is_shift_isolated_compatible_and_deterministic() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        lo = workbook(
            ["loading_order_no", "shift_gate_out", "spbu_no"],
            [["LO1", "Morning", "SPBU-A"], ["LO2", "Morning", "SPBU-B"], ["LO3", "Evening", "SPBU-C"]],
        )
        mt = workbook(
            ["shift", "vehicle_registration_no"],
            [["Morning", "B1001AA"], ["Morning", "B1002AA"], ["Evening", "B1001AA"]],
        )
        first = create_prediction_run(
            session,
            depot_id="D1",
            model_id="M1",
            loading_order_content=lo,
            loading_order_filename="lo.xlsx",
            availability_content=mt,
            availability_filename="mt.xlsx",
            parameters=None,
            created_by="tester",
        )
        second = create_prediction_run(
            session,
            depot_id="D1",
            model_id="M1",
            loading_order_content=lo,
            loading_order_filename="lo.xlsx",
            availability_content=mt,
            availability_filename="mt.xlsx",
            parameters=None,
            created_by="tester",
        )
        first_structure = [(row["shift_id"], [line["loading_order_no"] for line in row["lines"]]) for row in first["shipments"]]
        second_structure = [(row["shift_id"], [line["loading_order_no"] for line in row["lines"]]) for row in second["shipments"]]
        assert first_structure == second_structure == [("evening", ["LO3"]), ("morning", ["LO1", "LO2"])]
        assert all(row["assignment"]["assignment_status"] == "ASSIGNED" for row in first["shipments"])


def test_multi_spbu_compatibility_uses_intersection_and_unassigned_is_explicit() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        lo = workbook(
            ["loading_order_no", "shift_gate_out", "spbu_no"],
            [["LO1", "Evening", "SPBU-C"], ["LO2", "Evening", "SPBU-LIMITED"]],
        )
        # T3 (class 16) passes LIMITED but fails C under the existing EXACT_MATCH rule.
        mt = workbook(["shift", "vehicle_registration_no"], [["Evening", "B1003AA"]])
        result = create_prediction_run(
            session,
            depot_id="D1",
            model_id="M1",
            loading_order_content=lo,
            loading_order_filename="lo.xlsx",
            availability_content=mt,
            availability_filename="mt.xlsx",
            parameters=None,
            created_by="tester",
        )
        shipment = result["shipments"][0]
        assert len(shipment["lines"]) == 2
        assert shipment["candidates"][0]["compatibility_status"] == "FAIL"
        assert shipment["assignment"]["assignment_status"] == "UNASSIGNED"
        assert shipment["assignment"]["unassigned_reason"] == "NO_COMPATIBLE_MT"


def test_manual_mt_and_shipment_overrides_preserve_original_model_layer() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        lo = workbook(
            ["loading_order_no", "shift_gate_out", "spbu_no"],
            [["LO1", "Morning", "SPBU-A"], ["LO2", "Morning", "SPBU-B"]],
        )
        mt = workbook(["shift", "vehicle_registration_no"], [["Morning", "B1001AA"], ["Morning", "B1002AA"]])
        result = create_prediction_run(
            session,
            depot_id="D1",
            model_id="M1",
            loading_order_content=lo,
            loading_order_filename="lo.xlsx",
            availability_content=mt,
            availability_filename="mt.xlsx",
            parameters=None,
            created_by="tester",
        )
        original = result["original_model_prediction"]
        shipment = result["shipments"][0]
        alternative = next(candidate for candidate in shipment["candidates"] if candidate["vehicle_id"] != shipment["assignment"]["assigned_vehicle_id"])
        overridden = override_assignment(
            session,
            result["id"],
            shipment["assignment"]["id"],
            alternative["vehicle_id"],
            "Dispatcher preference",
            "dispatcher",
        )
        assert overridden["shipments"][0]["assignment"]["assignment_status"] == "MANUAL_OVERRIDE"
        assert overridden["shipments"][0]["assignment"]["original_vehicle_id"] == shipment["assignment"]["original_vehicle_id"]
        assert overridden["original_model_prediction"] == original

        adjusted = adjust_shipment(
            session,
            result["id"],
            shipment["id"],
            action="SPLIT_SINGLE",
            line_ids=[shipment["lines"][0]["id"]],
            target_shipment_id=None,
            user_id="dispatcher",
        )
        assert len(adjusted["shipments"]) == 2
        assert adjusted["original_model_prediction"] == original
        assert all(len(item["lines"]) == 1 for item in adjusted["shipments"])


def test_cross_shift_manual_combine_is_rejected() -> None:
    Session = make_session()
    with Session() as session:
        seed(session)
        lo = workbook(
            ["loading_order_no", "shift_gate_out", "spbu_no"],
            [["LO1", "Morning", "SPBU-A"], ["LO2", "Evening", "SPBU-C"]],
        )
        mt = workbook(["shift", "vehicle_registration_no"], [["Morning", "B1001AA"], ["Evening", "B1001AA"]])
        result = create_prediction_run(
            session,
            depot_id="D1",
            model_id="M1",
            loading_order_content=lo,
            loading_order_filename="lo.xlsx",
            availability_content=mt,
            availability_filename="mt.xlsx",
            parameters=None,
            created_by="tester",
        )
        morning, evening = sorted(result["shipments"], key=lambda item: item["shift_id"], reverse=True)
        try:
            adjust_shipment(
                session,
                result["id"],
                morning["id"],
                action="COMBINE",
                line_ids=[],
                target_shipment_id=evening["id"],
                user_id="dispatcher",
            )
        except HTTPException as exc:
            assert exc.detail["code"] == "CROSS_SHIFT_SHIPMENT"
        else:
            raise AssertionError("Cross-shift shipment combination must be rejected.")
