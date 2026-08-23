from __future__ import annotations


PHASE6_ALGORITHM_VERSION = "phase6.iterative_exact_capacity_assignment.v9"

PHASE6_PERMISSIONS = {
    "view": "phase6:view",
    "run": "phase6:run",
    "export": "phase6:export",
    "override": "phase6:override",
    "settings_view": "google_routes:view",
    "settings_manage": "google_routes:manage",
}
DEFAULT_PREDICTION_PARAMETERS = {
    "minimum_prediction_confidence": 0.40,
    "high_confidence_threshold": 0.80,
    "medium_confidence_threshold": 0.60,
    "blocking_prediction_confidence": None,
    "random_seed": 42,
    "maximum_pairing_time_gap_minutes": 90,
    "maximum_shipment_compartments": 4,
    "maximum_group_route_detour_ratio": 2.0,
    "require_full_mt_utilization": True,
    "assignment_mode": "STRICT_START",
    "maximum_allowed_delay_minutes": 30,
    "max_exact_sequence_stops": 4,
    "maximum_planning_horizon_days": 7,
}
