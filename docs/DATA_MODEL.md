# Data Model

Phase 0 core tables:

- Import audit: `import_audit`.
- Staging: `stg_mt`, `stg_spbu`, `stg_loading_order`, `stg_gps_data`.
- Canonical masters: `master_mt`, `master_spbu`, `master_depot`, `master_product`, `master_tag`, `master_tag_type`, `master_personnel`.
- Aliases and bridges: `tag_alias`, `depot_identifier_alias`, `spbu_identifier_alias`, `product_alias`, `bridge_mt_tag`, `bridge_spbu_tag`.
- Operations: `fact_shipment`, `fact_loading_order_line`, `fact_shipment_spbu`.
- GPS foundation: `fact_gps_event`, `spbu_geofence`, `depot_geofence`, `fact_spbu_visit`, `fact_shipment_stop`.
- Quality: `data_quality_issue`.

Important separation:

- Master/reference estimates remain in `master_spbu.master_distance_km` and `master_spbu.master_travel_time_min`.
- Observed travel/visit evidence belongs in GPS and derived fact tables.
- Loading Order row order is not an actual stop sequence when GPS evidence exists.
- `fact_loading_order_line` uses a composite primary key: `loading_order_number` + `source_depot_name`/`tbbm`. `loading_order_number` may repeat across depots, and source `shipment_id` is allowed to repeat because one shipment can contain multiple loading orders, SPBU destinations, and compartments in the same MT.
