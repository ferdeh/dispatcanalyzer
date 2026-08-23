from __future__ import annotations

import json
import shutil
import uuid
from collections import defaultdict
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    MLBehavioralModel,
    MLClusterProfile,
    MLModelArtifact,
    MLSPBUClusterAssignment,
    MLTrainingRun,
    MasterDepot,
    MasterSPBU,
    PredictionRun,
)
from .phase5_behavioral import _cluster_profiles, artifact_checksum
from .phase5_readiness import require_phase5_readiness


def save_behavioral_model(db: Session, training_run_id: str, *, model_name: str, description: str | None, created_by: str) -> dict:
    name = model_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model Name is required.")
    run = db.get(MLTrainingRun, training_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found.")
    if run.status != "COMPLETED" or not run.result_payload:
        raise HTTPException(status_code=409, detail="Train and review the model before saving it.")
    require_phase5_readiness(db, run.depot_id)
    existing_for_run = db.scalar(select(MLBehavioralModel).where(MLBehavioralModel.source_training_run_id == training_run_id))
    if existing_for_run:
        raise HTTPException(status_code=409, detail="This reviewed training result has already been saved.")
    source_artifact = Path(run.artifact_temp_path or "").resolve()
    artifact_root = get_settings().ml_artifact_dir.resolve()
    if not run.artifact_temp_path or not source_artifact.exists() or artifact_root not in source_artifact.parents:
        raise HTTPException(status_code=500, detail="The trained model artifact is unavailable; retrain before saving.")

    latest_version = db.scalar(
        select(func.max(MLBehavioralModel.model_version)).where(
            MLBehavioralModel.depot_id == run.depot_id,
            func.lower(MLBehavioralModel.model_name) == name.lower(),
        )
    ) or 0
    model_id = uuid.uuid4().hex
    version = int(latest_version) + 1
    config = run.training_configuration
    result = run.result_payload
    summary = result["summary"]
    model = MLBehavioralModel(
        model_id=model_id,
        model_name=name,
        model_description=(description or "").strip() or None,
        model_version=version,
        depot_id=run.depot_id,
        source_training_run_id=run.training_run_id,
        training_start_date=run.training_start_date,
        training_end_date=run.training_end_date,
        training_shipment_count=int(run.dataset_summary.get("shipment_count", 0)),
        training_spbu_count=int(summary["training_spbu_count"]),
        total_covered_spbu_count=int(summary.get("total_covered_spbu_count", summary["training_spbu_count"])),
        cold_start_covered_spbu_count=int(summary.get("cold_start_covered_spbu_count", 0)),
        no_history_spbu_count=int(summary.get("no_history_spbu_count", 0)),
        insufficient_history_spbu_count=int(summary.get("insufficient_history_spbu_count", 0)),
        minimum_shipment_observation=run.minimum_shipment_observation,
        tag_feature_configuration=run.dataset_payload.get("tag_feature_configuration", {}),
        tag_encoder_reference={
            "feature_names": run.dataset_payload.get("tag_feature_names", []),
            "artifact_type": "JOBLIB_MODEL_PACKAGE",
        },
        shift_definition_snapshot=run.shift_definition_snapshot,
        feature_weights=config["feature_weights"],
        node2vec_parameters=config["node2vec_parameters"],
        umap_parameters=config["umap_parameters"],
        hdbscan_parameters=config["hdbscan_parameters"],
        dependency_metadata=run.dataset_payload.get("dependency_metadata", {}),
        cluster_count=int(summary["cluster_count"]),
        noise_spbu_count=int(summary["noise_spbu_count"]),
        average_membership_probability=float(summary["average_membership_probability"]),
        algorithm_version=run.algorithm_version,
        library_versions=run.library_versions,
        random_seed=int(config["random_seed"]),
        model_status="SAVED",
        created_by=created_by,
    )
    target_dir = (artifact_root / model_id / f"v{version}").resolve()
    if artifact_root not in target_dir.parents:
        raise HTTPException(status_code=500, detail="Resolved model artifact path escaped ML_ARTIFACT_DIR.")
    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        package_path = target_dir / "model.joblib"
        shutil.copy2(source_artifact, package_path)
        manifest = {
            "model_id": model_id,
            "model_name": name,
            "model_version": version,
            "depot_id": run.depot_id,
            "algorithm_version": run.algorithm_version,
            "library_versions": run.library_versions,
            "configuration": config,
            "shift_definition_snapshot": run.shift_definition_snapshot,
            "dependency_metadata": run.dataset_payload.get("dependency_metadata", {}),
        }
        manifest_path = target_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        db.add(model)
        for assignment in result["assignments"]:
            db.add(
                MLSPBUClusterAssignment(
                    assignment_id=uuid.uuid4().hex,
                    model_id=model_id,
                    depot_id=run.depot_id,
                    spbu_id=assignment["spbu_id"],
                    cluster_id=assignment["cluster_id"],
                    cluster_label=assignment["cluster_label"],
                    membership_probability=assignment["membership_probability"],
                    is_noise=assignment["is_noise"],
                    shipment_observation_count=assignment.get("shipment_observation_count", 0),
                    coverage_source=assignment.get("coverage_source", "BEHAVIORAL_HISTORY"),
                    history_eligible=bool(assignment.get("history_eligible", True)),
                    dominant_shift=assignment["dominant_shift"],
                    key_tags=assignment["key_tags"],
                    visualization_x=assignment["visualization_x"],
                    visualization_y=assignment["visualization_y"],
                )
            )
        for profile in result["cluster_profiles"]:
            db.add(
                MLClusterProfile(
                    cluster_profile_id=uuid.uuid4().hex,
                    model_id=model_id,
                    cluster_id=profile["cluster_id"],
                    cluster_label=profile["cluster_label"],
                    cluster_size=profile["cluster_size"],
                    historical_member_count=profile.get("historical_member_count", profile["cluster_size"]),
                    cold_start_member_count=profile.get("cold_start_member_count", 0),
                    no_history_member_count=profile.get("no_history_member_count", 0),
                    training_spbu_percentage=profile["training_spbu_percentage"],
                    common_tags=profile["common_tags"],
                    shift_distribution=profile["shift_distribution"],
                    dominant_shift=profile["dominant_shift"],
                    top_internal_pairings=profile["top_internal_pairings"],
                    average_membership_probability=profile["average_membership_probability"],
                    low_confidence_member_count=profile["low_confidence_member_count"],
                )
            )
        for artifact_type, path in (("JOBLIB_MODEL_PACKAGE", package_path), ("MODEL_MANIFEST", manifest_path)):
            db.add(
                MLModelArtifact(
                    artifact_id=uuid.uuid4().hex,
                    model_id=model_id,
                    artifact_type=artifact_type,
                    storage_uri=str(path.relative_to(artifact_root)),
                    checksum_sha256=artifact_checksum(path),
                    byte_size=path.stat().st_size,
                )
            )
        run.result_payload = {**result, "saved": True, "saved_model_id": model_id}
        db.commit()
    except Exception as exc:
        db.rollback()
        if target_dir.exists() and artifact_root in target_dir.parents:
            shutil.rmtree(target_dir)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="Model artifact persistence failed; no registry record was created.") from exc
    return get_behavioral_model(db, model_id)


