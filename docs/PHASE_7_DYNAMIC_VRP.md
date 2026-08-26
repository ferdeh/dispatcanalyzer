# Phase 7 — Dynamic Multi-Trip VRP & Depot Bay Queue Optimization

## Purpose and boundary

Phase 7 turns a saved Phase 6 Prediction Run into a versioned daily operational plan. Phase 6 remains the immutable source of predicted shipment/MT/pairing/confidence. Phase 7 stores current vehicle, shipment, trip, stop sequence, compartment, bay, and gate-out assignment in separate tables.

Phase 7 uses:

- Google OR-Tools Routing Solver for vehicle/route sequencing inside each physical-vehicle trip round;
- OR-Tools CP-SAT for LO-to-compartment feasibility and depot bay scheduling;
- Google Routes API only for distance, duration, matrix, and final selected-leg geometry;
- Phase 1 compatibility as a hard filter;
- Phase 3 pairing, Phase 4 affinity, Phase 6 assignment/grouping, and the previous route version as soft preferences.

No Google Route Optimization/GMPRO endpoint is called.

## Dispatcher flow

```text
Select Depot
  → Create Job for Operating Date
  → Select completed Phase 6 Run ID
  → import LO + immutable Phase 6 warm-start fields
  → Load active depot MT from canonical master
  → enter initial Planned ETA Depot
  → configure bays, allowed products, and product/compartment loading duration
  → enter actual bay occupancy and queue
  → Load/review parameter profile
  → Validate
  → Initial Optimization → V1 baseline
  → execute and update LO/MT/bay actual state
  → freeze executing/near-term work
  → Re-Optimize Now → V2, V3, ...
```

All workspace pages render the same sticky Job Header: Job ID, depot, operating date, source Phase 6 Run ID, current route version, status, and LO/fleet/trip/volume KPIs.

## Backend modules

- `phase7_routes.py`: request/response routing only.
- `phase7_service.py`: jobs, Phase 6 import, operational state, validation, profiles, snapshots, persistence, version reads, KPIs, cost, simulation, and audit.
- `phase7_optimization.py`: compartment CP-SAT, Routing Solver, bay CP-SAT, and iterative coordination.
- `phase7_matrix.py`: pre-materialized route matrix, cache, request audit, fallback, and final selected-leg geometry.
- `phase7_constants.py`: statuses, reason codes, defaults, and built-in parameter profiles.

## Multi-trip model

One physical MT is not represented as one all-day route. The coordinator runs routing in rounds:

```text
effective ETA at depot
  → route one feasible trip
  → bay queue + loading + gate-out
  → visit SPBU sequence
  → predicted return depot
  → subtract working time and update vehicle availability
  → next routing round for the same physical MT
```

The next trip is eligible only when it starts before depot close, the MT has working time remaining, compatible LO remains, and its capacity/compartments can hold that trip. Bay-induced gate-out delay is propagated to return time and the next trip; coordination repeats until the departure delta is within tolerance or `max_coordination_iterations` is reached.

## Compartment assignment

CP-SAT creates binary `LO × compartment` assignment variables and `compartment × product` activation variables.

Hard constraints:

```text
each LO is assigned to exactly one compartment
sum LO volume per compartment <= compartment capacity
LO assignment implies that compartment's product
sum active products in one compartment <= 1
```

The result persists `compartment_id` on every route-version LO assignment. A capacity-feasible route that fails product/compartment placement is rejected with `COMPARTMENT_INFEASIBLE`.

## Routing Solver

The solver consumes only prebuilt integer distance/time matrices. Google is never called from a transit or cost callback.

Hard routing controls include:

- total capacity and post-solve compartment feasibility;
- Phase 1 `MT vehicle class <= SPBU allowed vehicle class`;
- canonical tag subset compatibility;
- depot scope;
- `effective_eta_depot` start time;
- remaining vehicle working time and no physical MT overlap;
- SPBU official receiving time window when available;
- explicit high-penalty disjunction for mandatory LO so infeasibility remains explainable;
- immutable frozen assignment on reroute.

Cost callbacks include the selected base objective and applicable Phase 6 vehicle/grouping deviation, Phase 3 pairing, Phase 4 affinity, previous vehicle/shipment, gate-out, and general plan-change penalties. These are soft evidence only and never bypass master compatibility.

## Bay CP-SAT

For each preliminary trip, CP-SAT creates a loading start/end and optional interval for every eligible bay. Exactly one eligible bay must be selected. `NoOverlap` protects bay capacity.

Before new intervals, each bay is blocked by:

1. actual current occupancy and remaining loading time;
2. actual queue rows in queue-position order.

Bay eligibility requires every trip product to be allowed or `all_products_allowed=true`. Sequential loading duration is the sum of configured minutes for every used compartment. Parallel mode uses loading arms and reserves a conservative parallel duration. Gate-out is:

```text
loading finish + gate_process_time
```

The result persists vehicle-ready, queue start, loading start/finish, gate-out, bay assignment, and per-compartment bay operations.

## Freeze and reroute

At reroute time:

```text
DONE → frozen
ONGOING → frozen
PLANNED with gate-out <= current time + freeze window → frozen
other PLANNED → re-optimizable
```

A trip is the indivisible execution unit. If any LO in its current trip is frozen, the remaining LO on the same trip receives `FROZEN_TRIP_DEPENDENCY`. The prior trip/stops/LO assignments are copied into the new version; the old version is never updated.

User ETA override outranks system ETA, which outranks initial planned ETA. Actual bay state and actual queue similarly outrank predicted state. Phase 6 is not rerun.

