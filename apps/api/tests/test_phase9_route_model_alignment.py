from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.affinity_intelligence import ALGORITHM_VERSION as AFFINITY_ALGORITHM_VERSION
from app.database import get_db
from app.main import app
from app.models import (
    AffinityAnalysisConfig,
    Base,
    FactSPBUMTPair,
    MasterDepot,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    MLBehavioralModel,
    MLSPBUClusterAssignment,
    MLTrainingRun,
    OptimizationJob,
    PredictionRun,
    RouteAlignmentEvaluationRow,
    RouteAlignmentEvaluationRun,
    RouteVersion,
    RouteVersionLOAssignment,
    RouteVersionTrip,
)
from app.phase9_alignment import create_evaluation, get_evaluation_row, list_evaluation_rows, list_evaluation_trips, list_route_options


SHIFTS = [
    {
        "shift_id": "SHIFT-1",
        "name": "Shift 1",
        "start_time": "06:00",
        "end_time": "11:59",
        "segments": [{"start_minute": 360, "end_exclusive_minute": 720}],
    },
    {
        "shift_id": "SHIFT-2",
        "name": "Shift 2",
        "start_time": "12:00",
        "end_time": "05:59",
        "segments": [
            {"start_minute": 720, "end_exclusive_minute": 1440},
            {"start_minute": 0, "end_exclusive_minute": 360},
        ],
    },
]


def _record(spbu_id: str, shift_1_share: float) -> dict:
    return {
        "spbu_id": spbu_id,
        "shipment_observation_count": 10,
        "valid_shift_observation_count": 10,
        "shift_distribution": [
            {"shift_id": "SHIFT-1", "shift_name": "Shift 1", "share": shift_1_share},
            {"shift_id": "SHIFT-2", "shift_name": "Shift 2", "share": 1 - shift_1_share},
        ],
        "dominant_shift": "Shift 1" if shift_1_share >= 0.5 else "Shift 2",
        "data_sufficiency_status": "SUFFICIENT",
    }


