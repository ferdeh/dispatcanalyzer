# Phase 7 — Dynamic Multi-Trip VRP & Depot Bay Queue Optimization

## Purpose and boundary

Phase 7 turns a saved Phase 6 Prediction Run into a versioned daily operational plan. Phase 6 remains the immutable source of predicted shipment/MT/pairing/confidence. Phase 7 stores current vehicle, shipment, trip, stop sequence, compartment, bay, and gate-out assignment in separate tables.

Phase 7 uses:

- Google OR-Tools Routing Solver for vehicle/route sequencing inside each physical-vehicle trip round;
- OR-Tools CP-SAT for LO-to-compartment feasibility, with deterministic FIFO_BALANCED as the default depot bay scheduler and bay CP-SAT available only as an explicit option;
- Google Routes API only for distance, duration, matrix, and final selected-leg geometry;
- Phase 1 compatibility as a hard filter;
- Phase 3 pairing, Phase 4 affinity, Phase 6 assignment/grouping, and the previous route version as soft preferences.

No Google Route Optimization/GMPRO endpoint is called.

## Dispatcher flow

```text
Select Depot
  → search/sort/page existing Jobs or Create Job for Operating Date
  → Select completed Phase 6 Run ID
  → import LO + immutable Phase 6 warm-start fields
  → Load active depot MT from canonical master
  → enter Planned ETA Depot for every MT
  → configure bays, allowed products, and product/compartment loading duration
  → enter actual bay occupancy and queue
  → Load/review parameter profile
  → click Validate in Optimization Flow
  → review Optimization Readiness popup
  → confirm Initial optimization date and time in depot timezone
  → pre-run validation requires every Planned ETA >= optimization time
  → API 202 Accepted + Job CALCULATING
  → return to Job Management while background task builds V1
  → Initial Optimization → V1 baseline
  → execute and update LO/MT/bay actual state
  → freeze executing/near-term work
  → confirm Re-optimize time; date remains locked to Initial
  → Re-Optimize Now → V2, V3, ...
```

All workspace pages render the same sticky Job Header: Job ID, depot, operating date, source Phase 6 Run ID, current route version, status, and LO/fleet/trip/volume KPIs.

The Job Management card owns the Create New Job action, free-text search, per-column sorting, row-count selection, pagination, Open Job, and Delete Job actions. Delete requires explicit confirmation and removes the selected Phase 7 operational workspace through database cascades. It never deletes the immutable Phase 6 Prediction Run or canonical master data. Provider request logs remain as detached audit evidence, and a `CALCULATING` Job returns HTTP `409` instead of being deleted.

Initial and reroute requests do not hold the browser open until OR-Tools finishes. The API completes validation, atomically reserves the Job as `CALCULATING`, returns HTTP `202 Accepted`, and schedules the optimization as an in-process background task with an independent SQLAlchemy session. The UI closes the reference-time dialog, navigates back to Job Management, and polls the depot-scoped Job list every two seconds while at least one row is calculating. A calculating row shows a spinner and cannot be deleted or submitted again. Terminal worker results change the Job to `ACTIVE`/`CLOSED` or `FAILED`; opening the Job remains available for progress and error inspection.

The durable run metadata distinguishes `BUILDING_MATRIX`, `SOLVING_ROUTES`, and `PERSISTING_RESULT` before the terminal `COMPLETED`/`FAILED` stage. Every checkpoint carries `progress_pct` and `stage_updated_at`. `PERSISTING_RESULT` includes final route-geometry acquisition and relational trip/stop/LO/bay persistence, so the Job correctly remains `CALCULATING` after OR-Tools has returned. A provider `403` or `429` is not a solver failure: the geometry guard eventually selects the visibly labelled cache/master fallback and persistence continues. Wall-clock elapsed time can include a suspended laptop/Docker VM, whereas `solve_duration_ms` records active optimization work; investigate a stuck run only when stage timestamps, process/database activity, and terminal recovery evidence are all absent.

LO Management and MT Management both provide free-text search, sorting on every data header, selectable page size, and Previous/Next pagination. LO select-all is page-scoped so bulk operational updates remain explicit. MT ETA/status edits remain in the page draft while filtering, sorting, or changing pages.

