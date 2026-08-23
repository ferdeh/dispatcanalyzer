from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models import (
    Base,
    BridgeMTTag,
    BridgeSPBUTag,
    FactLoadingOrderLine,
    FactShipment,
    FactShipmentSPBU,
    MLBehavioralModel,
    MLSPBUClusterAssignment,
    MLTrainingRun,
    MasterDepot,
    MasterMT,
    MasterSPBU,
    MasterTag,
    MasterTagType,
)
from app.phase5_behavioral import (
    create_pairing_graph,
    generate_node2vec_embeddings,
    prepare_training_dataset,
    recover_interrupted_behavioral_training_runs,
    train_behavioral_model,
    validate_feature_weights,
)
from app.phase5_concentration import (
    concentration_statistics,
    run_concentration_analysis,
    score_feature_rows,
    transform_anomaly_scores,
    utilization_breadth,
)
from app.phase5_readiness import build_phase5_readiness
from app.phase5_registry import (
    activate_behavioral_model,
    compare_behavioral_models,
    duplicate_behavioral_configuration,
    save_behavioral_model,
)


def make_session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def seed_compatible_master(session, spbu_ids: list[str], *, tags: bool = False) -> None:
    session.add(MasterDepot(depot_id="D1", depot_code="D1", depot_name="Depot One", latitude=3.6, longitude=98.7))
    session.add(MasterMT(mt_id="T1", vehicle_name_raw="Truck 1", vehicle_registration="T-001", vehicle_type_tag=1, depot_id="D1"))
    if tags:
        session.add(MasterTagType(tag_type_id="TYPE_PROJECT", code="PROJECT", name="Project"))
        for tag_id in ("TAG_A", "TAG_B"):
            session.add(MasterTag(tag_id=tag_id, tag_type_id="TYPE_PROJECT", tag_value=tag_id, normalized_tag=tag_id))
            session.add(BridgeMTTag(mt_id="T1", tag_id=tag_id))
    for index, spbu_id in enumerate(spbu_ids):
        session.add(
            MasterSPBU(
                spbu_id=spbu_id,
                spbu_code=spbu_id,
                spbu_name=f"SPBU {spbu_id}",
                latitude=3.5 + index * 0.01,
                longitude=98.6 + index * 0.01,
                vehicle_type_tag=1,
                primary_depot_id="D1",
            )
        )
        if tags:
            session.add(BridgeSPBUTag(spbu_id=spbu_id, tag_id="TAG_A" if index < len(spbu_ids) / 2 else "TAG_B"))
    session.commit()


def add_shipment(session, shipment_id: str, spbu_ids: list[str], day: int, hour: int, *, with_loading_orders: bool = False) -> None:
    operation_date = date(2026, 1, day)
    session.add(
        FactShipment(
            shipment_id=shipment_id,
            source_shipment_id=shipment_id,
            depot_id="D1",
            mt_id="T1",
            vehicle_registration="T-001",
            operating_date=operation_date,
            gate_out_datetime=datetime(2026, 1, day, hour, 0),
        )
    )
    for index, spbu_id in enumerate(spbu_ids):
        session.add(FactShipmentSPBU(shipment_id=shipment_id, spbu_id=spbu_id))
        if with_loading_orders:
            session.add(
                FactLoadingOrderLine(
                    loading_order_number=f"LO-{shipment_id}-{index}",
                    source_depot_name="Depot One",
                    shipment_id=shipment_id,
                    spbu_id=spbu_id,
                    source_product_name="P1",
                    quantity=8,
                )
            )


