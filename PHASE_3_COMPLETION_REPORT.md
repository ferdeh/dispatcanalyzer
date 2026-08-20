# Phase 3 Completion Report

## Implementation Summary

Implemented Phase 3 - SPBU Pairing Probability Intelligence as a reusable relationship intelligence layer over the existing Phase 0 shipment foundation.

The implementation calculates same-shipment SPBU co-occurrence, canonical unordered pairs, directional conditional probabilities, support, lift, evidence confidence, data-quality exclusions, matrix/network payloads, SPBU detail, historical evidence drill-down, and separate GPS consecutive transition statistics.

## Files Created

- `apps/api/app/pairing_intelligence.py`
- `apps/api/tests/test_pairing_intelligence.py`
- `db/migrations/versions/0007_phase3_spbu_pairing.py`
- `PHASE_3_COMPLETION_REPORT.md`

## Files Modified

- `apps/api/app/main.py`
- `apps/api/app/models.py`
- `apps/web/src/App.tsx`
- `README.md`
- `docs/PHASES.md`
- `docs/DATA_MODEL.md`

## Database Migrations

Added migration `0007_phase3_spbu_pairing`:

- `fact_spbu_pair`
- `fact_spbu_transition`
- indexes by depot/date and SPBU pair/transition keys
- unique constraints preventing duplicate derived rows within the same analysis scope and algorithm version

The current API calculates results statelessly from canonical facts for the requested filter. The new tables provide durable storage for future scheduled/batch materialization.

## API Changes

Added:

- `GET /api/v1/pairing-intelligence/analysis`
- `GET /api/v1/pairing-intelligence/available-dates`

Main filters:

- `depot_id`
- `start_date`
- `end_date`
- optional `product_id`
- optional `search`
- optional `selected_spbu_id`
- optional evidence pair
- pagination and sorting

## Frontend Changes

Added page:

- `/pairing-intelligence`

Dashboard sections:

- filter header with Depot, Date Range, Product, Search, Apply
- empty initial state
- KPI cards
- Data Quality panel
- Pairing Probability Distribution
- Top SPBU Pairings sortable table
- directional Pairing Matrix
- Pairing Network Prototype
- SPBU Pairing Detail
- Historical Evidence drill-down
- GPS Consecutive Transition Context

Changing filters does not run analysis until Apply is clicked.

## Formulas Implemented

- `pair_count(A,B) = count distinct eligible shipment_id containing A and B`
- `P(B|A) = pair_count(A,B) / shipment(A)`
- `P(A|B) = pair_count(A,B) / shipment(B)`
- `Support(A,B) = pair_count(A,B) / total eligible shipments`
- `Lift(A,B) = (pair_count(A,B) * total eligible shipments) / (shipment(A) * shipment(B))`

Zero denominators return `0`, never `Infinity` or `NaN`.

## Confidence Methodology

Centralized in `calculate_confidence()`.

- `INSUFFICIENT_DATA`: `min(shipment_a_count, shipment_b_count) < 5` or `pair_count < 3`
- `LOW`: otherwise `pair_count < 10`
- `MEDIUM`: otherwise `10 <= pair_count < 30`
- `HIGH`: otherwise `pair_count >= 30`

`confidence_score` is deterministic in the range `0-1`, based on pair-count evidence and minimum anchor shipment evidence.

Confidence is evidence confidence, not prediction probability.

## Data-Quality Rules

The analysis returns:

- Source Shipments
- Eligible Shipments
- Excluded Shipments
- Exclusion Reasons

Handled exclusions:

- no valid SPBU membership
- unknown SPBU
- missing mandatory analytical keys
- duplicate shipment-SPBU/product membership deduplicated

## Product Segmentation

- `All Products`: membership comes from `fact_shipment_spbu`
- selected product: membership comes from distinct `shipment_id + spbu_id` in `fact_loading_order_line` where `product_id` matches

This means a shipment can remain multi-product, while repeated product lines or compartments do not duplicate pair count.

## GPS Transition Distinction

Same-shipment pairing and consecutive transition are separate:

```text
A - B = same-shipment pairing
A -> B = consecutive stop transition
```

For GPS sequence `A -> C -> B`, the transition output is `A -> C` and `C -> B`; it does not create `A -> B` as a consecutive transition. The pairing output can still include `A - B` because both SPBUs are in the same shipment.

## Test Results

Automated verification:

```text
apps/api: 25 passed
apps/web: npm run build passed
```

Test coverage added for:

- canonical pair generation
- no self-pair
- N choose 2 combination counts
- pair counts
- `P(B|A)`
- `P(A|B)`
- support
- lift
- confidence classification
- insufficient data
- depot filtering
- date filtering
- product filtering
- multi-product duplicate protection
- historical evidence reconciliation
- GPS consecutive transition
- pairing versus transition separation
- invalid/no-membership shipment exclusion
- unknown SPBU exclusion
- API endpoint smoke coverage

## Visual Validation Results

Validated with Docker Compose using:

- API: `http://localhost:8000`
- Web: `http://localhost:3001` because port `3000` was already allocated
- Page: `http://localhost:3001/pairing-intelligence`

Checks completed:

- initial Phase 3 page renders empty state before Apply
- KPI cards are absent before Apply
- active depot date availability loads
- Apply renders KPI cards, Data Quality, probability distribution, Top Pairings, matrix, network, SPBU detail, evidence, and transition context
- all-product analysis returned 4,014 eligible shipments, 578 unique SPBU pairs, and 24 excluded shipments for the sample Medan date range
- selected pair evidence reconciled: pair count 6 and evidence 6 distinct shipments
- product-specific `PERTALITE` analysis returned 3,359 eligible shipments and surfaced duplicate membership deduplication
- mobile-sized viewport smoke check rendered Phase 3 header, Apply control, and empty/results state
- browser console error log was empty during validation

## Known Limitations

- `fact_spbu_pair` and `fact_spbu_transition` are schema-ready but not yet populated by a scheduled analytics job.
- Network view is intentionally a prototype and limits payload size.
- Phase 2 departure profile context is not yet embedded into Phase 3 SPBU detail.
- Data-quality exclusion detail is summarized, not yet a row-level audit table.

## Technical Debt

- Move the large single-file React app toward page-level components before Phase 4 expands the UI further.
- Add a batch materialization job for `fact_spbu_pair` and `fact_spbu_transition`.
- Add row-level exclusion drill-down if operations need audit records for each rejected source shipment.

## Recommended Phase 4 Preparation

- Reuse Phase 3 membership preparation and canonical pair generation as the route-pattern foundation.
- Keep `shipment set` and `route sequence` models separate from pair intelligence.
- Materialize Phase 3 facts before layering higher-volume Phase 4 route-pattern exploration.

Ready for Phase 4 = YES
