from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    MLConcentrationAnalysisRun,
    MLSPBUConcentrationProfile,
    MasterDepot,
    MasterSPBU,
)


def test_phase5_saved_concentration_analysis_roundtrip() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    with Session() as session:
        session.add(
            MasterDepot(
                depot_id="depot_phase5_saved",
                depot_code="DP5",
                depot_name="Depot Phase 5 Saved",
            )
        )
        for index, spbu_id in enumerate(("spbu_phase5_a", "spbu_phase5_b")):
            session.add(
                MasterSPBU(
                    spbu_id=spbu_id,
                    spbu_code=f"1420000{index + 1}",
                    spbu_name=f"SPBU Saved {index + 1}",
                    primary_depot_id="depot_phase5_saved",
                )
            )
        session.add(
            MLConcentrationAnalysisRun(
                analysis_run_id="run_phase5_saved",
                depot_id="depot_phase5_saved",
                baseline_start_date=date(2026, 8, 1),
                baseline_end_date=date(2026, 8, 19),
                minimum_shipment_observation=10,
                algorithm_parameters={
                    "n_estimators": 200,
                    "contamination": "auto",
                    "random_seed": 42,
                },
                status="COMPLETED",
                created_by="tester",
                completed_at=datetime(2026, 8, 20, 8, 0),
            )
        )
        for index, spbu_id in enumerate(("spbu_phase5_a", "spbu_phase5_b")):
            session.add(
                MLSPBUConcentrationProfile(
                    profile_id=f"profile_phase5_{index}",
                    analysis_run_id="run_phase5_saved",
                    depot_id="depot_phase5_saved",
                    spbu_id=spbu_id,
                    shipment_observation_count=12 + index,
                    compatible_mt_count=4,
                    historically_used_mt_count=2,
                    concentration_anomaly_score=90.0 - index * 40,
                    concentration_classification=(
                        "INVESTIGATION_RECOMMENDED" if index == 0 else "NORMAL"
                    ),
                    data_sufficiency_status="SUFFICIENT_DATA",
                )
            )
        session.commit()

    def override_db():
        with Session() as session:
            yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        save_payload = {
            "name": "Medan concentration baseline",
            "analysis_run_id": "run_phase5_saved",
            "ui_state": {
                "classification_filter": "INVESTIGATION_RECOMMENDED",
                "table_page_size": 10,
            },
        }
        save_response = client.post(
            "/api/v1/phase5/engine-a/saved-analyses", json=save_payload
        )
        assert save_response.status_code == 200
        saved = save_response.json()
        assert saved["name"] == "Medan concentration baseline"
        assert saved["spbu_count"] == 2
        assert saved["investigation_recommended_count"] == 1
        assert saved["analysis_run"]["analysis_run_id"] == "run_phase5_saved"

        overwrite_response = client.post(
            "/api/v1/phase5/engine-a/saved-analyses",
            json={**save_payload, "ui_state": {"classification_filter": "ALL"}},
        )
        assert overwrite_response.status_code == 200
        assert overwrite_response.json()["id"] == saved["id"]

        list_response = client.get(
            "/api/v1/phase5/engine-a/saved-analyses",
            params={"depot_id": "depot_phase5_saved", "limit": 5, "offset": 0},
        )
        assert list_response.status_code == 200
        listing = list_response.json()
        assert listing["total"] == 1
        assert listing["rows"][0]["name"] == "Medan concentration baseline"
        assert "analysis_run" not in listing["rows"][0]

        detail_response = client.get(
            f"/api/v1/phase5/engine-a/saved-analyses/{saved['id']}"
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["ui_state"] == {"classification_filter": "ALL"}
        assert len(detail["analysis_run"]["profiles"]) == 2

        delete_response = client.delete(
            f"/api/v1/phase5/engine-a/saved-analyses/{saved['id']}"
        )
        assert delete_response.status_code == 200
        assert delete_response.json() == {"status": "DELETED", "id": saved["id"]}
    finally:
        app.dependency_overrides = previous
