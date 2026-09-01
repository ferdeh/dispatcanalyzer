from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, FactShipment, FactShipmentSPBU, MasterDepot, MasterSPBU


def test_pairing_saved_configuration_roundtrip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add(MasterDepot(depot_id="depot_pairing_saved", depot_code="DPS", depot_name="Depot Pairing Saved"))
        session.add_all(
            [
                MasterSPBU(spbu_id="spbu_a", spbu_code="111", spbu_name="SPBU A", primary_depot_id="depot_pairing_saved"),
                MasterSPBU(spbu_id="spbu_b", spbu_code="222", spbu_name="SPBU B", primary_depot_id="depot_pairing_saved"),
                MasterSPBU(spbu_id="spbu_c", spbu_code="333", spbu_name="SPBU C", primary_depot_id="depot_pairing_saved"),
            ]
        )
        session.add_all(
            [
                FactShipment(shipment_id="shipment_1", source_shipment_id="shipment_1", depot_id="depot_pairing_saved", operating_date=date(2026, 8, 1)),
                FactShipment(shipment_id="shipment_2", source_shipment_id="shipment_2", depot_id="depot_pairing_saved", operating_date=date(2026, 8, 2)),
                FactShipment(shipment_id="shipment_3", source_shipment_id="shipment_3", depot_id="depot_pairing_saved", operating_date=date(2026, 8, 3)),
                FactShipmentSPBU(shipment_id="shipment_1", spbu_id="spbu_a"),
                FactShipmentSPBU(shipment_id="shipment_1", spbu_id="spbu_b"),
                FactShipmentSPBU(shipment_id="shipment_2", spbu_id="spbu_a"),
                FactShipmentSPBU(shipment_id="shipment_2", spbu_id="spbu_b"),
                FactShipmentSPBU(shipment_id="shipment_3", spbu_id="spbu_a"),
                FactShipmentSPBU(shipment_id="shipment_3", spbu_id="spbu_c"),
            ]
        )
        session.commit()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        analysis_response = client.get(
            "/api/v1/pairing-intelligence/analysis",
            params={
                "depot_id": "depot_pairing_saved",
                "start_date": "2026-08-01",
                "end_date": "2026-08-03",
                "limit": 10,
            },
        )
        assert analysis_response.status_code == 200
        pairing_snapshot = analysis_response.json()
        assert pairing_snapshot["page_name"] == "SPBU Pairing Probability Intelligence"
        assert pairing_snapshot["summary"]["unique_spbu_pairs"] == 2

        save_response = client.post(
            "/api/v1/pairing-intelligence/saved-configurations",
            json={
                "name": "Medan pairing baseline",
                "depot_id": "depot_pairing_saved",
                "start_date": "2026-08-01",
                "end_date": "2026-08-03",
                "sort_column": "evidence_strength",
                "sort_direction": "desc",
                "ui_state": {"range_preset": "CUSTOM", "pairing_limit": 10, "pairing_offset": 0},
                "pairing_analysis_snapshot": pairing_snapshot,
            },
        )
        assert save_response.status_code == 200
        saved_config = save_response.json()
        assert saved_config["name"] == "Medan pairing baseline"
        assert saved_config["pairing_analysis_snapshot"]["summary"]["unique_spbu_pairs"] == 2

        list_response = client.get("/api/v1/pairing-intelligence/saved-configurations?depot_id=depot_pairing_saved")
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 1
        assert "pairing_analysis_snapshot" not in list_response.json()["rows"][0]

        detail_response = client.get(f"/api/v1/pairing-intelligence/saved-configurations/{saved_config['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["pairing_analysis_snapshot"]["algorithm_version"] == "pairing_v1"

        delete_response = client.delete(f"/api/v1/pairing-intelligence/saved-configurations/{saved_config['id']}")
        assert delete_response.status_code == 200
        assert delete_response.json()["status"] == "DELETED"
    finally:
        app.dependency_overrides.clear()