def _pair(spbu_a_id: str, spbu_b_id: str, b_given_a: float, a_given_b: float) -> dict:
    return {
        "spbu_a_id": spbu_a_id,
        "spbu_b_id": spbu_b_id,
        "pair_count": 4,
        "shipment_a_count": 10,
        "shipment_b_count": 10,
        "total_shipment_count": 30,
        "probability_b_given_a": b_given_a,
        "probability_a_given_b": a_given_b,
        "support": 0.2,
        "lift": 1.1,
        "confidence_score": 0.8,
        "confidence_level": "HIGH",
        "analysis_start_date": "2026-07-01",
        "analysis_end_date": "2026-07-31",
        "algorithm_version": "spbu_pairing.shipment_v1",
    }


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db: Session) -> None:
    db.add(MasterDepot(depot_id="D1", depot_code="TBBM-1", depot_name="TBBM Test", timezone="UTC"))
    db.add_all(
        [
            MasterMT(mt_id="M1", vehicle_name_raw="MT 1", vehicle_registration="BK 1001 AA", depot_id="D1"),
            MasterMT(mt_id="M2", vehicle_name_raw="MT 2", vehicle_registration="BK 1002 AA", depot_id="D1"),
            MasterProduct(product_id="P1", product_name="Pertalite", normalized_product="PERTALITE"),
            *[
                MasterSPBU(spbu_id=spbu_id, spbu_code=f"14.200.00{index}", spbu_name=f"SPBU {index}", primary_depot_id="D1")
                for index, spbu_id in enumerate(["S1", "S2", "S3", "S4"], start=1)
            ],
        ]
    )
    db.flush()
    db.add(
        MLTrainingRun(
            training_run_id="TRAIN-1",
            depot_id="D1",
            training_start_date=date(2026, 7, 1),
            training_end_date=date(2026, 7, 31),
            status="COMPLETED",
            shift_definition_snapshot=SHIFTS,
            dataset_payload={
                "records": [_record("S1", 0.8), _record("S2", 0.6), _record("S3", 0.2)],
                "pair_rows": [_pair("S1", "S2", 0.8, 0.5), _pair("S2", "S3", 0.4, 0.2)],
                "dependency_metadata": {"pairing_algorithm_version": "spbu_pairing.shipment_v1"},
            },
        )
    )
    db.flush()
    db.add(
        MLBehavioralModel(
            model_id="MODEL-1",
            model_name="Source Model",
            model_version=1,
            depot_id="D1",
            source_training_run_id="TRAIN-1",
            training_start_date=date(2026, 7, 1),
            training_end_date=date(2026, 7, 31),
            shift_definition_snapshot=SHIFTS,
            model_status="SAVED",
        )
    )
    db.flush()
    db.add_all(
        [
            MLSPBUClusterAssignment(assignment_id="C1", model_id="MODEL-1", depot_id="D1", spbu_id="S1", cluster_id=1, cluster_label="Cluster 1", membership_probability=0.9, cluster_assignment_type="CORE_TRAINING", data_sufficiency_status="SUFFICIENT"),
            MLSPBUClusterAssignment(assignment_id="C2", model_id="MODEL-1", depot_id="D1", spbu_id="S2", cluster_id=1, cluster_label="Cluster 1", membership_probability=0.8, cluster_assignment_type="CORE_TRAINING", data_sufficiency_status="SUFFICIENT"),
            MLSPBUClusterAssignment(assignment_id="C3", model_id="MODEL-1", depot_id="D1", spbu_id="S3", cluster_id=2, cluster_label="Cluster 2", membership_probability=0.85, cluster_assignment_type="CORE_TRAINING", data_sufficiency_status="SUFFICIENT"),
        ]
    )
    db.add(
        PredictionRun(
            id="PRED-1",
            prediction_run_no="PRED-20260801-01",
            depot_id="D1",
            model_id="MODEL-1",
            model_version=1,
            status="COMPLETED",
            input_loading_order_filename="lo.xlsx",
            input_mt_availability_filename="mt.xlsx",
            algorithm_version="phase6.test",
        )
    )
    db.flush()
    db.add(
        OptimizationJob(
            job_id="JOB-1",
            job_no="P7-20260801-01",
            job_name="Route Test",
            depot_id="D1",
            operating_date=date(2026, 8, 1),
            source_prediction_run_id="PRED-1",
            current_route_version_id="ROUTE-1",
            status="COMPLETED",
        )
    )
    db.flush()
    db.add(
        RouteVersion(
            route_version_id="ROUTE-1",
            job_id="JOB-1",
            version_number=1,
            version_label="V1",
            reason="Initial",
            state_snapshot_id="STATE-SNAPSHOT",
            parameter_snapshot_id="PARAM-SNAPSHOT",
            objective="MIN_TOTAL_COST",
            solver_status="FEASIBLE",
        )
    )
    db.flush()
    gate_out = datetime(2026, 8, 1, 6, 30, tzinfo=timezone.utc)
    db.add(
        RouteVersionTrip(
            route_version_trip_id="TRIP-1",
            route_version_id="ROUTE-1",
            vehicle_id="M1",
            trip_number=1,
            shipment_id="SHIP-1",
            vehicle_ready_at_depot=datetime(2026, 8, 1, 5, 30, tzinfo=timezone.utc),
            gate_out=gate_out,
            estimated_return_depot=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        )
    )
    db.flush()
    assignments = [
        ("A1", "LO-001", "S1", "PLANNED", "TRIP-1", "M1", 1),
        ("A2", "LO-002", "S1", "PLANNED", "TRIP-1", "M1", 2),
        ("A3", "LO-003", "S2", "PLANNED", "TRIP-1", "M1", 3),
        ("A4", "LO-004", "S3", "PLANNED", "TRIP-1", "M1", 4),
        ("A5", "LO-DROP", "S4", "DROPPED", None, None, None),
    ]
    db.add_all(
        [
            RouteVersionLOAssignment(
                route_version_lo_assignment_id=row_id,
                route_version_id="ROUTE-1",
                route_version_trip_id=trip_id,
                loading_order_id=lo_id,
                vehicle_id=mt_id,
                trip_number=1 if trip_id else None,
                shipment_id="SHIP-1" if trip_id else None,
                spbu_id=spbu_id,
                product_id="P1",
                volume_kl=8,
                stop_sequence=sequence,
                planned_gate_out=gate_out if trip_id else None,
                assignment_status=assignment_status,
                dropped_reason_code="NO_CAPACITY" if not trip_id else None,
            )
            for row_id, lo_id, spbu_id, assignment_status, trip_id, mt_id, sequence in assignments
        ]
    )
    db.add_all(
        [
            FactSPBUMTPair(id="AFF-1", depot_id="D1", spbu_id="S1", mt_id="M1", shipment_count=7, total_spbu_shipment_count=10, total_mt_shipment_count=14, probability_mt_given_spbu=0.7, probability_spbu_given_mt=0.5, first_observed=date(2026, 7, 1), last_observed=date(2026, 7, 31), operating_day_count=10, confidence_score=0.9, confidence_level="HIGH", analysis_start_date=date(2026, 7, 1), analysis_end_date=date(2026, 7, 31), product_filter="ALL", algorithm_version=AFFINITY_ALGORITHM_VERSION),
            FactSPBUMTPair(id="AFF-2", depot_id="D1", spbu_id="S2", mt_id="M1", shipment_count=4, total_spbu_shipment_count=10, total_mt_shipment_count=14, probability_mt_given_spbu=0.4, probability_spbu_given_mt=0.29, first_observed=date(2026, 7, 1), last_observed=date(2026, 7, 31), operating_day_count=8, confidence_score=0.7, confidence_level="MEDIUM", analysis_start_date=date(2026, 7, 1), analysis_end_date=date(2026, 7, 31), product_filter="ALL", algorithm_version=AFFINITY_ALGORITHM_VERSION),
            FactSPBUMTPair(id="AFF-3", depot_id="D1", spbu_id="S3", mt_id="M2", shipment_count=5, total_spbu_shipment_count=10, total_mt_shipment_count=5, probability_mt_given_spbu=0.5, probability_spbu_given_mt=1.0, first_observed=date(2026, 7, 1), last_observed=date(2026, 7, 31), operating_day_count=9, confidence_score=0.8, confidence_level="HIGH", analysis_start_date=date(2026, 7, 1), analysis_end_date=date(2026, 7, 31), product_filter="ALL", algorithm_version=AFFINITY_ALGORITHM_VERSION),
        ]
    )
    db.commit()


