from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from itertools import combinations
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FactLoadingOrderLine, FactShipment, FactShipmentSPBU, FactShipmentStop, MasterDepot, MasterMT, MasterProduct, MasterSPBU


ALGORITHM_VERSION = "pairing_v1"
TRANSITION_ALGORITHM_VERSION = "spbu_transition.consecutive_v1"
CONFIDENCE_THRESHOLDS = {
    "minimum_anchor_shipments": 5,
    "minimum_pair_count": 3,
    "low_pair_count": 10,
    "high_pair_count": 30,
}
CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INSUFFICIENT_DATA": 0}


def calculate_confidence(pair_count: int, shipment_a_count: int, shipment_b_count: int) -> dict:
    minimum_anchor = min(shipment_a_count, shipment_b_count)
    sample_score = min(1.0, pair_count / CONFIDENCE_THRESHOLDS["high_pair_count"])
    anchor_score = min(1.0, minimum_anchor / CONFIDENCE_THRESHOLDS["high_pair_count"])
    confidence_score = round((sample_score * 0.7) + (anchor_score * 0.3), 4)
    if minimum_anchor < CONFIDENCE_THRESHOLDS["minimum_anchor_shipments"] or pair_count < CONFIDENCE_THRESHOLDS["minimum_pair_count"]:
        level = "INSUFFICIENT_DATA"
    elif pair_count < CONFIDENCE_THRESHOLDS["low_pair_count"]:
        level = "LOW"
    elif pair_count < CONFIDENCE_THRESHOLDS["high_pair_count"]:
        level = "MEDIUM"
    else:
        level = "HIGH"
    return {"confidence_score": confidence_score, "confidence_level": level}


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    if left == right:
        raise ValueError("Self-pair is not a valid SPBU pair.")
    return tuple(sorted((left, right)))


def generate_canonical_pairs(spbu_ids: Iterable[str]) -> list[tuple[str, str]]:
    unique_ids = sorted(set(spbu_ids))
    return [canonical_pair(left, right) for left, right in combinations(unique_ids, 2)]