def test_concentration_math_score_transform_and_directional_synthetic_example() -> None:
    concentrated = concentration_statistics([95, 5])
    broad = concentration_statistics([10] * 10)
    assert concentrated["hhi"] > broad["hhi"]
    assert concentrated["normalized_entropy"] < broad["normalized_entropy"]
    assert concentration_statistics([10])["normalized_entropy"] == 0
    assert utilization_breadth(3, 24) == 0.125
    assert utilization_breadth(0, 0) == 0
    assert transform_anomaly_scores([0.1, 0.3, 0.2]) == [0.0, 100.0, 50.0]

    broad_features = [[20, 17 + (index % 2), 0.85 + (index % 2) * 0.05, 0.08 + index * 0.002, 0.06, 0.98] for index in range(10)]
    concentrated_features = [20, 2, 0.1, 0.95, 0.905, 0.286]
    _raw, normalized, _metadata = score_feature_rows(
        [*broad_features, concentrated_features],
        {"n_estimators": 200, "contamination": "auto", "random_seed": 42},
    )
    assert normalized[-1] > max(normalized[:-1])


def test_readiness_uses_observed_assignments_and_engine_a_deduplicates_canonical_assignment() -> None:
    Session = make_session()
    with Session() as session:
        seed_compatible_master(session, ["A", "B"])
        no_observations = build_phase5_readiness(session, "D1")
        assert no_observations["is_ready"] is False
        assert no_observations["evaluated_assignment_count"] == 0

        add_shipment(session, "S1", ["A"], 1, 6)
        session.add(
            FactLoadingOrderLine(
                loading_order_number="LO-1",
                source_depot_name="Depot One",
                shipment_id="S1",
                spbu_id="A",
                source_product_name="P1",
                quantity=8,
            )
        )
        session.add(
            FactLoadingOrderLine(
                loading_order_number="LO-2",
                source_depot_name="Depot One",
                shipment_id="S1",
                spbu_id="A",
                source_product_name="P2",
                quantity=8,
            )
        )
        session.commit()
        readiness = build_phase5_readiness(session, "D1", include_matrix=True)
        assert readiness["is_ready"] is True
        assert readiness["master_compatibility_pass_percentage"] == 100.0
        assert readiness["depot_latitude"] == 3.6
        assert readiness["depot_longitude"] == 98.7
        assert readiness["evaluated_assignment_count"] == 2
        assert readiness["master_eligibility_matrix"]["blocks_readiness"] is False

        result = run_concentration_analysis(
            session,
            depot_id="D1",
            baseline_start_date=date(2026, 1, 1),
            baseline_end_date=date(2026, 1, 1),
            minimum_shipment_observation=1,
            parameters={"random_seed": 42},
            created_by="tester",
        )
        assert result["profiles"][0]["shipment_observation_count"] == 1
        assert result["methodology"]["observation_key"] == ["depot_id", "shipment_id", "spbu_id", "mt_id"]

        # An unused master combination that is ineligible must remain an Engine A
        # matrix exclusion and must not block Phase 5 readiness.
        session.add(MasterSPBU(spbu_id="BAD", spbu_code="BAD", vehicle_type_tag=0, primary_depot_id="D1"))
        session.commit()
        still_ready = build_phase5_readiness(session, "D1", include_matrix=True)
        assert still_ready["is_ready"] is True
        assert still_ready["master_eligibility_matrix"]["excluded_pair_count"] > 0

        # The same pair does block readiness once it appears as an actual Phase 1
        # Loading Order assignment.
        add_shipment(session, "S-BAD", ["BAD"], 1, 7, with_loading_orders=True)
        session.commit()
        blocked = build_phase5_readiness(session, "D1")
        assert blocked["is_ready"] is False
        assert blocked["master_compatibility_pass_percentage"] < 100
        assert blocked["mismatch_assignment_count"] == 1


