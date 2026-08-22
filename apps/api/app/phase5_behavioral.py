from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import math
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .departure_intelligence import (
    ALGORITHM_VERSION as DEPARTURE_ALGORITHM_VERSION,
    SHIFT_ASSIGNMENT_ALGORITHM_VERSION,
    build_observations,
    load_departure_rows,
    load_gps_departure_lookup,
    load_quantity_lookup,
    shift_for_minute,
    validate_shift_config,
)
from .models import (
    BridgeSPBUTag,
    MLTrainingRun,
    MasterDepot,
    MasterSPBU,
    MasterTag,
    MasterTagType,
)
from .pairing_intelligence import (
    ALGORITHM_VERSION as PAIRING_ALGORITHM_VERSION,
    build_pair_metrics,
    load_membership_rows,
    load_source_shipments,
    prepare_memberships,
)
from .phase5_constants import (
    BEHAVIORAL_ALGORITHM_VERSION,
    DEFAULT_FEATURE_WEIGHTS,
    DEFAULT_HDBSCAN_PARAMETERS,
    DEFAULT_NODE2VEC_PARAMETERS,
    DEFAULT_SHIFT_DEFINITIONS,
    DEFAULT_UMAP_PARAMETERS,
)
from .phase5_readiness import require_phase5_readiness


logger = logging.getLogger(__name__)

NODE2VEC_IMPLEMENTATION_VERSION = "portable_walk_ppmi_svd.v1"
INTERRUPTED_TRAINING_STATUSES = ("PREPARING_DATA", "TRAINING", "CALCULATING_PROFILES")


def library_versions() -> dict[str, str]:
    packages = ["numpy", "scikit-learn", "umap-learn", "networkx", "joblib"]
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT_INSTALLED"
    versions["hdbscan_implementation"] = "sklearn.cluster.HDBSCAN"
    versions["node2vec_implementation"] = NODE2VEC_IMPLEMENTATION_VERSION
    return versions


def recover_interrupted_behavioral_training_runs(db: Session) -> int:
    """Close transient runs left behind when the synchronous API process stopped."""
    runs = db.scalars(select(MLTrainingRun).where(MLTrainingRun.status.in_(INTERRUPTED_TRAINING_STATUSES))).all()
    if not runs:
        return 0
    completed_at = datetime.now(timezone.utc)
    for run in runs:
        interrupted_status = run.status
        run.status = "FAILED"
        run.error_message = (
            f"Training was interrupted while status was {interrupted_status} because the API process stopped. "
            "The retained dataset can be retried when it is complete."
        )
        run.completed_at = completed_at
    db.commit()
    return len(runs)


def validate_feature_weights(weights: dict[str, Any] | None) -> dict[str, float]:
    merged = {**DEFAULT_FEATURE_WEIGHTS, **(weights or {})}
    try:
        result = {key: float(merged[key]) for key in ("tag", "shift", "pairing")}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Feature weights must include numeric tag, shift, and pairing values.") from exc
    if any(value < 0 or value > 1 for value in result.values()) or not math.isclose(sum(result.values()), 1.0, abs_tol=1e-6):
        raise HTTPException(status_code=400, detail="Feature weights must be within 0-1 and sum to exactly 1.00.")
    return result


def _clean_shift_snapshot(shifts: list[dict]) -> list[dict]:
    return [
        {
            "shift_id": shift["shift_id"],
            "name": shift["name"],
            "order": shift["order"],
            "start_time": shift["start_time"],
            "end_time": shift["end_time"],
            "start_minute": shift["start_minute"],
            "end_minute": shift["end_minute"],
            "segments": shift["segments"],
        }
        for shift in shifts
    ]


