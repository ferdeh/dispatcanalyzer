from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import math
import statistics

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FactLoadingOrderLine, FactShipment, FactShipmentSPBU, MasterDepot, MasterMT, MasterProduct, MasterSPBU


ALGORITHM_VERSION = "spbu_mt_affinity.jsd_v1"
CLASSIFICATION_THRESHOLDS = {
    "very_high_consistency": 80.0,
    "high_consistency": 65.0,
    "medium": 40.0,
    "high_variability": 20.0,
}
CONFIDENCE_THRESHOLDS = {"medium": 40.0, "high": 70.0}
SHIFT_THRESHOLDS = {"stable": 0.10, "minor": 0.25, "moderate": 0.50}
CONFIDENCE_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator else 0.0


def concentration_metrics(counts: list[int]) -> dict:
    total = sum(counts)
    probabilities = [count / total for count in counts if count > 0 and total > 0]
    unique_count = len(probabilities)
    hhi = sum(probability**2 for probability in probabilities)
    if unique_count <= 1:
        normalized_hhi = 1.0 if unique_count == 1 else 0.0
        normalized_entropy = 0.0
    else:
        normalized_hhi = (hhi - (1 / unique_count)) / (1 - (1 / unique_count))
        entropy = -sum(probability * math.log(probability) for probability in probabilities)
        normalized_entropy = entropy / math.log(unique_count)
    return {
        "hhi": round(hhi, 6),
        "normalized_hhi": round(max(0.0, min(1.0, normalized_hhi)), 6),
        "normalized_entropy": round(max(0.0, min(1.0, normalized_entropy)), 6),
        "consistency_score": round(100 * max(0.0, min(1.0, normalized_hhi)), 2),
        "variability_score": round(100 * max(0.0, min(1.0, normalized_entropy)), 2),
    }


def consistency_classification(score: float) -> str:
    if score >= CLASSIFICATION_THRESHOLDS["very_high_consistency"]:
        return "VERY HIGH CONSISTENCY"
    if score >= CLASSIFICATION_THRESHOLDS["high_consistency"]:
        return "HIGH CONSISTENCY"
    if score >= CLASSIFICATION_THRESHOLDS["medium"]:
        return "MEDIUM"
    if score >= CLASSIFICATION_THRESHOLDS["high_variability"]:
        return "HIGH VARIABILITY"
    return "VERY HIGH VARIABILITY"


def analytical_pattern(dominant_probability: float, top3_share: float, consistency_score: float) -> str:
    if dominant_probability >= 0.75 or consistency_score >= 80:
        return "DEDICATED-LIKE"
    if top3_share >= 0.75 or consistency_score >= 40:
        return "PREFERRED-FLEET"
    return "FLEXIBLE"


def confidence_metrics(
    shipment_count: int,
    operating_day_count: int,
    first_observed: date,
    last_observed: date,
    analysis_start: date,
    analysis_end: date,
    active_period_count: int,
    possible_period_count: int = 4,
) -> dict:
    analysis_days = max(1, (analysis_end - analysis_start).days + 1)
    observed_span = max(1, (last_observed - first_observed).days + 1)
    recency_gap = max(0, (analysis_end - last_observed).days)
    components = {
        "sample": min(1.0, shipment_count / 50),
        "operating_days": min(1.0, operating_day_count / 20),
        "date_coverage": min(1.0, observed_span / analysis_days),
        "recency": max(0.0, 1.0 - (recency_gap / max(14, min(60, analysis_days)))),
        "temporal_coverage": min(1.0, active_period_count / max(1, min(4, possible_period_count))),
    }
    score = round(
        100
        * (
            components["sample"] * 0.40
            + components["operating_days"] * 0.20
            + components["date_coverage"] * 0.15
            + components["recency"] * 0.10
            + components["temporal_coverage"] * 0.15
        ),
        2,
    )
    level = "HIGH" if score >= CONFIDENCE_THRESHOLDS["high"] else "MEDIUM" if score >= CONFIDENCE_THRESHOLDS["medium"] else "LOW"
    return {"confidence_score": score, "confidence_level": level, "confidence_components": {key: round(value, 4) for key, value in components.items()}}


def resolve_temporal_bucket(requested: str, start_date: date, end_date: date) -> str:
    normalized = requested.upper()
    if normalized in {"DAILY", "WEEKLY", "MONTHLY"}:
        return normalized
    day_count = (end_date - start_date).days + 1
    if day_count <= 14:
        return "DAILY"
    if day_count <= 120:
        return "WEEKLY"
    return "MONTHLY"


