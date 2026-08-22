from __future__ import annotations

import math
import random
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .departure_intelligence import validate_shift_config
from .models import MLBehavioralModel, MasterDepot, MasterSPBU
from .phase6_export import workbook_bytes


DEMO_LOADING_ORDER_UNIT_KL = Decimal("8")
MAX_DEMO_TOTAL_ORDER_KL = Decimal("40000")


def generate_demo_loading_orders(
    db: Session,
    *,
    depot_id: str,
    model: MLBehavioralModel,
    total_order_kl: float,
    random_seed: int | None = None,
) -> tuple[bytes, str]:
    try:
        total = Decimal(str(total_order_kl))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_DEMO_TOTAL_ORDER", "message": "Total order must be a valid number in KL."},
        ) from exc
    if not total.is_finite() or total <= 0 or total > MAX_DEMO_TOTAL_ORDER_KL:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_DEMO_TOTAL_ORDER",
                "message": f"Total order must be greater than 0 and no more than {MAX_DEMO_TOTAL_ORDER_KL} KL.",
            },
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