def _load_tag_features(db: Session, spbus: list[MasterSPBU]) -> tuple[list[str], dict[str, list[float]], dict[str, list[str]], dict]:
    spbu_ids = [spbu.spbu_id for spbu in spbus]
    rows = db.execute(
        select(BridgeSPBUTag.spbu_id, MasterTag, MasterTagType)
        .join(MasterTag, MasterTag.tag_id == BridgeSPBUTag.tag_id)
        .outerjoin(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
        .where(BridgeSPBUTag.spbu_id.in_(spbu_ids))
    ).all() if spbu_ids else []
    tags_by_spbu: dict[str, set[str]] = defaultdict(set)
    labels_by_spbu: dict[str, list[str]] = defaultdict(list)
    tag_metadata: dict[str, dict] = {}
    for spbu_id, tag, tag_type in rows:
        type_code = tag_type.code if tag_type else "UNTYPED"
        if type_code == "VEHICLE_CLASS":
            continue
        feature_name = f"tag:{type_code}:{tag.tag_id}"
        tags_by_spbu[spbu_id].add(feature_name)
        labels_by_spbu[spbu_id].append(f"{type_code}: {tag.tag_value}")
        tag_metadata[feature_name] = {
            "tag_id": tag.tag_id,
            "tag_type": type_code,
            "tag_value": tag.tag_value,
            "encoding": "binary_multi_hot",
        }
    binary_names = sorted(tag_metadata, key=lambda name: (tag_metadata[name]["tag_type"], tag_metadata[name]["tag_value"], name))
    feature_names = ["vehicle_class_ordinal", *binary_names]
    vehicle_values = [float(spbu.vehicle_type_tag or 0) for spbu in spbus]
    maximum_vehicle_class = max(vehicle_values, default=0.0)
    vectors = {}
    for spbu in spbus:
        ordinal = float(spbu.vehicle_type_tag or 0) / maximum_vehicle_class if maximum_vehicle_class > 0 else 0.0
        vectors[spbu.spbu_id] = [ordinal, *[1.0 if name in tags_by_spbu[spbu.spbu_id] else 0.0 for name in binary_names]]
    configuration = {
        "feature_names": feature_names,
        "feature_group_boundaries": {"vehicle_class_ordinal": [0, 1], "categorical_tags": [1, len(feature_names)]},
        "vehicle_class_encoding": "business-ordinal value scaled by maximum training value",
        "vehicle_class_maximum": maximum_vehicle_class,
        "tag_metadata": tag_metadata,
    }
    return feature_names, vectors, {spbu_id: sorted(set(values)) for spbu_id, values in labels_by_spbu.items()}, configuration


def create_pairing_graph(spbu_ids: list[str], pair_rows: list[dict]) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(spbu_ids)
    allowed = set(spbu_ids)
    for row in pair_rows:
        left, right = row["spbu_a_id"], row["spbu_b_id"]
        if left not in allowed or right not in allowed:
            continue
        # Phase 3 stores directional conditional probabilities for an undirected
        # co-shipment pair. Their mean is a deterministic symmetric edge weight.
        weight = max(1e-9, (float(row["probability_b_given_a"]) + float(row["probability_a_given_b"])) / 2.0)
        graph.add_edge(left, right, weight=weight, pair_count=int(row["pair_count"]))
    return graph


def _weighted_choice(rng: np.random.Generator, candidates: list[str], weights: list[float]) -> str:
    probabilities = np.asarray(weights, dtype=float)
    total = float(probabilities.sum())
    if not math.isfinite(total) or total <= 0:
        return candidates[int(rng.integers(0, len(candidates)))]
    probabilities /= total
    return candidates[int(rng.choice(len(candidates), p=probabilities))]


def _node2vec_walk(
    graph: nx.Graph,
    start: str,
    walk_length: int,
    p: float,
    q: float,
    rng: np.random.Generator,
) -> list[str]:
    walk = [start]
    while len(walk) < walk_length:
        current = walk[-1]
        neighbors = sorted(str(node) for node in graph.neighbors(current))
        if not neighbors:
            break
        weights = []
        previous = walk[-2] if len(walk) > 1 else None
        for candidate in neighbors:
            edge_weight = max(1e-12, float(graph[current][candidate].get("weight", 1.0)))
            if previous is None:
                bias = 1.0
            elif candidate == previous:
                bias = 1.0 / p
            elif graph.has_edge(previous, candidate):
                bias = 1.0
            else:
                bias = 1.0 / q
            weights.append(edge_weight * bias)
        walk.append(_weighted_choice(rng, neighbors, weights))
    return walk


def generate_node2vec_embeddings(graph: nx.Graph, parameters: dict[str, Any]) -> tuple[dict[str, list[float]], dict]:
    dimensions = max(2, min(128, int(parameters.get("dimensions", 16))))
    nodes = sorted(str(node) for node in graph.nodes)
    isolated = sorted(str(node) for node, degree in graph.degree if degree == 0)
    metadata = {
        "implementation": NODE2VEC_IMPLEMENTATION_VERSION,
        "dimensions": dimensions,
        "isolated_nodes": isolated,
        "fallback": "isolated nodes receive a zero pairing vector",
    }
    if graph.number_of_edges() == 0:
        metadata["fallback"] = "zero vector because the training pairing graph has no edges"
        metadata["effective_dimensions"] = 0
        return ({node: [0.0] * dimensions for node in nodes}, metadata)

    try:
        from sklearn.decomposition import TruncatedSVD
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail="scikit-learn is required for the portable Node2Vec embedding.") from exc

    seed = int(parameters.get("seed", 42))
    walk_length = max(2, min(200, int(parameters.get("walk_length", 20))))
    num_walks = max(1, min(500, int(parameters.get("num_walks", 40))))
    window = max(2, min(50, int(parameters.get("window", 8))))
    p = max(0.01, float(parameters.get("p", 1.0)))
    q = max(0.01, float(parameters.get("q", 1.0)))
    isolated_set = set(isolated)
    active_nodes = [node for node in nodes if node not in isolated_set]
    node_index = {node: index for index, node in enumerate(active_nodes)}
    rng = np.random.default_rng(seed)
    cooccurrence = np.zeros((len(active_nodes), len(active_nodes)), dtype=float)
    walk_count = 0

    # Keep Node2Vec's second-order transition rule, but replace the native
    # Gensim Word2Vec extension with deterministic PPMI matrix factorization.
    # This avoids platform-specific SIGILL crashes observed on ARM64 containers.
    for _ in range(num_walks):
        for start_index in rng.permutation(len(active_nodes)):
            walk = _node2vec_walk(graph, active_nodes[int(start_index)], walk_length, p, q, rng)
            walk_count += 1
            for center_position, center in enumerate(walk):
                left = max(0, center_position - window)
                right = min(len(walk), center_position + window + 1)
                for context_position in range(left, right):
                    if context_position == center_position:
                        continue
                    context = walk[context_position]
                    distance = abs(context_position - center_position)
                    cooccurrence[node_index[center], node_index[context]] += 1.0 / distance

    total = float(cooccurrence.sum())
    row_totals = cooccurrence.sum(axis=1)
    column_totals = cooccurrence.sum(axis=0)
    expected = np.outer(row_totals, column_totals)
    ppmi = np.zeros_like(cooccurrence)
    valid = (cooccurrence > 0) & (expected > 0)
    ppmi[valid] = np.maximum(np.log((cooccurrence[valid] * total) / expected[valid]), 0.0)

    matrix = ppmi
    matrix_kind = "positive_pointwise_mutual_information"
    if not np.any(matrix):
        # Tiny/highly regular graphs can have no positive PMI; weighted
        # adjacency remains a deterministic structural signal in that case.
        matrix = nx.to_numpy_array(graph, nodelist=active_nodes, weight="weight", dtype=float)
        matrix_kind = "weighted_adjacency_fallback"

    effective_dimensions = min(dimensions, len(active_nodes), matrix.shape[1])
    reduced = TruncatedSVD(
        n_components=effective_dimensions,
        algorithm="randomized",
        n_iter=7,
        random_state=seed,
    ).fit_transform(matrix)
    # SVD component signs are mathematically arbitrary. Canonicalizing them
    # keeps persisted vectors stable across repeat runs on the same platform.
    for column in range(reduced.shape[1]):
        pivot = int(np.argmax(np.abs(reduced[:, column])))
        if reduced[pivot, column] < 0:
            reduced[:, column] *= -1
    norms = np.linalg.norm(reduced, axis=1, keepdims=True)
    reduced = np.divide(reduced, norms, out=np.zeros_like(reduced), where=norms > 0)

    embeddings = {node: [0.0] * dimensions for node in nodes}
    for node, vector in zip(active_nodes, reduced, strict=True):
        padded = np.zeros(dimensions, dtype=float)
        padded[:effective_dimensions] = vector
        embeddings[node] = padded.tolist()

    metadata.update(
        {
            "walk_length": walk_length,
            "num_walks": num_walks,
            "walk_count": walk_count,
            "p": p,
            "q": q,
            "window": window,
            "seed": seed,
            "context_weighting": "inverse_distance",
            "factorization_matrix": matrix_kind,
            "reducer": "sklearn.decomposition.TruncatedSVD",
            "effective_dimensions": effective_dimensions,
        }
    )
    return embeddings, metadata


