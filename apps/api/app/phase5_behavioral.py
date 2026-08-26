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
    DEFAULT_GEOGRAPHIC_CONFIGURATION,
    DEFAULT_HDBSCAN_PARAMETERS,
    DEFAULT_NODE2VEC_PARAMETERS,
    DEFAULT_PROJECTION_CONFIGURATION,
    DEFAULT_SHIFT_DEFINITIONS,
    DEFAULT_UMAP_PARAMETERS,
)
from .phase5_readiness import require_phase5_readiness
from .phase5_sufficiency import (
    build_geographic_features,
    calculate_data_sufficiency,
    validate_data_sufficiency_configuration,
    validate_geographic_configuration,
)


logger = logging.getLogger(__name__)

NODE2VEC_IMPLEMENTATION_VERSION = "portable_walk_ppmi_svd.v1"
INTERRUPTED_TRAINING_STATUSES = ("PREPARING_DATA", "TRAINING", "CALCULATING_PROFILES")


def projection_confidence_from_distance(distance: float, cluster_scale: float, multiplier: float = 2.0) -> float:
    if distance < 0 or cluster_scale <= 0 or multiplier <= 0:
        raise ValueError("Projection distance must be non-negative and scale values must be positive.")
    return max(0.0, min(1.0, math.exp(-distance / (cluster_scale * multiplier))))


def project_to_core_clusters(
    vector: np.ndarray,
    cluster_centroids: dict[int, np.ndarray],
    cluster_scales: dict[int, float],
    *,
    minimum_confidence: float,
    distance_scale_multiplier: float,
) -> dict[str, Any]:
    if not cluster_centroids:
        return {"projected_cluster_id": None, "projection_confidence": None, "projection_status": "UNASSIGNED"}
    label, distance = min(
        (
            (label, float(np.linalg.norm(vector - centroid)))
            for label, centroid in cluster_centroids.items()
        ),
        key=lambda item: (item[1], item[0]),
    )
    confidence = projection_confidence_from_distance(
        distance,
        cluster_scales[label],
        distance_scale_multiplier,
    )
    return {
        "projected_cluster_id": label if confidence >= minimum_confidence else None,
        "projection_confidence": confidence,
        "projection_status": "PROJECTED" if confidence >= minimum_confidence else "LOW_CONFIDENCE",
    }


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


