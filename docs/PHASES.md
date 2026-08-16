# Phases

Phase 0: master data strengthening and operational data foundation.

Phase 1: historical tag intelligence.

Phase 2: depot departure time intelligence.

Phase 3: SPBU pairing and directed edge intelligence.

Phase 4: shipment-set and route-pattern intelligence.

Phase 5: operational cluster intelligence.

Phase 6: interactive network intelligence explorer.

The repository now includes a read-only Phase 2 Depot Departure Time Intelligence page/API built on the Phase 0 shipment foundation. Phase 2 is strictly scoped to historical Mobil Tangki departure from depot and must not perform SPBU arrival, ETA, route sequence, travel-time, or route optimization analysis.

Phase 2 implementation notes:

- API: `GET /api/v1/departure-intelligence/analysis`
- UI: `/departure-intelligence`
- Required filters: `depot_id`, `start_date`, `end_date`
- Analysis runs only after Apply in the UI
- Analytical unit: unique `shipment_id + spbu_id`
- Source priority: reliable GPS depot-exit event first, otherwise LO gate-out timestamp
- Source lineage fields: `loading_order_gate_out_datetime`, `gps_actual_depot_exit_datetime`, `departure_datetime_used`, `departure_time_source`
- Algorithm version: `departure_profile.circular_gap_v1`
- Circular-time handling: sort time-of-day observations, cut at the largest circular gap, unwrap, then calculate percentiles and robust spread
- Preferred Historical Departure Window: descriptive P20-P80 circular-time window
- Peak departure time: midpoint of the highest-count departure bucket

Phase 1 and Phase 3-6 endpoints and jobs remain gated until their source facts and acceptance criteria are available.