def _feature_fusion(
    records: list[dict],
    embeddings: dict[str, list[float]],
    weights: dict[str, float],
) -> tuple[np.ndarray, dict]:
    try:
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail="scikit-learn is required for Phase 5 training.") from exc
    tag_matrix = np.asarray([record["tag_vector"] for record in records], dtype=float)
    shift_matrix = np.asarray([record["shift_vector"] for record in records], dtype=float)
    pairing_matrix = np.asarray([embeddings[record["spbu_id"]] for record in records], dtype=float)
    scalers = {}
    weighted_groups = []
    for name, matrix in (("tag", tag_matrix), ("shift", shift_matrix), ("pairing", pairing_matrix)):
        scaler = StandardScaler()
        transformed = scaler.fit_transform(matrix)
        # Division by sqrt(dimension) prevents a wide tag group from dominating a
        # narrower shift/pairing group solely because it owns more columns. After
        # independent standardization, each group's expected squared-distance
        # contribution is therefore proportional to its configured weight.
        weighted_groups.append(transformed * math.sqrt(weights[name] / max(1, matrix.shape[1])))
        scalers[name] = {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()}
    fused = np.concatenate(weighted_groups, axis=1)
    return fused, {
        "weight_application": "Each independently standardized group is multiplied by sqrt(group_weight / group_dimension) before concatenation.",
        "scalers": scalers,
        "group_dimensions": {"tag": tag_matrix.shape[1], "shift": shift_matrix.shape[1], "pairing": pairing_matrix.shape[1]},
    }