def validate_feature_weights(
    weights: dict[str, Any] | None,
    *,
    geography_enabled: bool = True,
) -> dict[str, float]:
    if not geography_enabled and weights is None:
        non_geographic_total = sum(DEFAULT_FEATURE_WEIGHTS[key] for key in ("tag", "shift", "pairing"))
        merged = {
            key: DEFAULT_FEATURE_WEIGHTS[key] / non_geographic_total
            for key in ("tag", "shift", "pairing")
        } | {"geographic": 0.0}
    else:
        merged = {**DEFAULT_FEATURE_WEIGHTS, **(weights or {})}
    try:
        result = {key: float(merged[key]) for key in ("tag", "shift", "pairing", "geographic")}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Feature weights must include numeric tag, shift, pairing, and geographic values.",
        ) from exc
    if any(value < 0 or value > 1 for value in result.values()) or not math.isclose(sum(result.values()), 1.0, abs_tol=1e-6):
        raise HTTPException(status_code=400, detail="Feature weights must be within 0-1 and sum to exactly 1.00.")
    if not geography_enabled and not math.isclose(result["geographic"], 0.0, abs_tol=1e-9):
        raise HTTPException(status_code=400, detail="Geographic weight must be 0 when Geographic Proximity is disabled.")
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
    *,
    fit_indices: list[int] | None = None,
    geography_enabled: bool = True,
) -> tuple[np.ndarray, dict]:
    try:
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail="scikit-learn is required for Phase 5 training.") from exc
    tag_matrix = np.asarray([record["tag_vector"] for record in records], dtype=float)
    shift_matrix = np.asarray([record["shift_vector"] for record in records], dtype=float)
    pairing_matrix = np.asarray([embeddings[record["spbu_id"]] for record in records], dtype=float)
    geographic_raw = [record.get("geographic_vector") or [None, None, None, None] for record in records]
    scalers = {}
    weighted_groups = []
    scaler_indices = fit_indices or list(range(len(records)))
    matrices: list[tuple[str, np.ndarray]] = [("tag", tag_matrix), ("shift", shift_matrix), ("pairing", pairing_matrix)]
    geographic_imputation = None
    if geography_enabled:
        valid_geo_rows = [
            index
            for index in scaler_indices
            if records[index].get("geographic_data_status") == "VALID"
            and all(value is not None for value in geographic_raw[index])
        ]
        if valid_geo_rows:
            medians = np.median(np.asarray([geographic_raw[index] for index in valid_geo_rows], dtype=float), axis=0)
        else:
            medians = np.zeros(4, dtype=float)
        geographic_matrix = np.asarray(
            [
                [
                    float(value) if value is not None else float(medians[column])
                    for column, value in enumerate(row)
                ]
                + [0.0 if records[index].get("geographic_data_status") == "VALID" else 1.0]
                for index, row in enumerate(geographic_raw)
            ],
            dtype=float,
        )
        matrices.append(("geographic", geographic_matrix))
        geographic_imputation = {
            "method": "core-training median per geographic feature plus explicit missing indicator",
            "median_values": medians.tolist(),
            "valid_core_coordinate_count": len(valid_geo_rows),
        }
    for name, matrix in matrices:
        scaler = StandardScaler()
        scaler.fit(matrix[scaler_indices])
        transformed = scaler.transform(matrix)
        # Division by sqrt(dimension) prevents a wide tag group from dominating a
        # narrower shift/pairing group solely because it owns more columns. After
        # independent standardization, each group's expected squared-distance
        # contribution is therefore proportional to its configured weight.
        weighted_groups.append(transformed * math.sqrt(weights[name] / max(1, matrix.shape[1])))
        scalers[name] = {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()}
    fused = np.concatenate(weighted_groups, axis=1)
    return fused, {
        "weight_application": "Each independently standardized group is multiplied by sqrt(group_weight / group_dimension) before concatenation.",
        "scaler_fit_spbu_count": len(scaler_indices),
        "scalers": scalers,
        "group_dimensions": {name: matrix.shape[1] for name, matrix in matrices},
        "geographic_imputation": geographic_imputation,
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
    data_sufficiency_configuration: dict[str, Any] | None = None,
    geographic_configuration: dict[str, Any] | None = None,
    feature_weights: dict[str, Any] | None = None,
) -> dict:
    if training_end_date < training_start_date:
        raise HTTPException(status_code=400, detail="training_end_date must be greater than or equal to training_start_date.")
    if minimum_shipment_observation < 1:
        raise HTTPException(status_code=400, detail="minimum_shipment_observation must be at least 1.")
    readiness = require_phase5_readiness(db, depot_id)
    shift_snapshot = _clean_shift_snapshot(validate_shift_config(shift_definitions or DEFAULT_SHIFT_DEFINITIONS))
    sufficiency_config = validate_data_sufficiency_configuration(
        data_sufficiency_configuration,
        minimum_shipment_observations=minimum_shipment_observation,
    )
    geography_config = validate_geographic_configuration(geographic_configuration)
    prepared_feature_weights = validate_feature_weights(
        feature_weights,
        geography_enabled=geography_config["enabled"],
    )
    training_run_id = uuid.uuid4().hex
    run = MLTrainingRun(
        training_run_id=training_run_id,
        depot_id=depot_id,
        training_start_date=training_start_date,
        training_end_date=training_end_date,
        minimum_shipment_observation=minimum_shipment_observation,
        status="PREPARING_DATA",
        training_configuration={
            "data_sufficiency_configuration": sufficiency_config,
            "geographic_configuration": geography_config,
            "feature_weights": prepared_feature_weights,
        },
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
        spbus = db.scalars(
            select(MasterSPBU)
            .where(
                MasterSPBU.primary_depot_id == depot_id,
                MasterSPBU.active_status == "ACTIVE",
            )
            .order_by(MasterSPBU.spbu_id)
        ).all()
        if not spbus:
            raise HTTPException(status_code=422, detail="No active SPBU exists for the selected depot.")
        active_ids = {spbu.spbu_id for spbu in spbus}
        inactive_history_ids = sorted(set(observation_counts) - active_ids)

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
            if observation["spbu_id"] not in active_ids or observation["departure_minute"] is None:
                continue
            shift = shift_for_minute(int(observation["departure_minute"]), shift_snapshot)
            shift_counts[observation["spbu_id"]][shift["shift_id"]] += 1
            shift_valid_counts[observation["spbu_id"]] += 1

        all_pair_rows = build_pair_metrics(memberships, spbu_lookup, depot_id, training_start_date, training_end_date)
        shipment_dates = {
            shipment.shipment_id: shipment.operating_date
            for shipment in source_shipments
            if shipment.shipment_id and shipment.operating_date
        }
        operating_dates_by_spbu: dict[str, set[date]] = defaultdict(set)
        paired_shipment_counts: Counter[str] = Counter()
        for shipment_id, member_ids in memberships.items():
            operating_date = shipment_dates.get(shipment_id)
            for spbu_id in member_ids:
                if operating_date:
                    operating_dates_by_spbu[spbu_id].add(operating_date)
                if len(member_ids) > 1:
                    paired_shipment_counts[spbu_id] += 1
        training_period_days = (training_end_date - training_start_date).days + 1
        records = []
        for spbu in spbus:
            total_shift = shift_valid_counts[spbu.spbu_id]
            shift_vector = [shift_counts[spbu.spbu_id][shift["shift_id"]] / total_shift if total_shift else 0.0 for shift in shift_snapshot]
            dominant_index = max(range(len(shift_vector)), key=lambda index: shift_vector[index]) if total_shift else None
            operating_dates = operating_dates_by_spbu[spbu.spbu_id]
            sufficiency = calculate_data_sufficiency(
                shipment_observation_count=observation_counts[spbu.spbu_id],
                operating_day_count=len(operating_dates),
                training_period_days=training_period_days,
                valid_shift_observation_count=total_shift,
                pairing_observation_count=paired_shipment_counts[spbu.spbu_id],
                last_operating_date=max(operating_dates) if operating_dates else None,
                training_end_date=training_end_date,
                configuration=sufficiency_config,
            )
            records.append(
                {
                    "spbu_id": spbu.spbu_id,
                    "spbu_code": spbu.spbu_code,
                    "spbu_name": spbu.spbu_name,
                    "latitude": float(spbu.latitude) if spbu.latitude is not None else None,
                    "longitude": float(spbu.longitude) if spbu.longitude is not None else None,
                    "shipment_observation_count": observation_counts[spbu.spbu_id],
                    **sufficiency,
                    "history_eligible": sufficiency["data_sufficiency_status"] == "SUFFICIENT",
                    "coverage_source": (
                        "BEHAVIORAL_HISTORY"
                        if sufficiency["data_sufficiency_status"] == "SUFFICIENT"
                        else "MARGINAL_HISTORY"
                        if sufficiency["data_sufficiency_status"] == "MARGINAL"
                        else "INSUFFICIENT_HISTORY"
                    ),
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
        geographic_metadata = build_geographic_features(records, geography_config)
        sufficient_ids = sorted(
            record["spbu_id"] for record in records if record["data_sufficiency_status"] == "SUFFICIENT"
        )
        marginal_ids = sorted(
            record["spbu_id"] for record in records if record["data_sufficiency_status"] == "MARGINAL"
        )
        insufficient_ids = sorted(
            record["spbu_id"] for record in records if record["data_sufficiency_status"] == "INSUFFICIENT"
        )
        graph_population_ids = set(sufficient_ids) | set(marginal_ids)
        pair_rows = [
            row
            for row in all_pair_rows
            if row["spbu_a_id"] in graph_population_ids and row["spbu_b_id"] in graph_population_ids
        ]
        depot = db.get(MasterDepot, depot_id)
        summary = {
            "depot_id": depot_id,
            "depot_name": depot.depot_name if depot else depot_id,
            "training_start_date": training_start_date.isoformat(),
            "training_end_date": training_end_date.isoformat(),
            "shipment_count": len(memberships),
            "source_shipment_count": len(source_shipments),
            "spbu_count": len(observation_counts),
            "active_master_spbu_count": len(records),
            "active_spbu_with_any_history_count": sum(observation_counts[spbu.spbu_id] > 0 for spbu in spbus),
            "mt_count": len({shipment.mt_id for shipment in source_shipments if shipment.mt_id}),
            "master_compatibility_pass_percentage": readiness["master_compatibility_pass_percentage"],
            "total_spbu": len(records),
            "sufficient_count": len(sufficient_ids),
            "marginal_count": len(marginal_ids),
            "insufficient_count": len(insufficient_ids),
            "core_training_count": len(sufficient_ids),
            "sufficient_history_spbu_count": len(sufficient_ids),
            "cold_start_active_spbu_count": len(marginal_ids) + len(insufficient_ids),
            "no_history_active_spbu_count": sum(observation_counts[spbu_id] == 0 for spbu_id in insufficient_ids),
            "insufficient_history_active_spbu_count": sum(observation_counts[spbu_id] > 0 for spbu_id in insufficient_ids),
            "excluded_insufficient_data_spbu_count": len(insufficient_ids),
            "excluded_inactive_history_spbu_count": len(inactive_history_ids),
            "geocoded_training_spbu_count": geographic_metadata["valid_coordinate_count"],
            "missing_coordinate_training_spbu_count": geographic_metadata["invalid_coordinate_count"],
            "valid_coordinate_count": geographic_metadata["valid_coordinate_count"],
            "invalid_coordinate_count": geographic_metadata["invalid_coordinate_count"],
            "geographic_coverage_percentage": geographic_metadata["geographic_coverage_percentage"],
            "geographic_proximity_enabled": geography_config["enabled"],
            "geography_configuration": geography_config,
            "feature_weights": prepared_feature_weights,
            "data_sufficiency_configuration": sufficiency_config,
            "pairing_edge_count": len(pair_rows),
            "isolated_spbu_count": len(set(sufficient_ids) - {row[side] for row in pair_rows for side in ("spbu_a_id", "spbu_b_id")}),
            "geographic_data_quality": geographic_metadata,
            "data_quality": data_quality,
        }
        run.dataset_summary = summary
        run.dataset_payload = {
            "records": records,
            "tag_feature_names": tag_feature_names,
            "shift_feature_names": [f"shift:{shift['shift_id']}" for shift in shift_snapshot],
            "tag_feature_configuration": tag_configuration,
            "pair_rows": pair_rows,
            "sufficient_spbu_ids": sufficient_ids,
            "marginal_spbu_ids": marginal_ids,
            "insufficient_spbu_ids": insufficient_ids,
            "cold_start_spbu_ids": [*marginal_ids, *insufficient_ids],
            "data_sufficiency_configuration": sufficiency_config,
            "geographic_configuration": geography_config,
            "geographic_metadata": geographic_metadata,
            "prepared_feature_weights": prepared_feature_weights,
            "excluded_inactive_history_spbu_ids": inactive_history_ids,
            "dependency_metadata": {
                "master_compatibility_rule_source": readiness["rule_source"],
                "master_tag_configuration": "canonical master_tag + bridge_spbu_tag snapshot at preparation time",
                "departure_algorithm_version": DEPARTURE_ALGORITHM_VERSION,
                "shift_assignment_algorithm_version": SHIFT_ASSIGNMENT_ALGORITHM_VERSION,
                "pairing_algorithm_version": PAIRING_ALGORITHM_VERSION,
                "active_spbu_coverage_policy": "All ACTIVE SPBU are assessed. Only SUFFICIENT SPBU fit UMAP/HDBSCAN; MARGINAL SPBU may be projected after training and INSUFFICIENT SPBU remain unassigned.",
                "geographic_coordinate_source": "Canonical MasterSPBU latitude/longitude snapshot; Haversine KNN proximity features, never routing distance/time.",
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


def _validated_training_configuration(
    configuration: dict[str, Any] | None,
    prepared_configuration: dict[str, Any] | None = None,
) -> dict:
    source = {**(prepared_configuration or {}), **(configuration or {})}
    geography = validate_geographic_configuration(source.get("geographic_configuration"))
    weights = validate_feature_weights(source.get("feature_weights"), geography_enabled=geography["enabled"])
    sufficiency = validate_data_sufficiency_configuration(source.get("data_sufficiency_configuration"))
    projection = {**DEFAULT_PROJECTION_CONFIGURATION, **(source.get("projection_configuration") or {})}
    projection["minimum_confidence"] = float(projection["minimum_confidence"])
    projection["distance_scale_multiplier"] = float(projection["distance_scale_multiplier"])
    projection["method"] = "UMAP_NEAREST_CORE_CENTROID"
    if not 0 <= projection["minimum_confidence"] <= 1:
        raise HTTPException(status_code=400, detail="Minimum projection confidence must be within 0-1.")
    if projection["distance_scale_multiplier"] <= 0:
        raise HTTPException(status_code=400, detail="Projection distance scale multiplier must be greater than 0.")
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
        "data_sufficiency_configuration": sufficiency,
        "geographic_configuration": geography,
        "projection_configuration": projection,
        "node2vec_parameters": node2vec,
        "umap_parameters": umap_parameters,
        "hdbscan_parameters": hdbscan_parameters,
        "random_seed": int(source.get("random_seed", node2vec.get("seed", 42))),
    }


def _cluster_profiles(assignments: list[dict], records: list[dict], pair_rows: list[dict], shift_snapshot: list[dict]) -> list[dict]:
    record_lookup = {record["spbu_id"]: record for record in records}
    historical_assignment_count = sum(
        assignment.get("cluster_assignment_type") == "CORE_MEMBER" for assignment in assignments
    )
    by_cluster: dict[int, list[dict]] = defaultdict(list)
    for assignment in assignments:
        if not assignment["is_noise"] and assignment.get("cluster_id") is not None:
            by_cluster[int(assignment["cluster_id"])].append(assignment)
    profiles = []
    for cluster_id, members in sorted(by_cluster.items()):
        historical_members = [member for member in members if member.get("cluster_assignment_type") == "CORE_MEMBER"]
        cold_start_members = [member for member in members if member.get("cluster_assignment_type") == "MARGINAL_PROJECTED"]
        member_ids = {member["spbu_id"] for member in historical_members}
        all_member_ids = {member["spbu_id"] for member in members}
        tag_counts = Counter(tag for member in historical_members for tag in record_lookup[member["spbu_id"]]["key_tags"])
        common_tags = [
            {"tag": tag, "member_count": count, "member_share": round(count / len(historical_members), 4)}
            for tag, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))
            if historical_members and count >= math.ceil(len(historical_members) * 0.5)
        ][:10]
        shift_means = [
            float(np.mean([record_lookup[member["spbu_id"]]["shift_vector"][index] for member in historical_members]))
            if historical_members else 0.0
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
        average_probability = float(np.mean([member["membership_probability"] for member in historical_members])) if historical_members else 0.0
        profiles.append(
            {
                "cluster_id": cluster_id,
                "cluster_label": f"Cluster {cluster_id + 1}",
                "cluster_size": len(members),
                "historical_member_count": len(historical_members),
                "cold_start_member_count": len(cold_start_members),
                "projected_member_count": len(cold_start_members),
                "no_history_member_count": sum(member.get("shipment_observation_count", 0) == 0 for member in cold_start_members),
                "training_spbu_percentage": round(100 * len(historical_members) / historical_assignment_count, 2) if historical_assignment_count else 0.0,
                "common_tags": common_tags,
                "shift_distribution": [
                    {"shift_id": shift["shift_id"], "shift_name": shift["name"], "share": round(shift_means[index], 4)}
                    for index, shift in enumerate(shift_snapshot)
                ],
                "dominant_shift": shift_snapshot[dominant_index]["name"] if dominant_index is not None else "Insufficient timestamp data",
                "top_internal_pairings": internal_pairs[:10],
                "inference_internal_pairings": internal_pairs,
                "average_membership_probability": round(average_probability, 4),
                "low_confidence_member_count": sum(member["membership_probability"] < 0.5 for member in historical_members),
                "member_spbu_ids": sorted(member_ids),
                "covered_member_spbu_ids": sorted(all_member_ids),
                "evidence_scope": "CORE_MEMBER SPBUs determine behavioral statistics; MARGINAL_PROJECTED SPBUs are reported separately and never alter core boundaries.",
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
    config = _validated_training_configuration(configuration, run.training_configuration)
    records = run.dataset_payload.get("records", [])
    history_indices = [
        index for index, record in enumerate(records) if record.get("data_sufficiency_status") == "SUFFICIENT"
    ]
    marginal_indices = [
        index for index, record in enumerate(records) if record.get("data_sufficiency_status") == "MARGINAL"
    ]
    minimum_cluster_size = int(config["hdbscan_parameters"]["min_cluster_size"])
    if len(history_indices) < max(3, minimum_cluster_size):
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
        graph_population = [
            record["spbu_id"]
            for record in records
            if record.get("data_sufficiency_status") in {"SUFFICIENT", "MARGINAL"}
        ]
        graph = create_pairing_graph(graph_population, pair_rows)
        embeddings, node2vec_metadata = generate_node2vec_embeddings(graph, config["node2vec_parameters"])
        embedding_dimensions = int(config["node2vec_parameters"].get("dimensions", 16))
        for record in records:
            embeddings.setdefault(record["spbu_id"], [0.0] * embedding_dimensions)
        fused, fusion_metadata = _feature_fusion(
            records,
            embeddings,
            config["feature_weights"],
            fit_indices=history_indices,
            geography_enabled=config["geographic_configuration"]["enabled"],
        )
        try:
            import joblib
            import umap
            from sklearn.cluster import HDBSCAN
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail="UMAP/HDBSCAN ML dependencies are not installed.") from exc

        umap_config = config["umap_parameters"]
        history_fused = fused[history_indices]
        neighbors = min(max(2, int(umap_config["n_neighbors"])), len(history_indices) - 1)
        components = min(max(2, int(umap_config["n_components"])), max(2, len(history_indices) - 2), fused.shape[1])
        internal_umap = umap.UMAP(
            n_neighbors=neighbors,
            n_components=components,
            min_dist=max(0.0, min(0.99, float(umap_config["min_dist"]))),
            metric=str(umap_config["metric"]),
            random_state=int(umap_config["random_state"]),
            transform_seed=int(config["random_seed"]),
        )
        reduced_history = internal_umap.fit_transform(history_fused)
        reduced = np.full((len(records), components), np.nan, dtype=float)
        reduced[history_indices] = reduced_history
        if marginal_indices:
            reduced[marginal_indices] = internal_umap.transform(fused[marginal_indices])
        cluster_config = config["hdbscan_parameters"]
        clusterer = HDBSCAN(
            min_cluster_size=minimum_cluster_size,
            min_samples=int(cluster_config["min_samples"]),
            metric=str(cluster_config["metric"]),
            cluster_selection_method=str(cluster_config["cluster_selection_method"]),
            n_jobs=1,
            copy=True,
        )
        history_labels = clusterer.fit_predict(reduced_history)
        history_probabilities = clusterer.probabilities_
        visualization_umap = umap.UMAP(
            n_neighbors=neighbors,
            n_components=2,
            min_dist=max(0.05, min(0.99, float(umap_config["min_dist"]))),
            metric=str(umap_config["metric"]),
            random_state=int(umap_config["random_state"]),
            transform_seed=int(config["random_seed"]),
        )
        visualization_history = visualization_umap.fit_transform(history_fused)
        visualization = np.full((len(records), 2), np.nan, dtype=float)
        visualization[history_indices] = visualization_history
        if marginal_indices:
            visualization[marginal_indices] = visualization_umap.transform(fused[marginal_indices])

        labels: list[int | None] = [None] * len(records)
        membership_probabilities: list[float | None] = [None] * len(records)
        projection_confidences: list[float | None] = [None] * len(records)
        projection_statuses: list[str] = ["NOT_APPLICABLE"] * len(records)
        for index, label, probability in zip(history_indices, history_labels, history_probabilities, strict=True):
            labels[index] = int(label)
            membership_probabilities[index] = float(probability)

        # sklearn.cluster.HDBSCAN does not expose approximate_predict. Marginal
        # records are therefore transformed by the fitted UMAP and compared with
        # core-cluster centroids in that immutable embedding. The distance-based
        # confidence and threshold are persisted and low-confidence cases stay
        # unassigned rather than being forced into a cluster.
        clustered_history_labels = sorted({int(label) for label in history_labels if int(label) >= 0})
        cluster_centroids: dict[int, np.ndarray] = {}
        cluster_scales: dict[int, float] = {}
        for label in clustered_history_labels:
            member_vectors = reduced_history[history_labels == label]
            centroid = np.mean(member_vectors, axis=0)
            distances = np.linalg.norm(member_vectors - centroid, axis=1)
            cluster_centroids[label] = centroid
            global_scale = float(np.median(np.linalg.norm(reduced_history - np.mean(reduced_history, axis=0), axis=1)))
            cluster_scales[label] = max(1e-3, global_scale * 0.10, float(np.percentile(distances, 90)) if len(distances) else 1.0)
        projection_config = config["projection_configuration"]
        for index in marginal_indices:
            projection = project_to_core_clusters(
                reduced[index],
                cluster_centroids,
                cluster_scales,
                minimum_confidence=float(projection_config["minimum_confidence"]),
                distance_scale_multiplier=float(projection_config["distance_scale_multiplier"]),
            )
            projection_confidences[index] = projection["projection_confidence"]
            labels[index] = projection["projected_cluster_id"]
            projection_statuses[index] = projection["projection_status"]
        assignments = []
        for index, record in enumerate(records):
            sufficiency_status = record["data_sufficiency_status"]
            label = labels[index]
            is_core_noise = sufficiency_status == "SUFFICIENT" and label == -1
            if sufficiency_status == "SUFFICIENT":
                assignment_type = "CORE_NOISE" if is_core_noise else "CORE_MEMBER"
            elif sufficiency_status == "MARGINAL":
                assignment_type = "MARGINAL_PROJECTED" if projection_statuses[index] == "PROJECTED" else "MARGINAL_UNASSIGNED"
            else:
                assignment_type = "INSUFFICIENT_UNASSIGNED"
                projection_statuses[index] = "UNASSIGNED"
            assigned_cluster_id = int(label) if label is not None and label >= 0 else None
            projection_confidence = projection_confidences[index]
            membership_probability = membership_probabilities[index]
            visualization_x = None if not math.isfinite(float(visualization[index, 0])) else round(float(visualization[index, 0]), 6)
            visualization_y = None if not math.isfinite(float(visualization[index, 1])) else round(float(visualization[index, 1]), 6)
            assignments.append(
                {
                    "spbu_id": record["spbu_id"],
                    "spbu_code": record["spbu_code"],
                    "spbu_name": record["spbu_name"],
                    "latitude": record.get("latitude"),
                    "longitude": record.get("longitude"),
                    "shipment_observation_count": record["shipment_observation_count"],
                    "operating_day_count": record["operating_day_count"],
                    "training_period_coverage": record["training_period_coverage"],
                    "shift_observation_coverage": record["shift_observation_coverage"],
                    "pairing_observation_count": record["pairing_observation_count"],
                    "pairing_observation_strength": record["pairing_observation_strength"],
                    "last_operating_date": record["last_operating_date"],
                    "recency_age_days": record["recency_age_days"],
                    "data_sufficiency_score": record["data_sufficiency_score"],
                    "data_sufficiency_status": sufficiency_status,
                    "data_sufficiency_components": record["data_sufficiency_components"],
                    "coverage_source": record.get("coverage_source", "BEHAVIORAL_HISTORY"),
                    "history_eligible": sufficiency_status == "SUFFICIENT",
                    "cluster_id": assigned_cluster_id,
                    "cluster_label": (
                        "Noise / Unique Behavioral Pattern"
                        if is_core_noise
                        else f"Cluster {assigned_cluster_id + 1}"
                        if assigned_cluster_id is not None
                        else "Not Assigned"
                    ),
                    "cluster_assignment_type": assignment_type,
                    "membership_probability": round(membership_probability, 6) if membership_probability is not None else None,
                    "projected_cluster_id": assigned_cluster_id if assignment_type == "MARGINAL_PROJECTED" else None,
                    "projection_confidence": round(projection_confidence, 6) if projection_confidence is not None else None,
                    "projection_status": projection_statuses[index],
                    "unassigned_reason": (
                        "Projection confidence below configured threshold"
                        if assignment_type == "MARGINAL_UNASSIGNED" and projection_statuses[index] == "LOW_CONFIDENCE"
                        else "No core cluster is available for projection"
                        if assignment_type == "MARGINAL_UNASSIGNED"
                        else "Insufficient historical evidence"
                        if assignment_type == "INSUFFICIENT_UNASSIGNED"
                        else None
                    ),
                    "is_noise": is_core_noise,
                    "dominant_shift": record["dominant_shift"],
                    "vehicle_class": record.get("vehicle_class"),
                    "key_tags": record["key_tags"],
                    "geographic_data_status": record["geographic_data_status"],
                    "geographic_duplicate_coordinate": record["geographic_duplicate_coordinate"],
                    "nearest_spbu_distance_km": record["nearest_spbu_distance_km"],
                    "average_k_nearest_distance_km": record["average_k_nearest_distance_km"],
                    "median_k_nearest_distance_km": record["median_k_nearest_distance_km"],
                    "local_spbu_density": record["local_spbu_density"],
                    "visualization_x": visualization_x,
                    "visualization_y": visualization_y,
                }
            )
        run.status = "CALCULATING_PROFILES"
        profiles = _cluster_profiles(assignments, records, pair_rows, run.shift_definition_snapshot)
        historical_assignments = [assignment for assignment in assignments if assignment["data_sufficiency_status"] == "SUFFICIENT"]
        marginal_assignments = [assignment for assignment in assignments if assignment["data_sufficiency_status"] == "MARGINAL"]
        insufficient_assignments = [assignment for assignment in assignments if assignment["data_sufficiency_status"] == "INSUFFICIENT"]
        marginal_projected = [assignment for assignment in marginal_assignments if assignment["cluster_assignment_type"] == "MARGINAL_PROJECTED"]
        cluster_count = len({assignment["cluster_id"] for assignment in historical_assignments if not assignment["is_noise"]})
        noise_count = sum(assignment["is_noise"] for assignment in historical_assignments)
        average_membership = float(np.mean([assignment["membership_probability"] for assignment in historical_assignments]))
        average_projection_confidence = (
            float(np.mean([assignment["projection_confidence"] for assignment in marginal_projected]))
            if marginal_projected
            else 0.0
        )
        warnings = []
        if graph.number_of_edges() == 0:
            warnings.append("No pairing edges were available; every SPBU received the documented zero pairing embedding.")
        if noise_count == len(historical_assignments):
            warnings.append("HDBSCAN marked every SPBU as noise. Review feature weights or density parameters before saving.")
        if marginal_assignments:
            warnings.append(
                f"{len(marginal_assignments)} MARGINAL SPBU were excluded from core fitting; {len(marginal_projected)} passed post-training projection confidence."
            )
        if insufficient_assignments:
            warnings.append(f"{len(insufficient_assignments)} INSUFFICIENT SPBU remain unassigned and are not HDBSCAN noise.")
        if config["feature_weights"]["pairing"] >= 0.4 and config["feature_weights"]["geographic"] >= 0.3:
            warnings.append("Pairing and Geographic weights are both high; correlated proximity behavior may have strong combined influence.")
        geographic_metadata = run.dataset_payload.get("geographic_metadata", {})
        result = {
            "summary": {
                "training_spbu_count": len(historical_assignments),
                "behavioral_history_spbu_count": len(history_indices),
                "historical_training_spbu_count": len(historical_assignments),
                "total_covered_spbu_count": len(assignments),
                "total_spbu_count": len(assignments),
                "sufficient_spbu_count": len(historical_assignments),
                "marginal_spbu_count": len(marginal_assignments),
                "insufficient_spbu_count": len(insufficient_assignments),
                "core_training_spbu_count": len(historical_assignments),
                "core_cluster_member_count": len(historical_assignments) - noise_count,
                "core_noise_count": noise_count,
                "marginal_projected_count": len(marginal_projected),
                "marginal_unassigned_count": len(marginal_assignments) - len(marginal_projected),
                "insufficient_unassigned_count": len(insufficient_assignments),
                "cold_start_covered_spbu_count": len(marginal_assignments),
                "no_history_spbu_count": sum(assignment["shipment_observation_count"] == 0 for assignment in insufficient_assignments),
                "insufficient_history_spbu_count": len(insufficient_assignments),
                "cluster_count": cluster_count,
                "clustered_spbu_count": len(historical_assignments) - noise_count,
                "noise_spbu_count": noise_count,
                "average_membership_probability": round(average_membership, 6),
                "average_projection_confidence": round(average_projection_confidence, 6),
                "valid_coordinate_count": int(geographic_metadata.get("valid_coordinate_count", 0)),
                "invalid_coordinate_count": int(geographic_metadata.get("invalid_coordinate_count", 0)),
                "geographic_coverage_percentage": float(geographic_metadata.get("geographic_coverage_percentage", 0.0)),
            },
            "assignments": assignments,
            "cluster_profiles": profiles,
            "configuration": config,
            "node2vec_metadata": node2vec_metadata,
            "fusion_metadata": fusion_metadata,
            "projection_metadata": {
                "method": projection_config["method"],
                "minimum_confidence": projection_config["minimum_confidence"],
                "distance_scale_multiplier": projection_config["distance_scale_multiplier"],
                "reference_space": "fitted internal UMAP",
                "core_cluster_reference_count": len(cluster_centroids),
            },
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
            "data_sufficiency_configuration": config["data_sufficiency_configuration"],
            "geographic_configuration": config["geographic_configuration"],
            "geographic_metadata": run.dataset_payload.get("geographic_metadata", {}),
            "projection_configuration": config["projection_configuration"],
            "projection_reference": {
                "cluster_centroids": {label: centroid.tolist() for label, centroid in cluster_centroids.items()},
                "cluster_scales": cluster_scales,
                "reference_space": "fitted internal UMAP",
            },
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
                    "data_sufficiency_score",
                    "data_sufficiency_status",
                    "operating_day_count",
                    "training_period_coverage",
                    "shift_observation_coverage",
                    "pairing_observation_count",
                    "pairing_observation_strength",
                    "last_operating_date",
                    "recency_age_days",
                    "geographic_data_status",
                    "nearest_spbu_distance_km",
                    "average_k_nearest_distance_km",
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