def test_phase9_source_aligned_scoring_is_deduplicated_and_auditable() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)

        route_options = list_route_options(db, "D1")
        assert route_options["total"] == 1
        assert route_options["rows"][0]["lineage_status"] == "READY"

        result = create_evaluation(db, "D1", "ROUTE-1", created_by="tester")
        metrics = result["summary"]["metrics"]
        assert metrics["cluster_cohesion"]["score_pct"] == pytest.approx(33.33)
        assert metrics["shift_alignment"]["score_pct"] == pytest.approx(53.33)
        assert metrics["historical_spbu_pairing"]["score_pct"] == pytest.approx(31.67)
        assert metrics["historical_mt_affinity"]["score_pct"] == pytest.approx(36.67)
        assert result["summary"]["scope"]["unique_spbu_pairs"] == 3
        assert result["summary"]["scope"]["unique_trip_spbu_observations"] == 3
        assert result["summary"]["scope"]["total_lo"] == 5
        assert result["source_bundle"]["lineage"]["phase5_model_id"] == "MODEL-1"
        assert result["source_bundle"]["components"]["historical_mt_affinity"]["resolution_method"] == "EXACT_FACT"

        # The same immutable route + source bundle must resolve to the same run.
        repeated = create_evaluation(db, "D1", "ROUTE-1", created_by="tester")
        assert repeated["evaluation_run_id"] == result["evaluation_run_id"]
        assert db.scalar(select(func.count()).select_from(RouteAlignmentEvaluationRun)) == 1

        rows = list_evaluation_rows(
            db,
            result["evaluation_run_id"],
            page=1,
            page_size=10,
            search="SPBU 1",
            sort_by="loading_order_id",
            sort_direction="desc",
        )
        assert [row["loading_order_id"] for row in rows["rows"]] == ["LO-002", "LO-001"]
        assert rows["rows"][0]["cluster_cohesion_score"] == 50.0
        assert rows["rows"][0]["spbu_pairing_score"] == 32.5

        dropped = db.scalar(select(RouteAlignmentEvaluationRow).where(RouteAlignmentEvaluationRow.loading_order_id == "LO-DROP"))
        detail = get_evaluation_row(db, result["evaluation_run_id"], dropped.evaluation_row_id)
        assert detail["evaluable_category_count"] == 0
        assert detail["cluster_cohesion_score"] is None
        assert detail["mt_affinity_status"] == "NO_ROUTE_ASSIGNMENT"
        assert detail["mt_affinity_evidence"]["reason"] == "NO_CAPACITY"


def test_phase9_api_supports_route_selection_pagination_sort_and_detail() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)

    def override_db():
        with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        routes = client.get("/api/v1/phase9/route-model-alignment/routes", params={"depot_id": "D1"})
        assert routes.status_code == 200
        assert routes.json()["rows"][0]["route_version_id"] == "ROUTE-1"

        created = client.post(
            "/api/v1/phase9/route-model-alignment/evaluations",
            json={"depot_id": "D1", "route_version_id": "ROUTE-1"},
        )
        assert created.status_code == 201
        run_id = created.json()["evaluation_run_id"]

        page = client.get(
            f"/api/v1/phase9/route-model-alignment/evaluations/{run_id}/rows",
            params={"page": 1, "page_size": 10, "sort_by": "mt_affinity", "sort_direction": "desc"},
        )
        assert page.status_code == 200
        assert page.json()["total"] == 5
        assert page.json()["rows"][0]["mt_affinity_score"] == 70.0
        assert page.json()["rows"][-1]["mt_affinity_score"] is None

        row_id = page.json()["rows"][0]["evaluation_row_id"]
        detail = client.get(f"/api/v1/phase9/route-model-alignment/evaluations/{run_id}/rows/{row_id}")
        assert detail.status_code == 200
        assert detail.json()["shift_evidence"]["route_shift_id"] == "SHIFT-1"

        trips = client.get(
            f"/api/v1/phase9/route-model-alignment/evaluations/{run_id}/trips",
            params={"page": 1, "page_size": 10, "search": "14.200.003", "sort_by": "spbu_number", "sort_direction": "desc"},
        )
        assert trips.status_code == 200
        assert trips.json()["total"] == 1
        assert trips.json()["rows"][0]["loading_order_numbers"] == ["LO-001", "LO-002", "LO-003", "LO-004"]
        assert trips.json()["rows"][0]["spbu_numbers"] == ["14.200.001", "14.200.002", "14.200.003"]

        latest = client.get("/api/v1/phase9/route-model-alignment/evaluations/by-route/ROUTE-1")
        assert latest.status_code == 200
        assert latest.json()["evaluation_run_id"] == run_id
    finally:
        app.dependency_overrides.clear()


