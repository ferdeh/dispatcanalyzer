# Phases

Phase 0: master data strengthening and operational data foundation.

Phase 1: historical tag intelligence.

Phase 2: depot departure time intelligence.

Phase 3: SPBU pairing and directed edge intelligence.

Phase 4: SPBU–MT historical affinity and stability intelligence.

Phase 5: operational cluster intelligence.

Phase 6: time-aware shipment prediction, rolling multi-trip MT assignment, and estimated availability.

Phase 7: dynamic fleet-wide multi-trip VRP, depot bay queue scheduling, rolling reroute, and immutable operational route versions.

Phase 8: manual dispatch adjustment, per-trip constraint validation and route recalculation, cascading fleet availability, operational simulation, dashboard, audit, versioning, and final dispatch.

The repository includes read-only Phase 2–4 intelligence, persisted Phase 5 ML workflows, Phase 6 inference/assignment/availability estimation, Phase 7 OR-Tools optimization/control, and Phase 8 human-in-the-loop dispatch finalization. Phase 6 may estimate a preliminary small-stop sequence for cycle time; Phase 7 owns fleet-wide route optimization and depot bay scheduling; Phase 8 owns manual adjustment, per-trip recalculation, simulation, audit, and the finalized dispatch version.

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
- Coverage: HDBSCAN is fit only to sufficient-history SPBUs; every other active master SPBU receives conservative nearest-cluster cold-start coverage with explicit `coverage_source` and `history_eligible=false`
- Cluster profiles and model comparison use historical members only; no-history/insufficient-history counts are reported separately
- Isolated pairing nodes: deterministic zero embedding; historical HDBSCAN noise remains unassigned
- Lifecycle: prepare, validate, train, review, name, save, activate/archive/version, compare
- Comparison: optimal Jaccard membership matching; cluster numbers are never treated as stable identities
- Schema: `ml_concentration_analysis_run`, `ml_spbu_concentration_profile`, `ml_training_run`, `ml_behavioral_model`, `ml_model_artifact`, `ml_spbu_cluster_assignment`, `ml_cluster_profile`
- Artifact storage: filesystem/volume under `ML_ARTIFACT_DIR`, relational metadata and SHA-256 only
- Algorithm versions: `phase5.concentration.iforest.v1`, `phase5.behavioral.portable_n2v_umap_hdbscan.v4`

## Phase 6

- API prefix: `/api/v1/phase6`
- UI: `/prediction-assignment`
- Google settings UI: `/settings/google-maps-integration`
- Scope: exactly one depot and one `SAVED`/`ACTIVE` Phase 5 model per run
- Inputs: Loading Order (`loading_order_no`, `shipment_start_datetime`, `spbu_no`, `order_quantity_kl`) and initial MT availability (`vehicle_registration_no`, `initial_available_datetime`) workbooks; every LO must equal exactly 8 KL
- Demo LO: total KL must be divisible by 8; one 8 KL LO is generated per unit using only active, non-noise `BEHAVIORAL_HISTORY` SPBUs with `history_eligible=true` from the selected model
- Demo MT availability: target KL is matched to the closest random subset of active depot MT master capacities. Default availability equals the first shift start; optional Random availability samples within first-shift start through last-shift end
- Validation: complete datetime, planning horizon, unique LO/MT, canonical master, active MT, depot, timezone, and full-day model shift snapshot
- Derived shift: `shipment_start_datetime` is mapped through the immutable Phase 2/Phase 5 shift snapshot; shift is not the availability input
- Shipment inference: capacity/time/route set packing within `maximum_pairing_time_gap_minutes`, same-derived-shift only; cluster, pairing, tag, capacity, and route feasibility remain explicit evidence/constraints
- MT score: Phase 4 historical affinity; master compatibility remains a separate Phase 1 hard filter
- Multi-SPBU compatibility: intersection across all shipment SPBU
- Assignment: iterative 32→24→16→8 KL tiers followed by chronological rolling vehicle state with `STRICT_START` or bounded `ALLOW_DELAY`; only assigned groups consume LO and unsuccessful larger groups are retried in smaller tiers
- Capacity: exact full-load only (`4/3/2/1 LO = 32/24/16/8 KL MT`); no larger-MT partial-load fallback
- Route estimate: server-side Google Compute Routes/Matrix client in enforced DRIVE-only mode for Indonesia, time/config-aware cache, and visibly marked historical/default fallback
- Cycle time: depot processing + travel legs + per-stop service + return processing; turnaround buffer is added once to return for next availability
- Persistence: run, shipment, line, candidate, assignment, trip, Google configuration, route cache, routing metrics, original/final snapshots, and audit
- Overrides: compatible MT change or same-shift shipment restructure recalculates route estimate, return, next availability, hourly/cumulative assigned KL, geographic routes, and downstream rolling state without Phase 5 training
- Shipment detail: every LO/SPBU shows its selected-model cluster; `Move to…` options show shipment ID, target SPBU, and target cluster
- Prediction Run History: client-side 10/25/50-row pagination, visible range, Previous/page/Next controls, immutable View/Export/Re-run actions, and refresh feedback
- Export: Summary, Shipment Result, Trip Timeline, MT Assignment, MT Candidates, Validation
- Visualization: predicted same-shipment network, assignment matrix, paginated MT multi-trip timeline, assigned KL hourly/cumulative chart, and searchable geographic route-per-MT map using master coordinates plus Google road geometry/fallback
- Algorithm: `phase6.iterative_exact_capacity_assignment.v9` using `CAPACITY_TIME_ROUTE_SET_PACKING` within each capacity tier
- Security: encrypted API key using environment-provided encryption secret; browser receives masked value only
- Phase 7 boundary: Phase 6 never calls Google Route Optimization/GMPRO `optimizeTours` and never solves fleet-wide VRP; Phase 7 owns final globally optimized route and constraints