def prepare_training_dataset(
    db: Session,
    *,
    depot_id: str,
    training_start_date: date,
    training_end_date: date,
    minimum_shipment_observation: int,
    shift_definitions: list[dict] | None,
    created_by: str,
) -> dict:
    if training_end_date < training_start_date:
        raise HTTPException(status_code=400, detail="training_end_date must be greater than or equal to training_start_date.")
    if minimum_shipment_observation < 1:
        raise HTTPException(status_code=400, detail="minimum_shipment_observation must be at least 1.")
    readiness = require_phase5_readiness(db, depot_id)
    shift_snapshot = _clean_shift_snapshot(validate_shift_config(shift_definitions or DEFAULT_SHIFT_DEFINITIONS))
    training_run_id = uuid.uuid4().hex
    run = MLTrainingRun(
        training_run_id=training_run_id,
        depot_id=depot_id,
        training_start_date=training_start_date,
        training_end_date=training_end_date,
        minimum_shipment_observation=minimum_shipment_observation,
        status="PREPARING_DATA",
        training_configuration={},
        dataset_summary={},
        dataset_payload={},
        result_payload={},
        shift_definition_snapshot=shift_snapshot,
        master_compatibility_snapshot=readiness,
        algorithm_version=BEHAVIORAL_ALGORITHM_VERSION,
        library_versions=library_versions(),
        created_by=created_by,
    )
    db.add(run)
    db.commit()
    try:
        source_shipments = load_source_shipments(db, depot_id, training_start_date, training_end_date)
        raw_memberships, duplicate_count = load_membership_rows(db, depot_id, training_start_date, training_end_date, None)
        memberships, spbu_lookup, data_quality = prepare_memberships(source_shipments, raw_memberships, duplicate_count)
        if not memberships:
            raise HTTPException(status_code=422, detail="No shipment data exists in the selected training period.")
        observation_counts: Counter[str] = Counter(spbu_id for values in memberships.values() for spbu_id in values)
        eligible_ids = sorted(spbu_id for spbu_id, count in observation_counts.items() if count >= minimum_shipment_observation)
        excluded_ids = sorted(spbu_id for spbu_id, count in observation_counts.items() if count < minimum_shipment_observation)
        spbus = db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(eligible_ids)).order_by(MasterSPBU.spbu_id)).all() if eligible_ids else []

        tag_feature_names, tag_vectors, key_tags, tag_configuration = _load_tag_features(db, spbus)
        departure_rows = load_departure_rows(db, depot_id, training_start_date, training_end_date, None)
        departure_observations = build_observations(
            departure_rows,
            load_gps_departure_lookup(db, depot_id, departure_rows),
            load_quantity_lookup(db, departure_rows),
        )
        shift_counts: dict[str, Counter[str]] = defaultdict(Counter)
        shift_valid_counts: Counter[str] = Counter()
        for observation in departure_observations:
            if observation["spbu_id"] not in set(eligible_ids) or observation["departure_minute"] is None:
                continue
            shift = shift_for_minute(int(observation["departure_minute"]), shift_snapshot)
            shift_counts[observation["spbu_id"]][shift["shift_id"]] += 1
            shift_valid_counts[observation["spbu_id"]] += 1

        pair_rows = build_pair_metrics(memberships, spbu_lookup, depot_id, training_start_date, training_end_date)
        pair_rows = [row for row in pair_rows if row["spbu_a_id"] in set(eligible_ids) and row["spbu_b_id"] in set(eligible_ids)]
        records = []
        for spbu in spbus:
            total_shift = shift_valid_counts[spbu.spbu_id]
            shift_vector = [shift_counts[spbu.spbu_id][shift["shift_id"]] / total_shift if total_shift else 0.0 for shift in shift_snapshot]
            dominant_index = max(range(len(shift_vector)), key=lambda index: shift_vector[index]) if total_shift else None
            records.append(
                {
                    "spbu_id": spbu.spbu_id,
                    "spbu_code": spbu.spbu_code,
                    "spbu_name": spbu.spbu_name,
                    "latitude": float(spbu.latitude) if spbu.latitude is not None else None,
                    "longitude": float(spbu.longitude) if spbu.longitude is not None else None,
                    "shipment_observation_count": observation_counts[spbu.spbu_id],
                    "vehicle_class": spbu.vehicle_type_tag,
                    "tag_vector": tag_vectors[spbu.spbu_id],
                    "key_tags": key_tags.get(spbu.spbu_id, []),
                    "shift_vector": [round(value, 8) for value in shift_vector],
                    "shift_distribution": [
                        {"shift_id": shift["shift_id"], "shift_name": shift["name"], "share": round(shift_vector[index], 8)}
                        for index, shift in enumerate(shift_snapshot)
                    ],
                    "dominant_shift": shift_snapshot[dominant_index]["name"] if dominant_index is not None else "Insufficient timestamp data",
                    "valid_shift_observation_count": total_shift,
                }
            )
        depot = db.get(MasterDepot, depot_id)
        summary = {
            "depot_id": depot_id,
            "depot_name": depot.depot_name if depot else depot_id,
            "training_start_date": training_start_date.isoformat(),
            "training_end_date": training_end_date.isoformat(),
            "shipment_count": len(memberships),
            "source_shipment_count": len(source_shipments),
            "spbu_count": len(observation_counts),
            "mt_count": len({shipment.mt_id for shipment in source_shipments if shipment.mt_id}),
            "master_compatibility_pass_percentage": readiness["master_compatibility_pass_percentage"],
            "sufficient_history_spbu_count": len(records),
            "excluded_insufficient_data_spbu_count": len(excluded_ids),
            "geocoded_training_spbu_count": sum(record["latitude"] is not None and record["longitude"] is not None for record in records),
            "missing_coordinate_training_spbu_count": sum(record["latitude"] is None or record["longitude"] is None for record in records),
            "pairing_edge_count": len(pair_rows),
            "isolated_spbu_count": len(set(eligible_ids) - {row[side] for row in pair_rows for side in ("spbu_a_id", "spbu_b_id")}),
            "data_quality": data_quality,
        }
        run.dataset_summary = summary
        run.dataset_payload = {
            "records": records,
            "tag_feature_names": tag_feature_names,
            "shift_feature_names": [f"shift:{shift['shift_id']}" for shift in shift_snapshot],
            "tag_feature_configuration": tag_configuration,
            "pair_rows": pair_rows,
            "excluded_spbu_ids": excluded_ids,
            "dependency_metadata": {
                "master_compatibility_rule_source": readiness["rule_source"],
                "master_tag_configuration": "canonical master_tag + bridge_spbu_tag snapshot at preparation time",
                "departure_algorithm_version": DEPARTURE_ALGORITHM_VERSION,
                "shift_assignment_algorithm_version": SHIFT_ASSIGNMENT_ALGORITHM_VERSION,
                "pairing_algorithm_version": PAIRING_ALGORITHM_VERSION,
                "geographic_coordinate_source": "MasterSPBU latitude/longitude snapshot; visualization only and excluded from model features",
                "source_observation_key": ["shipment_id", "spbu_id"],
            },
        }
        run.status = "DATASET_READY"
        db.commit()
        return get_training_run(db, training_run_id)
    except HTTPException as exc:
        db.rollback()
        persisted = db.get(MLTrainingRun, training_run_id)
        if persisted:
            persisted.status = "FAILED"
            persisted.error_message = str(exc.detail)
            persisted.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
    except Exception as exc:
        logger.exception("Phase 5 dataset preparation failed")
        db.rollback()
        persisted = db.get(MLTrainingRun, training_run_id)
        if persisted:
            persisted.status = "FAILED"
            persisted.error_message = f"{type(exc).__name__}: {exc}"
            persisted.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(status_code=500, detail="Training dataset preparation failed. Review the retained training run.") from exc