## Objectives and cost

Available objectives:

- `MIN_TOTAL_COST`
- `MIN_TOTAL_DISTANCE`
- `MIN_TOTAL_OPERATING_TIME`

Hard constraints are identical for all objectives. Cost reporting retains:

```text
activation
+ distance
+ operating time
+ queue
+ loading
+ overtime
+ penalties
= total cost
```

It also reports cost per MT, trip, kilometre, KL, and LO.

## Route matrix and Google Routes

Phase 7 uses its own `route_matrix_cache` and `route_api_request_log` because cache identity and request auditing belong to the optimization run. Cache identity includes origin, destination, departure bucket, traffic awareness, and `route_vehicle_mode`.

`GENERAL_VEHICLE` is the default. `TRUCK` is opt-in. If an account/region does not provide a supported truck road response, fallback/provider metadata must remain visible; a fallback must never be labelled as Google truck geometry.

Without a configured Google key, validation returns `WARNING` and the engine uses cached/master Haversine travel estimates. This preserves offline testability but records the provider/fallback source. Final selected legs—not solver evaluations—may call Compute Routes for map geometry.

## Persistence and audit

Migration: `0019_phase7_dynamic_vrp`.

Core groups:

- Job/version: `optimization_job`, `optimization_run`, `route_version`, `route_version_trip`, `route_version_stop`, `route_version_lo_assignment`, `route_version_vehicle_assignment`.
- State: `operational_state_snapshot`, `lo_operational_state`, `vehicle_operational_state`, `actual_vehicle_event`.
- Bay: `master_loading_bay`, `loading_bay_product_compatibility`, `product_compartment_loading_duration`, `actual_bay_state`, `optimization_initial_queue`, `optimization_bay_assignment`, `optimization_bay_operation`.
- Parameters: `optimization_parameter_profile`, `optimization_parameter_value`, `optimization_vehicle_cost_rule`, `optimization_parameter_snapshot`.
- Routes API: `route_matrix_cache`, `route_api_request_log`.

Every optimization stores the exact effective parameter JSON and SHA-256 checksum. Profile references are informative only; reproducibility uses the immutable snapshot. State snapshots preserve LO, vehicle, bay, queue, and user-update audit evidence that caused the route version.

## Solver result and dropped LO

Result statuses are `OPTIMAL`, `FEASIBLE`, `PARTIAL`, `INFEASIBLE`, `TIME_LIMIT`, and `FAILED`. Best feasible output is retained when available. A failed solver updates the Job to `FAILED` and stores run error details instead of crashing the application process.

Unserved reason codes include:

- `NO_COMPATIBLE_MT`
- `INSUFFICIENT_CAPACITY`
- `COMPARTMENT_INFEASIBLE`
- `VEHICLE_TIME_EXHAUSTED`
- `DEPOT_TIME_EXHAUSTED`
- `SPBU_TIME_WINDOW`
- `BAY_PRODUCT_CONSTRAINT`
- `BAY_CONGESTION`
- `NO_FEASIBLE_ROUTE`
- `USER_CANCELLED`
- `UNSERVED_END_OF_DAY`

When all LO are `DONE`, the Job becomes `CLOSED` and another route version is not created. When depot operating time is exhausted, remaining LO is explicitly persisted as `UNSERVED_END_OF_DAY`; downstream operations may classify it as dropped or carry-over.

## Prediction Run dropdown and operating-date matching

The Run ID dropdown is intentionally not a list of every Phase 6 run. A candidate is shown only when all of the following are true:

- the run status is `COMPLETED`;
- the run depot equals the Phase 7 Job depot; and
- at least one LO in the immutable input snapshot has a local operating date equal to the Job `operating_date`.

The local operating date comes from `shipment_start_datetime_local` when present. Otherwise, `shipment_start_datetime` is converted using the timezone stored on Master Depot. The date embedded in a generated ID such as `PRED-20260826-71EF00` represents run creation and must not be interpreted as the LO operating date.

If the dropdown is empty, verify the Job depot/date, run status, and distinct local dates inside the Phase 6 LO snapshot. The auditable remedies are to create a Phase 7 Job for the matching operating date or produce a completed Phase 6 run for the required date. Do not patch the saved snapshot, Job date, or source foreign key directly.

## API summary

All endpoints use `/api/v1/phase7`.

- Jobs: create, list by depot, get.
- Source: list matching completed Phase 6 runs, load selected Run ID.
- State: list/update LO, load/list/update MT, get/update actual bay state and queue.
- Bay master: get/upsert depot bay and loading durations.
- Validation: get current `READY/WARNING/BLOCKED` result.
- Optimization: initial optimize and reroute.
- Versions: list/get, trip detail, simulation, map, cost analysis, dropped LO.
- Parameters: list/get, Save as next version, Save As.

See FastAPI OpenAPI for exact request schemas.

## Environment and tests

Required package: `ortools==9.14.6206` in `apps/api/requirements.txt`.

Google key encryption remains configured with:

```text
GOOGLE_ROUTES_ENCRYPTION_KEY
```

The Google API key itself is managed in the Settings UI and is never returned to the browser.

Run focused acceptance tests:

```bash
cd apps/api
pytest tests/test_phase7_dynamic_vrp.py
```

The suite covers Phase 6 soft warm start, one-product compartments, multi-trip, availability, freeze horizon, ONGOING/DONE handling, bay product compatibility, actual queue delay, per-compartment loading, ETA override precedence, route version immutability, and explicit dropped LO.
