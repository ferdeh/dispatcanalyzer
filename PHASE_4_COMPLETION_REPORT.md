# Phase 4 Completion Report

## Result

Phase 4 — SPBU–MT Historical Affinity & Stability Intelligence is implemented across schema, analytics, API, React dashboard, automated tests, documentation, and visual validation.

## Implemented

- unique shipment–SPBU–MT observations with post-product-filter deduplication
- two-way historical probability, dominant MT, Top-N, Top-3 share, and Fleet Affinity Vector
- HHI, normalized HHI, normalized entropy, consistency, variability, and separate evidence confidence
- Daily/Weekly/Monthly/Auto temporal buckets
- dominant-MT persistence, Jensen–Shannon temporal stability, pattern shift, and recent-vs-full comparison
- SPBU profile, MT reverse profile, sortable/filterable rankings, scatter, pattern matrix, time series, and bipartite network
- node click, edge tooltip, and shipment evidence drill-down
- data-quality summary and exclusion reasons
- schema tables `fact_spbu_mt_pair`, `fact_spbu_mt_profile`, and `fact_spbu_mt_temporal_profile`
- API endpoints and explicit Apply-only dashboard behavior

## Verification

- backend: `33 passed`
- frontend: `npm run build` passed
- synthetic coverage includes 80/20 affinity, high variability, high consistency, stable weekly distribution, major pattern shift, duplicate multi-product LO, confidence separation, reverse probability, API, and available dates
- Docker migration: `0008_phase4_spbu_mt_affinity (head)`
- visual validation: empty-before-Apply, all-products and PERTALITE scopes, desktop/mobile layout, rankings, reverse stability, evidence, and clean browser console passed

## Algorithm

Version: `spbu_mt_affinity.jsd_v1`.

Formula and threshold details are documented in `docs/PHASE_4_SPBU_MT_AFFINITY.md` and returned by the API under `methodology`.

## Scope guardrails

No future vehicle assignment, recommendation, or optimization logic is present in the Phase 4 service or dashboard.

Ready for Phase 5 = YES