def _validated_training_configuration(configuration: dict[str, Any] | None) -> dict:
    source = configuration or {}
    weights = validate_feature_weights(source.get("feature_weights"))
    node2vec = {**DEFAULT_NODE2VEC_PARAMETERS, **(source.get("node2vec_parameters") or {})}
    umap_parameters = {**DEFAULT_UMAP_PARAMETERS, **(source.get("umap_parameters") or {})}
    hdbscan_parameters = {**DEFAULT_HDBSCAN_PARAMETERS, **(source.get("hdbscan_parameters") or {})}
    if int(hdbscan_parameters["min_cluster_size"]) < 2:
        raise HTTPException(status_code=400, detail="HDBSCAN min_cluster_size must be at least 2.")
    if int(hdbscan_parameters["min_samples"]) < 1:
        raise HTTPException(status_code=400, detail="HDBSCAN min_samples must be at least 1.")
    if str(hdbscan_parameters["cluster_selection_method"]) not in {"eom", "leaf"}:
        raise HTTPException(status_code=400, detail="HDBSCAN cluster_selection_method must be eom or leaf.")
    if int(umap_parameters["n_neighbors"]) < 2 or int(umap_parameters["n_components"]) < 2:
        raise HTTPException(status_code=400, detail="UMAP n_neighbors and n_components must be at least 2.")
    return {
        "feature_weights": weights,
        "node2vec_parameters": node2vec,
        "umap_parameters": umap_parameters,
        "hdbscan_parameters": hdbscan_parameters,
        "random_seed": int(source.get("random_seed", node2vec.get("seed", 42))),
    }