Bay Management provides Delete Bay on every persisted bay card. Deletion is a confirmed soft-delete from the active depot configuration and clears mutable current occupancy/queue rows for that bay; historical state snapshots, route versions, and bay assignments remain auditable. Newly added unsaved bay cards can be removed locally without an API mutation. Product loading speed is shown as a responsive product-card grid with a dedicated numeric duration and a separate `min / compartment` unit label.

Route Plan pagination is based on unique MT, not trip rows. Its controls are rendered immediately below the Solver Status / Gate Out / Dispatch Span summary. A separate Vehicle Multi-Trip Timeline card is rendered immediately after Route Plan and before the long Route Details card, so the operational Gantt remains directly visible. It renders only the MT on the active page; selecting one registration collapses the Gantt and details to that MT. Its global X domain is the route version's first gate-out through the final depot return, with MT registrations on the right-side category axis. Duration bars cover bay queue, loading, gate processing, travel, and SPBU service. Hoverable milestones expose the MT/trip, current movement or arrival status, SPBU/depot context, actual timestamp, and duration. A queue/loading event before the first gate-out is pinned to the left boundary and explicitly retains its actual time in the tooltip. Trip/LO/SPBU/product filters continue to refine route details inside the selected MT scope. Route-version responses expose both `product_id` and canonical `product_name`; the Product column renders the name while retaining the ID for audit and search. Operational KPI Groups is rendered in Simulation immediately below Simulation KPI.

Geographic Map shares the unique-MT pagination scope, so `All MT` means all MT through pages rather than hundreds of route layers in one browser render. Only trips belonging to the active page become Leaflet polylines, stop markers, and legend cards. A canvas renderer, coordinate validation, per-trip geometry-point guard, explicit map height, and automatic `invalidateSize`/`fitBounds` on version, filter, trip, and page changes keep the map visible after tab transitions and responsive on large route versions. Road geometry is hydrated lazily in this order: full-route cache, exact ordered-stop geometry from historical Phase 7/Phase 6 trips, live Google Routes, then OSRM road geometry. OSRM is strictly a geometry-only display fallback: its distance/duration never replace immutable Route Version facts, solver matrix values, ETA, assignment, sequence, or cost. Solid lines indicate road geometry; dashed lines indicate the final master-coordinate fallback.

## Optimization reference time

Initial Optimization and Re-Optimize never silently use the API server clock or browser clock. Clicking either action opens a required date/time dialog. The submitted local date/time is interpreted in the Master Depot timezone and persisted as `optimization_run.optimization_reference_time`.

For Initial Optimization:

- the date must equal the Job `operating_date`;
- MT readiness is `max(effective ETA depot, depot operational start, optimization reference time)`;
- the route matrix departure and all generated route timing use the same reference;
- after V1 exists, another Initial run is rejected; the dispatcher must use Re-Optimize.

For Re-Optimize:

- the date is locked to the Initial optimization date;
- the time cannot move backwards from the latest completed optimization;
- the selected time becomes `current time` in the freeze rule;
- the reference timestamp and depot timezone are copied into solver metadata and the operational-state audit event.

This makes historical/simulated runs deterministic: pressing the button at 14:00 does not force the calculation to use 14:00 unless the dispatcher confirms that time in the dialog.

## Backend modules

- `phase7_routes.py`: request/response routing, HTTP `202` acceptance, and background-task dispatch only.
- `phase7_service.py`: jobs, async reservation/background execution, Phase 6 import, operational state, validation, profiles, snapshots, persistence, version reads, KPIs, cost, simulation, and audit.
- `phase7_optimization.py`: compartment CP-SAT, Routing Solver, default FIFO_BALANCED bay scheduler, opt-in bay CP-SAT, and post-bay coordination.
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

