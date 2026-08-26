from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

try:  # The production image installs OR-Tools; this guard keeps diagnostics import-safe.
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    from ortools.sat.python import cp_model

    ORTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in incomplete host environments.
    pywrapcp = None
    routing_enums_pb2 = None
    cp_model = None
    ORTOOLS_AVAILABLE = False


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _minutes_between(start: datetime, end: datetime) -> int:
    return max(0, math.ceil((_utc(end) - _utc(start)).total_seconds() / 60))


class CompartmentAssignmentService:
    """Assign complete LOs to compartments with one product per compartment."""

    @staticmethod
    def _fallback(loading_orders: list[dict], compartments: list[dict]) -> dict:
        ordered = sorted(loading_orders, key=lambda row: (-float(row["volume_kl"]), str(row["loading_order_id"])))
        remaining = {str(row["compartment_id"]): float(row["capacity_kl"]) for row in compartments}
        products: dict[str, str | None] = {str(row["compartment_id"]): None for row in compartments}
        assignments: list[dict] = []
        for lo in ordered:
            product = str(lo.get("product_id") or "UNKNOWN")
            eligible = [
                compartment
                for compartment in compartments
                if remaining[str(compartment["compartment_id"])] + 1e-9 >= float(lo["volume_kl"])
                and products[str(compartment["compartment_id"])] in {None, product}
            ]
            if not eligible:
                return {"feasible": False, "assignments": [], "reason": "COMPARTMENT_INFEASIBLE", "engine": "DETERMINISTIC_FALLBACK"}
            compartment = min(
                eligible,
                key=lambda row: (
                    products[str(row["compartment_id"])] != product,
                    remaining[str(row["compartment_id"])] - float(lo["volume_kl"]),
                    str(row["compartment_id"]),
                ),
            )
            compartment_id = str(compartment["compartment_id"])
            remaining[compartment_id] -= float(lo["volume_kl"])
            products[compartment_id] = product
            assignments.append({**lo, "compartment_id": compartment_id})
        return {"feasible": True, "assignments": assignments, "reason": None, "engine": "DETERMINISTIC_FALLBACK"}

    def assign(self, loading_orders: list[dict], compartments: list[dict], *, time_limit_seconds: int = 5) -> dict:
        if not loading_orders:
            return {"feasible": True, "assignments": [], "reason": None, "engine": "OR_TOOLS_CP_SAT"}
        if not compartments:
            return {"feasible": False, "assignments": [], "reason": "COMPARTMENT_INFEASIBLE", "engine": "OR_TOOLS_CP_SAT"}
        if not ORTOOLS_AVAILABLE:
            return self._fallback(loading_orders, compartments)

        model = cp_model.CpModel()
        scale = 1000
        products = sorted({str(row.get("product_id") or "UNKNOWN") for row in loading_orders})
        x: dict[tuple[int, int], Any] = {}
        y: dict[tuple[int, str], Any] = {}
        for compartment_index, _ in enumerate(compartments):
            for product in products:
                y[compartment_index, product] = model.new_bool_var(f"product_{compartment_index}_{product}")
            model.add(sum(y[compartment_index, product] for product in products) <= 1)
        for lo_index, lo in enumerate(loading_orders):
            product = str(lo.get("product_id") or "UNKNOWN")
            for compartment_index, _ in enumerate(compartments):
                x[lo_index, compartment_index] = model.new_bool_var(f"lo_{lo_index}_compartment_{compartment_index}")
                model.add(x[lo_index, compartment_index] <= y[compartment_index, product])
            model.add(sum(x[lo_index, compartment_index] for compartment_index in range(len(compartments))) == 1)
        for compartment_index, compartment in enumerate(compartments):
            model.add(
                sum(
                    round(float(lo["volume_kl"]) * scale) * x[lo_index, compartment_index]
                    for lo_index, lo in enumerate(loading_orders)
                )
                <= round(float(compartment["capacity_kl"]) * scale)
            )
        # Prefer fewer used compartments, then deterministic lower compartment indexes.
        model.minimize(
            sum((100 + compartment_index) * y[compartment_index, product] for compartment_index in range(len(compartments)) for product in products)
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(max(1, time_limit_seconds))
        solver.parameters.num_search_workers = 1
        status = solver.solve(model)
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            return {"feasible": False, "assignments": [], "reason": "COMPARTMENT_INFEASIBLE", "engine": "OR_TOOLS_CP_SAT"}
        assignments = []
        for lo_index, lo in enumerate(loading_orders):
            compartment_index = next(
                index for index in range(len(compartments)) if solver.value(x[lo_index, index])
            )
            assignments.append({**lo, "compartment_id": str(compartments[compartment_index]["compartment_id"])})
        return {"feasible": True, "assignments": assignments, "reason": None, "engine": "OR_TOOLS_CP_SAT"}


class VRPOptimizationService:
    """OR-Tools Routing solver wrapped in a physical-vehicle multi-trip loop."""

    def __init__(self) -> None:
        self.compartments = CompartmentAssignmentService()

    @staticmethod
    def _vehicle_cost(parameters: dict, vehicle: dict) -> int:
        rules = sorted(
            parameters.get("vehicle_activation_cost_rules") or [],
            key=lambda rule: (int(rule.get("priority") or 0), bool(rule.get("vehicle_tag"))),
            reverse=True,
        )
        tags = set(vehicle.get("tags") or [])
        for rule in rules:
            class_matches = rule.get("vehicle_class") in {None, vehicle.get("vehicle_class")}
            tag_matches = not rule.get("vehicle_tag") or rule["vehicle_tag"] in tags
            if class_matches and tag_matches:
                return round(float(rule.get("activation_cost") or 0))
        return 0

    @staticmethod
    def _fallback_round(
        remaining: list[dict], vehicles: list[dict], distance_matrix: list[list[int]], time_matrix: list[list[int]]
    ) -> tuple[list[dict], set[int], str, float]:
        routes: list[dict] = []
        served: set[int] = set()
        for vehicle_index, vehicle in enumerate(vehicles):
            capacity = float(vehicle["capacity_kl"])
            allowed = [
                index for index, row in enumerate(remaining, start=1)
                if index not in served and vehicle["mt_id"] in set(row.get("allowed_vehicle_ids") or [])
            ]
            allowed.sort(key=lambda node: (remaining[node - 1].get("phase6_predicted_vehicle_id") != vehicle["mt_id"], node))
            selected = []
            used = 0.0
            for node in allowed:
                volume = float(remaining[node - 1]["volume_kl"])
                if used + volume <= capacity + 1e-9:
                    selected.append(node)
                    used += volume
            if selected:
                served.update(selected)
                routes.append({"vehicle_index": vehicle_index, "node_indices": selected})
        return routes, served, "FEASIBLE", 0.0

    def _solve_round(
        self,
        *,
        remaining: list[dict],
        vehicles: list[dict],
        distance_matrix: list[list[int]],
        time_matrix: list[list[int]],
        day_start: datetime,
        depot_close_minutes: int,
        parameters: dict,
    ) -> tuple[list[dict], set[int], str, float]:
        if not ORTOOLS_AVAILABLE:
            return self._fallback_round(remaining, vehicles, distance_matrix, time_matrix)
        manager = pywrapcp.RoutingIndexManager(len(remaining) + 1, len(vehicles), 0)
        routing = pywrapcp.RoutingModel(manager)
        service_minutes = int(parameters.get("default_spbu_service_minutes", 30))

        def minute_callback(from_index: int, to_index: int) -> int:
            source = manager.IndexToNode(from_index)
            target = manager.IndexToNode(to_index)
            return math.ceil(time_matrix[source][target] / 60) + (service_minutes if source else 0)

        minute_index = routing.RegisterTransitCallback(minute_callback)
        routing.AddDimension(minute_index, 24 * 60, max(depot_close_minutes + 24 * 60, 1), False, "Time")
        time_dimension = routing.GetDimensionOrDie("Time")

        def demand_callback(index: int) -> int:
            node = manager.IndexToNode(index)
            return 0 if node == 0 else round(float(remaining[node - 1]["volume_kl"]) * 1000)

        demand_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(
            demand_index,
            0,
            [round(float(vehicle["capacity_kl"]) * 1000) for vehicle in vehicles],
            True,
            "Capacity",
        )

        objective = parameters["objective"]
        for vehicle_index, vehicle in enumerate(vehicles):
            def cost_callback(from_index: int, to_index: int, vehicle_index: int = vehicle_index) -> int:
                source = manager.IndexToNode(from_index)
                target = manager.IndexToNode(to_index)
                if objective == "MIN_TOTAL_DISTANCE":
                    base = distance_matrix[source][target]
                elif objective == "MIN_TOTAL_OPERATING_TIME":
                    base = math.ceil(time_matrix[source][target] / 60) * 1000
                else:
                    base = round(
                        distance_matrix[source][target] / 1000 * float(parameters["cost_per_km"])
                        + time_matrix[source][target] / 3600 * float(parameters["cost_per_operating_hour"])
                    )
                if target:
                    target_lo = remaining[target - 1]
                    preferred = target_lo.get("phase6_predicted_vehicle_id")
                    if preferred and preferred != vehicles[vehicle_index]["mt_id"]:
                        base += round(float(parameters.get("phase6_vehicle_change_penalty", 0)))
                    current_vehicle = target_lo.get("current_vehicle_id")
                    if current_vehicle and current_vehicle != vehicles[vehicle_index]["mt_id"]:
                        base += round(float(parameters.get("vehicle_reassignment_penalty", 0)))
                        base += round(float(parameters.get("plan_change_penalty", 0)))
                    affinity = float((target_lo.get("historical_mt_affinity") or {}).get(vehicles[vehicle_index]["mt_id"], 0))
                    base += round((1 - affinity) * float(parameters.get("historical_mt_affinity_penalty", 0)))
                    previous_gate_out = target_lo.get("current_planned_gate_out_minutes")
                    if previous_gate_out is not None:
                        available = _minutes_between(day_start, vehicles[vehicle_index]["effective_eta_depot"])
                        if abs(int(previous_gate_out) - available) > int(parameters.get("departure_time_tolerance_minutes", 5)):
                            base += round(float(parameters.get("gateout_change_penalty", 0)))
                if source and target:
                    source_lo = remaining[source - 1]
                    target_lo = remaining[target - 1]
                    if source_lo.get("phase6_predicted_shipment_id") != target_lo.get("phase6_predicted_shipment_id"):
                        base += round(float(parameters.get("phase6_shipment_change_penalty", 0)))
                    if source_lo.get("current_shipment_id") and source_lo.get("current_shipment_id") != target_lo.get("current_shipment_id"):
                        base += round(float(parameters.get("shipment_change_penalty", 0)))
                    pairing_score = float((source_lo.get("historical_pairing_scores") or {}).get(target_lo.get("spbu_id"), 0))
                    base += round((1 - pairing_score) * float(parameters.get("historical_pairing_penalty", 0)))
                    source_sequence = source_lo.get("current_stop_sequence")
                    target_sequence = target_lo.get("current_stop_sequence")
                    if source_sequence is not None and target_sequence is not None and int(source_sequence) >= int(target_sequence):
                        base += round(float(parameters.get("route_sequence_change_penalty", 0)))
                return max(0, int(base))

            cost_index = routing.RegisterTransitCallback(cost_callback)
            routing.SetArcCostEvaluatorOfVehicle(cost_index, vehicle_index)
            routing.SetFixedCostOfVehicle(self._vehicle_cost(parameters, vehicle), vehicle_index)
            available_minutes = _minutes_between(day_start, vehicle["effective_eta_depot"])
            maximum_end = min(depot_close_minutes, available_minutes + int(vehicle["working_time_remaining_minutes"]))
            time_dimension.CumulVar(routing.Start(vehicle_index)).SetRange(available_minutes, maximum_end)
            time_dimension.CumulVar(routing.End(vehicle_index)).SetRange(available_minutes, maximum_end)

        unserved_penalty = max(1, round(float(parameters.get("unserved_penalty", 10_000_000))))
        mt_index_by_id = {vehicle["mt_id"]: index for index, vehicle in enumerate(vehicles)}
        for node, lo in enumerate(remaining, start=1):
            index = manager.NodeToIndex(node)
            allowed = [mt_index_by_id[mt_id] for mt_id in lo.get("allowed_vehicle_ids") or [] if mt_id in mt_index_by_id]
            if allowed:
                routing.SetAllowedVehiclesForIndex(allowed, index)
            else:
                # An empty compatibility intersection means explicitly
                # unserved; OR-Tools otherwise interprets an omitted vehicle
                # restriction as "all vehicles allowed".
                routing.ActiveVar(index).SetValue(0)
            routing.AddDisjunction([index], unserved_penalty)
            if lo.get("mandatory", True):
                # Mandatory remains droppable only through the explicit high-cost
                # disjunction so an infeasibility narrative can always be persisted.
                pass
            if lo.get("time_window_start_minutes") is not None and lo.get("time_window_end_minutes") is not None:
                time_dimension.CumulVar(index).SetRange(
                    int(lo["time_window_start_minutes"]), int(lo["time_window_end_minutes"])
                )

        search = pywrapcp.DefaultRoutingSearchParameters()
        search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
        search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search.time_limit.FromSeconds(int(parameters.get("optimization_time_limit", 30)))
        search.log_search = False

        warm_routes: list[list[int]] = [[] for _ in vehicles]
        for node, lo in enumerate(remaining, start=1):
            preferred_index = mt_index_by_id.get(lo.get("phase6_predicted_vehicle_id"))
            if preferred_index is not None and lo.get("phase6_predicted_vehicle_id") in set(lo.get("allowed_vehicle_ids") or []):
                warm_routes[preferred_index].append(node)
        warm_assignment = routing.ReadAssignmentFromRoutes(warm_routes, True) if any(warm_routes) else None
        solution = routing.SolveFromAssignmentWithParameters(warm_assignment, search) if warm_assignment else routing.SolveWithParameters(search)
        if solution is None:
            return [], set(), "INFEASIBLE", 0.0
        routes: list[dict] = []
        served: set[int] = set()
        for vehicle_index in range(len(vehicles)):
            index = routing.Start(vehicle_index)
            nodes: list[int] = []
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                if node:
                    nodes.append(node)
                    served.add(node)
                index = solution.Value(routing.NextVar(index))
            if nodes:
                routes.append({"vehicle_index": vehicle_index, "node_indices": nodes})
        status = "OPTIMAL" if routing.status() == routing_enums_pb2.RoutingSearchStatus.ROUTING_OPTIMAL else "FEASIBLE"
        return routes, served, status, float(solution.ObjectiveValue())

    def solve(
        self,
        *,
        loading_orders: list[dict],
        vehicles: list[dict],
        distance_matrix: list[list[int]],
        time_matrix: list[list[int]],
        day_start: datetime,
        depot_close: datetime,
        parameters: dict,
    ) -> dict:
        remaining = [dict(row) for row in loading_orders]
        # Matrices are indexed to the original loading-order list.  Every round
        # builds the corresponding depot + remaining-node submatrix.
        original_index = {str(row["loading_order_id"]): index for index, row in enumerate(loading_orders, start=1)}
        vehicle_state = {row["mt_id"]: dict(row) for row in vehicles}
        trips: list[dict] = []
        dropped: list[dict] = []
        solver_statuses: list[str] = []
        objective_value = 0.0
        depot_close_minutes = _minutes_between(day_start, depot_close)
        max_trips = int(parameters.get("maximum_trips_per_mt", 6))

        for trip_round in range(1, max_trips + 1):
            if not remaining:
                break
            active_vehicles = [
                row for row in vehicle_state.values()
                if row.get("operational_status") != "UNAVAILABLE"
                and row.get("effective_eta_depot") is not None
                and _utc(row["effective_eta_depot"]) < _utc(depot_close)
                and int(row.get("working_time_remaining_minutes") or 0) > 0
            ]
            if not active_vehicles:
                break
            matrix_indexes = [0, *[original_index[str(row["loading_order_id"])] for row in remaining]]
            round_distance = [[distance_matrix[i][j] for j in matrix_indexes] for i in matrix_indexes]
            round_time = [[time_matrix[i][j] for j in matrix_indexes] for i in matrix_indexes]
            raw_routes, _, status, round_objective = self._solve_round(
                remaining=remaining,
                vehicles=active_vehicles,
                distance_matrix=round_distance,
                time_matrix=round_time,
                day_start=day_start,
                depot_close_minutes=depot_close_minutes,
                parameters=parameters,
            )
            solver_statuses.append(status)
            objective_value += round_objective
            if not raw_routes:
                break
            accepted_ids: set[str] = set()
            for raw_route in raw_routes:
                vehicle = active_vehicles[raw_route["vehicle_index"]]
                route_los = [remaining[node - 1] for node in raw_route["node_indices"]]
                compartment_result = self.compartments.assign(
                    route_los, vehicle.get("compartments") or [], time_limit_seconds=min(5, int(parameters.get("optimization_time_limit", 30)))
                )
                if not compartment_result["feasible"]:
                    for lo in route_los:
                        dropped.append({**lo, "reason_code": "COMPARTMENT_INFEASIBLE", "reason_description": "LO product volumes cannot be placed into this MT without mixing products inside a compartment."})
                        accepted_ids.add(str(lo["loading_order_id"]))
                    continue
                ready = _utc(vehicle["effective_eta_depot"])
                grouped_stops: list[tuple[int, list[dict]]] = []
                grouped_index: dict[str, int] = {}
                for lo in route_los:
                    spbu_id = str(lo["spbu_id"])
                    if spbu_id in grouped_index:
                        grouped_stops[grouped_index[spbu_id]][1].append(lo)
                    else:
                        grouped_index[spbu_id] = len(grouped_stops)
                        grouped_stops.append((original_index[str(lo["loading_order_id"])], [lo]))
                previous = 0
                drive_seconds = 0
                stops = []
                arrival = ready
                for sequence, (node, stop_los) in enumerate(grouped_stops, start=1):
                    leg_seconds = int(time_matrix[previous][node])
                    drive_seconds += leg_seconds
                    arrival += timedelta(seconds=leg_seconds)
                    departure = arrival + timedelta(minutes=int(parameters.get("default_spbu_service_minutes", 30)))
                    stops.append({"sequence": sequence, "loading_order": stop_los[0], "loading_orders": stop_los, "arrival": arrival, "departure": departure, "leg_seconds": leg_seconds, "leg_distance_meters": int(distance_matrix[previous][node])})
                    arrival = departure
                    previous = node
                return_seconds = int(time_matrix[previous][0])
                drive_seconds += return_seconds
                preliminary_return = arrival + timedelta(seconds=return_seconds)
                distance_meters = sum(int(stop["leg_distance_meters"]) for stop in stops) + int(distance_matrix[previous][0])
                operating_minutes = _minutes_between(ready, preliminary_return)
                if preliminary_return > _utc(depot_close) or operating_minutes > int(vehicle["working_time_remaining_minutes"]):
                    # The time dimension normally prevents this; retain an explicit
                    # guard for fallback matrices and post-compartment validation.
                    continue
                trip_number = int(vehicle.get("completed_trip_count") or 0) + 1
                trip = {
                    "vehicle_id": vehicle["mt_id"],
                    "trip_number": trip_number,
                    "shipment_id": f"P7-SHIP-{vehicle['mt_id']}-{trip_number:02d}",
                    "vehicle_ready_at_depot": ready,
                    "preliminary_gate_out": ready,
                    "gate_out": ready,
                    "estimated_return_depot": preliminary_return,
                    "distance_meters": distance_meters,
                    "driving_seconds": drive_seconds,
                    "service_seconds": len(stops) * int(parameters.get("default_spbu_service_minutes", 30)) * 60,
                    "operating_minutes": operating_minutes,
                    "stops": stops,
                    "lo_assignments": compartment_result["assignments"],
                    "compartment_engine": compartment_result["engine"],
                }
                trips.append(trip)
                accepted_ids.update(str(lo["loading_order_id"]) for lo in route_los)
                state = vehicle_state[vehicle["mt_id"]]
                state["effective_eta_depot"] = preliminary_return
                state["completed_trip_count"] = trip_number
                state["working_time_used_minutes"] = int(state.get("working_time_used_minutes") or 0) + operating_minutes
                state["working_time_remaining_minutes"] = max(0, int(state.get("working_time_remaining_minutes") or 0) - operating_minutes)
            if not accepted_ids:
                break
            remaining = [row for row in remaining if str(row["loading_order_id"]) not in accepted_ids]

        for lo in remaining:
            if lo.get("user_cancelled"):
                code = "USER_CANCELLED"
                description = "LO was cancelled by the dispatcher and is excluded from routing."
            elif not lo.get("allowed_vehicle_ids"):
                code = "NO_COMPATIBLE_MT"
                description = "No active depot MT passes vehicle-class and master-tag compatibility for this SPBU."
            elif all(float(vehicle_state[mt_id]["capacity_kl"]) + 1e-9 < float(lo["volume_kl"]) for mt_id in lo["allowed_vehicle_ids"] if mt_id in vehicle_state):
                code = "INSUFFICIENT_CAPACITY"
                description = "All compatible MT have less total capacity than the LO volume."
            elif all(_utc(vehicle_state[mt_id]["effective_eta_depot"]) >= _utc(depot_close) for mt_id in lo["allowed_vehicle_ids"] if mt_id in vehicle_state):
                code = "DEPOT_TIME_EXHAUSTED"
                description = "All compatible MT become available after the depot gate-out operating limit."
            else:
                code = "NO_FEASIBLE_ROUTE"
                description = "No route satisfies the remaining capacity, working-time, time-window, and depot constraints."
            dropped.append({**lo, "reason_code": code, "reason_description": description})
        if trips and dropped:
            final_status = "PARTIAL"
        elif trips:
            final_status = "OPTIMAL" if solver_statuses and all(status == "OPTIMAL" for status in solver_statuses) else "FEASIBLE"
        else:
            final_status = "INFEASIBLE"
        return {
            "solver_status": final_status,
            "objective_value": objective_value,
            "trips": trips,
            "dropped": dropped,
            "vehicle_state": list(vehicle_state.values()),
            "solver_metadata": {
                "engine": "OR_TOOLS_ROUTING" if ORTOOLS_AVAILABLE else "DETERMINISTIC_FALLBACK",
                "round_statuses": solver_statuses,
                "multi_trip_rounds": max((trip["trip_number"] for trip in trips), default=0),
            },
        }


class BayQueueOptimizationService:
    """CP-SAT loading-bay scheduler with actual occupancy and queue blocks."""

    @staticmethod
    def loading_minutes(trip: dict, durations: dict[str, int], *, loading_mode: str, arms: int = 1) -> int:
        product_by_compartment: dict[str, str] = {}
        for assignment in trip.get("lo_assignments") or []:
            compartment = str(assignment["compartment_id"])
            product = str(assignment.get("product_id") or "UNKNOWN")
            existing = product_by_compartment.get(compartment)
            if existing and existing != product:
                raise ValueError("ONE_COMPARTMENT_ONE_PRODUCT")
            product_by_compartment[compartment] = product
        values = [int(durations.get(product, 0)) for product in product_by_compartment.values()]
        if not values or any(value <= 0 for value in values):
            raise ValueError("MISSING_LOADING_DURATION")
        if loading_mode == "PARALLEL":
            return max(max(values), math.ceil(sum(values) / max(1, arms)))
        return sum(values)

    def schedule(
        self,
        *,
        trips: list[dict],
        bays: list[dict],
        actual_states: list[dict],
        initial_queue: list[dict],
        loading_durations: dict[str, int],
        day_start: datetime,
        depot_close: datetime,
        parameters: dict,
    ) -> dict:
        if not trips:
            return {"solver_status": "FEASIBLE", "assignments": [], "dropped_trip_indexes": [], "engine": "OR_TOOLS_CP_SAT"}
        if not ORTOOLS_AVAILABLE:
            return self._fallback_schedule(
                trips=trips, bays=bays, actual_states=actual_states, initial_queue=initial_queue,
                loading_durations=loading_durations, day_start=day_start, depot_close=depot_close, parameters=parameters,
            )
        horizon = max(1, _minutes_between(day_start, depot_close))
        state_by_bay = {row["master_bay_id"]: row for row in actual_states}
        queue_by_bay: dict[str, list[dict]] = defaultdict(list)
        for row in initial_queue:
            queue_by_bay[row["master_bay_id"]].append(row)
        blocked_until: dict[str, int] = {}
        for bay in bays:
            bay_id = bay["master_bay_id"]
            state = state_by_bay.get(bay_id)
            relevant_queue = sorted(queue_by_bay[bay_id], key=lambda row: row["queue_position"])
            trip_ready_floor = min(
                (_minutes_between(day_start, trip["vehicle_ready_at_depot"]) for trip in trips),
                default=0,
            )
            queue_effective_floor = min(
                (
                    _minutes_between(day_start, row["state_effective_at"])
                    for row in relevant_queue
                    if row.get("state_effective_at")
                ),
                default=trip_ready_floor,
            )
            blocked = max(trip_ready_floor, queue_effective_floor)
            if state:
                blocked = max(blocked, _minutes_between(day_start, state["state_effective_at"]) + int(state.get("remaining_loading_minutes") or 0))
            blocked += sum(int(row.get("estimated_loading_duration_minutes") or 0) for row in relevant_queue)
            blocked_until[bay_id] = blocked

        model = cp_model.CpModel()
        starts: dict[int, Any] = {}
        ends: dict[int, Any] = {}
        presences: dict[tuple[int, int], Any] = {}
        intervals_by_bay: dict[int, list[Any]] = defaultdict(list)
        duration_by_trip_bay: dict[tuple[int, int], int] = {}
        eligible_by_trip: dict[int, list[int]] = defaultdict(list)
        gate_process = int(parameters.get("gate_process_time", 5))
        for trip_index, trip in enumerate(trips):
            starts[trip_index] = model.new_int_var(0, horizon, f"trip_{trip_index}_start")
            ends[trip_index] = model.new_int_var(0, horizon, f"trip_{trip_index}_end")
            ready = _minutes_between(day_start, trip["vehicle_ready_at_depot"])
            model.add(starts[trip_index] >= ready)
            trip_products = {str(row.get("product_id") or "UNKNOWN") for row in trip.get("lo_assignments") or []}
            for bay_index, bay in enumerate(bays):
                allowed_products = set(bay.get("allowed_product_ids") or [])
                if not bay.get("all_products_allowed") and not trip_products.issubset(allowed_products):
                    continue
                try:
                    duration = self.loading_minutes(
                        trip,
                        loading_durations,
                        loading_mode=str(bay.get("loading_mode") or parameters.get("loading_mode", "SEQUENTIAL")),
                        arms=int(bay.get("number_of_loading_arms") or 1),
                    )
                except ValueError:
                    continue
                presence = model.new_bool_var(f"trip_{trip_index}_bay_{bay_index}")
                interval = model.new_optional_interval_var(starts[trip_index], duration, ends[trip_index], presence, f"interval_{trip_index}_{bay_index}")
                presences[trip_index, bay_index] = presence
                intervals_by_bay[bay_index].append(interval)
                duration_by_trip_bay[trip_index, bay_index] = duration
                eligible_by_trip[trip_index].append(bay_index)
                model.add(starts[trip_index] >= blocked_until[bay["master_bay_id"]]).only_enforce_if(presence)
            if eligible_by_trip[trip_index]:
                model.add(sum(presences[trip_index, bay_index] for bay_index in eligible_by_trip[trip_index]) == 1)
        for bay_index, intervals in intervals_by_bay.items():
            model.add_no_overlap(intervals)
        schedulable = [index for index in range(len(trips)) if eligible_by_trip[index]]
        if not schedulable:
            return {"solver_status": "INFEASIBLE", "assignments": [], "dropped_trip_indexes": list(range(len(trips))), "engine": "OR_TOOLS_CP_SAT"}
        model.minimize(sum(ends[index] for index in schedulable))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(max(1, int(parameters.get("optimization_time_limit", 30))))
        solver.parameters.num_search_workers = 1
        status = solver.solve(model)
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            return {"solver_status": "INFEASIBLE", "assignments": [], "dropped_trip_indexes": list(range(len(trips))), "engine": "OR_TOOLS_CP_SAT"}
        assignments = []
        for trip_index in schedulable:
            bay_index = next(index for index in eligible_by_trip[trip_index] if solver.value(presences[trip_index, index]))
            trip = trips[trip_index]
            start_minutes = solver.value(starts[trip_index])
            finish_minutes = solver.value(ends[trip_index])
            ready_minutes = _minutes_between(day_start, trip["vehicle_ready_at_depot"])
            assignments.append(
                {
                    "trip_index": trip_index,
                    "master_bay_id": bays[bay_index]["master_bay_id"],
                    "vehicle_ready_at_depot": _utc(trip["vehicle_ready_at_depot"]),
                    "queue_start": _utc(trip["vehicle_ready_at_depot"]),
                    "loading_start": _utc(day_start) + timedelta(minutes=start_minutes),
                    "loading_finish": _utc(day_start) + timedelta(minutes=finish_minutes),
                    "gate_out": _utc(day_start) + timedelta(minutes=finish_minutes + gate_process),
                    "queue_minutes": max(0, start_minutes - ready_minutes),
                    "loading_minutes": duration_by_trip_bay[trip_index, bay_index],
                }
            )
        return {
            "solver_status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "assignments": assignments,
            "dropped_trip_indexes": [index for index in range(len(trips)) if index not in schedulable],
            "engine": "OR_TOOLS_CP_SAT",
        }

    def _fallback_schedule(
        self, *, trips: list[dict], bays: list[dict], actual_states: list[dict], initial_queue: list[dict],
        loading_durations: dict[str, int], day_start: datetime, depot_close: datetime, parameters: dict,
    ) -> dict:
        state_by_bay = {row["master_bay_id"]: row for row in actual_states}
        available: dict[str, datetime] = {}
        trip_ready_floor = min((_utc(trip["vehicle_ready_at_depot"]) for trip in trips), default=_utc(day_start))
        for bay in bays:
            state = state_by_bay.get(bay["master_bay_id"])
            queue_rows = [row for row in initial_queue if row["master_bay_id"] == bay["master_bay_id"]]
            queue_effective_floor = min(
                (_utc(row["state_effective_at"]) for row in queue_rows if row.get("state_effective_at")),
                default=trip_ready_floor,
            )
            value = max(trip_ready_floor, queue_effective_floor)
            if state:
                value = max(value, _utc(state["state_effective_at"]) + timedelta(minutes=int(state.get("remaining_loading_minutes") or 0)))
            value += timedelta(minutes=sum(int(row.get("estimated_loading_duration_minutes") or 0) for row in queue_rows))
            available[bay["master_bay_id"]] = value
        assignments, dropped = [], []
        for trip_index, trip in sorted(enumerate(trips), key=lambda item: _utc(item[1]["vehicle_ready_at_depot"])):
            products = {str(row.get("product_id") or "UNKNOWN") for row in trip.get("lo_assignments") or []}
            candidates = []
            for bay in bays:
                if not bay.get("all_products_allowed") and not products.issubset(set(bay.get("allowed_product_ids") or [])):
                    continue
                try:
                    duration = self.loading_minutes(trip, loading_durations, loading_mode=str(bay.get("loading_mode") or "SEQUENTIAL"), arms=int(bay.get("number_of_loading_arms") or 1))
                except ValueError:
                    continue
                start = max(_utc(trip["vehicle_ready_at_depot"]), available[bay["master_bay_id"]])
                candidates.append((start, bay, duration))
            if not candidates:
                dropped.append(trip_index)
                continue
            start, bay, duration = min(candidates, key=lambda row: (row[0], row[1]["master_bay_id"]))
            finish = start + timedelta(minutes=duration)
            gate_out = finish + timedelta(minutes=int(parameters.get("gate_process_time", 5)))
            if gate_out > _utc(depot_close):
                dropped.append(trip_index)
                continue
            ready = _utc(trip["vehicle_ready_at_depot"])
            available[bay["master_bay_id"]] = finish
            assignments.append({"trip_index": trip_index, "master_bay_id": bay["master_bay_id"], "vehicle_ready_at_depot": ready, "queue_start": ready, "loading_start": start, "loading_finish": finish, "gate_out": gate_out, "queue_minutes": _minutes_between(ready, start), "loading_minutes": duration})
        return {"solver_status": "FEASIBLE" if assignments else "INFEASIBLE", "assignments": assignments, "dropped_trip_indexes": dropped, "engine": "DETERMINISTIC_FALLBACK"}


class OptimizationCoordinatorService:
    def __init__(self) -> None:
        self.vrp = VRPOptimizationService()
        self.bay = BayQueueOptimizationService()

    def optimize(
        self,
        *,
        loading_orders: list[dict],
        vehicles: list[dict],
        distance_matrix: list[list[int]],
        time_matrix: list[list[int]],
        bays: list[dict],
        actual_bay_states: list[dict],
        initial_queue: list[dict],
        loading_durations: dict[str, int],
        day_start: datetime,
        depot_close: datetime,
        parameters: dict,
    ) -> dict:
        vrp_result = self.vrp.solve(
            loading_orders=loading_orders,
            vehicles=vehicles,
            distance_matrix=distance_matrix,
            time_matrix=time_matrix,
            day_start=day_start,
            depot_close=depot_close,
            parameters=parameters,
        )
        trips = vrp_result["trips"]
        iterations = 0
        previous_gateouts: list[datetime] = []
        bay_result = {"solver_status": "FEASIBLE", "assignments": [], "dropped_trip_indexes": [], "engine": "OR_TOOLS_CP_SAT"}
        for iterations in range(1, int(parameters.get("max_coordination_iterations", 5)) + 1):
            # Propagate the previous trip's return so one physical MT cannot
            # overlap itself after bay waiting shifts a gate-out.
            last_return: dict[str, datetime] = {}
            for trip in sorted(trips, key=lambda row: (row["vehicle_id"], row["trip_number"])):
                if trip["vehicle_id"] in last_return:
                    trip["vehicle_ready_at_depot"] = max(_utc(trip["vehicle_ready_at_depot"]), last_return[trip["vehicle_id"]])
                last_return[trip["vehicle_id"]] = _utc(trip["estimated_return_depot"])
            bay_result = self.bay.schedule(
                trips=trips,
                bays=bays,
                actual_states=actual_bay_states,
                initial_queue=initial_queue,
                loading_durations=loading_durations,
                day_start=day_start,
                depot_close=depot_close,
                parameters=parameters,
            )
            assignment_by_trip = {row["trip_index"]: row for row in bay_result["assignments"]}
            current_gateouts = []
            for trip_index, trip in enumerate(trips):
                schedule = assignment_by_trip.get(trip_index)
                if not schedule:
                    continue
                shift = _utc(schedule["gate_out"]) - _utc(trip["gate_out"])
                trip.update(schedule)
                trip["estimated_return_depot"] = _utc(trip["estimated_return_depot"]) + shift
                trip["operating_minutes"] = _minutes_between(trip["vehicle_ready_at_depot"], trip["estimated_return_depot"])
                current_gateouts.append(_utc(trip["gate_out"]))
            if previous_gateouts and len(previous_gateouts) == len(current_gateouts):
                delta = max(abs((current - previous).total_seconds()) / 60 for current, previous in zip(current_gateouts, previous_gateouts, strict=True)) if current_gateouts else 0
                if delta <= float(parameters.get("departure_time_tolerance_minutes", 5)):
                    break
            previous_gateouts = current_gateouts

        bay_dropped = set(bay_result.get("dropped_trip_indexes") or [])
        if bay_dropped:
            kept, extra_dropped = [], []
            for index, trip in enumerate(trips):
                if index not in bay_dropped:
                    kept.append(trip)
                    continue
                for lo in trip.get("lo_assignments") or []:
                    extra_dropped.append({**lo, "reason_code": "BAY_PRODUCT_CONSTRAINT", "reason_description": "No eligible loading bay can load every required compartment product before depot close."})
            trips = kept
            vrp_result["dropped"].extend(extra_dropped)
        solver_status = "PARTIAL" if trips and vrp_result["dropped"] else ("FEASIBLE" if trips else "INFEASIBLE")
        return {
            **vrp_result,
            "trips": trips,
            "solver_status": solver_status,
            "coordination_iterations": iterations,
            "solver_metadata": {
                **vrp_result["solver_metadata"],
                "bay_engine": bay_result["engine"],
                "bay_solver_status": bay_result["solver_status"],
            },
        }