def percentage(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def build_pair_metrics(memberships_by_shipment: dict[str, set[str]], spbu_lookup: dict[str, dict], depot_id: str, start_date: date, end_date: date) -> list[dict]:
    total_shipment_count = len(memberships_by_shipment)
    shipment_counts: Counter[str] = Counter()
    pair_shipments: dict[tuple[str, str], set[str]] = defaultdict(set)
    for shipment_id, spbu_ids in memberships_by_shipment.items():
        shipment_counts.update(spbu_ids)
        for pair in generate_canonical_pairs(spbu_ids):
            pair_shipments[pair].add(shipment_id)

    rows: list[dict] = []
    calculated_at = utc_now_label()
    for (spbu_a_id, spbu_b_id), shipment_ids in pair_shipments.items():
        pair_count = len(shipment_ids)
        shipment_a_count = shipment_counts[spbu_a_id]
        shipment_b_count = shipment_counts[spbu_b_id]
        probability_b_given_a = percentage(pair_count, shipment_a_count)
        probability_a_given_b = percentage(pair_count, shipment_b_count)
        support = percentage(pair_count, total_shipment_count)
        lift = 0.0
        if total_shipment_count and shipment_a_count and shipment_b_count:
            lift = round((pair_count * total_shipment_count) / (shipment_a_count * shipment_b_count), 4)
        confidence = calculate_confidence(pair_count, shipment_a_count, shipment_b_count)
        rows.append(
            {
                "depot_id": depot_id,
                "spbu_a_id": spbu_a_id,
                "spbu_b_id": spbu_b_id,
                "spbu_a_code": spbu_lookup.get(spbu_a_id, {}).get("spbu_code", spbu_a_id),
                "spbu_a_name": spbu_lookup.get(spbu_a_id, {}).get("spbu_name"),
                "spbu_b_code": spbu_lookup.get(spbu_b_id, {}).get("spbu_code", spbu_b_id),
                "spbu_b_name": spbu_lookup.get(spbu_b_id, {}).get("spbu_name"),
                "pair_count": pair_count,
                "shipment_a_count": shipment_a_count,
                "shipment_b_count": shipment_b_count,
                "total_shipment_count": total_shipment_count,
                "probability_b_given_a": probability_b_given_a,
                "probability_a_given_b": probability_a_given_b,
                "support": support,
                "lift": lift,
                "observation_count": pair_count,
                "evidence_count": pair_count,
                "analysis_start_date": start_date.isoformat(),
                "analysis_end_date": end_date.isoformat(),
                "calculated_at": calculated_at,
                "algorithm_version": ALGORITHM_VERSION,
                **confidence,
            }
        )
    return rows


def build_transition_metrics(db: Session, depot_id: str, start_date: date, end_date: date, spbu_lookup: dict[str, dict]) -> list[dict]:
    rows = db.execute(
        select(FactShipmentStop, FactShipment)
        .join(FactShipment, FactShipment.shipment_id == FactShipmentStop.shipment_id)
        .where(
            FactShipment.depot_id == depot_id,
            FactShipment.operating_date >= start_date,
            FactShipment.operating_date <= end_date,
            FactShipmentStop.spbu_id.is_not(None),
            FactShipmentStop.stop_sequence.is_not(None),
        )
        .order_by(FactShipmentStop.shipment_id, FactShipmentStop.stop_sequence)
    ).all()
    sequences: dict[str, list[FactShipmentStop]] = defaultdict(list)
    for stop, _shipment in rows:
        sequences[stop.shipment_id or ""].append(stop)

    transition_counts: Counter[tuple[str, str]] = Counter()
    from_counts: Counter[str] = Counter()
    for stops in sequences.values():
        ordered = sorted(stops, key=lambda stop: (stop.stop_sequence if stop.stop_sequence is not None else 10**9, stop.arrival_datetime or datetime.min))
        for left, right in zip(ordered, ordered[1:]):
            if not left.spbu_id or not right.spbu_id or left.spbu_id == right.spbu_id:
                continue
            transition_counts[(left.spbu_id, right.spbu_id)] += 1
            from_counts[left.spbu_id] += 1

    calculated_at = utc_now_label()
    result = []
    for (from_spbu_id, to_spbu_id), transition_count in transition_counts.items():
        confidence = calculate_confidence(transition_count, from_counts[from_spbu_id], from_counts[from_spbu_id])
        result.append(
            {
                "depot_id": depot_id,
                "from_spbu_id": from_spbu_id,
                "from_spbu_code": spbu_lookup.get(from_spbu_id, {}).get("spbu_code", from_spbu_id),
                "to_spbu_id": to_spbu_id,
                "to_spbu_code": spbu_lookup.get(to_spbu_id, {}).get("spbu_code", to_spbu_id),
                "transition_count": transition_count,
                "observation_count": from_counts[from_spbu_id],
                "transition_probability": percentage(transition_count, from_counts[from_spbu_id]),
                "analysis_start_date": start_date.isoformat(),
                "analysis_end_date": end_date.isoformat(),
                "calculated_at": calculated_at,
                "algorithm_version": TRANSITION_ALGORITHM_VERSION,
                "confidence_score": confidence["confidence_score"],
                "confidence_level": confidence["confidence_level"],
            }
        )
    return sorted(result, key=lambda row: (-row["transition_count"], row["from_spbu_code"], row["to_spbu_code"]))


def build_pairing_intelligence_payload(
    db: Session,
    depot_id: str,
    start_date: date,
    end_date: date,
    product_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
    sort_column: str = "evidence_strength",
    sort_direction: str = "desc",
    search: str | None = None,
    selected_spbu_id: str | None = None,
    evidence_spbu_a_id: str | None = None,
    evidence_spbu_b_id: str | None = None,
    matrix_limit: int = 30,
    network_limit: int = 40,
) -> dict:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be greater than or equal to start_date.")
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="Depot not found.")
    product = db.get(MasterProduct, product_id) if product_id else None
    if product_id and not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    source_shipments = load_source_shipments(db, depot_id, start_date, end_date)
    source_shipment_ids = {shipment.shipment_id for shipment in source_shipments if shipment.shipment_id}
    raw_memberships, product_duplicate_count = load_membership_rows(db, depot_id, start_date, end_date, product_id)
    memberships_by_shipment, spbu_lookup, data_quality = prepare_memberships(source_shipments, raw_memberships, product_duplicate_count)
    pair_rows = build_pair_metrics(memberships_by_shipment, spbu_lookup, depot_id, start_date, end_date)
    filtered_pairs = filter_pair_rows(pair_rows, search)
    sorted_pairs = sort_pair_rows(filtered_pairs, sort_column, sort_direction)
    visible_pairs = sorted_pairs[offset : offset + limit]

    selected_spbu_id = selected_spbu_id if selected_spbu_id in spbu_lookup else choose_default_spbu(memberships_by_shipment, sorted_pairs)
    evidence_pair = normalize_evidence_pair(evidence_spbu_a_id, evidence_spbu_b_id, visible_pairs, sorted_pairs)
    evidence = (
        build_evidence_rows(db, memberships_by_shipment, spbu_lookup, evidence_pair[0], evidence_pair[1], product_id)
        if evidence_pair
        else {"pair": None, "rows": [], "distinct_shipment_count": 0}
    )

    transitions = build_transition_metrics(db, depot_id, start_date, end_date, spbu_lookup)
    calculated_at = utc_now_label()
    return {
        "phase": 3,
        "page_name": "SPBU Pairing Probability Intelligence",
        "algorithm_version": ALGORITHM_VERSION,
        "effective_filters": {
            "depot_id": depot.depot_id,
            "depot_name": depot.depot_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "product_id": product.product_id if product else None,
            "product_name": product.product_name if product else "All Products",
            "sort_column": sort_column,
            "sort_direction": "asc" if sort_direction == "asc" else "desc",
            "search": search or "",
        },
        "summary": build_summary(source_shipment_ids, memberships_by_shipment, sorted_pairs),
        "data_quality": data_quality,
        "distribution": build_probability_distribution(sorted_pairs),
        "pairs": visible_pairs,
        "total": len(filtered_pairs),
        "limit": limit,
        "offset": offset,
        "matrix": build_matrix(sorted_pairs, memberships_by_shipment, spbu_lookup, selected_spbu_id, matrix_limit),
        "network": build_network(sorted_pairs, memberships_by_shipment, spbu_lookup, selected_spbu_id, network_limit),
        "detail": build_spbu_detail(selected_spbu_id, memberships_by_shipment, sorted_pairs, spbu_lookup, depot.depot_name),
        "evidence": evidence,
        "transitions": transitions[:50],
        "traceability": {
            "depot_id": depot.depot_id,
            "analysis_start_date": start_date.isoformat(),
            "analysis_end_date": end_date.isoformat(),
            "observation_count": len(memberships_by_shipment),
            "calculated_at": calculated_at,
            "algorithm_version": ALGORITHM_VERSION,
        },
        "notes": [
            "Pairing is undirected same-shipment co-occurrence; conditional probabilities are directional.",
            "A-B pairing is not the same analytical object as A -> B consecutive GPS transition.",
            "All Products uses fact_shipment_spbu; product-specific analysis uses distinct shipment_id + spbu_id from LO lines for the selected product.",
        ],
    }