def _cluster_profiles(assignments: list[dict], records: list[dict], pair_rows: list[dict], shift_snapshot: list[dict]) -> list[dict]:
    record_lookup = {record["spbu_id"]: record for record in records}
    by_cluster: dict[int, list[dict]] = defaultdict(list)
    for assignment in assignments:
        if not assignment["is_noise"]:
            by_cluster[int(assignment["cluster_id"])].append(assignment)
    profiles = []
    for cluster_id, members in sorted(by_cluster.items()):
        member_ids = {member["spbu_id"] for member in members}
        tag_counts = Counter(tag for member in members for tag in record_lookup[member["spbu_id"]]["key_tags"])
        common_tags = [
            {"tag": tag, "member_count": count, "member_share": round(count / len(members), 4)}
            for tag, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))
            if count >= math.ceil(len(members) * 0.5)
        ][:10]
        shift_means = [
            float(np.mean([record_lookup[member["spbu_id"]]["shift_vector"][index] for member in members]))
            for index in range(len(shift_snapshot))
        ]
        dominant_index = max(range(len(shift_means)), key=lambda index: shift_means[index]) if shift_means else None
        internal_pairs = []
        for row in pair_rows:
            if row["spbu_a_id"] in member_ids and row["spbu_b_id"] in member_ids:
                strength = (row["probability_b_given_a"] + row["probability_a_given_b"]) / 2
                internal_pairs.append(
                    {
                        "spbu_a_id": row["spbu_a_id"],
                        "spbu_a_code": record_lookup[row["spbu_a_id"]]["spbu_code"],
                        "spbu_b_id": row["spbu_b_id"],
                        "spbu_b_code": record_lookup[row["spbu_b_id"]]["spbu_code"],
                        "pair_count": row["pair_count"],
                        "pairing_strength": round(strength, 4),
                    }
                )
        internal_pairs.sort(key=lambda row: (-row["pairing_strength"], -row["pair_count"], row["spbu_a_code"], row["spbu_b_code"]))
        average_probability = float(np.mean([member["membership_probability"] for member in members])) if members else 0.0
        profiles.append(
            {
                "cluster_id": cluster_id,
                "cluster_label": f"Cluster {cluster_id + 1}",
                "cluster_size": len(members),
                "training_spbu_percentage": round(100 * len(members) / len(assignments), 2) if assignments else 0.0,
                "common_tags": common_tags,
                "shift_distribution": [
                    {"shift_id": shift["shift_id"], "shift_name": shift["name"], "share": round(shift_means[index], 4)}
                    for index, shift in enumerate(shift_snapshot)
                ],
                "dominant_shift": shift_snapshot[dominant_index]["name"] if dominant_index is not None else "Insufficient timestamp data",
                "top_internal_pairings": internal_pairs[:10],
                "average_membership_probability": round(average_probability, 4),
                "low_confidence_member_count": sum(member["membership_probability"] < 0.5 for member in members),
                "member_spbu_ids": sorted(member_ids),
            }
        )
    return profiles


