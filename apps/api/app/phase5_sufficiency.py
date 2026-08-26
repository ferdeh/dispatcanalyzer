from __future__ import annotations

import math
from collections import Counter
from datetime import date
from typing import Any

from fastapi import HTTPException

from .phase5_constants import (
    DEFAULT_DATA_SUFFICIENCY_COMPONENT_WEIGHTS,
    DEFAULT_DATA_SUFFICIENCY_CONFIGURATION,
    DEFAULT_DATA_SUFFICIENCY_THRESHOLDS,
    DEFAULT_GEOGRAPHIC_CONFIGURATION,
)


SUFFICIENCY_STATUSES = ("SUFFICIENT", "MARGINAL", "INSUFFICIENT")
GEOGRAPHIC_DATA_STATUSES = ("VALID", "MISSING", "INVALID")


def _bounded_ratio(value: float, target: float) -> float:
    if target <= 0:
        return 1.0
    return max(0.0, min(1.0, value / target))


def validate_data_sufficiency_configuration(
    configuration: dict[str, Any] | None,
    *,
    minimum_shipment_observations: int | None = None,
) -> dict[str, Any]:
    source = configuration or {}
    thresholds = {
        **DEFAULT_DATA_SUFFICIENCY_THRESHOLDS,
        **(source.get("thresholds") or {}),
    }
    component_weights = {
        **DEFAULT_DATA_SUFFICIENCY_COMPONENT_WEIGHTS,
        **(source.get("component_weights") or {}),
    }
    result = {
        **DEFAULT_DATA_SUFFICIENCY_CONFIGURATION,
        **{key: value for key, value in source.items() if key not in {"thresholds", "component_weights", "recency_configuration"}},
        "thresholds": {key: float(value) for key, value in thresholds.items()},
        "component_weights": {key: float(value) for key, value in component_weights.items()},
        "recency_configuration": {
            **DEFAULT_DATA_SUFFICIENCY_CONFIGURATION["recency_configuration"],
            **(source.get("recency_configuration") or {}),
        },
    }
    if minimum_shipment_observations is not None:
        result["minimum_shipment_observations"] = int(minimum_shipment_observations)
    numeric_minimums = (
        "minimum_shipment_observations",
        "minimum_operating_days",
        "minimum_pairing_evidence",
    )
    if any(int(result[key]) < 1 for key in numeric_minimums):
        raise HTTPException(status_code=400, detail="Data sufficiency absolute minimums must be at least 1.")
    for key in ("minimum_period_coverage", "minimum_shift_coverage"):
        result[key] = float(result[key])
        if result[key] <= 0 or result[key] > 1:
            raise HTTPException(status_code=400, detail=f"{key} must be greater than 0 and at most 1.")
    result["recency_configuration"]["maximum_age_days"] = int(result["recency_configuration"]["maximum_age_days"])
    if result["recency_configuration"]["maximum_age_days"] < 1:
        raise HTTPException(status_code=400, detail="recency maximum_age_days must be at least 1.")
    if not 0 <= result["thresholds"]["marginal"] < result["thresholds"]["sufficient"] <= 100:
        raise HTTPException(status_code=400, detail="Data sufficiency thresholds must satisfy 0 <= marginal < sufficient <= 100.")
    expected_components = set(DEFAULT_DATA_SUFFICIENCY_COMPONENT_WEIGHTS)
    if set(result["component_weights"]) != expected_components:
        raise HTTPException(status_code=400, detail="Data sufficiency component weights contain unsupported components.")
    if any(value < 0 or value > 1 for value in result["component_weights"].values()) or not math.isclose(
        sum(result["component_weights"].values()), 1.0, abs_tol=1e-6
    ):
        raise HTTPException(status_code=400, detail="Data sufficiency component weights must be within 0-1 and sum to 1.00.")
    return result


def classify_data_sufficiency(score: float, thresholds: dict[str, float] | None = None) -> str:
    values = {**DEFAULT_DATA_SUFFICIENCY_THRESHOLDS, **(thresholds or {})}
    if score >= float(values["sufficient"]):
        return "SUFFICIENT"
    if score >= float(values["marginal"]):
        return "MARGINAL"
    return "INSUFFICIENT"


