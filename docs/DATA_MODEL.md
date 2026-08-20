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