def train_behavioral_model(db: Session, training_run_id: str, configuration: dict[str, Any] | None) -> dict:
    run = db.get(MLTrainingRun, training_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found.")
    retrying_failed_dataset = run.status == "FAILED" and bool((run.dataset_payload or {}).get("records"))
    if run.status not in {"DATASET_READY", "COMPLETED"} and not retrying_failed_dataset:
        raise HTTPException(status_code=409, detail="Prepare and validate the dataset before training.")
    require_phase5_readiness(db, run.depot_id)
    config = _validated_training_configuration(configuration)
    records = run.dataset_payload.get("records", [])
    minimum_cluster_size = int(config["hdbscan_parameters"]["min_cluster_size"])
    if len(records) < max(3, minimum_cluster_size):
        raise HTTPException(status_code=422, detail=f"Too few SPBUs for clustering. Need at least {max(3, minimum_cluster_size)} sufficient-history SPBUs.")
    run.status = "TRAINING"
    run.training_configuration = config
    run.algorithm_version = BEHAVIORAL_ALGORITHM_VERSION
    run.library_versions = library_versions()
    run.error_message = None
    run.completed_at = None
    db.commit()
    try:
        pair_rows = run.dataset_payload.get("pair_rows", [])
        graph = create_pairing_graph([record["spbu_id"] for record in records], pair_rows)
        embeddings, node2vec_metadata = generate_node2vec_embeddings(graph, config["node2vec_parameters"])
        fused, fusion_metadata = _feature_fusion(records, embeddings, config["feature_weights"])
        try:
            import joblib
            import umap
            from sklearn.cluster import HDBSCAN
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail="UMAP/HDBSCAN ML dependencies are not installed.") from exc

        umap_config = config["umap_parameters"]
        neighbors = min(max(2, int(umap_config["n_neighbors"])), len(records) - 1)
        components = min(max(2, int(umap_config["n_components"])), max(2, len(records) - 2), fused.shape[1])
        internal_umap = umap.UMAP(
            n_neighbors=neighbors,
            n_components=components,
            min_dist=max(0.0, min(0.99, float(umap_config["min_dist"]))),
            metric=str(umap_config["metric"]),
            random_state=int(umap_config["random_state"]),
            transform_seed=int(config["random_seed"]),
        )
        reduced = internal_umap.fit_transform(fused)
        cluster_config = config["hdbscan_parameters"]
        clusterer = HDBSCAN(
            min_cluster_size=minimum_cluster_size,
            min_samples=int(cluster_config["min_samples"]),
            metric=str(cluster_config["metric"]),
            cluster_selection_method=str(cluster_config["cluster_selection_method"]),
            n_jobs=1,
            copy=True,
        )
        labels = clusterer.fit_predict(reduced)
        probabilities = clusterer.probabilities_
        visualization_umap = umap.UMAP(
            n_neighbors=neighbors,
            n_components=2,
            min_dist=max(0.05, min(0.99, float(umap_config["min_dist"]))),
            metric=str(umap_config["metric"]),
            random_state=int(umap_config["random_state"]),
            transform_seed=int(config["random_seed"]),
        )
        visualization = visualization_umap.fit_transform(fused)
        assignments = []
        for index, record in enumerate(records):
            label = int(labels[index])
            is_noise = label == -1
            assignments.append(
                {
                    "spbu_id": record["spbu_id"],
                    "spbu_code": record["spbu_code"],
                    "spbu_name": record["spbu_name"],
                    "latitude": record.get("latitude"),
                    "longitude": record.get("longitude"),
                    "shipment_observation_count": record["shipment_observation_count"],
                    "cluster_id": None if is_noise else label,
                    "cluster_label": "Noise / Unique Behavioral Pattern" if is_noise else f"Cluster {label + 1}",
                    "membership_probability": round(float(probabilities[index]), 6),
                    "is_noise": is_noise,
                    "dominant_shift": record["dominant_shift"],
                    "vehicle_class": record.get("vehicle_class"),
                    "key_tags": record["key_tags"],
                    "visualization_x": round(float(visualization[index, 0]), 6),
                    "visualization_y": round(float(visualization[index, 1]), 6),
                }
            )
        run.status = "CALCULATING_PROFILES"
        profiles = _cluster_profiles(assignments, records, pair_rows, run.shift_definition_snapshot)
        cluster_count = len({assignment["cluster_id"] for assignment in assignments if not assignment["is_noise"]})
        noise_count = sum(assignment["is_noise"] for assignment in assignments)
        average_membership = float(np.mean([assignment["membership_probability"] for assignment in assignments]))
        warnings = []
        if graph.number_of_edges() == 0:
            warnings.append("No pairing edges were available; every SPBU received the documented zero pairing embedding.")
        if noise_count == len(assignments):
            warnings.append("HDBSCAN marked every SPBU as noise. Review feature weights or density parameters before saving.")
        result = {
            "summary": {
                "training_spbu_count": len(assignments),
                "cluster_count": cluster_count,
                "clustered_spbu_count": len(assignments) - noise_count,
                "noise_spbu_count": noise_count,
                "average_membership_probability": round(average_membership, 6),
            },
            "assignments": assignments,
            "cluster_profiles": profiles,
            "configuration": config,
            "node2vec_metadata": node2vec_metadata,
            "fusion_metadata": fusion_metadata,
            "warnings": warnings,
            "review_required": True,
            "saved": False,
        }

        artifact_root = get_settings().ml_artifact_dir.resolve()
        staging_dir = artifact_root / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = (staging_dir / f"{training_run_id}.joblib").resolve()
        if artifact_root not in artifact_path.parents:
            raise RuntimeError("Resolved training artifact path escaped ML_ARTIFACT_DIR.")
        bundle = {
            "algorithm_version": BEHAVIORAL_ALGORITHM_VERSION,
            "library_versions": library_versions(),
            "configuration": config,
            "tag_feature_configuration": run.dataset_payload.get("tag_feature_configuration", {}),
            "shift_definition_snapshot": run.shift_definition_snapshot,
            "dependency_metadata": run.dataset_payload.get("dependency_metadata", {}),
            "node2vec_embeddings": embeddings,
            "feature_fusion_metadata": fusion_metadata,
            "feature_vectors": fused,
            "internal_umap_model": internal_umap,
            "visualization_umap_model": visualization_umap,
            "hdbscan_model": clusterer,
            "assignments": assignments,
            "cluster_profiles": profiles,
        }
        joblib.dump(bundle, artifact_path, compress=3)
        run.artifact_temp_path = str(artifact_path)
        run.result_payload = result
        run.library_versions = library_versions()
        run.status = "COMPLETED"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        return get_training_run(db, training_run_id)
    except HTTPException as exc:
        db.rollback()
        persisted = db.get(MLTrainingRun, training_run_id)
        if persisted:
            persisted.status = "FAILED"
            persisted.error_message = str(exc.detail)
            persisted.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise
    except Exception as exc:
        logger.exception("Phase 5 behavioral training failed")
        db.rollback()
        persisted = db.get(MLTrainingRun, training_run_id)
        if persisted:
            persisted.status = "FAILED"
            persisted.error_message = f"{type(exc).__name__}: {exc}"
            persisted.completed_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(status_code=500, detail="Behavioral clustering failed. The training run was retained with diagnostic details.") from exc