def load_source_shipments(db: Session, depot_id: str, start_date: date, end_date: date) -> list[FactShipment]:
    return db.scalars(
        select(FactShipment).where(
            FactShipment.depot_id == depot_id,
            FactShipment.operating_date >= start_date,
            FactShipment.operating_date <= end_date,
        )
    ).all()


def load_membership_rows(db: Session, depot_id: str, start_date: date, end_date: date, product_id: str | None) -> tuple[list[tuple], int]:
    if product_id:
        rows = db.execute(
            select(FactLoadingOrderLine.shipment_id, FactLoadingOrderLine.spbu_id, FactShipment, MasterSPBU)
            .join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id)
            .outerjoin(MasterSPBU, MasterSPBU.spbu_id == FactLoadingOrderLine.spbu_id)
            .where(
                FactShipment.depot_id == depot_id,
                FactShipment.operating_date >= start_date,
                FactShipment.operating_date <= end_date,
                FactLoadingOrderLine.product_id == product_id,
            )
        ).all()
        duplicate_count = max(0, len(rows) - len({(shipment_id, spbu_id) for shipment_id, spbu_id, *_ in rows if shipment_id and spbu_id}))
        return rows, duplicate_count
    rows = db.execute(
        select(FactShipmentSPBU.shipment_id, FactShipmentSPBU.spbu_id, FactShipment, MasterSPBU)
        .join(FactShipment, FactShipment.shipment_id == FactShipmentSPBU.shipment_id)
        .outerjoin(MasterSPBU, MasterSPBU.spbu_id == FactShipmentSPBU.spbu_id)
        .where(
            FactShipment.depot_id == depot_id,
            FactShipment.operating_date >= start_date,
            FactShipment.operating_date <= end_date,
        )
    ).all()
    return rows, 0


