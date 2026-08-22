# Data Model

Phase 0 core tables:

- Import audit: `import_audit`.
- Staging: `stg_mt`, `stg_spbu`, `stg_loading_order`, `stg_gps_data`.
- Canonical masters: `master_mt`, `master_spbu`, `master_depot`, `master_product`, `master_tag`, `master_tag_type`, `master_personnel`.
- Aliases and bridges: `tag_alias`, `depot_identifier_alias`, `spbu_identifier_alias`, `product_alias`, `bridge_mt_tag`, `bridge_spbu_tag`.
- Operations: `fact_shipment`, `fact_loading_order_line`, `fact_shipment_spbu`.
- GPS foundation: `fact_gps_event`, `spbu_geofence`, `depot_geofence`, `fact_spbu_visit`, `fact_shipment_stop`.
- Phase 3 derived relationship intelligence: `fact_spbu_pair`, `fact_spbu_transition`.
- Phase 4 derived historical fleet intelligence: `fact_spbu_mt_pair`, `fact_spbu_mt_profile`, `fact_spbu_mt_temporal_profile`.
- Quality: `data_quality_issue`.

Important separation:

- Master/reference estimates remain in `master_spbu.master_distance_km` and `master_spbu.master_travel_time_min`.
- Observed travel/visit evidence belongs in GPS and derived fact tables.
- Loading Order row order is not an actual stop sequence when GPS evidence exists.
- `fact_loading_order_line` uses a composite primary key: `loading_order_number` + `source_depot_name`/`tbbm`. `loading_order_number` may repeat across depots, and source `shipment_id` is allowed to repeat because one shipment can contain multiple loading orders, SPBU destinations, and compartments in the same MT.

Phase 3 derived facts:

- `fact_spbu_pair` stores canonical same-shipment SPBU pairs with pair counts, directional conditional probabilities, support, lift, confidence, analysis date range, and algorithm version.
- `fact_spbu_transition` stores directional actual consecutive stop transitions from reconstructed sequence evidence. It must not be used as same-shipment pairing.
- Product-specific Phase 3 analysis uses distinct `shipment_id + spbu_id` from `fact_loading_order_line` for the selected product. `All Products` uses `fact_shipment_spbu`.
- Repeated Loading Order lines, compartments, or products are deduplicated before pair generation.

Semantic distinction:

```text
A - B = same-shipment pairing
A -> B = actual consecutive transition
```

Phase 4 derived facts:

- `fact_spbu_mt_pair` stores a historical SPBU–MT edge, both directional probabilities, first/last evidence, operating days, and evidence confidence for one analysis scope.
- `fact_spbu_mt_profile` stores dominant MT, Top-3 share, HHI, normalized HHI, normalized entropy, consistency, variability, dominant persistence, temporal stability, pattern shift, and confidence.
- `fact_spbu_mt_temporal_profile` stores per-bucket MT probability and dominant-MT status.
- The analytical observation is unique `depot_id + shipment_id + spbu_id + mt_id`.
- Product is a filter on eligible LO rows, not part of the final observation key.
- Phase 4 tables contain no future-assignment or optimization fields.

Phase 5 ML persistence:

- `ml_concentration_analysis_run`: depot, baseline period, minimum evidence, exact compatibility snapshot, Isolation Forest parameters/version, status, creator, and timestamps.
- `ml_spbu_concentration_profile`: concentration features, raw/0–100 score, classification, sufficiency, deterministic peer context, and used/unused compatible-MT distribution for one run/SPBU.
- `ml_training_run`: staged dataset summary/payload, shift snapshot, model configuration, review result, library versions, temporary artifact reference, status, and audit fields. Training runs are not Model Registry entries.
- `ml_behavioral_model`: one immutable named/versioned saved package with depot, training window, feature configs/weights, algorithm configs, dependency metadata, quality summary, status, and audit fields.
- `ml_model_artifact`: relative storage URI, type, SHA-256, and byte size. Serialized binaries are not stored in relational JSON/BLOB columns.
- `ml_spbu_cluster_assignment`: saved cluster, membership probability, noise flag, operational display context, and 2D visualization coordinates per model/SPBU.
- `ml_cluster_profile`: interpretable cluster size, tag/shift/pairing profile, membership quality, and low-confidence count.

All Phase 5 run/model facts retain `depot_id`. Unique/index constraints cover run-SPBU, model-SPBU, model-cluster, depot/status, depot/date range, model/version, and score lookup paths.

Phase 6 prediction persistence:

- `prediction_run`: human-readable/UUID identity, depot/model/version, filenames, normalized LO/MT input, validation, parameter/model/original-prediction snapshots, algorithm version, status, duration metrics, creator, timestamps, and failure diagnostic.
- `prediction_job`: durable one-to-one queue state for a run, including worker/lease token, heartbeat and lease expiry, attempt/retry limit, recovery timestamps, completion, and last worker diagnostic. Keeping this row separate lets heartbeat commits proceed while the larger prediction result transaction is open.
- `prediction_shipment`: current final shipment structure per run/shift with model score, confidence, explanation, and manual flag.
- `prediction_shipment_line`: canonical LO/SPBU membership plus `model_predicted_shipment_id`, which preserves the original model grouping when the dispatcher moves a line.
- `prediction_mt_candidate`: available-shift candidates including historical score, compatibility pass/fail, rank, exclusion reason, and structured evidence. Failed master compatibility may be retained for explainability but cannot be optimized.
- `prediction_assignment`: original and final vehicle/score, assigned/unassigned/manual status, unassigned reason, and override user/reason/time.

Frequently filtered run/model/depot/shift/shipment/vehicle/SPBU/time fields are indexed. Run children cascade on run deletion, but the model foreign key is intentionally restrictive so an audited prediction cannot silently lose model lineage. The original prediction snapshot remains immutable while current shipment and final assignment rows support dispatcher overrides.