def test_phase9_trip_matrix_pagination_sort_search_and_legacy_enrichment() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        result = create_evaluation(db, "D1", "ROUTE-1")
        run = db.get(RouteAlignmentEvaluationRun, result["evaluation_run_id"])
        original = dict(run.summary_snapshot["trips"][0])
        snapshot = dict(run.summary_snapshot)
        snapshot["trips"] = [
            {
                **original,
                "route_version_trip_id": f"SYNTHETIC-{index:02d}",
                "shipment_id": f"SHIP-{index:02d}",
                "trip_number": index,
            }
            for index in range(1, 13)
        ]
        run.summary_snapshot = snapshot
        db.commit()

        second_page = list_evaluation_trips(
            db,
            result["evaluation_run_id"],
            page=2,
            page_size=10,
            sort_by="trip_number",
            sort_direction="asc",
        )
        assert second_page["total"] == 12
        assert second_page["page_count"] == 2
        assert [row["trip_number"] for row in second_page["rows"]] == [11, 12]

        searched = list_evaluation_trips(
            db,
            result["evaluation_run_id"],
            search="LO-004",
            sort_by="loading_order_number",
            sort_direction="desc",
        )
        assert searched["total"] == 12
        assert searched["rows"][0]["loading_order_numbers"] == ["LO-001", "LO-002", "LO-003", "LO-004"]
        assert searched["rows"][0]["spbu_numbers"] == ["14.200.001", "14.200.002", "14.200.003"]


def test_phase9_prefers_an_exact_complete_phase4_saved_snapshot() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        db.add(
            AffinityAnalysisConfig(
                id="AFF-CONFIG-1",
                name="Exact source affinity",
                normalized_name="EXACT SOURCE AFFINITY",
                depot_id="D1",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 31),
                product_id=None,
                minimum_observations=1,
                confidence_filter="ALL",
                affinity_analysis_snapshot={
                    "algorithm_version": AFFINITY_ALGORITHM_VERSION,
                    "effective_filters": {
                        "depot_id": "D1",
                        "start_date": "2026-07-01",
                        "end_date": "2026-07-31",
                        "product_id": None,
                    },
                    "profiles": [
                        {
                            "spbu_id": spbu_id,
                            "shipment_count": 10,
                            "operating_day_count": 8,
                            "first_observed": "2026-07-01",
                            "last_observed": "2026-07-31",
                            "confidence_score": 0.8,
                            "confidence_level": "HIGH",
                            "fleet_affinity_vector": {"M1": probability},
                        }
                        for spbu_id, probability in [("S1", 0.9), ("S2", 0.8), ("S3", 0.7)]
                    ],
                    "affinity_distribution": [],
                    "traceability": {"calculated_at": "2026-08-01T00:00:00+00:00"},
                },
            )
        )
        db.commit()

        result = create_evaluation(db, "D1", "ROUTE-1")

        metric = result["summary"]["metrics"]["historical_mt_affinity"]
        assert metric["score_pct"] == 80.0
        assert metric["resolution_method"] == "EXACT_SAVED_SNAPSHOT"
        assert result["source_bundle"]["saved_analysis_links"]["phase4_affinity_saved_analysis_id"] == "AFF-CONFIG-1"


def test_phase9_blocks_future_source_evidence() -> None:
    SessionLocal = _session_factory()
    with SessionLocal() as db:
        _seed(db)
        model = db.get(MLBehavioralModel, "MODEL-1")
        model.training_end_date = date(2026, 8, 1)
        db.commit()

        with pytest.raises(HTTPException) as raised:
            create_evaluation(db, "D1", "ROUTE-1")

        assert raised.value.status_code == 422
        assert raised.value.detail["code"] == "BLOCKED_FUTURE_EVIDENCE"