def prepare_memberships(source_shipments: list[FactShipment], raw_memberships: list[tuple], product_duplicate_count: int) -> tuple[dict[str, set[str]], dict[str, dict], dict]:
    source_shipment_ids = {shipment.shipment_id for shipment in source_shipments if shipment.shipment_id}
    memberships_by_shipment: dict[str, set[str]] = defaultdict(set)
    spbu_lookup: dict[str, dict] = {}
    unknown_spbu = 0
    missing_key = 0
    for shipment_id, spbu_id, shipment, spbu in raw_memberships:
        if not shipment_id or not spbu_id:
            missing_key += 1
            continue
        if not shipment or not shipment.depot_id or not shipment.operating_date:
            missing_key += 1
            continue
        if not spbu:
            unknown_spbu += 1
            continue
        memberships_by_shipment[shipment_id].add(spbu_id)
        spbu_lookup[spbu_id] = {"spbu_id": spbu_id, "spbu_code": spbu.spbu_code, "spbu_name": spbu.spbu_name}

    eligible_shipment_ids = set(memberships_by_shipment)
    no_valid_membership = len(source_shipment_ids - eligible_shipment_ids)
    excluded = no_valid_membership + unknown_spbu + missing_key
    exclusion_reasons = [
        {"reason": "No valid SPBU membership", "count": no_valid_membership},
        {"reason": "Unknown SPBU", "count": unknown_spbu},
        {"reason": "Missing mandatory analytical keys", "count": missing_key},
        {"reason": "Duplicate shipment-SPBU/product membership deduplicated", "count": product_duplicate_count},
    ]
    return (
        dict(memberships_by_shipment),
        spbu_lookup,
        {
            "source_shipments": len(source_shipment_ids),
            "eligible_shipments": len(eligible_shipment_ids),
            "excluded_shipments": max(0, excluded),
            "exclusion_reasons": [row for row in exclusion_reasons if row["count"] > 0],
        },
    )


def build_summary(source_shipment_ids: set[str], memberships_by_shipment: dict[str, set[str]], pair_rows: list[dict]) -> dict:
    spbu_ids = {spbu_id for spbus in memberships_by_shipment.values() for spbu_id in spbus}
    spbu_per_shipment = [len(spbus) for spbus in memberships_by_shipment.values()]
    return {
        "total_shipments": len(memberships_by_shipment),
        "source_shipments": len(source_shipment_ids),
        "multi_spbu_shipments": sum(1 for count in spbu_per_shipment if count > 1),
        "unique_spbu": len(spbu_ids),
        "unique_spbu_pairs": len(pair_rows),
        "high_confidence_pairs": sum(1 for row in pair_rows if row["confidence_level"] == "HIGH"),
        "average_spbu_per_shipment": round(sum(spbu_per_shipment) / len(spbu_per_shipment), 2) if spbu_per_shipment else 0.0,
    }