def period_start(value: date, bucket: str) -> date:
    if bucket == "DAILY":
        return value
    if bucket == "WEEKLY":
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def period_end(value: date, bucket: str) -> date:
    if bucket == "DAILY":
        return value
    if bucket == "WEEKLY":
        return value + timedelta(days=6)
    next_month = (value.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def distribution_from_counter(counter: Counter[str]) -> dict[str, float]:
    total = sum(counter.values())
    return {key: value / total for key, value in counter.items()} if total else {}


def jensen_shannon_distance(left: dict[str, float], right: dict[str, float]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    midpoint = {key: (left.get(key, 0.0) + right.get(key, 0.0)) / 2 for key in keys}

    def divergence(source: dict[str, float]) -> float:
        return sum(value * math.log2(value / midpoint[key]) for key, value in source.items() if value > 0 and midpoint[key] > 0)

    return round(math.sqrt(max(0.0, (divergence(left) + divergence(right)) / 2)), 6)


def pattern_shift_level(distance: float) -> str:
    if distance <= SHIFT_THRESHOLDS["stable"]:
        return "STABLE"
    if distance <= SHIFT_THRESHOLDS["minor"]:
        return "MINOR SHIFT"
    if distance <= SHIFT_THRESHOLDS["moderate"]:
        return "MODERATE SHIFT"
    return "MAJOR SHIFT"


def _dominant(counter: Counter[str]) -> tuple[str | None, int]:
    if not counter:
        return None, 0
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0]


def temporal_metrics(period_counters: dict[date, Counter[str]], first_half: Counter[str], second_half: Counter[str], recent: Counter[str], previous: Counter[str]) -> dict:
    ordered = [(bucket, period_counters[bucket]) for bucket in sorted(period_counters) if period_counters[bucket]]
    dominant_ids = [_dominant(counter)[0] for _bucket, counter in ordered]
    dominant_mode_count = Counter(dominant_ids).most_common(1)[0][1] if dominant_ids else 0
    persistence = ratio(dominant_mode_count, len(dominant_ids))
    consecutive_distances = [
        jensen_shannon_distance(distribution_from_counter(left), distribution_from_counter(right))
        for (_left_bucket, left), (_right_bucket, right) in zip(ordered, ordered[1:])
    ]
    mean_distance = statistics.fmean(consecutive_distances) if consecutive_distances else 0.0
    stability_score = round(100 * ((1 - mean_distance) * 0.70 + persistence * 0.30), 2) if ordered else 0.0
    half_distance = jensen_shannon_distance(distribution_from_counter(first_half), distribution_from_counter(second_half)) if first_half and second_half else 0.0
    recent_distance = jensen_shannon_distance(distribution_from_counter(previous), distribution_from_counter(recent)) if previous and recent else 0.0
    shift_distance = max(half_distance, recent_distance, max(consecutive_distances, default=0.0))
    previous_dominant = _dominant(previous)[0] if previous else (_dominant(ordered[-2][1])[0] if len(ordered) > 1 else None)
    recent_dominant = _dominant(recent)[0] if recent else (_dominant(ordered[-1][1])[0] if ordered else None)
    return {
        "dominant_mt_persistence": round(100 * persistence, 2),
        "temporal_stability_score": max(0.0, min(100.0, stability_score)),
        "pattern_shift_distance": round(shift_distance, 6),
        "pattern_shift_level": pattern_shift_level(shift_distance),
        "previous_dominant_id": previous_dominant,
        "recent_dominant_id": recent_dominant,
        "active_period_count": len(ordered),
    }


def build_affinity_intelligence_payload(
    db: Session,
    depot_id: str,
    start_date: date,
    end_date: date,
    product_id: str | None = None,
    minimum_observations: int = 1,
    confidence_filter: str = "ALL",
    temporal_bucket: str = "AUTO",
    recent_days: int = 7,
    top_n: int = 5,
    selected_spbu_id: str | None = None,
    selected_mt_id: str | None = None,
    edge_metric: str = "SHIPMENT_COUNT",
    network_limit: int = 100,
) -> dict:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be greater than or equal to start_date.")
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="Depot not found.")
    product = db.get(MasterProduct, product_id) if product_id else None
    if product_id and not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    bucket = resolve_temporal_bucket(temporal_bucket, start_date, end_date)
    source_shipments = db.scalars(
        select(FactShipment).where(
            FactShipment.depot_id == depot_id,
            FactShipment.operating_date >= start_date,
            FactShipment.operating_date <= end_date,
        )
    ).all()
    raw_rows = _load_observation_rows(db, depot_id, start_date, end_date, product_id)
    prepared = _prepare_observations(source_shipments, raw_rows)
    observations = prepared["observations"]
    spbu_lookup = prepared["spbu_lookup"]
    mt_lookup = prepared["mt_lookup"]

    profile_data = _build_profiles(observations, spbu_lookup, mt_lookup, start_date, end_date, bucket, recent_days)
    all_profiles = profile_data["profiles"]
    filtered_profiles = [
        profile
        for profile in all_profiles
        if profile["shipment_count"] >= minimum_observations and _passes_confidence(profile["confidence_level"], confidence_filter)
    ]
    eligible_spbu_ids = {profile["spbu_id"] for profile in filtered_profiles}
    pair_rows = [row for row in profile_data["pairs"] if row["spbu_id"] in eligible_spbu_ids]

    selected_spbu_id = selected_spbu_id if selected_spbu_id in eligible_spbu_ids else (filtered_profiles[0]["spbu_id"] if filtered_profiles else None)
    selected_profile = next((row for row in filtered_profiles if row["spbu_id"] == selected_spbu_id), None)
    if selected_mt_id not in mt_lookup:
        selected_mt_id = selected_profile["dominant_mt_id"] if selected_profile else (pair_rows[0]["mt_id"] if pair_rows else None)

    reverse_detail = _build_reverse_detail(observations, selected_mt_id, spbu_lookup, mt_lookup, start_date, end_date, bucket, recent_days)
    evidence = _build_evidence(db, observations, selected_spbu_id, selected_mt_id, depot.depot_name, product_id)
    recent_comparison = _build_recent_comparison(observations, selected_spbu_id, mt_lookup, start_date, end_date, recent_days)
    temporal_profile = [row for row in profile_data["temporal_profiles"] if row["spbu_id"] == selected_spbu_id]
    display_pairs = [row for row in pair_rows if row["spbu_id"] == selected_spbu_id]
    if top_n > 0:
        display_pairs = display_pairs[:top_n]

    calculated_at = datetime.now(timezone.utc).isoformat()
    return {
        "phase": 4,
        "page_name": "SPBU-MT Historical Affinity & Stability Intelligence",
        "algorithm_version": ALGORITHM_VERSION,
        "effective_filters": {
            "depot_id": depot_id,
            "depot_name": depot.depot_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "product_id": product_id,
            "product_name": product.product_name if product else "All Products",
            "minimum_observations": minimum_observations,
            "confidence": confidence_filter.upper(),
            "temporal_bucket_requested": temporal_bucket.upper(),
            "temporal_bucket_used": bucket,
            "recent_days": recent_days,
            "top_n": top_n if top_n > 0 else "ALL",
            "edge_metric": edge_metric.upper(),
        },
        "summary": _build_summary(observations, filtered_profiles, pair_rows),
        "data_quality": prepared["data_quality"],
        "profiles": filtered_profiles,
        "rankings": _build_rankings(filtered_profiles, mt_lookup),
        "scatter": [
            {
                "spbu_id": row["spbu_id"],
                "spbu_code": row["spbu_code"],
                "spbu_name": row.get("spbu_name"),
                "value": [row["unique_mt_count"], row["consistency_score"], row["shipment_count"]],
                **{key: row[key] for key in ["shipment_count", "dominant_mt_label", "dominant_mt_probability", "consistency_score", "variability_score", "temporal_stability_score", "confidence_level"]},
            }
            for row in filtered_profiles
        ],
        "pattern_matrix": _build_pattern_matrix(filtered_profiles),
        "selected_spbu_profile": selected_profile,
        "affinity_distribution": display_pairs,
        "fleet_affinity_vector": selected_profile["fleet_affinity_vector"] if selected_profile else {},
        "temporal_profile": temporal_profile,
        "recent_comparison": recent_comparison,
        "reverse_detail": reverse_detail,
        "network": _build_network(pair_rows, filtered_profiles, spbu_lookup, mt_lookup, selected_spbu_id, selected_mt_id, edge_metric, network_limit),
        "evidence": evidence,
        "traceability": {
            "unique_observation_count": len(observations),
            "calculated_at": calculated_at,
            "algorithm_version": ALGORITHM_VERSION,
            "observation_key": ["depot_id", "shipment_id", "spbu_id", "mt_id"],
        },
        "methodology": {
            "consistency": "100 x normalized HHI; N=1 is deterministically 100.",
            "variability": "100 x normalized Shannon entropy; N=1 is deterministically 0.",
            "temporal_stability": "70% mean consecutive-period Jensen-Shannon similarity + 30% modal dominant-MT persistence.",
            "pattern_shift": "Maximum measured Jensen-Shannon distance across consecutive buckets, first-vs-second half, and prior-vs-recent distributions.",
            "confidence": "40% shipment sample, 20% operating days, 15% date coverage, 10% recency, and 15% temporal coverage; never multiplied into pattern metrics.",
            "thresholds": {"classification": CLASSIFICATION_THRESHOLDS, "confidence": CONFIDENCE_THRESHOLDS, "pattern_shift": SHIFT_THRESHOLDS},
        },
        "notes": [
            "All metrics describe historical dispatch behavior; no future vehicle recommendation is produced.",
            "Master data is used only for identifiers and display metadata.",
            "Product filtering occurs before final unique shipment-SPBU-MT deduplication.",
        ],
    }


