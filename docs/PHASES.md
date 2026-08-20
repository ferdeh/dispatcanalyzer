# Phases

Phase 0: master data strengthening and operational data foundation.

Phase 1: historical tag intelligence.

Phase 2: depot departure time intelligence.

Phase 3: SPBU pairing and directed edge intelligence.

Phase 4: SPBU–MT historical affinity and stability intelligence.

Phase 5: operational cluster intelligence.

Phase 6: shipment and MT assignment prediction.

The repository includes read-only Phase 2–4 intelligence, persisted Phase 5 ML workflows, and Phase 6 inference/assignment. No phase through Phase 6 performs route sequencing or VRP.

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
- Engine B features: typed master-tag vector + full shift distribution + Phase 3 pairing Node2Vec embedding
- Engine B pipeline: independently scaled/weighted feature fusion → UMAP → HDBSCAN
- Isolated pairing nodes: deterministic zero embedding; HDBSCAN noise remains unassigned
- Lifecycle: prepare, validate, train, review, name, save, activate/archive/version, compare
- Comparison: optimal Jaccard membership matching; cluster numbers are never treated as stable identities
- Schema: `ml_concentration_analysis_run`, `ml_spbu_concentration_profile`, `ml_training_run`, `ml_behavioral_model`, `ml_model_artifact`, `ml_spbu_cluster_assignment`, `ml_cluster_profile`
- Artifact storage: filesystem/volume under `ML_ARTIFACT_DIR`, relational metadata and SHA-256 only
- Algorithm versions: `phase5.concentration.iforest.v1`, `phase5.behavioral.n2v_umap_hdbscan.v1`

## Phase 6

- API prefix: `/api/v1/phase6`
- UI: `/prediction-assignment`
- Scope: exactly one depot and one `SAVED`/`ACTIVE` Phase 5 model per run
- Inputs: Loading Order and shift-scoped available MT `.xlsx` workbooks
- Validation: required/empty/duplicate fields, canonical master, depot, and model shift snapshot
- Shipment inference: deterministic, independent per shift, allows single-SPBU shipment, retains normalized confidence evidence
- MT score: Phase 4 historical affinity; master compatibility remains a separate Phase 1 hard filter
- Multi-SPBU compatibility: intersection across all shipment SPBU
- Assignment: exact global maximum-weight one-to-one matching per shift, not greedy
- Persistence: run, shipment, line, candidate/diagnostic, assignment/original/final layers, snapshots, durations, audit
- Overrides: compatible MT change and same-shift shipment restructure; affected shift is rescored/reassigned without training
- Export: Summary, Shipment Result, MT Assignment, MT Candidates, Validation
- Visualization: predicted same-shipment network and assignment matrix; neither is route sequencing
- Algorithm: `phase6.shipment_mt_prediction.v1`
- Phase 7 boundary: output is a warm start only; distance/time/VRP/multi-trip remain out of scope
