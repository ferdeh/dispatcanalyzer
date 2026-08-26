from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, BridgeSPBUTag, FactLoadingOrderLine, FactShipment, FactShipmentSPBU, FactShipmentStop, MasterDepot, MasterProduct, MasterSPBU, MasterTag
from app.pairing_intelligence import build_pairing_intelligence_payload, calculate_confidence, generate_canonical_pairs


def test_canonical_pair_generation_and_n_choose_two() -> None:
    assert generate_canonical_pairs(["C", "A", "B", "A"]) == [("A", "B"), ("A", "C"), ("B", "C")]
    assert len(generate_canonical_pairs(["A", "B"])) == 1
    assert len(generate_canonical_pairs(["A", "B", "C", "D", "E"])) == 10
    assert ("A", "A") not in generate_canonical_pairs(["A", "A", "B"])


def test_confidence_classification() -> None:
    assert calculate_confidence(pair_count=2, shipment_a_count=100, shipment_b_count=100)["confidence_level"] == "INSUFFICIENT_DATA"
    assert calculate_confidence(pair_count=3, shipment_a_count=4, shipment_b_count=100)["confidence_level"] == "INSUFFICIENT_DATA"
    assert calculate_confidence(pair_count=8, shipment_a_count=20, shipment_b_count=20)["confidence_level"] == "LOW"
    assert calculate_confidence(pair_count=20, shipment_a_count=40, shipment_b_count=40)["confidence_level"] == "MEDIUM"
    assert calculate_confidence(pair_count=30, shipment_a_count=40, shipment_b_count=40)["confidence_level"] == "HIGH"


def make_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def seed_pairing_acceptance(session) -> None:
    session.add(MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot Medan"))
    for spbu_id in ["A", "B", "C", "D"]:
        session.add(MasterSPBU(spbu_id=spbu_id, spbu_code=spbu_id, spbu_name=f"SPBU {spbu_id}", primary_depot_id="D1"))
    session.add(MasterProduct(product_id="P1", product_name="Pertalite", normalized_product="PERTALITE"))
    session.add(MasterProduct(product_id="P2", product_name="Biosolar", normalized_product="BIOSOLAR"))
    for tag_id, tag_value, spbu_id in [
        ("TAG_A", "Urban", "A"),
        ("TAG_B", "High Volume", "B"),
        ("TAG_C", "Rural", "C"),
    ]:
        session.add(MasterTag(tag_id=tag_id, tag_value=tag_value, normalized_tag=tag_value.upper().replace(" ", "_")))
        session.add(BridgeSPBUTag(spbu_id=spbu_id, tag_id=tag_id))
    memberships = {
        "S1": ["A", "B"],
        "S2": ["A", "B"],
        "S3": ["A", "B"],
        "S4": ["A", "C"],
        "S5": ["A"],
        "S6": ["C", "D"],
    }
    for shipment_id, spbu_ids in memberships.items():
        session.add(
            FactShipment(
                shipment_id=shipment_id,
                source_shipment_id=shipment_id,
                depot_id="D1",
                operating_date=date(2026, 1, 1),
                vehicle_registration=f"BK-{shipment_id}",
                gate_out_datetime=datetime(2026, 1, 1, 6, 0),
            )
        )
        for index, spbu_id in enumerate(spbu_ids, start=1):
            session.add(FactShipmentSPBU(shipment_id=shipment_id, spbu_id=spbu_id))
            session.add(
                FactLoadingOrderLine(
                    loading_order_number=f"LO-{shipment_id}-{index}",
                    source_depot_name="Depot Medan",
                    shipment_id=shipment_id,
                    spbu_id=spbu_id,
                    product_id="P1",
                    source_product_name="Pertalite",
                    quantity=8,
                )
            )
    session.commit()


def pair_by_ids(payload: dict, left: str, right: str) -> dict:
    return next(row for row in payload["pairs"] if {row["spbu_a_id"], row["spbu_b_id"]} == {left, right})


def test_pairing_acceptance_metrics_and_evidence_reconciliation() -> None:
    Session = make_session()
    with Session() as session:
        seed_pairing_acceptance(session)
        payload = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), limit=10)

    assert payload["summary"]["total_shipments"] == 6
    assert payload["summary"]["multi_spbu_shipments"] == 5
    assert payload["summary"]["unique_spbu"] == 4
    assert payload["summary"]["unique_spbu_pairs"] == 3

    ab = pair_by_ids(payload, "A", "B")
    assert ab["pair_count"] == 3
    assert ab["shipment_a_count"] == 5
    assert ab["shipment_b_count"] == 3
    assert ab["probability_b_given_a"] == 0.6
    assert ab["probability_a_given_b"] == 1.0
    assert ab["support"] == 0.5
    assert ab["lift"] == 1.2
    assert ab["confidence_level"] == "INSUFFICIENT_DATA"
    assert ab["spbu_a_tags"] == ["Urban"]
    assert ab["spbu_b_tags"] == ["High Volume"]
    assert payload["matrix"]["data"][0][9]
    assert any(node["tags"] for node in payload["network"]["nodes"])
    assert payload["evidence"]["distinct_shipment_count"] == ab["pair_count"]
    assert len({row["shipment_id"] for row in payload["evidence"]["rows"]}) == ab["pair_count"]
    assert payload["evidence"]["rows"][0]["spbu_tags"][0]["tags"]

    assert pair_by_ids(payload, "A", "C")["pair_count"] == 1
    assert pair_by_ids(payload, "C", "D")["pair_count"] == 1