def test_pairing_graph_isolated_nodes_and_feature_weight_validation() -> None:
    graph = create_pairing_graph(
        ["A", "B", "ISOLATED"],
        [{"spbu_a_id": "A", "spbu_b_id": "B", "probability_b_given_a": 0.8, "probability_a_given_b": 0.4, "pair_count": 5}],
    )
    assert graph.number_of_nodes() == 3
    assert graph.degree["ISOLATED"] == 0
    assert abs(graph["A"]["B"]["weight"] - 0.6) < 1e-9
    parameters = {"dimensions": 4, "walk_length": 6, "num_walks": 8, "window": 3, "seed": 42}
    embeddings, metadata = generate_node2vec_embeddings(graph, parameters)
    repeated_embeddings, repeated_metadata = generate_node2vec_embeddings(graph, parameters)
    assert embeddings == repeated_embeddings
    assert metadata == repeated_metadata
    assert metadata["implementation"] == "portable_walk_ppmi_svd.v1"
    assert metadata["walk_count"] == 16
    assert embeddings["ISOLATED"] == [0.0] * 4
    assert any(abs(value) > 0 for value in embeddings["A"])
    assert all(len(vector) == 4 for vector in embeddings.values())
    assert validate_feature_weights({"tag": 0.4, "shift": 0.25, "pairing": 0.35})["pairing"] == 0.35
    try:
        validate_feature_weights({"tag": 0.5, "shift": 0.5, "pairing": 0.5})
    except Exception as exc:
        assert "sum" in str(exc).lower()
    else:
        raise AssertionError("Invalid feature weights must be rejected.")


def test_interrupted_behavioral_runs_are_failed_once_and_completed_runs_are_preserved() -> None:
    Session = make_session()
    with Session() as session:
        seed_compatible_master(session, ["A"])
        for index, status in enumerate(("PREPARING_DATA", "TRAINING", "CALCULATING_PROFILES")):
            session.add(
                MLTrainingRun(
                    training_run_id=f"INTERRUPTED-{index}",
                    depot_id="D1",
                    training_start_date=date(2026, 1, 1),
                    training_end_date=date(2026, 1, 2),
                    status=status,
                    dataset_payload={"records": [{"spbu_id": "A"}]},
                )
            )
        session.add(
            MLTrainingRun(
                training_run_id="COMPLETED",
                depot_id="D1",
                training_start_date=date(2026, 1, 1),
                training_end_date=date(2026, 1, 2),
                status="COMPLETED",
            )
        )
        session.commit()

        assert recover_interrupted_behavioral_training_runs(session) == 3
        for index in range(3):
            run = session.get(MLTrainingRun, f"INTERRUPTED-{index}")
            assert run.status == "FAILED"
            assert "API process stopped" in run.error_message
            assert run.completed_at is not None
        assert session.get(MLTrainingRun, "COMPLETED").status == "COMPLETED"
        assert recover_interrupted_behavioral_training_runs(session) == 0


