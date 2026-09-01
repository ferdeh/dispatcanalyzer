# Phase 4 — SPBU–MT Historical Affinity & Stability

## Analytical unit

Each observation is unique by:

```text
depot_id + shipment_id + spbu_id + mt_id
```

For a selected product, eligible Loading Order rows are filtered first and then deduplicated to the same key. Product is not part of the final key.

## Affinity formulas

```text
P(MT i | SPBU A) = distinct shipments for A with i / distinct shipments for A
P(SPBU A | MT i) = distinct shipments for i serving A / distinct eligible shipments for i
Top3Share = p1 + p2 + p3
HHI = sum(p_i^2)
NormalizedHHI = (HHI - 1/N) / (1 - 1/N)
ConsistencyScore = 100 * NormalizedHHI
Entropy = -sum(p_i * ln(p_i))
NormalizedEntropy = Entropy / ln(N)
VariabilityScore = 100 * NormalizedEntropy
```

For `N=1`, consistency is `100` and variability is `0`.

Consistency classification thresholds:

- `>= 80`: VERY HIGH CONSISTENCY
- `>= 65`: HIGH CONSISTENCY
- `>= 40`: MEDIUM
- `>= 20`: HIGH VARIABILITY
- `< 20`: VERY HIGH VARIABILITY

Historical pattern labels are analytical only:

- `DEDICATED-LIKE`: dominant probability at least 75% or consistency at least 80
- `PREFERRED-FLEET`: Top-3 share at least 75% or consistency at least 40
- `FLEXIBLE`: remaining distributions

## Evidence confidence

Confidence is not multiplied into consistency, variability, affinity, or stability.

```text
40% shipment sample coverage (saturates at 50 shipments)
20% operating-day coverage (saturates at 20 days)
15% observed date-span / selected date-span
10% recency
15% active temporal buckets (saturates at 4 buckets)
```

- `< 40`: LOW
- `40–<70`: MEDIUM
- `>= 70`: HIGH

## Temporal stability

Daily, Weekly, and Monthly buckets are supported. Auto visibly resolves to Daily for up to 14 days, Weekly for 15–120 days, and Monthly for longer periods.

Distribution distance uses Jensen–Shannon distance in `[0,1]`.

```text
TemporalStability = 100 * (
  0.70 * (1 - mean consecutive-bucket JS distance)
  + 0.30 * modal dominant-MT persistence
)
```

Pattern shift uses the maximum measured JS distance across consecutive buckets, first-half vs second-half, and prior vs recent distributions:

- `<= 0.10`: STABLE
- `<= 0.25`: MINOR SHIFT
- `<= 0.50`: MODERATE SHIFT
- `> 0.50`: MAJOR SHIFT

No causal explanation is inferred.

## API

```text
GET /api/v1/affinity-intelligence/available-dates?depot_id=...
GET /api/v1/affinity-intelligence/analysis
```

Main query parameters are `depot_id`, `start_date`, `end_date`, `product_id`, `minimum_observations`, `confidence`, `temporal_bucket`, `recent_days`, `top_n`, `selected_spbu_id`, `selected_mt_id`, and `edge_metric`.

## Saved analysis configurations

Phase 4 can persist and restore the applied filters, selected SPBU and MT detail, chart viewport state, and the complete analysis snapshot. Loading a saved configuration restores that immutable snapshot without rerunning the analysis. Saving the same normalized name for the same depot updates the existing record.

```text
GET    /api/v1/affinity-intelligence/saved-configurations
POST   /api/v1/affinity-intelligence/saved-configurations
GET    /api/v1/affinity-intelligence/saved-configurations/{config_id}
DELETE /api/v1/affinity-intelligence/saved-configurations/{config_id}
```

## Guardrails

Phase 4 reads historical assignments. It does not create a recommended MT, optimal MT, preferred future assignment, causal explanation, or optimization result.