The next trip is eligible only when its loading/gate-out can occur before depot close, the MT has working time remaining, compatible LO remains, and its capacity/compartments can hold that trip. FIFO_BALANCED propagates each selected bay gate-out plus the original route duration directly to the same MT's next-trip readiness in one event-driven pass. The opt-in CP_SAT strategy retains iterative departure/return coordination through `max_coordination_iterations`. A prior trip may return after depot close, but that MT cannot start another depot loading/gate-out after close. Route search uses `route_optimization_time_limit` and reserves a fair share for every remaining trip round, so Trip 1 cannot consume the entire multi-trip budget.

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

The built-in profile keeps the following routing controls in `HARD` mode:

- total capacity and post-solve compartment feasibility;
- Phase 1 `MT vehicle class <= SPBU allowed vehicle class`;
- canonical tag subset compatibility;
- depot scope;
- required `planned_eta_depot` start time;
- remaining vehicle working time and no physical MT overlap;
- required SPBU official receiving time window from Master SPBU;
- explicit high-penalty disjunction for mandatory LO so infeasibility remains explainable;
- immutable frozen assignment on reroute.

Cost callbacks include the selected base objective and any constraint currently enabled in `SOFT` mode. Phase 6 vehicle/grouping, Phase 3 pairing, Phase 4 affinity, previous vehicle/shipment, and route/gate-out stability start as soft preferences, but the dispatcher can convert every constraint listed in the constraint catalog to `HARD`, `SOFT`, or disabled.

## Configurable constraint contract

The Parameter tab exposes the backend `constraint_catalog`. Every listed rule stores:

```text
enabled
mode = HARD | SOFT
penalty
limit_minutes  # only for constraints with a numeric limit, currently vehicle_working_time
```

Effective behavior is deterministic:

- enabled + `HARD`: the solver rejects the violation; saved penalty is ignored;
- enabled + `SOFT`: the solver may retain the violation and adds the configured penalty;
- disabled: neither enforcement nor penalty applies;
- changing back from `HARD` to `SOFT` reuses the saved penalty unless the dispatcher edits it.

The registry covers MT–SPBU compatibility, vehicle/compartment capacity, product separation, availability, working/depot/SPBU windows, reroute freeze, bay product/queue/no-overlap, serving LO, Phase 6 and historical preferences, and reroute stability. Mode/rule values are normalized by `effective_parameters`, copied to `optimization_parameter_snapshot`, hashed, and summarized in `optimization_run.solver_metadata`. Soft violations and effective penalties are persisted in the trip cost breakdown.

`vehicle_working_time.limit_minutes` is the single source of the MT working-time limit; the former top-level `default_vehicle_working_time_minutes` no longer exists. Working time is cumulative per physical MT, from vehicle use through bay queue, loading, driving, SPBU service, and final return to the depot. HARD rejects the trip that crosses the limit, SOFT retains it with the configured penalty, and disabled ignores the limit and penalty.

`depot_operating_window` has dispatch semantics: loading cannot start before depot open and gate-out cannot occur after depot close. It does not require the dispatched MT to return before close. Return remains constrained by `vehicle_working_time`, vehicle availability for a later trip, and the SPBU receiving window. Depot Operation Span is `last gate-out - first loading start`, not `last return - first departure`.

`DONE`/`ONGOING` execution state, one persisted assignment identity per LO, relational integrity, and append-only route-version history remain non-configurable structural safeguards. The configurable `freeze_window` applies to near-term `PLANNED` work.

## Bay FIFO_BALANCED

`bay_scheduler_strategy=FIFO_BALANCED` is the default operational scheduler. It does not build a dense trip-by-bay search model. Instead, it keeps only the first pending trip of each physical MT in a global priority queue ordered by:

1. MT ready time at depot;
2. preliminary gate-out;
3. trip number;
4. MT ID, LO ID, and stable input index as deterministic tie-breaks.

For each FIFO event, the scheduler enumerates the active bays, applies HARD product and bay-change eligibility, calculates loading duration, then rejects candidates whose gate-out exceeds a HARD bay/depot window. The selected candidate uses this ordering: lowest SOFT penalty, earliest gate-out, lowest projected loading workload, fewest assigned trips, least-flexible eligible bay, then stable bay ID. Therefore identical all-product bays split a simultaneous queue evenly, while product-specific bays are preserved before consuming an equally available all-product bay.

