from __future__ import annotations


PHASE7_ALGORITHM_VERSION = "phase7.dynamic_multitrip_vrp_bay.v1"

JOB_STATUSES = {"DRAFT", "READY", "CALCULATING", "COMPLETED", "ACTIVE", "CLOSED", "FAILED"}
LO_STATUSES = {"PLANNED", "ONGOING", "DONE"}
MT_STATUSES = {"READY", "ON_TRIP", "RETURNING", "QUEUEING", "LOADING", "UNAVAILABLE"}
SOLVER_STATUSES = {"OPTIMAL", "FEASIBLE", "PARTIAL", "INFEASIBLE", "TIME_LIMIT", "FAILED"}
OBJECTIVES = {"MIN_TOTAL_COST", "MIN_TOTAL_DISTANCE", "MIN_TOTAL_OPERATING_TIME"}
DROPPED_REASON_CODES = {
    "NO_COMPATIBLE_MT",
    "INSUFFICIENT_CAPACITY",
    "COMPARTMENT_INFEASIBLE",
    "VEHICLE_TIME_EXHAUSTED",
    "DEPOT_TIME_EXHAUSTED",
    "SPBU_TIME_WINDOW",
    "BAY_PRODUCT_CONSTRAINT",
    "BAY_CONGESTION",
    "NO_FEASIBLE_ROUTE",
    "USER_CANCELLED",
    "UNSERVED_END_OF_DAY",
}


DEFAULT_PHASE7_PARAMETERS: dict = {
    "objective": "MIN_TOTAL_COST",
    "freeze_window_minutes": 60,
    "reoptimization_interval_minutes": 60,
    "optimization_time_limit": 30,
    "max_coordination_iterations": 5,
    "departure_time_tolerance_minutes": 5,
    "return_time_tolerance_minutes": 5,
    "maximum_trips_per_mt": 6,
    "default_vehicle_working_time_minutes": 720,
    "default_spbu_service_minutes": 30,
    "cost_per_km": 10_000.0,
    "cost_per_operating_hour": 50_000.0,
    "queue_cost": 1_000.0,
    "loading_cost": 1_000.0,
    "overtime_cost": 5_000.0,
    "unserved_penalty": 10_000_000.0,
    "late_penalty": 250_000.0,
    "overtime_penalty": 500_000.0,
    "phase6_vehicle_change_penalty": 250_000.0,
    "phase6_shipment_change_penalty": 150_000.0,
    "historical_pairing_penalty": 25_000.0,
    "historical_mt_affinity_penalty": 25_000.0,
    "plan_change_penalty": 200_000.0,
    "vehicle_reassignment_penalty": 150_000.0,
    "shipment_change_penalty": 100_000.0,
    "route_sequence_change_penalty": 50_000.0,
    "gateout_change_penalty": 50_000.0,
    "bay_queue_penalty": 25_000.0,
    "bay_change_penalty": 25_000.0,
    "route_vehicle_mode": "GENERAL_VEHICLE",
    "traffic_aware": True,
    "route_matrix_cache_enabled": True,
    "route_matrix_cache_ttl_minutes": 60,
    "gate_process_time": 5,
    "loading_mode": "SEQUENTIAL",
    "vehicle_activation_cost_rules": [
        {"vehicle_class": 8, "vehicle_tag": None, "activation_cost": 500_000.0, "priority": 10},
        {"vehicle_class": 16, "vehicle_tag": None, "activation_cost": 700_000.0, "priority": 10},
        {"vehicle_class": 24, "vehicle_tag": None, "activation_cost": 900_000.0, "priority": 10},
        {"vehicle_class": 32, "vehicle_tag": None, "activation_cost": 1_100_000.0, "priority": 10},
    ],
}


DEFAULT_PARAMETER_PROFILES = (
    ("Balanced Default", "Balanced cost, route, historical adherence, and plan stability.", {}),
    ("Cost Efficiency", "Prioritize total cost while retaining every hard constraint.", {"objective": "MIN_TOTAL_COST"}),
    ("High Historical Adherence", "Apply stronger Phase 6 and historical soft preferences.", {"phase6_vehicle_change_penalty": 750_000.0, "phase6_shipment_change_penalty": 500_000.0}),
    ("Peak Operation", "Shorter solve cycle and stronger gate-out stability during peak hours.", {"reoptimization_interval_minutes": 30, "gateout_change_penalty": 250_000.0}),
    ("High Bay Congestion", "Increase queue and bay-change penalties.", {"queue_cost": 5_000.0, "bay_queue_penalty": 250_000.0, "bay_change_penalty": 100_000.0}),
)


def effective_parameters(overrides: dict | None = None) -> dict:
    parameters = {**DEFAULT_PHASE7_PARAMETERS, **(overrides or {})}
    if parameters["objective"] not in OBJECTIVES:
        raise ValueError(f"Unsupported Phase 7 objective: {parameters['objective']}")
    if str(parameters.get("route_vehicle_mode", "GENERAL_VEHICLE")).upper() not in {"GENERAL_VEHICLE", "TRUCK"}:
        raise ValueError("route_vehicle_mode must be GENERAL_VEHICLE or TRUCK")
    parameters["route_vehicle_mode"] = str(parameters["route_vehicle_mode"]).upper()
    parameters["loading_mode"] = str(parameters.get("loading_mode", "SEQUENTIAL")).upper()
    if parameters["loading_mode"] not in {"SEQUENTIAL", "PARALLEL"}:
        raise ValueError("loading_mode must be SEQUENTIAL or PARALLEL")
    integer_bounds = {
        "freeze_window_minutes": (0, 1440),
        "reoptimization_interval_minutes": (1, 1440),
        "optimization_time_limit": (1, 3600),
        "max_coordination_iterations": (1, 20),
        "departure_time_tolerance_minutes": (0, 240),
        "return_time_tolerance_minutes": (0, 240),
        "maximum_trips_per_mt": (1, 20),
        "default_vehicle_working_time_minutes": (1, 2880),
        "default_spbu_service_minutes": (0, 1440),
        "gate_process_time": (0, 240),
        "route_matrix_cache_ttl_minutes": (1, 10080),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        value = int(parameters[key])
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        parameters[key] = value
    return parameters
