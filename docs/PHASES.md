# Phases

Phase 0: master data strengthening and operational data foundation.

Phase 1: historical tag intelligence.

Phase 2: depot departure time intelligence.

Phase 3: SPBU pairing and directed edge intelligence.

Phase 4: SPBU–MT historical affinity and stability intelligence.

Phase 5: operational cluster intelligence.

Phase 6: interactive network intelligence explorer.

The repository includes read-only Phase 2, Phase 3, and Phase 4 pages/APIs. They describe historical operations and do not perform route optimization or future dispatch assignment.

## Phase 2

- API: `GET /api/v1/departure-intelligence/analysis`
- UI: `/departure-intelligence`
- Analytical unit: unique `shipment_id + spbu_id`
- Source priority: reliable GPS depot-exit event, then LO gate-out
- Algorithm: `departure_profile.circular_gap_v1`

## Phase 3

- API: `GET /api/v1/pairing-intelligence/analysis`
- UI: `/pairing-intelligence`
- Pairing: unordered same-shipment `A - B`
- Transition: actual consecutive stop `A -> B`
- Product-specific membership is deduplicated `shipment_id + spbu_id`
- Algorithm: `pairing_v1`

## Phase 4

- API: `GET /api/v1/affinity-intelligence/analysis`
- UI: `/affinity-intelligence`
- Required scope: depot and date range; optional product segmentation
- Analysis runs only after Apply in the UI
- Analytical unit: unique `depot_id + shipment_id + spbu_id + mt_id`
- Metrics: `P(MT|SPBU)`, `P(SPBU|MT)`, dominant MT, Top-3 Share, HHI, normalized HHI, normalized entropy, consistency, variability, confidence, dominant persistence, temporal stability, and pattern shift
- Temporal buckets: Daily, Weekly, Monthly, or visible Auto selection
- Stability: 70% mean consecutive-period Jensen–Shannon similarity + 30% modal dominant-MT persistence
- Product filtering occurs before final observation deduplication
- Algorithm: `spbu_mt_affinity.jsd_v1`
- Schema: `fact_spbu_mt_pair`, `fact_spbu_mt_profile`, `fact_spbu_mt_temporal_profile`
- Output is historical evidence only; no future assignment or optimization is produced