The chosen bay is reserved through loading finish. Gate process follows the reservation, and its timestamp becomes the trip's actual gate-out. The same MT's next trip is not inserted into FIFO until this gate-out plus the previous calculated route duration returns it to the depot. This prevents Trip 2 from jumping ahead while Trip 1 is still loading or travelling. Complexity is approximately `O(T log T + T × B)` for `T` trips and `B` active bays; it has no bay search worker, no bay timeout, and no combinatorial symmetry across identical bays.

With `serve_loading_order=SOFT`, a trip that cannot obtain a valid bay is omitted and the result remains `PARTIAL` when other trips are served. With `serve_loading_order=HARD`, one blocking trip makes the bay result `INFEASIBLE`. `BAY_PRODUCT_CONSTRAINT` is reserved for no structurally eligible bay; `BAY_WINDOW_EXHAUSTED` means compatible bays exist but all valid gate-outs exceed their operational window. A later same-MT trip blocked by failure of an earlier trip is reported as `BAY_CONGESTION`.

`CP_SAT` remains available as an explicit experimental strategy in the Parameter tab for comparison and exceptional optimization studies. Only this strategy uses `bay_optimization_time_limit`, `bay_cp_sat_workers`, and iterative `max_coordination_iterations`. Its `UNKNOWN` and elapsed-limit `TIMEOUT` states remain distinct from physical `INFEASIBLE`.

Before new intervals, each bay is blocked by:

1. actual current occupancy and remaining loading time;
2. actual queue rows in queue-position order.

`state_effective_at` for both facts is aligned to the dispatcher-entered Initial/Re-optimize timestamp. It never defaults to the API server's current date/time. Before the first run, a saved state uses Job day start; once a run exists it uses the latest optimization reference until the next submitted timestamp replaces it.

Bay eligibility requires every trip product to be allowed or `all_products_allowed=true`. Sequential loading duration is the sum of configured minutes for every used compartment. Parallel mode uses loading arms and reserves a conservative parallel duration. Gate-out is:

```text
loading finish + gate_process_time
```

The result persists vehicle-ready, queue start, loading start/finish, gate-out, bay assignment, and per-compartment bay operations. FIFO audit metadata records its sequence, queue position, eligible bay list, workload before/after selection, per-bay totals, and candidate-evaluation count. With soft service, a later trip whose MT returns after depot close is omitted without making the entire bay result infeasible.

## Freeze and reroute

At the dispatcher-confirmed reroute reference time:

```text
DONE → frozen
ONGOING → frozen
PLANNED with gate-out <= optimization reference time + freeze window → frozen
other PLANNED → re-optimizable
```

A trip is the indivisible execution unit. If any LO in its current trip is frozen, the remaining LO on the same trip receives `FROZEN_TRIP_DEPENDENCY`. The prior trip/stops/LO assignments are copied into the new version; the old version is never updated.

`planned_eta_depot` is the only MT availability input for both Initial and Reroute. Every loaded MT must have a value, including an MT marked unavailable, and it must be equal to or later than the dispatcher-selected optimization reference time. Equality is valid; for example optimization at 00:00 accepts Planned ETA 00:00, while optimization at 12:00 rejects Planned ETA 07:00. The input is reset to `null` only after successful result persistence, so a failed run keeps the entered values for correction/retry. System ETA is `null` before V1 because no route has been calculated. Initial completion publishes the calculated `estimated_return_depot` of Trip 1. Each Reroute publishes the calculated return of the trip in the newest route version whose LO is `ONGOING`; if Trip 2 is ongoing, it selects Trip 2. An MT with no ongoing trip publishes the newest version's calculated Trip 1 return, while an MT with no calculated trip remains `null`. Planned ETA is never copied directly into System ETA. The legacy User ETA Override database field is no longer accepted, returned, displayed, or used by the solver. Actual bay state and actual queue similarly outrank predicted bay state. Phase 6 is not rerun.

## Objectives and cost

Available objectives:

- `MIN_TOTAL_COST`
- `MIN_TOTAL_DISTANCE`
- `MIN_TOTAL_OPERATING_TIME`

Constraint modes are independent of the selected objective. Cost reporting retains:

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

