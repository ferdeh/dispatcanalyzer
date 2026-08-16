# GPS Model

Phase 0 prepares GPS ingestion without inventing fields:

- `stg_gps_data` accepts raw rows and marks them `PENDING_MAPPING`.
- `fact_gps_event` contains optional canonical fields that should only be populated when present in the real source.
- `spbu_geofence` and `depot_geofence` support visit detection.
- `fact_spbu_visit` stores grouped visits.
- `fact_shipment_stop` stores actual shipment sequence with source and confidence.

GPS visit detection must group sequential pings inside a geofence into one visit and must use configurable dwell, event-count, gap, and radius thresholds.
