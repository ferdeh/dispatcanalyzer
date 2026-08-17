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

## Operational Shift Intelligence

Operational Shift Intelligence extends Phase 2 with historical shift affinity and shift assignment per SPBU. It belongs in Phase 2 because it uses only the same historical depot departure timestamps as the departure profile:

```text
departure_datetime_used
```

The unit of analysis remains unique `shipment_id + spbu_id`, with `departure_time_source` preserving GPS-vs-LO lineage.

Operational Shift Intelligence is descriptive. It can say an SPBU is historically associated with a shift. It does not prescribe future dispatch schedules.

### Shift Configuration

Users can configure operational shift boundaries for the selected depot. The default UI configuration is four shifts:

- Shift 1: `00:00-05:59`
- Shift 2: `06:00-11:59`
- Shift 3: `12:00-17:59`
- Shift 4: `18:00-23:59`

The number of shifts is not hardcoded. The UI supports Add Shift and Remove Shift.

Shift ranges are validated before analysis:

- every shift needs a name, start time, and end time;
- names cannot be duplicated;
- times must be valid `HH:mm`;
- ranges cannot overlap;
- ranges must cover the full 24-hour day.

Internally, times are converted to minute-of-day values. User-facing ranges are inclusive, for example `00:00-05:59`, while the algorithm treats that as the deterministic interval `00:00 <= t < 06:00`.

The current UI can save and load an active shift configuration per depot in browser storage. The API is stateless and receives the shift configuration in the analysis request. If a future authorization/settings model is added, this can map directly to depot-specific tables such as `config_operational_shift_set` and `config_operational_shift`.

### API

Shift analysis endpoint:

```text
POST /api/v1/departure-intelligence/shift-analysis
```

Request fields:

- `depot_id`
- `start_date`
- `end_date`
- `bucket_minutes`
- `shifts`
- `assignment_method`
- optional `search`, `sort_column`, `sort_direction`

Supported assignment methods are exactly:

- `DOMINANT_SHIFT`
- `MEDIAN_BASED`
- `HYBRID_CONFIDENCE_AWARE`

Algorithm version:

```text
shift_assignment.descriptive_v1
```

The response preserves traceability fields including `depot_id`, `spbu_id`, `shift_config_id`, `assignment_method`, primary and secondary shift IDs/shares, assignment score/status, observation count, source period, calculated timestamp, and algorithm version. Raw shift-share distribution is retained for every SPBU.

### Historical Shift Affinity

For every SPBU, the system counts how many historical departure observations fall in each configured shift and calculates percentages.

Example:

```text
Shift 1 = 68%
Shift 2 = 24%
Shift 3 = 6%
Shift 4 = 2%
```

This vector is the SPBU's Historical Shift Affinity. It is always preserved regardless of assignment method. The SPBU Shift Affinity Heatmap displays this raw distribution with SPBU rows and configured shift columns.

### Assignment Methods

Dominant Shift assigns the SPBU to the shift with the largest historical share. It is simple and transparent when one shift clearly dominates.

Median-Based assigns the SPBU to the shift containing its circular P50 departure time. It is robust to isolated extreme observations but can hide multi-shift behavior.

Hybrid / Confidence-Aware combines:

- historical shift share;
- preferred historical departure-window overlap;
- median alignment;
- peak alignment;
- observation count and Phase 2 confidence.

Hybrid uses centralized weights:

```text
0.40 historical shift share
0.25 preferred window overlap
0.20 median alignment
0.15 peak alignment
```

Confidence factors are:

```text
HIGH = 1.00
MEDIUM = 0.80
LOW = 0.60
```

Hybrid is still historical/descriptive. It does not generate an optimized shift schedule.

### Assignment Status

Assignments are classified as:

- `CLEAR`
- `MODERATE`
- `AMBIGUOUS`
- `INSUFFICIENT_DATA`

Dominant and Median-Based use primary share and primary-secondary gap thresholds. Hybrid uses assignment score and score gap thresholds, with low sample counts marked `INSUFFICIENT_DATA`.

### UI Integration

The Phase 2 page now includes:

- Operational Shift Configuration;
- assignment method help popup;
- Operational Shift Summary;
- SPBU Shift Affinity Heatmap;
- SPBU Shift Assignment Table with search, shift filter, status filter, and sortable columns;
- optional box plot highlighting by primary shift, assignment status, or confidence;
- subtle shift boundary lines on the SPBU Departure Time Box Plot;
- Operational Shift Intelligence details inside SPBU Explorer.

The box plot's underlying statistics remain unchanged by shift assignment method.

### Relationship to Phase 5

Phase 2 answers:

> Historically, which operational shift is this SPBU most associated with?

Phase 5 may later answer:

> Which SPBUs should be clustered together based on departure behavior, shift affinity, tags, pairing, and other operational characteristics?

Phase 2 does not implement K-Means, hierarchical clustering, DBSCAN, route clustering, geographic clustering, tag-based clustering, pairing-based clustering, or Phase 5 network cluster generation.
