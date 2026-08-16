# Phase 2 - Depot Departure Time Intelligence

Phase 2 builds descriptive historical departure profiles for Mobil Tangki shipments leaving a selected depot.

It answers:

> For shipments serving this SPBU, at what time does the vehicle usually depart from the depot, and how consistent is that historical pattern?

It does not recommend dispatch times and does not analyze SPBU arrival, ETA, route sequence, travel time, or route optimization.

## Entry Points

- UI: `/departure-intelligence`
- API: `GET /api/v1/departure-intelligence/analysis`

Required API filters:

- `depot_id`
- `start_date`
- `end_date`

Optional API filters:

- `bucket_minutes`: `30` or `60`, default `30`
- `search`: SPBU code/name or shipment id
- `limit`
- `offset`

## Unit of Analysis

The analytical observation is unique:

```text
shipment_id + spbu_id
```

If one shipment contains multiple Loading Order rows for the same SPBU, such as multiple products or compartments, it still contributes one departure observation for that SPBU.

If one shipment serves multiple SPBU, the same depot departure timestamp contributes one observation to each served SPBU while preserving `shipment_id` for later phase enrichment.

## Timestamp Source Lineage

The response preserves:

- `loading_order_gate_out_datetime`
- `gps_actual_depot_exit_datetime`
- `departure_datetime_used`
- `departure_time_source`

Source priority:

1. Use GPS depot-exit event when available and matched as a reliable depot-exit event.
2. Otherwise fall back to Loading Order gate-out timestamp.

Current GPS depot-exit matching uses `fact_gps_event` rows for the selected depot, vehicle, operation date, and depot-exit event type. If LO gate-out exists, GPS candidates are constrained to a six-hour window around LO gate-out.

## Algorithm

Algorithm version:

```text
departure_profile.circular_gap_v1
```

Time-of-day is circular. For each SPBU profile:

1. Convert departure timestamp to minute-of-day.
2. Sort all minutes.
3. Find the largest gap around the 24-hour circle.
4. Cut the circle at that gap and unwrap minutes into a linear series.
5. Calculate percentiles on the unwrapped series.
6. Normalize output times back to `HH:mm`.

Calculated profile fields include:

- observation count
- P20
- P25 / Q1
- P50 / median
- P75 / Q3
- P80
- P90
- P95
- peak departure bucket
- peak departure time
- Preferred Historical Departure Window
- IQR dispersion
- outlier count
- confidence score and level

Preferred Historical Departure Window is descriptive and uses the circular-time P20-P80 range. Peak departure time is the midpoint of the highest-count bucket.
