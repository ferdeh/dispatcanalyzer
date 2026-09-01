from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base, FactLoadingOrderLine, FactShipment, FactShipmentSPBU, MasterDepot, MasterMT, MasterProduct, MasterSPBU


def test_affinity_saved_configuration_roundtrip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    with Session() as session:
        session.add(MasterDepot(depot_id="depot_affinity_saved", depot_code="DAS", depot_name="Depot Affinity Saved"))
        session.add(MasterProduct(product_id="product_affinity_saved", product_name="Pertalite", normalized_product="PERTALITE"))
        session.add(MasterMT(mt_id="mt_affinity_saved", vehicle_name_raw="MT Saved", vehicle_registration="BK1234AA", depot_id="depot_affinity_saved"))
        session.add(MasterSPBU(spbu_id="spbu_affinity_saved", spbu_code="14200001", spbu_name="SPBU Saved", primary_depot_id="depot_affinity_saved"))
        session.add(
            FactShipment(
                shipment_id="shipment_affinity_saved",
                source_shipment_id="shipment_affinity_saved",
                depot_id="depot_affinity_saved",
                mt_id="mt_affinity_saved",
                vehicle_registration="BK1234AA",
                operating_date=date(2026, 8, 1),
                gate_out_datetime=datetime(2026, 8, 1, 6, 0),
            )
        )
        session.add(FactShipmentSPBU(shipment_id="shipment_affinity_saved", spbu_id="spbu_affinity_saved"))
        session.add(
            FactLoadingOrderLine(
                loading_order_number="LO-AFFINITY-SAVED",
                source_depot_name="Depot Affinity Saved",
                shipment_id="shipment_affinity_saved",
                spbu_id="spbu_affinity_saved",
                product_id="product_affinity_saved",
                source_product_name="Pertalite",
                quantity=8,
            )
        )
        session.commit()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        analysis_response = client.get(
            "/api/v1/affinity-intelligence/analysis",
            params={
                "depot_id": "depot_affinity_saved",
                "start_date": "2026-08-01",
                "end_date": "2026-08-01",
                "product_id": "product_affinity_saved",
                "temporal_bucket": "DAILY",
            },
        )
        assert analysis_response.status_code == 200
        snapshot = analysis_response.json()
        assert snapshot["summary"]["spbu_analyzed"] == 1

        save_payload = {
            "name": "Medan affinity baseline",
            "depot_id": "depot_affinity_saved",
            "start_date": "2026-08-01",
            "end_date": "2026-08-01",
            "product_id": "product_affinity_saved",
            "minimum_observations": 1,
            "confidence": "ALL",
            "temporal_bucket": "DAILY",
            "recent_days": 7,
            "top_n": 5,
            "edge_metric": "SHIPMENT_COUNT",
            "selected_spbu_id": "spbu_affinity_saved",
            "selected_mt_id": "mt_affinity_saved",
            "ui_state": {"spbu_search": "14200001 — SPBU Saved", "scatter_viewport": {"start": 15, "end": 85}},
            "affinity_analysis_snapshot": snapshot,
        }
        save_response = client.post("/api/v1/affinity-intelligence/saved-configurations", json=save_payload)
        assert save_response.status_code == 200
        saved = save_response.json()
        assert saved["name"] == "Medan affinity baseline"
        assert saved["affinity_analysis_snapshot"]["algorithm_version"] == "spbu_mt_affinity.jsd_v1"

        overwrite_response = client.post(
            "/api/v1/affinity-intelligence/saved-configurations",
            json={**save_payload, "recent_days": 14},
        )
        assert overwrite_response.status_code == 200
        assert overwrite_response.json()["id"] == saved["id"]
        assert overwrite_response.json()["recent_days"] == 14

        list_response = client.get("/api/v1/affinity-intelligence/saved-configurations?depot_id=depot_affinity_saved")
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 1
        assert list_response.json()["rows"][0]["spbu_analyzed"] == 1
        assert "affinity_analysis_snapshot" not in list_response.json()["rows"][0]

        detail_response = client.get(f"/api/v1/affinity-intelligence/saved-configurations/{saved['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["selected_spbu_id"] == "spbu_affinity_saved"
        assert detail_response.json()["ui_state"]["scatter_viewport"] == {"start": 15, "end": 85}

        delete_response = client.delete(f"/api/v1/affinity-intelligence/saved-configurations/{saved['id']}")
        assert delete_response.status_code == 200
        assert delete_response.json()["status"] == "DELETED"
    finally:
        app.dependency_overrides.clear()
