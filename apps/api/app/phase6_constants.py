from __future__ import annotations


PHASE6_ALGORITHM_VERSION = "phase6.shipment_mt_prediction.v1"

PHASE6_PERMISSIONS = {
    "view": "phase6:view",
    "run": "phase6:run",
    "export": "phase6:export",
    "override": "phase6:override",
}
DEFAULT_PREDICTION_PARAMETERS = {
    "minimum_prediction_confidence": 0.60,
    "high_confidence_threshold": 0.80,
    "medium_confidence_threshold": 0.60,
    "blocking_prediction_confidence": None,
    "random_seed": 42,
}