def _load_observation_rows(db: Session, depot_id: str, start_date: date, end_date: date, product_id: str | None) -> list[tuple]:
    membership = FactLoadingOrderLine if product_id else FactShipmentSPBU
    statement = (
        select(membership.shipment_id, membership.spbu_id, FactShipment, MasterSPBU, MasterMT)
        .join(FactShipment, FactShipment.shipment_id == membership.shipment_id)
        .outerjoin(MasterSPBU, MasterSPBU.spbu_id == membership.spbu_id)
        .outerjoin(MasterMT, MasterMT.mt_id == FactShipment.mt_id)
        .where(FactShipment.depot_id == depot_id, FactShipment.operating_date >= start_date, FactShipment.operating_date <= end_date)
    )
    if product_id:
        statement = statement.where(FactLoadingOrderLine.product_id == product_id)
    return db.execute(statement).all()


def _prepare_observations(source_shipments: list[FactShipment], raw_rows: list[tuple]) -> dict:
    observations_by_key: dict[tuple[str, str, str, str], dict] = {}
    spbu_lookup: dict[str, dict] = {}
    mt_lookup: dict[str, dict] = {}
    bad: dict[str, set[str]] = defaultdict(set)
    raw_valid_key_count = 0
    for shipment_id, spbu_id, shipment, spbu, mt in raw_rows:
        identity = shipment_id or "<missing>"
        if not shipment_id or not shipment or not shipment.depot_id:
            bad["Invalid shipment or depot"].add(identity)
            continue
        if not shipment.operating_date:
            bad["Invalid operating date"].add(identity)
            continue
        if not spbu_id or not spbu:
            bad["Unmapped SPBU"].add(identity)
            continue
        if not shipment.mt_id or not mt:
            bad["Unmapped or missing MT"].add(identity)
            continue
        raw_valid_key_count += 1
        key = (shipment.depot_id, shipment_id, spbu_id, shipment.mt_id)
        observations_by_key[key] = {
            "depot_id": shipment.depot_id,
            "shipment_id": shipment_id,
            "source_shipment_id": shipment.source_shipment_id,
            "operating_date": shipment.operating_date,
            "gate_out": shipment.gate_out_datetime,
            "spbu_id": spbu_id,
            "mt_id": shipment.mt_id,
        }
        spbu_lookup[spbu_id] = {"spbu_id": spbu_id, "spbu_code": spbu.spbu_code, "spbu_name": spbu.spbu_name}
        mt_lookup[shipment.mt_id] = {
            "mt_id": shipment.mt_id,
            "vehicle_registration": mt.vehicle_registration,
            "vehicle_name": mt.vehicle_name_raw,
            "mt_label": mt.vehicle_registration or mt.vehicle_name_raw or shipment.mt_id,
        }
    observations = list(observations_by_key.values())
    source_ids = {shipment.shipment_id for shipment in source_shipments if shipment.shipment_id}
    eligible_ids = {row["shipment_id"] for row in observations}
    specifically_excluded = set().union(*bad.values()) if bad else set()
    no_observation = source_ids - eligible_ids - specifically_excluded
    if no_observation:
        bad["No eligible SPBU-MT observation"].update(no_observation)
    duplicate_count = max(0, raw_valid_key_count - len(observations))
    reasons = [{"reason": reason, "count": len(ids)} for reason, ids in bad.items() if ids]
    if duplicate_count:
        reasons.append({"reason": "Duplicate shipment-SPBU-MT rows deduplicated", "count": duplicate_count})
    excluded_ids = set().union(*bad.values()) if bad else set()
    return {
        "observations": observations,
        "spbu_lookup": spbu_lookup,
        "mt_lookup": mt_lookup,
        "data_quality": {
            "source_shipments": len(source_ids),
            "eligible_shipments": len(eligible_ids),
            "excluded_shipments": len(source_ids & excluded_ids),
            "eligible_pct": round(100 * len(eligible_ids) / len(source_ids), 2) if source_ids else 0.0,
            "duplicate_observations_removed": duplicate_count,
            "exclusion_reasons": reasons,
        },
    }