## Vehicle activation cost rules and priority

Vehicle activation cost is a fixed objective cost applied when a physical MT is used in a routing round. It influences economic selection but never grants compatibility or overrides a HARD constraint. MT–SPBU eligibility remains controlled by `vehicle_compatibility`, capacity, availability, time, compartment, and other active constraints.

Each activation rule contains:

```text
vehicle_class
vehicle_tag       # optional
activation_cost
priority
```

Rule selection is first-match after sorting by descending `priority`. A larger priority does not make the MT itself preferable; it only makes that rule win when several rules match the same MT. For equal priorities, a rule with `vehicle_tag` is evaluated before a class-only rule because it is more specific. The selected rule's `activation_cost` is used once; matching costs are not added together. If no rule matches, activation cost is zero.

Example for an MT with class `24` and tags `PROJECT_A, URBAN`:

| Vehicle class | Vehicle tag | Activation cost | Priority | Result |
|---:|---|---:|---:|---|
| 24 | — | 900,000 | 10 | matches, but loses priority |
| 24 | URBAN | 700,000 | 15 | matches, but loses priority |
| 24 | PROJECT_A | 600,000 | 20 | selected |

Therefore, `priority` answers **which cost rule applies**, while the resulting `activation_cost` affects **how attractive the MT is to the objective**. Use distinct priorities for overlapping rules so the intended result remains explicit and auditable.

## Route matrix and Google Routes

Phase 7 uses its own `route_matrix_cache` and `route_api_request_log` because cache identity and request auditing belong to the optimization run. Cache identity includes origin, destination, departure bucket, traffic awareness, and `route_vehicle_mode`.

The solver still receives one node per LO, but matrix acquisition is deduplicated by physical Depot/SPBU location and expanded back to LO-node indexes. Google `ComputeRouteMatrix` calls are batched up to 625 elements per request instead of issuing one HTTP request per pair. The default operational guards are:

- `route_matrix_time_limit_seconds=90`;
- `route_matrix_google_element_budget=2500`, below the default Google limit of 3,000 matrix elements per minute;
- `route_geometry_time_limit_seconds=120` and `route_geometry_google_request_budget=500` for final selected-leg geometry.

Pairs beyond the time/element budget use the visible Master/Haversine fallback. Completed matrix/cache batches are committed before OR-Tools starts, so a later solver or persistence failure does not discard expensive matrix work. The active run exposes `BUILDING_MATRIX`, `SOLVING_ROUTES`, `PERSISTING_RESULT`, and terminal progress through the Job response. An API restart marks orphaned in-process runs `FAILED/INTERRUPTED`, allowing an auditable retry instead of leaving the Job permanently `CALCULATING`.

Final geometry has its own wall-clock and logical-request guards. An invalid key (`403`) or rate limit (`429`) is logged as provider evidence but does not discard the OR-Tools plan. Once the guard is exhausted, remaining routes are persisted as `MIXED_OR_MASTER_FALLBACK`. Profiles may lower these two geometry values when fast completion is more important than attempting road geometry for every selected trip.

The page-scoped `POST /jobs/{job_id}/map/road-geometry` endpoint can hydrate old immutable versions without rerunning the solver. It reuses full-route/historical geometry first, tries Google next, and uses OSRM only to draw the already-selected ordered stops along roads. The resulting display geometry is cached for 30 days by default and never writes back to `route_version_trip`.

`GENERAL_VEHICLE` is the default. `TRUCK` is opt-in. If an account/region does not provide a supported truck road response, fallback/provider metadata must remain visible; a fallback must never be labelled as Google truck geometry.

Without a configured Google key, validation returns `WARNING` and the engine uses cached/master Haversine travel estimates. This preserves offline testability but records the provider/fallback source. For each final selected trip, Phase 7 calls Compute Routes once with the solver-ordered SPBU list as intermediate waypoints, producing one road-following Depot → SPBU → Depot GeoJSON polyline. Solver evaluations never call Compute Routes. Per-leg cache/fallback remains only as a visible resilience path when the full road request fails.

## Persistence and audit