def filter_pair_rows(pair_rows: list[dict], search: str | None) -> list[dict]:
    if not search or not search.strip():
        return pair_rows
    needle = search.strip().lower()
    return [
        row
        for row in pair_rows
        if needle in str(row["spbu_a_code"]).lower()
        or needle in str(row.get("spbu_a_name") or "").lower()
        or needle in str(row["spbu_b_code"]).lower()
        or needle in str(row.get("spbu_b_name") or "").lower()
    ]


def sort_pair_rows(pair_rows: list[dict], sort_column: str, sort_direction: str) -> list[dict]:
    reverse = sort_direction != "asc"

    def evidence_key(row: dict):
        return (CONFIDENCE_RANK.get(row["confidence_level"], 0), row["pair_count"], row["probability_b_given_a"], row["lift"])

    if sort_column == "evidence_strength":
        return sorted(pair_rows, key=evidence_key, reverse=True)
    sortable = {
        "pair_count",
        "probability_b_given_a",
        "probability_a_given_b",
        "support",
        "lift",
        "confidence_score",
        "spbu_a_code",
        "spbu_b_code",
    }
    key_name = sort_column if sort_column in sortable else "pair_count"
    return sorted(pair_rows, key=lambda row: row.get(key_name) or 0, reverse=reverse)


def build_probability_distribution(pair_rows: list[dict]) -> list[dict]:
    buckets = [
        ("0-20%", 0.0, 0.2),
        ("20-40%", 0.2, 0.4),
        ("40-60%", 0.4, 0.6),
        ("60-80%", 0.6, 0.8),
        ("80-100%", 0.8, 1.000001),
    ]
    return [
        {
            "name": label,
            "value": sum(1 for row in pair_rows if lower <= row["probability_b_given_a"] < upper),
        }
        for label, lower, upper in buckets
    ]


def choose_default_spbu(memberships_by_shipment: dict[str, set[str]], pair_rows: list[dict]) -> str | None:
    if pair_rows:
        return pair_rows[0]["spbu_a_id"]
    counts = Counter(spbu_id for spbus in memberships_by_shipment.values() for spbu_id in spbus)
    return counts.most_common(1)[0][0] if counts else None


def selected_spbu_candidates(selected_spbu_id: str | None, memberships_by_shipment: dict[str, set[str]], pair_rows: list[dict], limit: int) -> list[str]:
    shipment_counts = Counter(spbu_id for spbus in memberships_by_shipment.values() for spbu_id in spbus)
    if selected_spbu_id:
        neighbors = []
        for row in pair_rows:
            if row["spbu_a_id"] == selected_spbu_id:
                neighbors.append((row["spbu_b_id"], row["pair_count"]))
            elif row["spbu_b_id"] == selected_spbu_id:
                neighbors.append((row["spbu_a_id"], row["pair_count"]))
        ordered = [selected_spbu_id] + [spbu_id for spbu_id, _count in sorted(neighbors, key=lambda item: -item[1])[: max(0, limit - 1)]]
        return list(dict.fromkeys(ordered))
    return [spbu_id for spbu_id, _count in shipment_counts.most_common(limit)]


