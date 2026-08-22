# Phases

Phase 0: master data strengthening and operational data foundation.

Phase 1: historical tag intelligence.

Phase 2: depot departure time intelligence.

Phase 3: SPBU pairing and directed edge intelligence.

Phase 4: SPBU–MT historical affinity and stability intelligence.

Phase 5: operational cluster intelligence.

Phase 6: time-aware shipment prediction, rolling multi-trip MT assignment, and estimated availability.

The repository includes read-only Phase 2–4 intelligence, persisted Phase 5 ML workflows, and Phase 6 inference/assignment/availability estimation. Phase 6 may estimate a preliminary small-stop sequence for cycle time, but no phase through Phase 6 performs fleet-wide route optimization or VRP.

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

## Phase 5

- API prefix: `/api/v1/phase5`
- UI: `/machine-learning-intelligence`
- Hard gate: exact 100% canonical master compatibility within one depot
- Engine A: baseline-period historical concentration using Isolation Forest; no train/test split
- Engine A observation: unique `depot_id + shipment_id + spbu_id + mt_id`
- Engine B features: typed master-tag vector + full shift distribution + Phase 3 pairing embedding from seeded Node2Vec walks and PPMI/SVD
- Engine B pipeline: independently scaled/weighted feature fusion → UMAP → HDBSCAN
- Isolated pairing nodes: deterministic zero embedding; HDBSCAN noise remains unassigned
- Lifecycle: prepare, validate, train, review, name, save, activate/archive/version, compare
- Comparison: optimal Jaccard membership matching; cluster numbers are never treated as stable identities
- Schema: `ml_concentration_analysis_run`, `ml_spbu_concentration_profile`, `ml_training_run`, `ml_behavioral_model`, `ml_model_artifact`, `ml_spbu_cluster_assignment`, `ml_cluster_profile`
- Artifact storage: filesystem/volume under `ML_ARTIFACT_DIR`, relational metadata and SHA-256 only
- Algorithm versions: `phase5.concentration.iforest.v1`, `phase5.behavioral.portable_n2v_umap_hdbscan.v2`

## Phase 6

- API prefix: `/api/v1/phase6`
- UI: `/prediction-assignment`
- Google settings UI: `/settings/google-maps-integration`
- Scope: exactly one depot and one `SAVED`/`ACTIVE` Phase 5 model per run
- Inputs: Loading Order (`loading_order_no`, `shipment_start_datetime`, `spbu_no`) and initial MT availability (`vehicle_registration_no`, `initial_available_datetime`) workbooks
- Demo input: total KL is split into 8 KL Loading Orders (with a remainder), using random active depot SPBU and generated timestamps valid for the selected model shift snapshot
- Validation: complete datetime, planning horizon, unique LO/MT, canonical master, active MT, depot, timezone, and full-day model shift snapshot
- Derived shift: `shipment_start_datetime` is mapped through the immutable Phase 2/Phase 5 shift snapshot; shift is not the availability input
- Shipment inference: deterministic, time-aware within `maximum_pairing_time_gap_minutes`, same-derived-shift only; planned start is the latest LO timestamp
- MT score: Phase 4 historical affinity; master compatibility remains a separate Phase 1 hard filter
- Multi-SPBU compatibility: intersection across all shipment SPBU
- Assignment: chronological rolling vehicle state with `STRICT_START` or bounded `ALLOW_DELAY`; the same MT can serve multiple non-overlapping trips
- Route estimate: server-side Google Compute Routes/Matrix client, TRUCK profile when capability/profile allow, visibly marked DRIVE fallback, profile/time/mode-aware cache, historical/default fallback
- Cycle time: depot processing + travel legs + per-stop service + return processing; turnaround buffer is added once to return for next availability
- Persistence: run, shipment, line, candidate, assignment, trip, Google configuration, route cache, routing metrics, original/final snapshots, and audit
- Overrides: compatible MT change or same-shift shipment restructure recalculates vehicle profile, route estimate, return, next availability, and downstream rolling state without Phase 5 training
- Export: Summary, Shipment Result, Trip Timeline, MT Assignment, MT Candidates, Validation
- Visualization: predicted same-shipment network, assignment matrix, and MT multi-trip Gantt timeline
- Algorithm: `phase6.time_aware_multitrip.v2`
- Security: encrypted API key using environment-provided encryption secret; browser receives masked value only
- Phase 7 boundary: Phase 6 never calls Google Route Optimization/GMPRO `optimizeTours` and never solves fleet-wide VRP; Phase 7 owns final globally optimized route and constraints