def calculate_data_sufficiency(
    *,
    shipment_observation_count: int,
    operating_day_count: int,
    training_period_days: int,
    valid_shift_observation_count: int,
    pairing_observation_count: int,
    last_operating_date: date | None,
    training_end_date: date,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = validate_data_sufficiency_configuration(configuration)
    shipment_count = max(0, int(shipment_observation_count))
    operating_days = max(0, int(operating_day_count))
    period_days = max(1, int(training_period_days))
    valid_shifts = max(0, int(valid_shift_observation_count))
    pairing_count = max(0, int(pairing_observation_count))
    period_coverage = min(1.0, operating_days / period_days)
    shift_coverage = min(1.0, valid_shifts / shipment_count) if shipment_count else 0.0
    pairing_strength = min(1.0, pairing_count / shipment_count) if shipment_count else 0.0
    recency_age_days = max(0, (training_end_date - last_operating_date).days) if last_operating_date else None
    max_age = int(config["recency_configuration"]["maximum_age_days"])
    component_scores = {
        "shipment_count": 100.0 * _bounded_ratio(shipment_count, int(config["minimum_shipment_observations"])),
        "operating_days": 100.0 * _bounded_ratio(operating_days, int(config["minimum_operating_days"])),
        "period_coverage": 100.0 * _bounded_ratio(period_coverage, float(config["minimum_period_coverage"])),
        "shift_coverage": 100.0 * _bounded_ratio(shift_coverage, float(config["minimum_shift_coverage"])),
        "pairing_evidence": 100.0 * _bounded_ratio(pairing_count, int(config["minimum_pairing_evidence"])),
        "recency": 0.0 if recency_age_days is None else 100.0 * max(0.0, 1.0 - recency_age_days / max_age),
    }
    score = round(
        sum(component_scores[key] * float(config["component_weights"][key]) for key in component_scores),
        4,
    )
    return {
        "data_sufficiency_score": score,
        "data_sufficiency_status": classify_data_sufficiency(score, config["thresholds"]),
        "data_sufficiency_components": {key: round(value, 4) for key, value in component_scores.items()},
        "operating_day_count": operating_days,
        "training_period_coverage": round(period_coverage, 6),
        "shift_observation_coverage": round(shift_coverage, 6),
        "pairing_observation_count": pairing_count,
        "pairing_observation_strength": round(pairing_strength, 6),
        "last_operating_date": last_operating_date.isoformat() if last_operating_date else None,
        "recency_age_days": recency_age_days,
    }


def validate_geographic_configuration(configuration: dict[str, Any] | None) -> dict[str, Any]:
    result = {**DEFAULT_GEOGRAPHIC_CONFIGURATION, **(configuration or {})}
    result["enabled"] = bool(result["enabled"])
    result["k_nearest_neighbors"] = int(result["k_nearest_neighbors"])
    result["maximum_proximity_distance_km"] = float(result["maximum_proximity_distance_km"])
    result["distance_method"] = "HAVERSINE"
    if result["k_nearest_neighbors"] < 1:
        raise HTTPException(status_code=400, detail="K nearest geographic neighbors must be at least 1.")
    if result["maximum_proximity_distance_km"] <= 0:
        raise HTTPException(status_code=400, detail="Maximum geographic proximity distance must be greater than 0 km.")
    return result


def geographic_data_status(latitude: Any, longitude: Any) -> tuple[str, float | None, float | None]:
    if latitude is None or longitude is None:
        return "MISSING", None, None
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return "INVALID", None, None
    if not math.isfinite(lat) or not math.isfinite(lon) or lat < -90 or lat > 90 or lon < -180 or lon > 180:
        return "INVALID", None, None
    if math.isclose(lat, 0.0, abs_tol=1e-12) and math.isclose(lon, 0.0, abs_tol=1e-12):
        return "INVALID", None, None
    return "VALID", lat, lon


def haversine_distance_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    for latitude in (latitude_a, latitude_b):
        if not math.isfinite(float(latitude)) or float(latitude) < -90 or float(latitude) > 90:
            raise ValueError("Latitude must be finite and within -90..90.")
    for longitude in (longitude_a, longitude_b):
        if not math.isfinite(float(longitude)) or float(longitude) < -180 or float(longitude) > 180:
            raise ValueError("Longitude must be finite and within -180..180.")
    radius_km = 6371.0088
    lat_a, lat_b = math.radians(float(latitude_a)), math.radians(float(latitude_b))
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(float(longitude_b) - float(longitude_a))
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    return radius_km * 2 * math.asin(min(1.0, math.sqrt(value)))


def build_geographic_features(records: list[dict], configuration: dict[str, Any] | None = None) -> dict[str, Any]:
    config = validate_geographic_configuration(configuration)
    coordinate_counts: Counter[tuple[float, float]] = Counter()
    for record in records:
        status, latitude, longitude = geographic_data_status(record.get("latitude"), record.get("longitude"))
        record["geographic_data_status"] = status
        record["latitude"] = latitude
        record["longitude"] = longitude
        if status == "VALID":
            coordinate_counts[(latitude, longitude)] += 1
    valid_records = [record for record in records if record["geographic_data_status"] == "VALID"]
    maximum_distance = float(config["maximum_proximity_distance_km"])
    for record in records:
        duplicate_count = coordinate_counts[(record["latitude"], record["longitude"])] if record["geographic_data_status"] == "VALID" else 0
        record["geographic_duplicate_coordinate"] = duplicate_count > 1
        if not config["enabled"] or record["geographic_data_status"] != "VALID":
            record["geographic_vector"] = [None, None, None, None]
            record["nearest_spbu_distance_km"] = None
            record["average_k_nearest_distance_km"] = None
            record["median_k_nearest_distance_km"] = None
            record["local_spbu_density"] = None
            continue
        distances = sorted(
            haversine_distance_km(record["latitude"], record["longitude"], other["latitude"], other["longitude"])
            for other in valid_records
            if other["spbu_id"] != record["spbu_id"]
        )
        k_distances = distances[: min(int(config["k_nearest_neighbors"]), len(distances))]
        nearest = k_distances[0] if k_distances else 0.0
        average = sum(k_distances) / len(k_distances) if k_distances else 0.0
        median = (
            k_distances[len(k_distances) // 2]
            if len(k_distances) % 2
            else (k_distances[len(k_distances) // 2 - 1] + k_distances[len(k_distances) // 2]) / 2
        ) if k_distances else 0.0
        density = sum(distance <= maximum_distance for distance in distances) / max(1, len(valid_records) - 1)
        values = [nearest, average, median, density]
        record["geographic_vector"] = [round(value, 8) for value in values]
        record["nearest_spbu_distance_km"] = round(nearest, 6)
        record["average_k_nearest_distance_km"] = round(average, 6)
        record["median_k_nearest_distance_km"] = round(median, 6)
        record["local_spbu_density"] = round(density, 6)
    valid_count = len(valid_records)
    invalid_count = len(records) - valid_count
    return {
        "configuration": config,
        "valid_coordinate_count": valid_count,
        "invalid_coordinate_count": invalid_count,
        "missing_coordinate_count": sum(record["geographic_data_status"] == "MISSING" for record in records),
        "invalid_value_coordinate_count": sum(record["geographic_data_status"] == "INVALID" for record in records),
        "duplicate_coordinate_spbu_count": sum(bool(record["geographic_duplicate_coordinate"]) for record in records),
        "geographic_coverage_percentage": round(100.0 * valid_count / len(records), 2) if records else 0.0,
        "feature_schema": [
            "distance_to_nearest_spbu_km",
            "average_distance_to_k_nearest_neighbors_km",
            "median_distance_to_k_nearest_neighbors_km",
            "local_spbu_density_within_maximum_distance",
            "missing_or_invalid_coordinate_indicator",
        ],
    }
