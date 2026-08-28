from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .google_routes import GoogleRoutesClient, GoogleRoutesError, decrypt_api_key, get_google_routes_configuration
from .models import MasterDepot, MasterSPBU, RouteAPIRequestLog, RouteMatrixCache


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _coordinates(entity: MasterDepot | MasterSPBU) -> tuple[float, float] | None:
    if entity.latitude is None or entity.longitude is None:
        return None
    latitude, longitude = float(entity.latitude), float(entity.longitude)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
        return None
    return latitude, longitude


def _location_id(entity: MasterDepot | MasterSPBU) -> str:
    return entity.depot_id if isinstance(entity, MasterDepot) else entity.spbu_id


def _haversine_meters(first: tuple[float, float], second: tuple[float, float]) -> int:
    latitude_1, longitude_1 = map(math.radians, first)
    latitude_2, longitude_2 = map(math.radians, second)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = longitude_2 - longitude_1
    value = math.sin(delta_latitude / 2) ** 2 + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(delta_longitude / 2) ** 2
    return round(6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)))


class RouteMatrixService:
    """Materialize matrix data before solver callbacks and cache every pair."""

    def __init__(self, db: Session, *, job_id: str | None = None):
        self.db = db
        self.job_id = job_id
        configuration = get_google_routes_configuration(db)
        self.configuration = configuration
        self.client = None
        if configuration and configuration.encrypted_api_key:
            try:
                self.client = GoogleRoutesClient(decrypt_api_key(configuration.encrypted_api_key))
            except Exception:
                self.client = None

    @staticmethod
    def _bucket(departure: datetime) -> datetime:
        value = _utc(departure)
        return value.replace(minute=(value.minute // 15) * 15, second=0, microsecond=0)

    def _key(self, origin: MasterDepot | MasterSPBU, destination: MasterDepot | MasterSPBU, departure: datetime, parameters: dict) -> str:
        payload = {
            "origin": _location_id(origin),
            "destination": _location_id(destination),
            "departure": self._bucket(departure).isoformat(),
            "vehicle_mode": parameters.get("route_vehicle_mode", "GENERAL_VEHICLE"),
            "traffic_aware": bool(parameters.get("traffic_aware", True)),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _fallback(self, origin: MasterDepot | MasterSPBU, destination: MasterDepot | MasterSPBU) -> dict:
        first, second = _coordinates(origin), _coordinates(destination)
        if first and second:
            distance = round(_haversine_meters(first, second) * 1.25)
            duration = max(60, round(distance / (35_000 / 3600)))
            geometry = [
                {"latitude": first[0], "longitude": first[1]},
                {"latitude": second[0], "longitude": second[1]},
            ]
        else:
            spbu = destination if isinstance(destination, MasterSPBU) else origin if isinstance(origin, MasterSPBU) else None
            distance = round(float(spbu.master_distance_km or 0) * 1000) if spbu else 0
            duration = round(float(spbu.master_travel_time_min or 120) * 60) if spbu else 7200
            geometry = []
        return {
            "distance_meters": distance,
            "duration_seconds": duration,
            "route_geometry": geometry,
            "provider": "MASTER_HAVERSINE_FALLBACK",
            "metadata": {"fallback_used": True},
        }

    def _leg(self, origin: MasterDepot | MasterSPBU, destination: MasterDepot | MasterSPBU, departure: datetime, parameters: dict) -> tuple[dict, bool, bool]:
        if _location_id(origin) == _location_id(destination):
            return {"distance_meters": 0, "duration_seconds": 0, "route_geometry": [], "provider": "SAME_LOCATION", "metadata": {}}, True, False
        key = self._key(origin, destination, departure, parameters)
        now = datetime.now(timezone.utc)
        use_cache = bool(parameters.get("route_matrix_cache_enabled", True))
        cached = self.db.scalar(select(RouteMatrixCache).where(RouteMatrixCache.cache_key == key, RouteMatrixCache.expires_at > now)) if use_cache else None
        if cached:
            return {
                "distance_meters": cached.distance_meters,
                "duration_seconds": cached.duration_seconds,
                "route_geometry": cached.route_geometry or [],
                "route_polyline": cached.route_polyline,
                "provider": cached.provider,
                "metadata": cached.response_metadata or {},
            }, True, False
        estimate = None
        api_called = False
        origin_coordinates, destination_coordinates = _coordinates(origin), _coordinates(destination)
        truck_requested = parameters.get("route_vehicle_mode") == "TRUCK"
        if self.client and origin_coordinates and destination_coordinates and not truck_requested:
            api_called = True
            try:
                response = self.client.compute_route_matrix(
                    origin=origin_coordinates,
                    destination=destination_coordinates,
                    departure_datetime=departure if parameters.get("traffic_aware", True) else None,
                )
                duration = str(response.get("duration") or "0s")
                estimate = {
                    "distance_meters": int(response.get("distanceMeters") or 0),
                    "duration_seconds": round(float(duration[:-1])) if duration.endswith("s") else 0,
                    "route_geometry": [],
                    "provider": "GOOGLE_ROUTES_MATRIX",
                    "metadata": {"fallback_used": False, "condition": response.get("condition")},
                }
            except GoogleRoutesError as exc:
                estimate = self._fallback(origin, destination)
                estimate["metadata"]["google_error_code"] = exc.code
        if estimate is None:
            estimate = self._fallback(origin, destination)
            if truck_requested:
                estimate["provider"] = "TRUCK_MODE_UNAVAILABLE_MASTER_HAVERSINE_FALLBACK"
                estimate["metadata"]["truck_routing_supported"] = False
        if use_cache:
            row = self.db.scalar(select(RouteMatrixCache).where(RouteMatrixCache.cache_key == key))
            if not row:
                row = RouteMatrixCache(route_matrix_cache_id=uuid.uuid4().hex, cache_key=key)
                self.db.add(row)
            row.origin_location_id = _location_id(origin)
            row.destination_location_id = _location_id(destination)
            row.departure_time_bucket = self._bucket(departure)
            row.route_vehicle_mode = parameters.get("route_vehicle_mode", "GENERAL_VEHICLE")
            row.traffic_aware = bool(parameters.get("traffic_aware", True))
            row.distance_meters = int(estimate["distance_meters"])
            row.duration_seconds = int(estimate["duration_seconds"])
            row.route_geometry = estimate.get("route_geometry") or []
            row.provider = estimate["provider"]
            row.response_metadata = estimate.get("metadata") or {}
            row.calculated_at = now
            row.expires_at = now + timedelta(minutes=int(parameters.get("route_matrix_cache_ttl_minutes", 60)))
        return estimate, False, api_called

    @staticmethod
    def _cached_estimate(row: RouteMatrixCache) -> dict:
        return {
            "distance_meters": row.distance_meters,
            "duration_seconds": row.duration_seconds,
            "route_geometry": row.route_geometry or [],
            "route_polyline": row.route_polyline,
            "provider": row.provider,
            "metadata": row.response_metadata or {},
        }

    def _store_cache_estimate(
        self,
        *,
        cache_rows: dict[str, RouteMatrixCache],
        key: str,
        origin: MasterDepot | MasterSPBU,
        destination: MasterDepot | MasterSPBU,
        departure: datetime,
        parameters: dict,
        estimate: dict,
    ) -> None:
        row = cache_rows.get(key)
        if row is None:
            row = RouteMatrixCache(route_matrix_cache_id=uuid.uuid4().hex, cache_key=key)
            cache_rows[key] = row
            self.db.add(row)
        now = datetime.now(timezone.utc)
        row.origin_location_id = _location_id(origin)
        row.destination_location_id = _location_id(destination)
        row.departure_time_bucket = self._bucket(departure)
        row.route_vehicle_mode = parameters.get("route_vehicle_mode", "GENERAL_VEHICLE")
        row.traffic_aware = bool(parameters.get("traffic_aware", True))
        row.distance_meters = int(estimate["distance_meters"])
        row.duration_seconds = int(estimate["duration_seconds"])
        row.route_geometry = estimate.get("route_geometry") or []
        row.provider = str(estimate["provider"])
        row.response_metadata = estimate.get("metadata") or {}
        row.calculated_at = now
        row.expires_at = now + timedelta(minutes=int(parameters.get("route_matrix_cache_ttl_minutes", 60)))

    def build(
        self,
        *,
        depot: MasterDepot,
        loading_orders: list[dict],
        spbus: dict[str, MasterSPBU],
        departure: datetime,
        parameters: dict,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        started = perf_counter()
        # The solver retains one node per LO, but road estimates are materialized
        # once per unique physical location and expanded back to LO-node indexes.
        # This prevents duplicate LOs for one SPBU from multiplying SQL and HTTP
        # work, and lets Google compute up to 625 elements in one request.
        entities: list[MasterDepot | MasterSPBU] = [depot]
        for row in loading_orders:
            entities.append(spbus[row["spbu_id"]])
        size = len(entities)
        unique_entities: list[MasterDepot | MasterSPBU] = []
        unique_index_by_location: dict[str, int] = {}
        entity_to_unique: list[int] = []
        for entity in entities:
            location_id = _location_id(entity)
            if location_id not in unique_index_by_location:
                unique_index_by_location[location_id] = len(unique_entities)
                unique_entities.append(entity)
            entity_to_unique.append(unique_index_by_location[location_id])

        unique_pair_count = max(0, len(unique_entities) * (len(unique_entities) - 1))
        pair_keys = {
            (origin_index, destination_index): self._key(origin, destination, departure, parameters)
            for origin_index, origin in enumerate(unique_entities)
            for destination_index, destination in enumerate(unique_entities)
            if origin_index != destination_index
        }
        use_cache = bool(parameters.get("route_matrix_cache_enabled", True))
        cache_rows: dict[str, RouteMatrixCache] = {}
        if use_cache and pair_keys:
            keys = list(pair_keys.values())
            # Keep each IN predicate portable to SQLite tests and bounded for
            # PostgreSQL query planning on large depot days.
            for offset in range(0, len(keys), 500):
                rows = self.db.scalars(
                    select(RouteMatrixCache).where(RouteMatrixCache.cache_key.in_(keys[offset : offset + 500]))
                ).all()
                cache_rows.update({row.cache_key: row for row in rows})

        now = datetime.now(timezone.utc)
        estimates: dict[tuple[int, int], dict] = {}
        cache_hits = 0
        for pair, key in pair_keys.items():
            cached = cache_rows.get(key)
            if cached and cached.expires_at and _utc(cached.expires_at) > now:
                estimates[pair] = self._cached_estimate(cached)
                cache_hits += 1

        missing = set(pair_keys) - set(estimates)
        google_batch_requests = 0
        google_elements = 0
        fallback_pairs = 0
        google_error_codes: set[str] = set()
        matrix_time_limit = int(parameters.get("route_matrix_time_limit_seconds", 90))
        google_element_budget = int(parameters.get("route_matrix_google_element_budget", 2500))

        def record(pair: tuple[int, int], estimate: dict) -> None:
            nonlocal fallback_pairs
            estimates[pair] = estimate
            missing.discard(pair)
            if estimate.get("provider") != "GOOGLE_ROUTES_MATRIX":
                fallback_pairs += 1
            if use_cache:
                self._store_cache_estimate(
                    cache_rows=cache_rows,
                    key=pair_keys[pair],
                    origin=unique_entities[pair[0]],
                    destination=unique_entities[pair[1]],
                    departure=departure,
                    parameters=parameters,
                    estimate=estimate,
                )

        truck_requested = parameters.get("route_vehicle_mode") == "TRUCK"
        google_indexes = [index for index, entity in enumerate(unique_entities) if _coordinates(entity)]
        google_index_set = set(google_indexes)
        for pair in list(missing):
            if self.client and not truck_requested and pair[0] in google_index_set and pair[1] in google_index_set:
                continue
            estimate = self._fallback(unique_entities[pair[0]], unique_entities[pair[1]])
            if truck_requested:
                estimate["provider"] = "TRUCK_MODE_UNAVAILABLE_MASTER_HAVERSINE_FALLBACK"
                estimate["metadata"]["truck_routing_supported"] = False
            record(pair, estimate)

        if progress_callback:
            progress_callback(
                {
                    "stage": "BUILDING_MATRIX",
                    "completed_unique_pairs": unique_pair_count - len(missing),
                    "total_unique_pairs": unique_pair_count,
                    "cache_hit_count": cache_hits,
                    "google_batch_request_count": google_batch_requests,
                    "google_element_count": google_elements,
                }
            )

        # 25 × 25 is the documented 625-element non-transit request limit.
        blocks = [google_indexes[index : index + 25] for index in range(0, len(google_indexes), 25)]
        stop_google = False
        for origin_block in blocks:
            if stop_google:
                break
            for destination_block in blocks:
                candidates = {
                    (origin_index, destination_index)
                    for origin_index in origin_block
                    for destination_index in destination_block
                    if (origin_index, destination_index) in missing
                }
                if not candidates:
                    continue
                if perf_counter() - started >= matrix_time_limit or google_elements >= google_element_budget:
                    stop_google = True
                    break

                remaining_budget = google_element_budget - google_elements
                request_origins = list(origin_block[: min(len(origin_block), remaining_budget)])
                if not request_origins:
                    stop_google = True
                    break
                destination_limit = min(len(destination_block), remaining_budget // len(request_origins))
                if destination_limit <= 0:
                    stop_google = True
                    break
                request_destinations = list(destination_block[:destination_limit])
                requested_pairs = {
                    (origin_index, destination_index)
                    for origin_index in request_origins
                    for destination_index in request_destinations
                    if (origin_index, destination_index) in missing
                }
                if not requested_pairs:
                    continue
                google_elements += len(request_origins) * len(request_destinations)
                google_batch_requests += 1
                try:
                    response_rows = self.client.compute_route_matrix_batch(
                        origins=[_coordinates(unique_entities[index]) for index in request_origins],
                        destinations=[_coordinates(unique_entities[index]) for index in request_destinations],
                        departure_datetime=departure if parameters.get("traffic_aware", True) else None,
                    )
                    response_by_pair = {
                        (request_origins[int(row.get("originIndex") or 0)], request_destinations[int(row.get("destinationIndex") or 0)]): row
                        for row in response_rows
                        if int(row.get("originIndex") or 0) < len(request_origins)
                        and int(row.get("destinationIndex") or 0) < len(request_destinations)
                    }
                    for pair in requested_pairs:
                        row = response_by_pair.get(pair) or {}
                        status = row.get("status") or {}
                        status_code = int(status.get("code") or 0) if isinstance(status, dict) else 0
                        if row.get("condition") in {None, "ROUTE_EXISTS"} and status_code == 0 and row.get("distanceMeters") is not None:
                            record(
                                pair,
                                {
                                    "distance_meters": int(row.get("distanceMeters") or 0),
                                    "duration_seconds": int(round(float(str(row.get("duration") or "0s")[:-1]))) if str(row.get("duration") or "0s").endswith("s") else 0,
                                    "route_geometry": [],
                                    "provider": "GOOGLE_ROUTES_MATRIX",
                                    "metadata": {"fallback_used": False, "condition": row.get("condition")},
                                },
                            )
                        else:
                            estimate = self._fallback(unique_entities[pair[0]], unique_entities[pair[1]])
                            estimate["metadata"]["google_error_code"] = "GOOGLE_ROUTE_NOT_FOUND"
                            record(pair, estimate)
                except GoogleRoutesError as exc:
                    google_error_codes.add(exc.code)
                    for pair in requested_pairs:
                        estimate = self._fallback(unique_entities[pair[0]], unique_entities[pair[1]])
                        estimate["metadata"]["google_error_code"] = exc.code
                        record(pair, estimate)

                if progress_callback:
                    progress_callback(
                        {
                            "stage": "BUILDING_MATRIX",
                            "completed_unique_pairs": unique_pair_count - len(missing),
                            "total_unique_pairs": unique_pair_count,
                            "cache_hit_count": cache_hits,
                            "google_batch_request_count": google_batch_requests,
                            "google_element_count": google_elements,
                        }
                    )

        fallback_reason = "GOOGLE_MATRIX_TIME_BUDGET" if perf_counter() - started >= matrix_time_limit else "GOOGLE_MATRIX_ELEMENT_BUDGET"
        for pair in list(missing):
            estimate = self._fallback(unique_entities[pair[0]], unique_entities[pair[1]])
            estimate["metadata"]["google_error_code"] = fallback_reason
            record(pair, estimate)

        distance_matrix = [[0] * size for _ in range(size)]
        time_matrix = [[0] * size for _ in range(size)]
        geometry: dict[str, dict] = {}
        for origin_index, origin in enumerate(entities):
            for destination_index, destination in enumerate(entities):
                unique_origin = entity_to_unique[origin_index]
                unique_destination = entity_to_unique[destination_index]
                if unique_origin == unique_destination:
                    continue
                estimate = estimates[unique_origin, unique_destination]
                distance_matrix[origin_index][destination_index] = int(estimate["distance_meters"])
                time_matrix[origin_index][destination_index] = int(estimate["duration_seconds"])
                geometry[f"{origin_index}:{destination_index}"] = {
                    "geometry": estimate.get("route_geometry") or [],
                    "provider": estimate.get("provider"),
                }
        fingerprint = hashlib.sha256(
            json.dumps([_location_id(entity) for entity in entities], sort_keys=True).encode()
        ).hexdigest()
        self.db.add(
            RouteAPIRequestLog(
                request_log_id=uuid.uuid4().hex,
                job_id=self.job_id,
                request_type="PHASE7_MATRIX_BUILD",
                provider="GOOGLE_ROUTES_WITH_FALLBACK",
                request_fingerprint=fingerprint,
                requested_pair_count=max(0, size * (size - 1)),
                cache_hit_count=cache_hits,
                duration_ms=round((perf_counter() - started) * 1000),
                success=True,
                error_message=None,
            )
        )
        return {
            "distance_matrix": distance_matrix,
            "time_matrix": time_matrix,
            "geometry": geometry,
            "metadata": {
                "location_count": size,
                "pair_count": max(0, size * (size - 1)),
                "unique_location_count": len(unique_entities),
                "unique_pair_count": unique_pair_count,
                "cache_hit_count": cache_hits,
                "google_request_count": google_batch_requests,
                "google_batch_request_count": google_batch_requests,
                "google_element_count": google_elements,
                "fallback_pair_count": fallback_pairs,
                "google_error_codes": sorted(google_error_codes),
                "matrix_duration_ms": round((perf_counter() - started) * 1000),
                "google_routes_is_optimizer": False,
                "route_vehicle_mode": parameters.get("route_vehicle_mode", "GENERAL_VEHICLE"),
            },
        }

    def build_route_geometry(
        self,
        *,
        depot: MasterDepot,
        ordered_spbus: list[MasterSPBU],
        departure: datetime,
        parameters: dict,
    ) -> dict:
        """Fetch geometry only for final solver-selected legs, never callbacks."""
        started = perf_counter()
        google_request_budget = max(0, int(parameters.get("route_geometry_google_request_budget", 500)))
        geometry_time_limit = max(0, int(parameters.get("route_geometry_time_limit_seconds", 120)))
        entities: list[MasterDepot | MasterSPBU] = [depot, *ordered_spbus, depot]
        points: list[dict] = []
        sources: list[str] = []
        api_calls = 0
        cache_hits = 0
        cursor = _utc(departure)
        all_coordinates = [_coordinates(entity) for entity in entities]
        truck_requested = parameters.get("route_vehicle_mode") == "TRUCK"
        if (
            self.client
            and not truck_requested
            and google_request_budget > 0
            and geometry_time_limit > 0
            and len(entities) >= 2
            and all(coordinates is not None for coordinates in all_coordinates)
        ):
            try:
                api_calls = 1
                response = self.client.compute_route(
                    origin=all_coordinates[0],
                    destination=all_coordinates[-1],
                    intermediates=all_coordinates[1:-1],
                    departure_datetime=cursor if parameters.get("traffic_aware", True) else None,
                    routing_mode="DRIVE",
                    routing_preference="TRAFFIC_AWARE" if parameters.get("traffic_aware", True) else "TRAFFIC_UNAWARE",
                )
                road_geometry = response.get("route_geometry") or []
                if len(road_geometry) >= 2:
                    return {
                        "route_geometry": road_geometry,
                        "route_geometry_source": "GOOGLE_ROUTES_GEOJSON",
                        "leg_sources": ["GOOGLE_ROUTES_GEOJSON"] * (len(entities) - 1),
                        "google_request_count": api_calls,
                        "cache_hit_count": 0,
                        "geometry_duration_ms": round((perf_counter() - started) * 1000),
                        "google_request_budget": google_request_budget,
                        "geometry_strategy": "SINGLE_ROUTE_WITH_INTERMEDIATES",
                    }
            except GoogleRoutesError:
                # Continue through per-leg cache/API/fallback materialization so
                # one provider failure never removes the route from the map.
                pass
        for origin, destination in zip(entities, entities[1:]):
            leg_departure = cursor
            key = self._key(origin, destination, leg_departure, parameters)
            cached = self.db.scalar(
                select(RouteMatrixCache).where(
                    RouteMatrixCache.cache_key == key,
                    RouteMatrixCache.expires_at > datetime.now(timezone.utc),
                )
            ) if parameters.get("route_matrix_cache_enabled", True) else None
            estimate = None
            if cached and cached.provider == "GOOGLE_ROUTES_GEOJSON" and len(cached.route_geometry or []) >= 2:
                estimate = {
                    "distance_meters": cached.distance_meters,
                    "duration_seconds": cached.duration_seconds,
                    "route_geometry": cached.route_geometry,
                    "provider": cached.provider,
                    "metadata": cached.response_metadata or {},
                }
                cache_hits += 1
            origin_coordinates, destination_coordinates = _coordinates(origin), _coordinates(destination)
            google_budget_available = api_calls < google_request_budget and perf_counter() - started < geometry_time_limit
            if estimate is None and self.client and origin_coordinates and destination_coordinates and not truck_requested and google_budget_available:
                try:
                    api_calls += 1
                    response = self.client.compute_route(
                        origin=origin_coordinates,
                        destination=destination_coordinates,
                        departure_datetime=leg_departure if parameters.get("traffic_aware", True) else None,
                        routing_mode="DRIVE",
                        routing_preference="TRAFFIC_AWARE" if parameters.get("traffic_aware", True) else "TRAFFIC_UNAWARE",
                    )
                    estimate = {
                        "distance_meters": response["distance_meters"],
                        "duration_seconds": response["duration_seconds"],
                        "route_geometry": response["route_geometry"],
                        "provider": response["route_geometry_source"],
                        "metadata": {"fallback_used": False},
                    }
                except GoogleRoutesError:
                    estimate = None
            if estimate is None:
                estimate = self._fallback(origin, destination)
                if truck_requested:
                    estimate["provider"] = "TRUCK_MODE_UNAVAILABLE_MASTER_HAVERSINE_FALLBACK"
                    estimate["metadata"]["truck_routing_supported"] = False
            geometry = estimate.get("route_geometry") or []
            if points and geometry and points[-1] == geometry[0]:
                geometry = geometry[1:]
            points.extend(geometry)
            sources.append(str(estimate["provider"]))
            cursor += timedelta(seconds=int(estimate["duration_seconds"]))
            if parameters.get("route_matrix_cache_enabled", True):
                row = cached or self.db.scalar(select(RouteMatrixCache).where(RouteMatrixCache.cache_key == key))
                if not row:
                    row = RouteMatrixCache(route_matrix_cache_id=uuid.uuid4().hex, cache_key=key)
                    self.db.add(row)
                row.origin_location_id = _location_id(origin)
                row.destination_location_id = _location_id(destination)
                row.departure_time_bucket = self._bucket(leg_departure)
                row.route_vehicle_mode = parameters.get("route_vehicle_mode", "GENERAL_VEHICLE")
                row.traffic_aware = bool(parameters.get("traffic_aware", True))
                row.distance_meters = int(estimate["distance_meters"])
                row.duration_seconds = int(estimate["duration_seconds"])
                row.route_geometry = estimate.get("route_geometry") or []
                row.provider = str(estimate["provider"])
                row.response_metadata = estimate.get("metadata") or {}
                row.calculated_at = datetime.now(timezone.utc)
                row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=int(parameters.get("route_matrix_cache_ttl_minutes", 60)))
        return {
            "route_geometry": points,
            "route_geometry_source": "GOOGLE_ROUTES_GEOJSON" if sources and all(source == "GOOGLE_ROUTES_GEOJSON" for source in sources) else "MIXED_OR_MASTER_FALLBACK",
            "leg_sources": sources,
            "google_request_count": api_calls,
            "cache_hit_count": cache_hits,
            "geometry_duration_ms": round((perf_counter() - started) * 1000),
            "google_request_budget": google_request_budget,
            "geometry_strategy": "PER_LEG_WITH_FALLBACK",
        }
