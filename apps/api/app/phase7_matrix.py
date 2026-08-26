from __future__ import annotations

import hashlib
import json
import math
import uuid
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

    def build(self, *, depot: MasterDepot, loading_orders: list[dict], spbus: dict[str, MasterSPBU], departure: datetime, parameters: dict) -> dict:
        started = perf_counter()
        # Preserve one node per LO for solver assignment, while the pair cache
        # prevents repeated Google calls for LOs sharing one SPBU.
        entities: list[MasterDepot | MasterSPBU] = [depot]
        for row in loading_orders:
            entities.append(spbus[row["spbu_id"]])
        size = len(entities)
        distance_matrix = [[0] * size for _ in range(size)]
        time_matrix = [[0] * size for _ in range(size)]
        geometry: dict[str, dict] = {}
        cache_hits = 0
        api_calls = 0
        for origin_index, origin in enumerate(entities):
            for destination_index, destination in enumerate(entities):
                if origin_index == destination_index:
                    continue
                estimate, cached, api_called = self._leg(origin, destination, departure, parameters)
                cache_hits += int(cached)
                api_calls += int(api_called)
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
                "cache_hit_count": cache_hits,
                "google_request_count": api_calls,
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
        entities: list[MasterDepot | MasterSPBU] = [depot, *ordered_spbus, depot]
        points: list[dict] = []
        sources: list[str] = []
        api_calls = 0
        cache_hits = 0
        cursor = _utc(departure)
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
            if cached and len(cached.route_geometry or []) >= 2:
                estimate = {
                    "distance_meters": cached.distance_meters,
                    "duration_seconds": cached.duration_seconds,
                    "route_geometry": cached.route_geometry,
                    "provider": cached.provider,
                    "metadata": cached.response_metadata or {},
                }
                cache_hits += 1
            origin_coordinates, destination_coordinates = _coordinates(origin), _coordinates(destination)
            truck_requested = parameters.get("route_vehicle_mode") == "TRUCK"
            if estimate is None and self.client and origin_coordinates and destination_coordinates and not truck_requested:
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
        }