def test_depot_and_date_filters() -> None:
    Session = make_session()
    with Session() as session:
        seed_pairing_acceptance(session)
        session.add(MasterDepot(depot_id="D2", depot_code="D2", depot_name="Other Depot"))
        session.add(FactShipment(shipment_id="S7", source_shipment_id="S7", depot_id="D2", operating_date=date(2026, 1, 1)))
        session.add(FactShipmentSPBU(shipment_id="S7", spbu_id="A"))
        session.add(FactShipment(shipment_id="S8", source_shipment_id="S8", depot_id="D1", operating_date=date(2026, 1, 2)))
        session.add(FactShipmentSPBU(shipment_id="S8", spbu_id="A"))
        session.add(FactShipmentSPBU(shipment_id="S8", spbu_id="B"))
        session.commit()

        payload = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), limit=10)
        payload_next_day = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 2), date(2026, 1, 2), limit=10)

    assert payload["summary"]["total_shipments"] == 6
    assert pair_by_ids(payload, "A", "B")["pair_count"] == 3
    assert payload_next_day["summary"]["total_shipments"] == 1
    assert pair_by_ids(payload_next_day, "A", "B")["pair_count"] == 1


def test_product_duplicate_protection_and_product_filter_semantics() -> None:
    Session = make_session()
    with Session() as session:
        session.add(MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot Medan"))
        session.add(MasterSPBU(spbu_id="A", spbu_code="A", spbu_name="SPBU A", primary_depot_id="D1"))
        session.add(MasterSPBU(spbu_id="B", spbu_code="B", spbu_name="SPBU B", primary_depot_id="D1"))
        session.add(MasterProduct(product_id="P1", product_name="Pertalite", normalized_product="PERTALITE"))
        session.add(MasterProduct(product_id="P2", product_name="Biosolar", normalized_product="BIOSOLAR"))
        session.add(FactShipment(shipment_id="S100", source_shipment_id="S100", depot_id="D1", operating_date=date(2026, 1, 1)))
        session.add(FactShipmentSPBU(shipment_id="S100", spbu_id="A"))
        session.add(FactShipmentSPBU(shipment_id="S100", spbu_id="B"))
        for number, spbu_id, product_id, product_name in [
            ("LO1", "A", "P1", "Pertalite"),
            ("LO2", "A", "P2", "Biosolar"),
            ("LO3", "A", "P1", "Pertalite"),
            ("LO4", "B", "P1", "Pertalite"),
        ]:
            session.add(
                FactLoadingOrderLine(
                    loading_order_number=number,
                    source_depot_name="Depot Medan",
                    shipment_id="S100",
                    spbu_id=spbu_id,
                    product_id=product_id,
                    source_product_name=product_name,
                    quantity=1,
                )
            )
        session.commit()
        overall = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), limit=10)
        pertalite = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), product_id="P1", limit=10)
        biosolar = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), product_id="P2", limit=10)

    assert pair_by_ids(overall, "A", "B")["pair_count"] == 1
    assert pair_by_ids(pertalite, "A", "B")["pair_count"] == 1
    assert pertalite["data_quality"]["exclusion_reasons"][0]["count"] >= 1
    assert biosolar["summary"]["unique_spbu_pairs"] == 0