def test_engine_b_dataset_training_artifacts_versioning_activation_and_comparison(tmp_path) -> None:
    get_settings().ml_artifact_dir = tmp_path
    Session = make_session()
    spbu_ids = [f"A{index}" for index in range(4)] + [f"B{index}" for index in range(4)]
    all_active_spbu_ids = [*spbu_ids, "COLD"]
    with Session() as session:
        seed_compatible_master(session, all_active_spbu_ids, tags=True)
        session.get(MasterSPBU, spbu_ids[-1]).latitude = None
        session.get(MasterSPBU, spbu_ids[-1]).longitude = None
        for day in range(1, 13):
            add_shipment(session, f"A-{day}", spbu_ids[:4], day, 2, with_loading_orders=True)
            add_shipment(session, f"B-{day}", spbu_ids[4:], day, 14, with_loading_orders=True)
        session.commit()
        prepared = prepare_training_dataset(
            session,
            depot_id="D1",
            training_start_date=date(2026, 1, 1),
            training_end_date=date(2026, 1, 12),
            minimum_shipment_observation=5,
            shift_definitions=None,
            created_by="tester",
        )
        assert prepared["status"] == "DATASET_READY"
        assert prepared["dataset_summary"]["sufficient_history_spbu_count"] == 8
        assert prepared["dataset_summary"]["active_master_spbu_count"] == 9
        assert prepared["dataset_summary"]["cold_start_active_spbu_count"] == 1
        assert prepared["dataset_summary"]["no_history_active_spbu_count"] == 1
        assert prepared["dataset_summary"]["insufficient_history_active_spbu_count"] == 0
        assert prepared["dataset_summary"]["geocoded_training_spbu_count"] == 8
        assert prepared["dataset_summary"]["missing_coordinate_training_spbu_count"] == 1
        assert len(prepared["shift_definition_snapshot"]) == 4
        training_run_id = prepared["training_run_id"]
        trained = train_behavioral_model(
            session,
            training_run_id,
            {
                "feature_weights": {"tag": 0.4, "shift": 0.25, "pairing": 0.35},
                "node2vec_parameters": {"dimensions": 4, "walk_length": 6, "num_walks": 8, "window": 3, "seed": 42},
                "umap_parameters": {"n_neighbors": 3, "n_components": 2, "min_dist": 0.05, "metric": "euclidean", "random_state": 42},
                "hdbscan_parameters": {"min_cluster_size": 2, "min_samples": 1, "metric": "euclidean", "cluster_selection_method": "eom"},
                "random_seed": 42,
            },
        )
        assert trained["status"] == "COMPLETED"
        assert trained["algorithm_version"] == "phase5.behavioral.portable_n2v_umap_hdbscan.v4"
        assert all("inference_internal_pairings" in profile for profile in trained["result"]["cluster_profiles"])
        assert trained["library_versions"]["node2vec_implementation"] == "portable_walk_ppmi_svd.v1"
        assert "gensim" not in trained["library_versions"]
        assert len(trained["result"]["assignments"]) == 9
        assert trained["result"]["summary"]["historical_training_spbu_count"] == 8
        assert trained["result"]["summary"]["total_covered_spbu_count"] == 9
        assert trained["result"]["summary"]["cold_start_covered_spbu_count"] == 1
        assert trained["result"]["summary"]["no_history_spbu_count"] == 1
        assert trained["result"]["summary"]["insufficient_history_spbu_count"] == 0
        assert sum(profile["historical_member_count"] for profile in trained["result"]["cluster_profiles"]) == 8
        assert sum(profile["cold_start_member_count"] for profile in trained["result"]["cluster_profiles"]) == 1
        cold_start = next(row for row in trained["result"]["assignments"] if row["spbu_id"] == "COLD")
        assert cold_start["coverage_source"] == "ACTIVE_MASTER_COLD_START"
        assert cold_start["history_eligible"] is False
        assert cold_start["is_noise"] is False
        assert cold_start["membership_probability"] <= 0.49
        assert all(row["vehicle_class"] == 1 for row in trained["result"]["assignments"])
        assert sum(row["latitude"] is not None and row["longitude"] is not None for row in trained["result"]["assignments"]) == 8
        assert sum(row["latitude"] is None or row["longitude"] is None for row in trained["result"]["assignments"]) == 1
        model_v1 = save_behavioral_model(session, training_run_id, model_name="Behavior 2026", description="v1", created_by="tester")
        assert model_v1["model_version"] == 1
        assert model_v1["historical_training_spbu_count"] == 8
        assert model_v1["total_covered_spbu_count"] == 9
        assert model_v1["cold_start_covered_spbu_count"] == 1
        assert model_v1["artifacts"]
        assert model_v1["shift_definition_snapshot"] == prepared["shift_definition_snapshot"]
        assert all(row["vehicle_class"] == 1 for row in model_v1["assignments"])
        assert sum(row["latitude"] is not None and row["longitude"] is not None for row in model_v1["assignments"]) == 8
        assert sum(row["latitude"] is None or row["longitude"] is None for row in model_v1["assignments"]) == 1

        first_run = session.get(MLTrainingRun, training_run_id)
        copied_run_id = uuid.uuid4().hex
        session.add(
            MLTrainingRun(
                training_run_id=copied_run_id,
                depot_id=first_run.depot_id,
                training_start_date=first_run.training_start_date,
                training_end_date=first_run.training_end_date,
                minimum_shipment_observation=first_run.minimum_shipment_observation,
                status="COMPLETED",
                training_configuration=first_run.training_configuration,
                dataset_summary=first_run.dataset_summary,
                dataset_payload=first_run.dataset_payload,
                result_payload={**first_run.result_payload, "saved": False, "saved_model_id": None},
                shift_definition_snapshot=first_run.shift_definition_snapshot,
                master_compatibility_snapshot=first_run.master_compatibility_snapshot,
                algorithm_version=first_run.algorithm_version,
                library_versions=first_run.library_versions,
                artifact_temp_path=first_run.artifact_temp_path,
                created_by="tester",
            )
        )
        session.commit()
        model_v2 = save_behavioral_model(session, copied_run_id, model_name="Behavior 2026", description="v2", created_by="tester")
        assert model_v2["model_version"] == 2

        # Cluster numbers are deliberately unrelated between versions. Comparison
        # must recover the same memberships through Jaccard matching.
        assignments_v1 = session.scalars(select(MLSPBUClusterAssignment).where(MLSPBUClusterAssignment.model_id == model_v1["model_id"])).all()
        assignments_v2 = session.scalars(select(MLSPBUClusterAssignment).where(MLSPBUClusterAssignment.model_id == model_v2["model_id"])).all()
        for assignment in assignments_v1:
            assignment.is_noise = False
            assignment.cluster_id = 0 if assignment.spbu_id.startswith("A") else 1
            assignment.cluster_label = f"Cluster {assignment.cluster_id + 1}"
            assignment.membership_probability = 0.9
        for assignment in assignments_v2:
            assignment.is_noise = False
            assignment.cluster_id = 11 if assignment.spbu_id.startswith("A") else 10
            assignment.cluster_label = f"Cluster {assignment.cluster_id + 1}"
            assignment.membership_probability = 0.9
        session.commit()
        comparison = compare_behavioral_models(session, model_v1["model_id"], model_v2["model_id"])
        assert len(comparison["stable_cluster_neighborhood_spbu_ids"]) == 8
        assert "cold-start coverage is excluded" in comparison["methodology"]
        assert all(match["jaccard_similarity"] == 1.0 for match in comparison["cluster_matches"])

        activated_v1 = activate_behavioral_model(session, model_v1["model_id"])
        assert activated_v1["model_status"] == "ACTIVE"
        activated_v2 = activate_behavioral_model(session, model_v2["model_id"])
        assert activated_v2["model_status"] == "ACTIVE"
        assert session.get(MLBehavioralModel, model_v1["model_id"]).model_status == "SAVED"
        draft = duplicate_behavioral_configuration(session, model_v2["model_id"])
        assert "artifact" not in draft
        assert draft["feature_weights"] == {"tag": 0.4, "shift": 0.25, "pairing": 0.35}


def test_phase5_api_permission_seam() -> None:
    Session = make_session()
    with Session() as session:
        seed_compatible_master(session, ["A"])

    def override_db():
        with Session() as test_session:
            yield test_session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        ready = client.get("/api/v1/phase5/readiness?depot_id=D1", headers={"X-User": "viewer", "X-Permissions": "phase5:view"})
        assert ready.status_code == 200
        forbidden = client.post(
            "/api/v1/phase5/engine-a/analyze",
            headers={"X-User": "viewer", "X-Permissions": "phase5:view"},
            json={
                "depot_id": "D1",
                "baseline_start_date": "2026-01-01",
                "baseline_end_date": "2026-01-01",
                "minimum_shipment_observation": 1,
            },
        )
        assert forbidden.status_code == 403
        assert "phase5:run" in forbidden.text
    finally:
        app.dependency_overrides = previous