def _model_summary(model: MLBehavioralModel, depot_name: str | None = None) -> dict:
    return {
        "model_id": model.model_id,
        "model_name": model.model_name,
        "model_description": model.model_description,
        "model_version": model.model_version,
        "depot_id": model.depot_id,
        "depot_name": depot_name or model.depot_id,
        "training_start_date": model.training_start_date.isoformat(),
        "training_end_date": model.training_end_date.isoformat(),
        "training_shipment_count": model.training_shipment_count,
        "training_spbu_count": model.training_spbu_count,
        "historical_training_spbu_count": model.training_spbu_count,
        "total_covered_spbu_count": model.total_covered_spbu_count,
        "cold_start_covered_spbu_count": model.cold_start_covered_spbu_count,
        "no_history_spbu_count": model.no_history_spbu_count,
        "insufficient_history_spbu_count": model.insufficient_history_spbu_count,
        "minimum_shipment_observation": model.minimum_shipment_observation,
        "cluster_count": model.cluster_count,
        "noise_spbu_count": model.noise_spbu_count,
        "average_membership_probability": model.average_membership_probability,
        "algorithm_version": model.algorithm_version,
        "model_status": model.model_status,
        "created_by": model.created_by,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }


def list_behavioral_models(db: Session, depot_id: str | None = None) -> list[dict]:
    statement = select(MLBehavioralModel)
    if depot_id:
        statement = statement.where(MLBehavioralModel.depot_id == depot_id)
    models = db.scalars(statement.order_by(desc(MLBehavioralModel.created_at))).all()
    depot_ids = sorted({model.depot_id for model in models})
    depots = {row.depot_id: row.depot_name for row in (db.scalars(select(MasterDepot).where(MasterDepot.depot_id.in_(depot_ids))).all() if depot_ids else [])}
    return [_model_summary(model, depots.get(model.depot_id)) for model in models]