def test_unknown_spbu_and_missing_membership_are_reported_as_exclusions() -> None:
    Session = make_session()
    with Session() as session:
        session.add(MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot Medan"))
        session.add(MasterSPBU(spbu_id="A", spbu_code="A", spbu_name="SPBU A", primary_depot_id="D1"))
        session.add(MasterSPBU(spbu_id="B", spbu_code="B", spbu_name="SPBU B", primary_depot_id="D1"))
        for shipment_id in ["S1", "S2", "S3"]:
            session.add(FactShipment(shipment_id=shipment_id, source_shipment_id=shipment_id, depot_id="D1", operating_date=date(2026, 1, 1)))
        session.add(FactShipmentSPBU(shipment_id="S1", spbu_id="A"))
        session.add(FactShipmentSPBU(shipment_id="S1", spbu_id="B"))
        session.add(FactShipmentSPBU(shipment_id="S2", spbu_id="UNKNOWN_SPBU"))
        session.commit()
        payload = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), limit=10)

    reasons = {row["reason"]: row["count"] for row in payload["data_quality"]["exclusion_reasons"]}
    assert payload["data_quality"]["source_shipments"] == 3
    assert payload["data_quality"]["eligible_shipments"] == 1
    assert reasons["Unknown SPBU"] == 1
    assert reasons["No valid SPBU membership"] == 2


def test_gps_transition_is_consecutive_and_separate_from_pairing() -> None:
    Session = make_session()
    with Session() as session:
        seed_pairing_acceptance(session)
        stop_rows = [
            ("S1", "A", 1),
            ("S1", "B", 2),
            ("S2", "B", 1),
            ("S2", "A", 2),
            ("S3", "A", 1),
            ("S3", "C", 2),
            ("S3", "B", 3),
        ]
        for shipment_id, spbu_id, sequence in stop_rows:
            session.add(
                FactShipmentStop(
                    shipment_stop_id=f"STOP-{shipment_id}-{sequence}",
                    shipment_id=shipment_id,
                    spbu_id=spbu_id,
                    stop_sequence=sequence,
                    sequence_source="GPS",
                    sequence_confidence=1.0,
                )
            )
        session.commit()
        payload = build_pairing_intelligence_payload(session, "D1", date(2026, 1, 1), date(2026, 1, 1), selected_spbu_id="A", limit=10)

    transition_counts = {(row["from_spbu_id"], row["to_spbu_id"]): row["transition_count"] for row in payload["transitions"]}
    assert transition_counts[("A", "B")] == 1
    assert transition_counts[("B", "A")] == 1
    assert transition_counts[("A", "C")] == 1
    assert transition_counts[("C", "B")] == 1
    assert pair_by_ids(payload, "A", "B")["pair_count"] == 3


def test_pairing_api_endpoint() -> None:
    Session = make_session()
    with Session() as session:
        seed_pairing_acceptance(session)

    def override_db():
        with Session() as test_session:
            yield test_session

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/v1/pairing-intelligence/analysis?depot_id=D1&start_date=2026-01-01&end_date=2026-01-01")
        assert response.status_code == 200
        payload = response.json()
        assert payload["page_name"] == "SPBU Pairing Probability Intelligence"
        assert payload["summary"]["unique_spbu_pairs"] == 3
        assert payload["matrix"]["data"]
        assert payload["network"]["edges"]
    finally:
        app.dependency_overrides = previous_overrides
