from __future__ import annotations


PHASE6_ALGORITHM_VERSION = "phase6.time_aware_multitrip.v2"

PHASE6_PERMISSIONS = {
    "view": "phase6:view",
    "run": "phase6:run",
    "export": "phase6:export",
    "override": "phase6:override",
    "settings_view": "google_routes:view",
    "settings_manage": "google_routes:manage",
}
DEFAULT_PREDICTION_PARAMETERS = {
    "minimum_prediction_confidence": 0.60,
    "high_confidence_threshold": 0.80,
    "medium_confidence_threshold": 0.60,
    "blocking_prediction_confidence": None,
    "random_seed": 42,
    "maximum_pairing_time_gap_minutes": 30,
    "assignment_mode": "STRICT_START",
    "maximum_allowed_delay_minutes": 30,
    "max_exact_sequence_stops": 4,
    "maximum_planning_horizon_days": 7,
}
