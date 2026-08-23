from __future__ import annotations

import math
from typing import Any

from .models import MasterMT
from .normalization import mt_capacity_kl


LOADING_ORDER_COMPARTMENT_KL = 8.0
_TOLERANCE = 1e-6


def loading_order_compartments(quantity_kl: Any) -> int | None:
    """Return the number of 8 KL compartments consumed by one Loading Order."""
    try:
        quantity = float(quantity_kl)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(quantity) or quantity <= 0:
        return None
    units = quantity / LOADING_ORDER_COMPARTMENT_KL
    rounded = round(units)
    return int(rounded) if rounded > 0 and math.isclose(units, rounded, abs_tol=_TOLERANCE) else None


def shipment_capacity(lines: list[dict]) -> dict:
    quantities = [line.get("order_quantity_kl") for line in lines]
    compartments = [loading_order_compartments(quantity) for quantity in quantities]
    valid = bool(lines) and all(value is not None for value in compartments)
    return {
        "valid": valid,
        "total_order_kl": round(sum(float(quantity or 0) for quantity in quantities), 3),
        "required_compartments": sum(value or 0 for value in compartments),
        "loading_order_count": len(lines),
        "compartment_unit_kl": LOADING_ORDER_COMPARTMENT_KL,
    }


def mt_compartment_profile(mt: MasterMT) -> dict:
    """Resolve and validate the usable 8 KL compartments in an MT master row."""
    capacity = mt_capacity_kl(mt.capacity_label, mt.vehicle_type_tag)
    capacity_compartments = loading_order_compartments(capacity)
    configured_compartments = mt.number_of_compartments if mt.number_of_compartments is not None and mt.number_of_compartments > 0 else None
    failed_rules: list[str] = []
    if mt.number_of_compartments is not None and mt.number_of_compartments <= 0:
        failed_rules.append("MT_COMPARTMENT_COUNT_INVALID")
    if capacity_compartments is None:
        failed_rules.append("MT_CAPACITY_NOT_8_KL_MULTIPLE")
    if configured_compartments is not None and capacity_compartments is not None and configured_compartments != capacity_compartments:
        failed_rules.append("MT_COMPARTMENT_CAPACITY_MISMATCH")
    effective_compartments = configured_compartments or capacity_compartments
    if effective_compartments is None:
        failed_rules.append("MT_COMPARTMENT_COUNT_MISSING")
    return {
        "valid": not failed_rules,
        "capacity_kl": capacity,
        "capacity_compartments": capacity_compartments,
        "configured_compartments": configured_compartments,
        "effective_compartments": effective_compartments,
        "compartment_unit_kl": LOADING_ORDER_COMPARTMENT_KL,
        "compartment_source": "MASTER" if configured_compartments is not None else "INFERRED_FROM_CAPACITY",
        "failed_rules": failed_rules,
    }


def capacity_compatible(lines: list[dict], mt: MasterMT, *, require_full_utilization: bool = False) -> dict:
    shipment = shipment_capacity(lines)
    vehicle = mt_compartment_profile(mt)
    required = shipment["required_compartments"]
    available = vehicle["effective_compartments"]
    fits = bool(shipment["valid"] and vehicle["valid"] and available is not None)
    if fits:
        fits = required == available if require_full_utilization else required <= available
    failed_rules = [*vehicle["failed_rules"]]
    if not shipment["valid"]:
        failed_rules.append("SHIPMENT_VOLUME_NOT_8_KL_MULTIPLE")
    elif not fits and vehicle["valid"]:
        failed_rules.append("SHIPMENT_MT_CAPACITY_MISMATCH")
    return {
        "compatible": fits,
        "utilization_policy": "EXACT_COMPARTMENT_MATCH" if require_full_utilization else "ALLOW_PARTIAL_LOAD",
        "failed_rules": sorted(set(failed_rules)),
        "shipment": shipment,
        "vehicle": vehicle,
    }
