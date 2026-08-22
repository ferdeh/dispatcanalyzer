from __future__ import annotations


CONCENTRATION_ALGORITHM_VERSION = "phase5.concentration.iforest.v1"
BEHAVIORAL_ALGORITHM_VERSION = "phase5.behavioral.portable_n2v_umap_hdbscan.v2"
DEFAULT_MINIMUM_OBSERVATIONS = 10

CONCENTRATION_CLASSIFICATION_THRESHOLDS = {
    "moderate": 40.0,
    "high": 60.0,
    "investigation": 80.0,
}
DEFAULT_ENGINE_A_PARAMETERS = {
    "n_estimators": 200,
    "contamination": "auto",
    "random_seed": 42,
    "classification_thresholds": CONCENTRATION_CLASSIFICATION_THRESHOLDS,
}

DEFAULT_SHIFT_DEFINITIONS = [
    {"shift_id": "shift_1", "name": "Shift 1", "start_time": "00:00", "end_time": "05:59"},
    {"shift_id": "shift_2", "name": "Shift 2", "start_time": "06:00", "end_time": "11:59"},
    {"shift_id": "shift_3", "name": "Shift 3", "start_time": "12:00", "end_time": "17:59"},
    {"shift_id": "shift_4", "name": "Shift 4", "start_time": "18:00", "end_time": "23:59"},
]

DEFAULT_FEATURE_WEIGHTS = {"tag": 0.40, "shift": 0.25, "pairing": 0.35}
DEFAULT_NODE2VEC_PARAMETERS = {
    "dimensions": 16,
    "walk_length": 20,
    "num_walks": 40,
    "p": 1.0,
    "q": 1.0,
    "window": 8,
    "seed": 42,
}
DEFAULT_UMAP_PARAMETERS = {
    "n_neighbors": 15,
    "n_components": 5,
    "min_dist": 0.05,
    "metric": "euclidean",
    "random_state": 42,
}
DEFAULT_HDBSCAN_PARAMETERS = {
    "min_cluster_size": 5,
    "min_samples": 3,
    "metric": "euclidean",
    "cluster_selection_method": "eom",
}

PHASE5_PERMISSIONS = {
    "view": "phase5:view",
    "run": "phase5:run",
    "train": "phase5:train",
    "save": "phase5:save",
    "activate": "phase5:activate",
    "delete": "phase5:delete",
}