## Phase 7

- API prefix: `/api/v1/phase7`
- UI: `/phase7-optimization`
- Job scope: one depot + one operating date + one explicitly selected completed Phase 6 Prediction Run
- Source boundary: original Phase 6 prediction fields are immutable warm-start/soft-preference evidence; current Phase 7 assignment is stored separately
- Engine A: Google OR-Tools Routing Solver with a physical-vehicle multi-trip loop
- Compartment engine: CP-SAT LO-to-compartment assignment with one product per compartment
- Engine B: deterministic `FIFO_BALANCED` bay eligibility, actual occupancy/queue, balanced loading, and gate-out schedule; CP-SAT remains opt-in
- Google role: prebuilt distance/time matrix, cache, final selected-leg geometry, and map display only; Google is never called from a solver callback
- Hard compatibility: Phase 1 vehicle-class limit, canonical tag subset, depot, capacity, compartments, availability, working time, SPBU window, frozen assignment, and bay products
- Soft evidence: Phase 3 pairing, Phase 4 MT affinity, Phase 6 vehicle/grouping, and previous-plan stability penalties
- Reroute: current Route Version seeds the next version; copy DONE, retain ONGOING with actual return ETA, lock on-time near-term MT/sequence with recalculated time, and release late/unavailable future work; never rerun Phase 6 automatically
- Operational UI: LO System ETA resolves the current trip's return-to-depot time; MT Delivery Status is derived from assigned LO state with `ONGOING > DONE > PLANNED`; Comparison audits Phase 6 against V1/V2/... or two Route Versions per LO, MT, gate-out, and ETA Depot
- Versioning: every optimization appends V1/V2/... plus state snapshot, parameter snapshot/checksum, solver metadata, cost, dropped reason, and comparison
- End of day: all-DONE closes the Job without a new version; remaining LO at depot close is explicit `UNSERVED_END_OF_DAY`
- Schema: migration `0019_phase7_dynamic_vrp`
- Algorithm: `phase7.dynamic_multitrip_vrp_bay.v6` (`FIFO_BALANCED`, per-trip candidate-audited post-bay repair, current Route Version reroute seed, and actual-state-aware future-trip release)
- Technical documentation: `docs/PHASE_7_DYNAMIC_VRP.md`

## Phase 8

- API prefix: `/api/v1/phase8/manual-dispatch`
- UI: `/phase-8/manual-dispatch` and `/phase-8/manual-dispatch/:jobId?tab=...`
- Source: immutable snapshot of Phase 6 warm start or any dynamically discovered Phase 7 Route Version
- Boundary: manual editing and per-trip recalculation only; no global VRP reoptimization and no mutation of Phase 6/7 records
- Hierarchy: `MT → Trip → Loading Order`, with relational Unassigned LO scope
- Eligibility: canonical `compatibility.evaluate_mt_spbu_compatibility`, depot/active/scope/uniqueness/capacity/compartment guardrails
- Apply: validation → Google Routes legs → service time → return/turnaround → availability → downstream invalidation
- Simulation: 15/30/60-minute server aggregates of gate-out KL, available fleet KL, capacity gap, and MT movement Gantt
- Dashboard: hourly/cumulative KL, saved shift/cluster distributions, separate time/volume utilization metrics, and remaining demand
- Geographic Map: search/select one MT and render all of its trips with stored or live Google Routes road geometry without mutating the dispatch snapshot
- Versioning: finalized versions are immutable; new versions are deep-copy working snapshots with parent lineage
- Finalization: hard errors block; unassigned LO is an explicit acknowledgment warning by default
- Schema: migration `0022_phase8_manual_dispatch`
- Technical documentation: `docs/PHASE_8_MANUAL_DISPATCH.md`

## Phase 7 → Phase 8 operational handoff

1. Phase 7 persists an immutable Route Version (`V1`, `V2`, and later versions) while the Job points to the current operational version.
2. The dispatcher opens Phase 8 and selects depot, operational date, Phase 7 Job, and one dynamically discovered source route. Phase 6 Warm Start remains selectable through the same Phase 7 lineage.
3. `Create & Load` copies the selected MT, trip, LO scope/assignment, route metadata, cluster, shift, tags, configuration, and source lineage into a separate Manual Dispatch Job.
4. Manual edits affect only Phase 8. Apply recalculates one trip and cascades availability invalidation only along that MT's later trips; no fleet-wide solver runs.
5. Simulation and Dashboard read the same current Phase 8 state. Finalization produces a read-only Dispatch Version; subsequent edits require a child version.

This handoff is intentionally one-way. Phase 8 never writes assignments, timestamps, statuses, or current-version pointers back to Phase 6 or Phase 7.