Migrations: `0019_phase7_dynamic_vrp`, `0020_phase7_reference_time` for the auditable dispatcher-selected timestamp, and `0021_master_operating_windows` for required SPBU/Depot master windows with `00:00–23:59` defaults.

Core groups:

- Job/version: `optimization_job`, `optimization_run`, `route_version`, `route_version_trip`, `route_version_stop`, `route_version_lo_assignment`, `route_version_vehicle_assignment`.
- State: `operational_state_snapshot`, `lo_operational_state`, `vehicle_operational_state`, `actual_vehicle_event`.
- Bay: `master_loading_bay`, `loading_bay_product_compatibility`, `product_compartment_loading_duration`, `actual_bay_state`, `optimization_initial_queue`, `optimization_bay_assignment`, `optimization_bay_operation`.
- Parameters: `optimization_parameter_profile`, `optimization_parameter_value`, `optimization_vehicle_cost_rule`, `optimization_parameter_snapshot`.
- Routes API: `route_matrix_cache`, `route_api_request_log`.

Every optimization stores the selected reference timestamp, exact effective parameter JSON, and SHA-256 checksum. Profile references are informative only; reproducibility uses the immutable snapshot. State snapshots preserve LO, vehicle, bay, queue, the reference-time audit event, and user-update evidence that caused the route version.

## Solver result and dropped LO

Result statuses are `OPTIMAL`, `FEASIBLE`, `PARTIAL`, `INFEASIBLE`, `UNKNOWN`, `TIMEOUT`, legacy `TIME_LIMIT`, and `FAILED`. `PARTIAL` means a feasible subset was retained. `INFEASIBLE` is used only when the model proved that no required solution exists; it is not used for an interrupted or exhausted search. Best feasible output is retained when available. A failed solver updates the Job to `FAILED` and stores run error details instead of crashing the application process.

Routing gate-out/return time is preliminary until the global bay schedule applies actual queue and loading. If that post-bay shift causes a hard MT working-time failure, the coordinator removes the failed trip, returns its LO to the routing pool, excludes the failed MT from those LO, and recomputes every candidate MT from retained post-bay trips: effective availability becomes the latest retained depot return and working-time remaining includes retained queue/loading/travel/service/return. A successful alternative route triggers a global bay re-schedule for retained and reassigned trips together. This loop continues until stable or until no compatible alternative remains. Only the latter is persisted as `VEHICLE_TIME_EXHAUSTED`; `POST_BAY_REASSIGNMENT_TIMEOUT` means the independent retry route/bay budget expired without proving infeasibility. Audit metadata records attempt, reassigned, exhausted, timeout, route-status, and retry-duration counts.

Bay drop reasons distinguish cause:

- `BAY_PRODUCT_CONSTRAINT`: no structurally eligible bay passes an active hard product/loading-duration/bay-stability rule;
- `BAY_CONGESTION`: the trip is structurally eligible, but available bay time/capacity cannot serve it relative to the `serve_loading_order` penalty.

Unserved reason codes include:

- `NO_COMPATIBLE_MT`
- `INSUFFICIENT_CAPACITY`
- `COMPARTMENT_INFEASIBLE`
- `VEHICLE_TIME_EXHAUSTED`
- `DEPOT_TIME_EXHAUSTED`
- `SPBU_TIME_WINDOW`
- `BAY_PRODUCT_CONSTRAINT`
- `BAY_CONGESTION`
- `POST_BAY_REASSIGNMENT_TIMEOUT`
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
- Validation: GET with defaults or POST the current parameter draft to receive profile-aware `READY/WARNING/BLOCKED` results.
- Optimization: initial optimize and reroute; both require `current_time`, interpreted in depot timezone when the submitted value has no offset.
- Versions: list/get, trip detail, simulation, map, cost analysis, dropped LO.
- Parameters: list/get, Save as next version, Save As, and read the canonical constraint catalog.

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

The suite covers Phase 6 soft warm start, one-product compartments, multi-trip, mandatory Planned ETA validation, equal-time availability, freeze horizon, ONGOING/DONE handling, bay product compatibility, actual queue delay, per-compartment loading, ETA lifecycle, route version immutability, and explicit dropped LO.
