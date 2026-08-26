from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.affinity_intelligence import (
    build_affinity_intelligence_payload,
    concentration_metrics,
    jensen_shannon_distance,
    pattern_shift_level,
)
from app.database import get_db
from app.main import app
from app.models import Base, BridgeMTTag, BridgeSPBUTag, FactLoadingOrderLine, FactShipment, FactShipmentSPBU, MasterDepot, MasterMT, MasterProduct, MasterSPBU, MasterTag


def make_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def seed_master(session, mt_ids: list[str], spbu_ids: list[str]) -> None:
    session.add(MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot Medan"))
    session.add(MasterProduct(product_id="P1", product_name="Pertalite", normalized_product="PERTALITE"))
    session.add(MasterProduct(product_id="P2", product_name="Biosolar", normalized_product="BIOSOLAR"))
    for mt_id in mt_ids:
        session.add(MasterMT(mt_id=mt_id, vehicle_name_raw=mt_id, vehicle_registration=mt_id, depot_id="D1"))
    for spbu_id in spbu_ids:
        session.add(MasterSPBU(spbu_id=spbu_id, spbu_code=spbu_id, spbu_name=f"SPBU {spbu_id}", primary_depot_id="D1"))


def add_observation(session, shipment_id: str, spbu_id: str, mt_id: str, operating_date: date, product_id: str = "P1") -> None:
    if not session.get(FactShipment, shipment_id):
        session.add(
            FactShipment(
                shipment_id=shipment_id,
                source_shipment_id=shipment_id,
                depot_id="D1",
                mt_id=mt_id,
                vehicle_registration=mt_id,
                operating_date=operating_date,
                gate_out_datetime=datetime.combine(operating_date, datetime.min.time()).replace(hour=6),
            )
        )
    session.add(FactShipmentSPBU(shipment_id=shipment_id, spbu_id=spbu_id))
    session.add(
        FactLoadingOrderLine(
            loading_order_number=f"LO-{shipment_id}-{spbu_id}-{product_id}",
            source_depot_name="Depot Medan",
            shipment_id=shipment_id,
            spbu_id=spbu_id,
            product_id=product_id,
            source_product_name="Pertalite" if product_id == "P1" else "Biosolar",
            quantity=8,
        )
    )


def test_historical_affinity_acceptance_and_reverse_probability() -> None:
    Session = make_session()
    with Session() as session:
        seed_master(session, ["T01", "T02"], ["A", "B"])
        session.add(MasterTag(tag_id="TAG_SPBU", tag_value="Urban", normalized_tag="URBAN"))
        session.add(MasterTag(tag_id="TAG_MT", tag_value="Project Alpha", normalized_tag="PROJECT_ALPHA"))
        session.add(BridgeSPBUTag(spbu_id="A", tag_id="TAG_SPBU"))
        session.add(BridgeMTTag(mt_id="T01", tag_id="TAG_MT"))
        for index, mt_id in enumerate(["T01", "T01", "T01", "T02", "T01"], 1):
            add_observation(session, f"S{index}", "A", mt_id, date(2026, 1, index))
        add_observation(session, "S1", "B", "T01", date(2026, 1, 1))
        session.commit()
        payload = build_affinity_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 5), temporal_bucket="DAILY")

    profile = next(row for row in payload["profiles"] if row["spbu_id"] == "A")
    t01 = next(row for row in payload["affinity_distribution"] if row["mt_id"] == "T01")
    assert profile["shipment_count"] == 5
    assert profile["unique_mt_count"] == 2
    assert profile["dominant_mt_id"] == "T01"
    assert profile["dominant_mt_probability"] == 0.8
    assert t01["probability_mt_given_spbu"] == 0.8
    assert t01["probability_spbu_given_mt"] == 1.0
    assert payload["reverse_detail"]["historical_shipments"] == 4
    assert "temporal_stability_score" in payload["reverse_detail"]
    assert payload["evidence"]["distinct_shipment_count"] == 4
    assert payload["scatter"][0]["consistency_score"] >= 0
    assert "spbu_name" in payload["scatter"][0]
    assert profile["spbu_tags"] == ["Urban"]
    assert t01["spbu_tags"] == ["Urban"]
    assert t01["mt_tags"] == ["Project Alpha"]
    assert next(row for row in payload["scatter"] if row["spbu_id"] == "A")["spbu_tags"] == ["Urban"]
    assert next(row for row in payload["pattern_matrix"]["points"] if row["spbu_id"] == "A")["spbu_tags"] == ["Urban"]
    assert next(row for row in payload["network"]["nodes"] if row["entity_type"] == "SPBU" and row["entity_id"] == "A")["tags"] == ["Urban"]
    assert next(row for row in payload["network"]["nodes"] if row["entity_type"] == "MT" and row["entity_id"] == "T01")["tags"] == ["Project Alpha"]
    network_edge = next(row for row in payload["network"]["edges"] if row["spbu_id"] == "A" and row["mt_id"] == "T01")
    assert network_edge["spbu_tags"] == ["Urban"]
    assert network_edge["mt_tags"] == ["Project Alpha"]


def test_high_variability_and_high_consistency_math() -> None:
    even = concentration_metrics([1] * 10)
    concentrated = concentration_metrics([95, 5])
    assert even["consistency_score"] == 0
    assert even["variability_score"] == 100
    assert concentrated["consistency_score"] > 80
    assert concentrated["variability_score"] < 30
    assert concentration_metrics([3])["consistency_score"] == 100
    assert concentration_metrics([3])["variability_score"] == 0