def get_training_run(db: Session, training_run_id: str) -> dict:
    run = db.get(MLTrainingRun, training_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found.")
    depot = db.get(MasterDepot, run.depot_id)
    return {
        "training_run_id": run.training_run_id,
        "depot_id": run.depot_id,
        "depot_name": depot.depot_name if depot else run.depot_id,
        "training_start_date": run.training_start_date.isoformat(),
        "training_end_date": run.training_end_date.isoformat(),
        "minimum_shipment_observation": run.minimum_shipment_observation,
        "status": run.status,
        "training_configuration": run.training_configuration,
        "dataset_summary": run.dataset_summary,
        "dataset_preview": [
            {
                key: record.get(key)
                for key in (
                    "spbu_id",
                    "spbu_code",
                    "spbu_name",
                    "latitude",
                    "longitude",
                    "shipment_observation_count",
                    "dominant_shift",
                    "key_tags",
                    "shift_distribution",
                )
            }
            for record in run.dataset_payload.get("records", [])[:50]
        ],
        "shift_definition_snapshot": run.shift_definition_snapshot,
        "master_compatibility_snapshot": run.master_compatibility_snapshot,
        "algorithm_version": run.algorithm_version,
        "library_versions": run.library_versions,
        "result": run.result_payload,
        "error_message": run.error_message,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def artifact_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