def build_matrix(pair_rows: list[dict], memberships_by_shipment: dict[str, set[str]], spbu_lookup: dict[str, dict], selected_spbu_id: str | None, limit: int) -> dict:
    spbu_ids = selected_spbu_candidates(selected_spbu_id, memberships_by_shipment, pair_rows, max(2, min(limit, 60)))
    labels = [spbu_lookup.get(spbu_id, {}).get("spbu_code", spbu_id) for spbu_id in spbu_ids]
    pair_lookup = {(row["spbu_a_id"], row["spbu_b_id"]): row for row in pair_rows}
    data = []
    for row_index, anchor_id in enumerate(spbu_ids):
        for col_index, candidate_id in enumerate(spbu_ids):
            if anchor_id == candidate_id:
                continue
            pair = pair_lookup.get(canonical_pair(anchor_id, candidate_id))
            if not pair:
                continue
            probability = pair["probability_b_given_a"] if pair["spbu_a_id"] == anchor_id else pair["probability_a_given_b"]
            reverse_probability = pair["probability_a_given_b"] if pair["spbu_a_id"] == anchor_id else pair["probability_b_given_a"]
            data.append(
                [
                    col_index,
                    row_index,
                    probability,
                    pair["pair_count"],
                    reverse_probability,
                    pair["support"],
                    pair["lift"],
                    pair["observation_count"],
                    pair["confidence_level"],
                ]
            )
    return {"spbu_ids": spbu_ids, "x_axis": labels, "y_axis": labels, "data": data, "selected_spbu_id": selected_spbu_id}


def build_network(pair_rows: list[dict], memberships_by_shipment: dict[str, set[str]], spbu_lookup: dict[str, dict], selected_spbu_id: str | None, limit: int) -> dict:
    if selected_spbu_id:
        edges = [row for row in pair_rows if row["spbu_a_id"] == selected_spbu_id or row["spbu_b_id"] == selected_spbu_id][:limit]
    else:
        edges = pair_rows[:limit]
    node_ids = sorted({row["spbu_a_id"] for row in edges} | {row["spbu_b_id"] for row in edges})
    shipment_counts = Counter(spbu_id for spbus in memberships_by_shipment.values() for spbu_id in spbus)
    nodes = [
        {
            "id": spbu_id,
            "name": spbu_lookup.get(spbu_id, {}).get("spbu_code", spbu_id),
            "value": shipment_counts[spbu_id],
            "symbolSize": max(18, min(54, 14 + shipment_counts[spbu_id] * 2)),
        }
        for spbu_id in node_ids
    ]
    return {
        "nodes": nodes,
        "edges": [
            {
                "source": row["spbu_a_id"],
                "target": row["spbu_b_id"],
                "value": row["pair_count"],
                "lineStyle": {"width": max(1, min(8, row["pair_count"] / 4)), "opacity": 0.72},
                "label": f"{row['spbu_a_code']} - {row['spbu_b_code']}",
                "metrics": row,
            }
            for row in edges
        ],
    }


def build_spbu_detail(selected_spbu_id: str | None, memberships_by_shipment: dict[str, set[str]], pair_rows: list[dict], spbu_lookup: dict[str, dict], depot_name: str) -> dict | None:
    if not selected_spbu_id:
        return None
    shipment_count = sum(1 for spbus in memberships_by_shipment.values() if selected_spbu_id in spbus)
    top_pairs = []
    for row in pair_rows:
        if row["spbu_a_id"] == selected_spbu_id:
            top_pairs.append({**row, "candidate_spbu_id": row["spbu_b_id"], "candidate_spbu_code": row["spbu_b_code"], "pair_probability": row["probability_b_given_a"], "reverse_probability": row["probability_a_given_b"]})
        elif row["spbu_b_id"] == selected_spbu_id:
            top_pairs.append({**row, "candidate_spbu_id": row["spbu_a_id"], "candidate_spbu_code": row["spbu_a_code"], "pair_probability": row["probability_a_given_b"], "reverse_probability": row["probability_b_given_a"]})
    return {
        "spbu_id": selected_spbu_id,
        "spbu_code": spbu_lookup.get(selected_spbu_id, {}).get("spbu_code", selected_spbu_id),
        "spbu_name": spbu_lookup.get(selected_spbu_id, {}).get("spbu_name"),
        "depot_name": depot_name,
        "historical_shipments": shipment_count,
        "top_pairs": top_pairs[:10],
    }


def normalize_evidence_pair(left: str | None, right: str | None, visible_pairs: list[dict], sorted_pairs: list[dict]) -> tuple[str, str] | None:
    if left and right and left != right:
        return canonical_pair(left, right)
    row = visible_pairs[0] if visible_pairs else sorted_pairs[0] if sorted_pairs else None
    if not row:
        return None
    return row["spbu_a_id"], row["spbu_b_id"]