def _build_profiles(observations: list[dict], spbu_lookup: dict, mt_lookup: dict, start_date: date, end_date: date, bucket: str, recent_days: int) -> dict:
    by_spbu: dict[str, list[dict]] = defaultdict(list)
    by_mt_shipments: dict[str, set[str]] = defaultdict(set)
    pair_observations: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in observations:
        by_spbu[row["spbu_id"]].append(row)
        by_mt_shipments[row["mt_id"]].add(row["shipment_id"])
        pair_observations[(row["spbu_id"], row["mt_id"])].append(row)

    recent_start = max(start_date, end_date - timedelta(days=max(1, recent_days) - 1))
    midpoint = start_date + timedelta(days=((end_date - start_date).days // 2))
    possible_period_count = len({period_start(start_date + timedelta(days=offset), bucket) for offset in range((end_date - start_date).days + 1)})
    profiles = []
    temporal_profiles = []
    pairs = []
    for spbu_id, rows in by_spbu.items():
        shipment_ids = {row["shipment_id"] for row in rows}
        mt_shipments: dict[str, set[str]] = defaultdict(set)
        bucket_mt_shipments: dict[date, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        first_half: Counter[str] = Counter()
        second_half: Counter[str] = Counter()
        recent: Counter[str] = Counter()
        previous: Counter[str] = Counter()
        for row in rows:
            mt_shipments[row["mt_id"]].add(row["shipment_id"])
            bucket_mt_shipments[period_start(row["operating_date"], bucket)][row["mt_id"]].add(row["shipment_id"])
            (first_half if row["operating_date"] <= midpoint else second_half)[row["mt_id"]] += 1
            (recent if row["operating_date"] >= recent_start else previous)[row["mt_id"]] += 1
        counts = Counter({mt_id: len(ids) for mt_id, ids in mt_shipments.items()})
        distribution = sorted(counts.items(), key=lambda item: (-item[1], mt_lookup[item[0]]["mt_label"]))
        dominant_mt_id, dominant_count = distribution[0]
        probabilities = {mt_id: ratio(count, len(shipment_ids)) for mt_id, count in distribution}
        concentration = concentration_metrics(list(counts.values()))
        period_counters = {bucket_start: Counter({mt_id: len(ids) for mt_id, ids in values.items()}) for bucket_start, values in bucket_mt_shipments.items()}
        temporal = temporal_metrics(period_counters, first_half, second_half, recent, previous)
        operating_dates = {row["operating_date"] for row in rows}
        first_observed = min(operating_dates)
        last_observed = max(operating_dates)
        confidence = confidence_metrics(len(shipment_ids), len(operating_dates), first_observed, last_observed, start_date, end_date, temporal["active_period_count"], possible_period_count)
        top3_share = round(sum(probabilities[mt_id] for mt_id, _count in distribution[:3]), 6)
        dominant_probability = ratio(dominant_count, len(shipment_ids))
        profile = {
            "depot_id": observations[0]["depot_id"] if observations else None,
            "spbu_id": spbu_id,
            **spbu_lookup[spbu_id],
            "shipment_count": len(shipment_ids),
            "operating_day_count": len(operating_dates),
            "unique_mt_count": len(distribution),
            "dominant_mt_id": dominant_mt_id,
            "dominant_mt_label": mt_lookup[dominant_mt_id]["mt_label"],
            "dominant_mt_probability": dominant_probability,
            "second_mt_probability": probabilities[distribution[1][0]] if len(distribution) > 1 else 0.0,
            "top3_mt_share": top3_share,
            **concentration,
            **temporal,
            **confidence,
            "consistency_classification": consistency_classification(concentration["consistency_score"]),
            "historical_pattern": analytical_pattern(dominant_probability, top3_share, concentration["consistency_score"]),
            "first_observed": first_observed.isoformat(),
            "last_observed": last_observed.isoformat(),
            "fleet_affinity_vector": probabilities,
            "analysis_start_date": start_date.isoformat(),
            "analysis_end_date": end_date.isoformat(),
            "temporal_bucket": bucket,
            "algorithm_version": ALGORITHM_VERSION,
        }
        profile["previous_dominant_label"] = mt_lookup.get(profile.pop("previous_dominant_id"), {}).get("mt_label")
        profile["recent_dominant_label"] = mt_lookup.get(profile.pop("recent_dominant_id"), {}).get("mt_label")
        profiles.append(profile)

        for bucket_start, counter in sorted(period_counters.items()):
            total = sum(counter.values())
            dominant = _dominant(counter)[0]
            for mt_id, count in sorted(counter.items(), key=lambda item: (-item[1], mt_lookup[item[0]]["mt_label"])):
                temporal_profiles.append(
                    {
                        "spbu_id": spbu_id,
                        "spbu_code": spbu_lookup[spbu_id]["spbu_code"],
                        "mt_id": mt_id,
                        "mt_label": mt_lookup[mt_id]["mt_label"],
                        "period_type": bucket,
                        "period_start": max(bucket_start, start_date).isoformat(),
                        "period_end": min(period_end(bucket_start, bucket), end_date).isoformat(),
                        "shipment_count": count,
                        "total_spbu_shipment_count": total,
                        "probability_mt_given_spbu": ratio(count, total),
                        "is_dominant_mt": mt_id == dominant,
                    }
                )
        for mt_id, count in distribution:
            pair_dates = {row["operating_date"] for row in pair_observations[(spbu_id, mt_id)]}
            pair_confidence = confidence_metrics(count, len(pair_dates), min(pair_dates), max(pair_dates), start_date, end_date, len({period_start(value, bucket) for value in pair_dates}), possible_period_count)
            pairs.append(
                {
                    "depot_id": observations[0]["depot_id"] if observations else None,
                    "spbu_id": spbu_id,
                    **spbu_lookup[spbu_id],
                    "mt_id": mt_id,
                    **mt_lookup[mt_id],
                    "shipment_count": count,
                    "total_spbu_shipment_count": len(shipment_ids),
                    "total_mt_shipment_count": len(by_mt_shipments[mt_id]),
                    "probability_mt_given_spbu": probabilities[mt_id],
                    "probability_spbu_given_mt": ratio(count, len(by_mt_shipments[mt_id])),
                    "first_observed": min(pair_dates).isoformat(),
                    "last_observed": max(pair_dates).isoformat(),
                    "operating_day_count": len(pair_dates),
                    **pair_confidence,
                    "algorithm_version": ALGORITHM_VERSION,
                }
            )
    profiles.sort(key=lambda row: (-row["shipment_count"], row["spbu_code"]))
    pairs.sort(key=lambda row: (row["spbu_id"], -row["shipment_count"], row["mt_label"]))
    return {"profiles": profiles, "pairs": pairs, "temporal_profiles": temporal_profiles}


def _passes_confidence(level: str, selected: str) -> bool:
    normalized = selected.upper()
    if normalized in {"ALL", ""}:
        return True
    if normalized in {"MEDIUM+", "MEDIUM_PLUS"}:
        return CONFIDENCE_RANK[level] >= CONFIDENCE_RANK["MEDIUM"]
    return level == "HIGH" if normalized == "HIGH" else True


def _build_summary(observations: list[dict], profiles: list[dict], pairs: list[dict]) -> dict:
    mt_counts = [row["unique_mt_count"] for row in profiles]
    return {
        "total_eligible_shipments": len({row["shipment_id"] for row in observations}),
        "spbu_analyzed": len(profiles),
        "mt_observed": len({row["mt_id"] for row in pairs}),
        "unique_spbu_mt_pairs": len(pairs),
        "average_mt_per_spbu": round(statistics.fmean(mt_counts), 2) if mt_counts else 0.0,
        "median_mt_per_spbu": round(statistics.median(mt_counts), 2) if mt_counts else 0.0,
        "high_consistency_spbu": sum(row["consistency_score"] >= CLASSIFICATION_THRESHOLDS["high_consistency"] for row in profiles),
        "high_variability_spbu": sum(row["variability_score"] >= 65 for row in profiles),
        "low_stability_spbu": sum(row["temporal_stability_score"] < 50 for row in profiles),
        "historical_pattern_shifts": sum(row["pattern_shift_level"] != "STABLE" for row in profiles),
    }


def _build_rankings(profiles: list[dict], mt_lookup: dict) -> dict:
    def with_rank(rows: list[dict]) -> list[dict]:
        return [{"rank": index, **row} for index, row in enumerate(rows[:20], 1)]

    consistent = sorted(profiles, key=lambda row: (-row["consistency_score"], -row["shipment_count"], row["spbu_code"]))
    variable = sorted(profiles, key=lambda row: (-row["variability_score"], -row["shipment_count"], row["spbu_code"]))
    unstable = sorted(profiles, key=lambda row: (row["temporal_stability_score"], -row["pattern_shift_distance"], -row["shipment_count"]))
    return {"most_consistent": with_rank(consistent), "most_variable": with_rank(variable), "least_stable": with_rank(unstable)}


def _build_pattern_matrix(profiles: list[dict]) -> dict:
    median_unique = statistics.median([row["unique_mt_count"] for row in profiles]) if profiles else 0
    points = []
    for row in profiles:
        high_unique = row["unique_mt_count"] > median_unique
        high_affinity = row["dominant_mt_probability"] >= 0.60
        quadrant = "PREFERRED-FLEET" if high_unique and high_affinity else "DEDICATED-LIKE" if high_affinity else "HIGHLY FLEXIBLE" if high_unique else "LIMITED BALANCED"
        points.append({"spbu_id": row["spbu_id"], "spbu_code": row["spbu_code"], "value": [row["unique_mt_count"], row["dominant_mt_probability"]], "quadrant": quadrant, "shipment_count": row["shipment_count"]})
    return {"unique_mt_split": median_unique, "affinity_split": 0.60, "points": points}


def _build_reverse_detail(observations: list[dict], mt_id: str | None, spbu_lookup: dict, mt_lookup: dict, start_date: date, end_date: date, bucket: str, recent_days: int) -> dict | None:
    if not mt_id or mt_id not in mt_lookup:
        return None
    rows = [row for row in observations if row["mt_id"] == mt_id]
    shipment_ids = {row["shipment_id"] for row in rows}
    spbu_shipments: dict[str, set[str]] = defaultdict(set)
    operating_dates = {row["operating_date"] for row in rows}
    bucket_spbu_counts: dict[date, Counter[str]] = defaultdict(Counter)
    first_half: Counter[str] = Counter()
    second_half: Counter[str] = Counter()
    recent: Counter[str] = Counter()
    previous: Counter[str] = Counter()
    midpoint = start_date + timedelta(days=((end_date - start_date).days // 2))
    recent_start = max(start_date, end_date - timedelta(days=max(1, recent_days) - 1))
    for row in rows:
        spbu_shipments[row["spbu_id"]].add(row["shipment_id"])
        bucket_spbu_counts[period_start(row["operating_date"], bucket)][row["spbu_id"]] += 1
        (first_half if row["operating_date"] <= midpoint else second_half)[row["spbu_id"]] += 1
        (recent if row["operating_date"] >= recent_start else previous)[row["spbu_id"]] += 1
    distribution = [
        {**spbu_lookup[spbu_id], "shipment_count": len(ids), "probability_spbu_given_mt": ratio(len(ids), len(shipment_ids))}
        for spbu_id, ids in spbu_shipments.items()
    ]
    distribution.sort(key=lambda row: (-row["shipment_count"], row["spbu_code"]))
    concentration = concentration_metrics([len(ids) for ids in spbu_shipments.values()])
    temporal = temporal_metrics(dict(bucket_spbu_counts), first_half, second_half, recent, previous)
    return {
        "mt_id": mt_id,
        **mt_lookup[mt_id],
        "historical_shipments": len(shipment_ids),
        "unique_spbu_count": len(spbu_shipments),
        "operating_day_count": len(operating_dates),
        **concentration,
        "dominant_spbu_persistence": temporal["dominant_mt_persistence"],
        "temporal_stability_score": temporal["temporal_stability_score"],
        "pattern_shift_level": temporal["pattern_shift_level"],
        "previous_dominant_spbu_code": spbu_lookup.get(temporal["previous_dominant_id"], {}).get("spbu_code"),
        "recent_dominant_spbu_code": spbu_lookup.get(temporal["recent_dominant_id"], {}).get("spbu_code"),
        "distribution": distribution,
    }


def _build_recent_comparison(observations: list[dict], spbu_id: str | None, mt_lookup: dict, start_date: date, end_date: date, recent_days: int) -> dict:
    if not spbu_id:
        return {"recent_start_date": None, "full_period": [], "recent_period": []}
    rows = [row for row in observations if row["spbu_id"] == spbu_id]
    recent_start = max(start_date, end_date - timedelta(days=max(1, recent_days) - 1))

    def distribution(source: list[dict]) -> list[dict]:
        counts = Counter(row["mt_id"] for row in source)
        total = sum(counts.values())
        return [
            {"mt_id": mt_id, "mt_label": mt_lookup[mt_id]["mt_label"], "shipment_count": count, "probability": ratio(count, total)}
            for mt_id, count in sorted(counts.items(), key=lambda item: (-item[1], mt_lookup[item[0]]["mt_label"]))
        ]

    return {"recent_start_date": recent_start.isoformat(), "full_period": distribution(rows), "recent_period": distribution([row for row in rows if row["operating_date"] >= recent_start])}


def _build_network(pair_rows: list[dict], profiles: list[dict], spbu_lookup: dict, mt_lookup: dict, selected_spbu_id: str | None, selected_mt_id: str | None, edge_metric: str, limit: int) -> dict:
    sorted_edges = sorted(pair_rows, key=lambda row: (-row["shipment_count"], -row["probability_mt_given_spbu"]))[:limit]
    spbu_ids = {row["spbu_id"] for row in sorted_edges}
    mt_ids = {row["mt_id"] for row in sorted_edges}
    profile_lookup = {row["spbu_id"]: row for row in profiles}
    nodes = [
        {"id": f"spbu:{spbu_id}", "entity_id": spbu_id, "entity_type": "SPBU", "name": spbu_lookup[spbu_id]["spbu_code"], "category": 0, "symbolSize": 18 + min(24, math.sqrt(profile_lookup[spbu_id]["shipment_count"]) * 2), "selected": spbu_id == selected_spbu_id}
        for spbu_id in spbu_ids
    ] + [
        {"id": f"mt:{mt_id}", "entity_id": mt_id, "entity_type": "MT", "name": mt_lookup[mt_id]["mt_label"], "category": 1, "symbolSize": 20, "selected": mt_id == selected_mt_id}
        for mt_id in mt_ids
    ]
    metric = edge_metric.upper()
    edges = [
        {
            "source": f"spbu:{row['spbu_id']}",
            "target": f"mt:{row['mt_id']}",
            "value": row["probability_mt_given_spbu"] if metric == "AFFINITY_PROBABILITY" else row["shipment_count"],
            **{key: row[key] for key in ["spbu_id", "spbu_code", "mt_id", "mt_label", "shipment_count", "probability_mt_given_spbu", "probability_spbu_given_mt", "first_observed", "last_observed", "operating_day_count", "confidence_level"]},
            "highlighted": row["spbu_id"] == selected_spbu_id or row["mt_id"] == selected_mt_id,
        }
        for row in sorted_edges
    ]
    return {"nodes": nodes, "edges": edges, "categories": [{"name": "SPBU"}, {"name": "MT"}], "edge_metric": metric}


def _build_evidence(db: Session, observations: list[dict], spbu_id: str | None, mt_id: str | None, depot_name: str, product_id: str | None) -> dict:
    matching = [row for row in observations if row["spbu_id"] == spbu_id and row["mt_id"] == mt_id]
    shipment_ids = {row["shipment_id"] for row in matching}
    if not shipment_ids:
        return {"relationship": None, "distinct_shipment_count": 0, "rows": []}
    lo_statement = select(FactLoadingOrderLine).where(FactLoadingOrderLine.shipment_id.in_(shipment_ids))
    if product_id:
        lo_statement = lo_statement.where(FactLoadingOrderLine.product_id == product_id)
    lo_rows = db.scalars(lo_statement).all()
    lo_by_shipment: dict[str, list[FactLoadingOrderLine]] = defaultdict(list)
    for line in lo_rows:
        lo_by_shipment[line.shipment_id].append(line)
    spbus_by_shipment: dict[str, set[str]] = defaultdict(set)
    for row in observations:
        if row["shipment_id"] in shipment_ids:
            spbus_by_shipment[row["shipment_id"]].add(row["spbu_id"])
    all_spbu_ids = {value for values in spbus_by_shipment.values() for value in values}
    spbu_code_lookup = dict(db.execute(select(MasterSPBU.spbu_id, MasterSPBU.spbu_code).where(MasterSPBU.spbu_id.in_(all_spbu_ids))).all()) if all_spbu_ids else {}
    evidence_rows = []
    for row in sorted(matching, key=lambda item: (item["operating_date"], item["shipment_id"]), reverse=True):
        lines = lo_by_shipment.get(row["shipment_id"], [])
        evidence_rows.append(
            {
                "shipment_id": row["shipment_id"],
                "source_shipment_id": row["source_shipment_id"],
                "date": row["operating_date"].isoformat(),
                "depot": depot_name,
                "gate_out": row["gate_out"].isoformat() if row["gate_out"] else None,
                "mt_id": mt_id,
                "spbu_id": spbu_id,
                "products": sorted({line.source_product_name or line.product_id for line in lines if line.source_product_name or line.product_id}),
                "quantity": round(sum(line.quantity or 0 for line in lines if line.spbu_id == spbu_id), 3),
                "other_spbu_ids": sorted(spbu_code_lookup.get(value, value) for value in spbus_by_shipment[row["shipment_id"]] - {spbu_id}),
            }
        )
    return {"relationship": {"spbu_id": spbu_id, "mt_id": mt_id}, "distinct_shipment_count": len(shipment_ids), "rows": evidence_rows}


def build_affinity_date_availability(db: Session, depot_id: str) -> dict:
    depot = db.get(MasterDepot, depot_id)
    if not depot:
        raise HTTPException(status_code=404, detail="Depot not found.")
    rows = db.execute(
        select(FactShipment.operating_date, FactShipment.shipment_id).where(FactShipment.depot_id == depot_id, FactShipment.operating_date.is_not(None)).order_by(FactShipment.operating_date)
    ).all()
    counts = Counter(value for value, _shipment_id in rows if value)
    dates = sorted(counts)
    return {
        "depot_id": depot_id,
        "depot_name": depot.depot_name,
        "available_dates": [value.isoformat() for value in dates],
        "dates": [{"date": value.isoformat(), "shipment_count": counts[value]} for value in dates],
        "min_date": dates[0].isoformat() if dates else None,
        "max_date": dates[-1].isoformat() if dates else None,
    }