def get_behavioral_model(db: Session, model_id: str) -> dict:
    model = db.get(MLBehavioralModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Behavioral model not found.")
    depot = db.get(MasterDepot, model.depot_id)
    assignments = db.scalars(
        select(MLSPBUClusterAssignment)
        .where(MLSPBUClusterAssignment.model_id == model_id)
        .order_by(MLSPBUClusterAssignment.is_noise, MLSPBUClusterAssignment.cluster_id, MLSPBUClusterAssignment.spbu_id)
    ).all()
    profiles = db.scalars(select(MLClusterProfile).where(MLClusterProfile.model_id == model_id).order_by(MLClusterProfile.cluster_id)).all()
    artifacts = db.scalars(select(MLModelArtifact).where(MLModelArtifact.model_id == model_id)).all()
    spbu_ids = [assignment.spbu_id for assignment in assignments]
    spbus = {row.spbu_id: row for row in (db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all() if spbu_ids else [])}
    assignment_payloads = [
        {
            "spbu_id": assignment.spbu_id,
            "spbu_code": spbus[assignment.spbu_id].spbu_code if assignment.spbu_id in spbus else assignment.spbu_id,
            "spbu_name": spbus[assignment.spbu_id].spbu_name if assignment.spbu_id in spbus else None,
            "latitude": (
                float(spbus[assignment.spbu_id].latitude)
                if assignment.spbu_id in spbus and spbus[assignment.spbu_id].latitude is not None
                else None
            ),
            "longitude": (
                float(spbus[assignment.spbu_id].longitude)
                if assignment.spbu_id in spbus and spbus[assignment.spbu_id].longitude is not None
                else None
            ),
            "shipment_observation_count": assignment.shipment_observation_count,
            "coverage_source": assignment.coverage_source,
            "history_eligible": assignment.history_eligible,
            "cluster_id": assignment.cluster_id,
            "cluster_label": assignment.cluster_label,
            "membership_probability": assignment.membership_probability,
            "is_noise": assignment.is_noise,
            "dominant_shift": assignment.dominant_shift,
            "vehicle_class": spbus[assignment.spbu_id].vehicle_type_tag if assignment.spbu_id in spbus else None,
            "key_tags": assignment.key_tags,
            "visualization_x": assignment.visualization_x,
            "visualization_y": assignment.visualization_y,
        }
        for assignment in assignments
    ]
    stored_profile_payloads = [
        {
            "cluster_id": profile.cluster_id,
            "cluster_label": profile.cluster_label,
            "cluster_size": profile.cluster_size,
            "historical_member_count": profile.historical_member_count,
            "cold_start_member_count": profile.cold_start_member_count,
            "no_history_member_count": profile.no_history_member_count,
            "training_spbu_percentage": profile.training_spbu_percentage,
            "common_tags": profile.common_tags,
            "shift_distribution": profile.shift_distribution,
            "dominant_shift": profile.dominant_shift,
            "top_internal_pairings": profile.top_internal_pairings,
            "average_membership_probability": profile.average_membership_probability,
            "low_confidence_member_count": profile.low_confidence_member_count,
            "evidence_scope": "Historical members only; cold-start coverage is reported separately.",
        }
        for profile in profiles
    ]
    source_run = db.get(MLTrainingRun, model.source_training_run_id) if model.source_training_run_id else None
    if source_run and (source_run.dataset_payload or {}).get("records"):
        profile_payloads = _cluster_profiles(
            assignment_payloads,
            source_run.dataset_payload.get("records", []),
            source_run.dataset_payload.get("pair_rows", []),
            model.shift_definition_snapshot,
        )
    else:
        profile_payloads = stored_profile_payloads
    return {
        **_model_summary(model, depot.depot_name if depot else None),
        "tag_feature_configuration": model.tag_feature_configuration,
        "tag_encoder_reference": model.tag_encoder_reference,
        "shift_definition_snapshot": model.shift_definition_snapshot,
        "feature_weights": model.feature_weights,
        "node2vec_parameters": model.node2vec_parameters,
        "umap_parameters": model.umap_parameters,
        "hdbscan_parameters": model.hdbscan_parameters,
        "dependency_metadata": model.dependency_metadata,
        "library_versions": model.library_versions,
        "random_seed": model.random_seed,
        "assignments": assignment_payloads,
        "cluster_profiles": profile_payloads,
        "artifacts": [
            {
                "artifact_type": artifact.artifact_type,
                "checksum_sha256": artifact.checksum_sha256,
                "byte_size": artifact.byte_size,
            }
            for artifact in artifacts
        ],
    }


def activate_behavioral_model(db: Session, model_id: str) -> dict:
    model = db.get(MLBehavioralModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Behavioral model not found.")
    require_phase5_readiness(db, model.depot_id)
    db.query(MLBehavioralModel).filter(
        MLBehavioralModel.depot_id == model.depot_id,
        MLBehavioralModel.model_status == "ACTIVE",
        MLBehavioralModel.model_id != model_id,
    ).update({MLBehavioralModel.model_status: "SAVED"}, synchronize_session=False)
    model.model_status = "ACTIVE"
    db.commit()
    return get_behavioral_model(db, model_id)


def duplicate_behavioral_configuration(db: Session, model_id: str) -> dict:
    model = db.get(MLBehavioralModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Behavioral model not found.")
    return {
        "source_model_id": model_id,
        "depot_id": model.depot_id,
        "training_start_date": model.training_start_date.isoformat(),
        "training_end_date": model.training_end_date.isoformat(),
        "minimum_shipment_observation": model.minimum_shipment_observation,
        "shift_definitions": model.shift_definition_snapshot,
        "feature_weights": model.feature_weights,
        "node2vec_parameters": model.node2vec_parameters,
        "umap_parameters": model.umap_parameters,
        "hdbscan_parameters": model.hdbscan_parameters,
        "random_seed": model.random_seed,
        "notice": "Configuration copied into a new unsaved training draft. No trained artifact was duplicated.",
    }


def delete_behavioral_model(db: Session, model_id: str) -> dict:
    model = db.get(MLBehavioralModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Behavioral model not found.")
    if model.model_status == "ACTIVE":
        raise HTTPException(status_code=409, detail="An ACTIVE model cannot be deleted. Activate another model or archive it first.")
    if db.scalar(select(PredictionRun.id).where(PredictionRun.model_id == model_id).limit(1)):
        raise HTTPException(status_code=409, detail="This model is referenced by an auditable Phase 6 prediction run and cannot be deleted.")
    artifact_root = get_settings().ml_artifact_dir.resolve()
    model_dir = (artifact_root / model_id).resolve()
    if artifact_root not in model_dir.parents:
        raise HTTPException(status_code=500, detail="Resolved model artifact path escaped ML_ARTIFACT_DIR.")
    db.execute(delete(MLModelArtifact).where(MLModelArtifact.model_id == model_id))
    db.execute(delete(MLSPBUClusterAssignment).where(MLSPBUClusterAssignment.model_id == model_id))
    db.execute(delete(MLClusterProfile).where(MLClusterProfile.model_id == model_id))
    db.delete(model)
    db.commit()
    if model_dir.exists():
        shutil.rmtree(model_dir)
    return {"deleted": True, "model_id": model_id, "artifacts_removed": not model_dir.exists()}


def _assignment_sets(db: Session, model_id: str) -> tuple[dict[int, set[str]], set[str], dict[str, MLSPBUClusterAssignment]]:
    assignments = db.scalars(select(MLSPBUClusterAssignment).where(MLSPBUClusterAssignment.model_id == model_id)).all()
    clusters: dict[int, set[str]] = defaultdict(set)
    noise = set()
    lookup = {}
    for assignment in assignments:
        if not assignment.history_eligible:
            continue
        lookup[assignment.spbu_id] = assignment
        if assignment.is_noise or assignment.cluster_id is None:
            noise.add(assignment.spbu_id)
        else:
            clusters[assignment.cluster_id].add(assignment.spbu_id)
    return dict(clusters), noise, lookup


def compare_behavioral_models(db: Session, model_a_id: str, model_b_id: str) -> dict:
    if model_a_id == model_b_id:
        raise HTTPException(status_code=400, detail="Select two different saved models.")
    model_a = db.get(MLBehavioralModel, model_a_id)
    model_b = db.get(MLBehavioralModel, model_b_id)
    if not model_a or not model_b:
        raise HTTPException(status_code=404, detail="One or both behavioral models were not found.")
    if model_a.depot_id != model_b.depot_id:
        raise HTTPException(status_code=400, detail="Behavioral models can only be compared within the same depot.")
    clusters_a, noise_a, lookup_a = _assignment_sets(db, model_a_id)
    clusters_b, noise_b, lookup_b = _assignment_sets(db, model_b_id)
    ids_a, ids_b = sorted(clusters_a), sorted(clusters_b)
    similarities = np.zeros((len(ids_a), len(ids_b)), dtype=float)
    for left_index, left_id in enumerate(ids_a):
        for right_index, right_id in enumerate(ids_b):
            intersection = clusters_a[left_id] & clusters_b[right_id]
            union = clusters_a[left_id] | clusters_b[right_id]
            similarities[left_index, right_index] = len(intersection) / len(union) if union else 0.0
    matches = []
    if similarities.size:
        try:
            from scipy.optimize import linear_sum_assignment
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail="SciPy is required for model comparison.") from exc
        row_indexes, column_indexes = linear_sum_assignment(1.0 - similarities)
        matches = [
            {
                "model_a_cluster_id": ids_a[row_index],
                "model_b_cluster_id": ids_b[column_index],
                "jaccard_similarity": round(float(similarities[row_index, column_index]), 6),
                "intersection_count": len(clusters_a[ids_a[row_index]] & clusters_b[ids_b[column_index]]),
            }
            for row_index, column_index in zip(row_indexes, column_indexes)
        ]
    match_by_a = {row["model_a_cluster_id"]: row for row in matches}
    common_spbus = sorted(set(lookup_a) & set(lookup_b))
    stable = []
    changed = []
    for spbu_id in common_spbus:
        left, right = lookup_a[spbu_id], lookup_b[spbu_id]
        if left.is_noise or right.is_noise:
            continue
        match = match_by_a.get(int(left.cluster_id))
        if match and match["model_b_cluster_id"] == right.cluster_id and match["jaccard_similarity"] >= 0.5:
            stable.append(spbu_id)
        else:
            changed.append(spbu_id)
    splits = [
        {
            "model_a_cluster_id": cluster_id,
            "overlapping_model_b_clusters": [right_id for right_id, values in clusters_b.items() if values & members],
        }
        for cluster_id, members in clusters_a.items()
        if sum(bool(values & members) for values in clusters_b.values()) > 1
    ]
    merges = [
        {
            "model_b_cluster_id": cluster_id,
            "overlapping_model_a_clusters": [left_id for left_id, values in clusters_a.items() if values & members],
        }
        for cluster_id, members in clusters_b.items()
        if sum(bool(values & members) for values in clusters_a.values()) > 1
    ]
    depot = db.get(MasterDepot, model_a.depot_id)
    return {
        "depot_id": model_a.depot_id,
        "depot_name": depot.depot_name if depot else model_a.depot_id,
        "model_a": {
            **_model_summary(model_a, depot.depot_name if depot else None),
            "feature_weights": model_a.feature_weights,
            "node2vec_parameters": model_a.node2vec_parameters,
            "umap_parameters": model_a.umap_parameters,
            "hdbscan_parameters": model_a.hdbscan_parameters,
        },
        "model_b": {
            **_model_summary(model_b, depot.depot_name if depot else None),
            "feature_weights": model_b.feature_weights,
            "node2vec_parameters": model_b.node2vec_parameters,
            "umap_parameters": model_b.umap_parameters,
            "hdbscan_parameters": model_b.hdbscan_parameters,
        },
        "cluster_matches": matches,
        "stable_cluster_neighborhood_spbu_ids": stable,
        "matched_cluster_changed_spbu_ids": changed,
        "new_noise_spbu_ids": sorted(noise_b - noise_a),
        "noise_returning_to_cluster_spbu_ids": sorted(noise_a - noise_b),
        "new_spbu_ids": sorted(set(lookup_b) - set(lookup_a)),
        "removed_spbu_ids": sorted(set(lookup_a) - set(lookup_b)),
        "cluster_splits": splits,
        "cluster_merges": merges,
        "methodology": "Only sufficient-history SPBUs are compared; cold-start coverage is excluded. Cluster IDs are ignored. Hungarian optimal matching maximizes Jaccard similarity between historical SPBU membership sets; stable neighborhood requires a matched-cluster Jaccard score >= 0.5.",
    }
