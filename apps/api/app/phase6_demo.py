from __future__ import annotations

import math
import random
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .departure_intelligence import validate_shift_config
from .models import MLBehavioralModel, MasterDepot, MasterMT, MasterSPBU
from .normalization import mt_capacity_kl
from .phase6_export import workbook_bytes


DEMO_LOADING_ORDER_UNIT_KL = Decimal("8")
MAX_DEMO_TOTAL_ORDER_KL = Decimal("40000")
MAX_DEMO_TOTAL_MT_CAPACITY_KL = Decimal("40000")
CAPACITY_SCALE = Decimal("1000")
MAX_CAPACITY_SELECTION_STATES = 200_000


def _positive_demo_total(value: float, *, code: str, label: str, maximum: Decimal) -> Decimal:
    try:
        total = Decimal(str(value))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": code, "message": f"{label} must be a valid number in KL."},
        ) from exc
    if not total.is_finite() or total <= 0 or total > maximum:
        raise HTTPException(
            status_code=422,
            detail={"code": code, "message": f"{label} must be greater than 0 and no more than {maximum} KL."},
        )
    return total


def _capacity_units(value: Decimal) -> int:
    return int((value * CAPACITY_SCALE).to_integral_value(rounding=ROUND_HALF_UP))


def _select_mts_near_capacity(
    candidates: list[tuple[MasterMT, Decimal]],
    target: Decimal,
    rng: random.Random,
) -> list[tuple[MasterMT, Decimal]]:
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    target_units = _capacity_units(target)
    maximum_capacity_units = max(_capacity_units(capacity) for _, capacity in shuffled)
    upper_bound = target_units + maximum_capacity_units
    states: dict[int, tuple[int, ...]] = {0: ()}
    for index, (_, capacity) in enumerate(shuffled):
        capacity_units = _capacity_units(capacity)
        additions: dict[int, tuple[int, ...]] = {}
        for current_total, selected_indices in list(states.items()):
            next_total = current_total + capacity_units
            if next_total <= upper_bound and next_total not in states and next_total not in additions:
                additions[next_total] = (*selected_indices, index)
        states.update(additions)
        if len(states) > MAX_CAPACITY_SELECTION_STATES:
            closest = sorted(
                ((total, selected) for total, selected in states.items() if total > 0),
                key=lambda item: (abs(item[0] - target_units), -item[0], len(item[1])),
            )[:MAX_CAPACITY_SELECTION_STATES]
            states = {0: (), **dict(closest)}

    available_totals = [total for total in states if total > 0]
    if not available_totals:
        return []
    best_difference = min(abs(total - target_units) for total in available_totals)
    best_totals = [total for total in available_totals if abs(total - target_units) == best_difference]
    selected_total = rng.choice(best_totals)
    return [shuffled[index] for index in states[selected_total]]