def build_evidence_rows(db: Session, memberships_by_shipment: dict[str, set[str]], spbu_lookup: dict[str, dict], spbu_a_id: str, spbu_b_id: str, product_id: str | None) -> dict:
    evidence_shipment_ids = sorted(
        shipment_id for shipment_id, spbus in memberships_by_shipment.items() if spbu_a_id in spbus and spbu_b_id in spbus
    )
    if not evidence_shipment_ids:
        return {"pair": {"spbu_a_id": spbu_a_id, "spbu_b_id": spbu_b_id}, "rows": [], "distinct_shipment_count": 0}
    shipment_rows = db.execute(
        select(FactShipment, MasterMT)
        .outerjoin(MasterMT, MasterMT.mt_id == FactShipment.mt_id)
        .where(FactShipment.shipment_id.in_(evidence_shipment_ids))
        .order_by(FactShipment.operating_date, FactShipment.source_shipment_id)
    ).all()
    line_stmt = select(FactLoadingOrderLine).where(FactLoadingOrderLine.shipment_id.in_(evidence_shipment_ids))
    if product_id:
        line_stmt = line_stmt.where(FactLoadingOrderLine.product_id == product_id)
    lines = db.scalars(line_stmt).all()
    lines_by_shipment: dict[str, list[FactLoadingOrderLine]] = defaultdict(list)
    for line in lines:
        lines_by_shipment[line.shipment_id].append(line)
    rows = []
    for shipment, mt in shipment_rows[:100]:
        shipment_spbus = sorted(memberships_by_shipment.get(shipment.shipment_id, set()), key=lambda spbu_id: spbu_lookup.get(spbu_id, {}).get("spbu_code", spbu_id))
        shipment_lines = lines_by_shipment.get(shipment.shipment_id, [])
        rows.append(
            {
                "shipment_id": shipment.shipment_id,
                "source_shipment_id": shipment.source_shipment_id,
                "date": shipment.operating_date.isoformat() if shipment.operating_date else None,
                "vehicle_registration": shipment.vehicle_registration or (mt.vehicle_registration if mt else None),
                "gate_out": shipment.gate_out_datetime.isoformat() if shipment.gate_out_datetime else None,
                "spbu_in_shipment": [spbu_lookup.get(spbu_id, {}).get("spbu_code", spbu_id) for spbu_id in shipment_spbus],
                "products": sorted({line.source_product_name or line.product_id or "UNKNOWN" for line in shipment_lines}),
                "quantity": round(sum(float(line.quantity or 0) for line in shipment_lines), 2),
            }
        )
    return {
        "pair": {
            "spbu_a_id": spbu_a_id,
            "spbu_a_code": spbu_lookup.get(spbu_a_id, {}).get("spbu_code", spbu_a_id),
            "spbu_b_id": spbu_b_id,
            "spbu_b_code": spbu_lookup.get(spbu_b_id, {}).get("spbu_code", spbu_b_id),
        },
        "rows": rows,
        "distinct_shipment_count": len(evidence_shipment_ids),
    }


def build_pairing_date_availability(db: Session, depot_id: str) -> dict:
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="Depot not found.")
    rows = db.execute(
        select(FactShipment.operating_date, func.count())
        .where(FactShipment.depot_id == depot_id, FactShipment.operating_date.is_not(None))
        .group_by(FactShipment.operating_date)
        .order_by(FactShipment.operating_date)
    ).all()
    dates = [{"date": operating_date.isoformat(), "shipment_count": count} for operating_date, count in rows]
    return {
        "depot_id": depot.depot_id,
        "depot_name": depot.depot_name,
        "available_dates": [item["date"] for item in dates],
        "dates": dates,
        "min_date": dates[0]["date"] if dates else None,
        "max_date": dates[-1]["date"] if dates else None,
    }


def utc_now_label() -> str:
    return datetime.now(timezone.utc).isoformat()