def test_stability_and_pattern_shift_acceptance() -> None:
    Session = make_session()
    with Session() as session:
        seed_master(session, ["T01", "T02", "T05"], ["D", "E"])
        start = date(2026, 1, 5)
        shipment_number = 0
        for week, t01_count in enumerate([8, 8, 8, 8]):
            for item in range(10):
                shipment_number += 1
                mt_id = "T01" if item < t01_count else "T02"
                add_observation(session, f"D{shipment_number}", "D", mt_id, start + timedelta(days=week * 7 + item % 7))
        for week in range(4):
            for item in range(10):
                shipment_number += 1
                mt_id = "T01" if week < 2 else "T05"
                add_observation(session, f"E{shipment_number}", "E", mt_id, start + timedelta(days=week * 7 + item % 7))
        session.commit()
        payload = build_affinity_intelligence_payload(session, "D1", start, start + timedelta(days=27), temporal_bucket="WEEKLY", recent_days=14)

    stable = next(row for row in payload["profiles"] if row["spbu_id"] == "D")
    shifted = next(row for row in payload["profiles"] if row["spbu_id"] == "E")
    assert stable["dominant_mt_persistence"] == 100
    assert stable["temporal_stability_score"] > 90
    assert stable["pattern_shift_level"] == "STABLE"
    assert shifted["temporal_stability_score"] < stable["temporal_stability_score"]
    assert shifted["pattern_shift_level"] == "MAJOR SHIFT"
    assert shifted["previous_dominant_label"] == "T01"
    assert shifted["recent_dominant_label"] == "T05"


def test_duplicate_lo_and_product_filter_are_deduplicated_after_filtering() -> None:
    Session = make_session()
    with Session() as session:
        seed_master(session, ["T01"], ["A"])
        add_observation(session, "S001", "A", "T01", date(2026, 1, 1), "P1")
        session.add(
            FactLoadingOrderLine(
                loading_order_number="LO-S001-A-P2",
                source_depot_name="Depot Medan",
                shipment_id="S001",
                spbu_id="A",
                product_id="P2",
                source_product_name="Biosolar",
                quantity=8,
            )
        )
        session.add(
            FactLoadingOrderLine(
                loading_order_number="LO-S001-A-P1-DUP",
                source_depot_name="Depot Medan",
                shipment_id="S001",
                spbu_id="A",
                product_id="P1",
                source_product_name="Pertalite",
                quantity=8,
            )
        )
        session.commit()
        overall = build_affinity_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1))
        pertalite = build_affinity_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), product_id="P1")
        biosolar = build_affinity_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), product_id="P2")

    assert overall["traceability"]["unique_observation_count"] == 1
    assert pertalite["traceability"]["unique_observation_count"] == 1
    assert biosolar["traceability"]["unique_observation_count"] == 1
    assert pertalite["data_quality"]["duplicate_observations_removed"] == 1


def test_confidence_is_separate_from_consistency() -> None:
    Session = make_session()
    with Session() as session:
        seed_master(session, ["T01"], ["A"])
        for index in range(3):
            add_observation(session, f"S{index}", "A", "T01", date(2026, 1, index + 1))
        session.commit()
        payload = build_affinity_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 31))

    profile = payload["profiles"][0]
    assert profile["consistency_score"] == 100
    assert profile["confidence_level"] == "LOW"
    assert profile["confidence_score"] < 40


def test_one_hundred_shipments_support_high_confidence_acceptance() -> None:
    Session = make_session()
    with Session() as session:
        seed_master(session, ["T05", "T06"], ["C"])
        for index in range(100):
            add_observation(session, f"S{index}", "C", "T05" if index < 95 else "T06", date(2026, 1, 1))
        session.commit()
        payload = build_affinity_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), temporal_bucket="DAILY")

    profile = payload["profiles"][0]
    assert profile["dominant_mt_probability"] == 0.95
    assert profile["consistency_classification"] == "VERY HIGH CONSISTENCY"
    assert profile["confidence_level"] == "HIGH"


def test_jensen_shannon_distance_and_shift_levels() -> None:
    assert jensen_shannon_distance({"T01": 0.8, "T02": 0.2}, {"T01": 0.8, "T02": 0.2}) == 0
    distance = jensen_shannon_distance({"T01": 1.0}, {"T05": 1.0})
    assert distance == 1
    assert pattern_shift_level(distance) == "MAJOR SHIFT"


def test_affinity_api_endpoint() -> None:
    Session = make_session()
    with Session() as session:
        seed_master(session, ["T01", "T02"], ["A"])
        for index, mt_id in enumerate(["T01", "T01", "T01", "T02", "T01"], 1):
            add_observation(session, f"S{index}", "A", mt_id, date(2026, 1, index))
        session.commit()

    def override_db():
        with Session() as test_session:
            yield test_session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/v1/affinity-intelligence/analysis?depot_id=D1&start_date=2026-01-01&end_date=2026-01-05")
        assert response.status_code == 200
        payload = response.json()
        assert payload["phase"] == 4
        assert payload["summary"]["unique_spbu_mt_pairs"] == 2
        assert payload["network"]["nodes"]
        assert "compatibility" not in response.text.lower()
        dates = client.get("/api/v1/affinity-intelligence/available-dates?depot_id=D1")
        assert dates.status_code == 200
        assert dates.json()["max_date"] == "2026-01-05"
    finally:
        app.dependency_overrides = previous