def generate_demo_loading_orders(
    db: Session,
    *,
    depot_id: str,
    model: MLBehavioralModel,
    total_order_kl: float,
    random_seed: int | None = None,
) -> tuple[bytes, str]:
    total = _positive_demo_total(
        total_order_kl,
        code="INVALID_DEMO_TOTAL_ORDER",
        label="Total order",
        maximum=MAX_DEMO_TOTAL_ORDER_KL,
    )

    spbus = db.scalars(
        select(MasterSPBU)
        .where(MasterSPBU.primary_depot_id == depot_id, MasterSPBU.active_status == "ACTIVE")
        .order_by(MasterSPBU.spbu_code)
    ).all()
    if not spbus:
        raise HTTPException(
            status_code=409,
            detail={"code": "DEMO_SPBU_NOT_FOUND", "message": "No active SPBU is available for the selected depot."},
        )

    try:
        shifts = validate_shift_config(model.shift_definition_snapshot or [])
    except HTTPException as exc:
        raise HTTPException(status_code=409, detail={"code": "DEMO_SHIFT_NOT_FOUND", "message": "The selected model has no usable shift definition."}) from exc
    depot = db.get(MasterDepot, depot_id)
    try:
        depot_timezone = ZoneInfo(depot.timezone if depot and depot.timezone else "Asia/Jakarta")
    except ZoneInfoNotFoundError:
        depot_timezone = ZoneInfo("Asia/Jakarta")

    seed = random_seed if random_seed is not None else secrets.randbits(63)
    rng = random.Random(seed)
    order_count = math.ceil(total / DEMO_LOADING_ORDER_UNIT_KL)
    token = f"{seed % 1_000_000:06d}"
    planning_day = datetime.now(depot_timezone).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    rows: list[list] = []
    for index in range(order_count):
        remaining = total - (DEMO_LOADING_ORDER_UNIT_KL * index)
        quantity = min(DEMO_LOADING_ORDER_UNIT_KL, remaining)
        spbu = rng.choice(spbus)
        shift = shifts[index % len(shifts)]
        segment = rng.choice(shift["segments"])
        latest_minute = max(segment["start_minute"], segment["end_exclusive_minute"] - 1)
        minute_of_day = rng.randint(segment["start_minute"], latest_minute)
        start_datetime = planning_day + timedelta(minutes=minute_of_day)
        rows.append(
            [
                f"DEMO-LO-{token}-{index + 1:04d}",
                start_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                spbu.spbu_code,
                spbu.spbu_name or spbu.spbu_code,
                float(quantity),
            ]
        )

    content = workbook_bytes(
        [
            (
                "Loading Order",
                ["loading_order_no", "shipment_start_datetime", "spbu_no", "spbu_name", "order_quantity_kl"],
                rows,
            )
        ]
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return content, f"phase6-demo-loading-order-{timestamp}.xlsx"


def generate_demo_mt_availability(
    db: Session,
    *,
    depot_id: str,
    total_capacity_kl: float,
    random_seed: int | None = None,
) -> tuple[bytes, str]:
    target = _positive_demo_total(
        total_capacity_kl,
        code="INVALID_DEMO_MT_CAPACITY",
        label="Total available MT capacity",
        maximum=MAX_DEMO_TOTAL_MT_CAPACITY_KL,
    )
    mts = db.scalars(
        select(MasterMT)
        .where(
            MasterMT.depot_id == depot_id,
            MasterMT.active_status == "ACTIVE",
            MasterMT.vehicle_registration.is_not(None),
        )
        .order_by(MasterMT.vehicle_registration)
    ).all()
    candidates = []
    for mt in mts:
        capacity = mt_capacity_kl(mt.capacity_label, mt.vehicle_type_tag)
        if capacity is not None:
            candidates.append((mt, Decimal(str(capacity))))
    if not candidates:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEMO_MT_CAPACITY_NOT_FOUND",
                "message": "No active MT with a usable KL capacity is available for the selected depot.",
            },
        )

    depot = db.get(MasterDepot, depot_id)
    try:
        depot_timezone = ZoneInfo(depot.timezone if depot and depot.timezone else "Asia/Jakarta")
    except ZoneInfoNotFoundError:
        depot_timezone = ZoneInfo("Asia/Jakarta")
    seed = random_seed if random_seed is not None else secrets.randbits(63)
    rng = random.Random(seed)
    selected = _select_mts_near_capacity(candidates, target, rng)
    if not selected:
        raise HTTPException(
            status_code=409,
            detail={"code": "DEMO_MT_NOT_SELECTED", "message": "No MT could be selected for the requested capacity."},
        )

    planning_day = datetime.now(depot_timezone).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    rows = []
    for mt, capacity in selected:
        available_datetime = planning_day + timedelta(minutes=rng.randint(0, (24 * 60) - 1))
        rows.append(
            [
                mt.vehicle_registration,
                available_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                float(capacity),
            ]
        )
    content = workbook_bytes(
        [
            (
                "MT Availability",
                ["vehicle_registration_no", "initial_available_datetime", "capacity_kl"],
                rows,
            )
        ]
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return content, f"phase6-demo-mt-availability-{timestamp}.xlsx"
