from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings


class RoadGeometryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class OSRMRoadGeometryClient:
    """Geometry-only road fallback.

    It never participates in the distance/time matrix or solver objective. The
    optimized stop order remains Phase 7 output; this client only draws that
    immutable order along roads when Google Routes is rate-limited.
    """

    def __init__(self, *, base_url: str | None = None, transport: httpx.BaseTransport | None = None):
        settings = get_settings()
        self._base_url = (base_url if base_url is not None else settings.road_geometry_fallback_url or "").rstrip("/")
        self._timeout = settings.road_geometry_fallback_timeout_seconds
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    def compute_route(self, coordinates: list[tuple[float, float]]) -> dict[str, Any]:
        if not self.enabled:
            raise RoadGeometryError("ROAD_GEOMETRY_FALLBACK_DISABLED", "Road geometry fallback is disabled.", status_code=503)
        if len(coordinates) < 2:
            raise RoadGeometryError("ROAD_GEOMETRY_COORDINATES_REQUIRED", "At least two coordinates are required.", status_code=422)

        coordinate_path = ";".join(f"{longitude:.7f},{latitude:.7f}" for latitude, longitude in coordinates)
        url = f"{self._base_url}/route/v1/driving/{coordinate_path}"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.get(
                    url,
                    params={"overview": "full", "geometries": "geojson", "steps": "false"},
                )
        except httpx.TimeoutException as exc:
            raise RoadGeometryError("ROAD_GEOMETRY_TIMEOUT", "Road geometry fallback timed out.", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise RoadGeometryError("ROAD_GEOMETRY_CONNECTION_ERROR", "Road geometry fallback could not be reached.") from exc
        if not response.is_success:
            raise RoadGeometryError("ROAD_GEOMETRY_REQUEST_FAILED", "Road geometry fallback rejected the request.", status_code=response.status_code)

        payload = response.json()
        routes = payload.get("routes") or []
        if payload.get("code") != "Ok" or not routes:
            raise RoadGeometryError("ROAD_GEOMETRY_NOT_FOUND", "Road geometry fallback returned no route.", status_code=404)
        route = routes[0]
        raw_coordinates = ((route.get("geometry") or {}).get("coordinates") or [])
        geometry = []
        for coordinate in raw_coordinates:
            if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                continue
            try:
                longitude, latitude = float(coordinate[0]), float(coordinate[1])
            except (TypeError, ValueError):
                continue
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                geometry.append({"latitude": latitude, "longitude": longitude})
        if len(geometry) < 2:
            raise RoadGeometryError("ROAD_GEOMETRY_INVALID", "Road geometry fallback returned invalid geometry.")
        return {
            "distance_meters": int(round(float(route.get("distance") or 0))),
            "duration_seconds": int(round(float(route.get("duration") or 0))),
            "route_geometry": geometry,
            "route_geometry_source": "OSRM_ROAD_GEOMETRY",
        }
