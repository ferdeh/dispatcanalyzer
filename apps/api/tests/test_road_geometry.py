from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, MasterDepot, MasterSPBU, RouteMatrixCache
from app.phase7_matrix import RouteMatrixService
from app.road_geometry import OSRMRoadGeometryClient


def test_osrm_client_keeps_order_and_converts_geojson_coordinates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/route/v1/driving/98.6700000,3.5900000;98.7200000,3.6500000;98.6700000,3.5900000")
        assert request.url.params["overview"] == "full"
        assert request.url.params["geometries"] == "geojson"
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [{
                    "distance": 25_100.4,
                    "duration": 2_415.2,
                    "geometry": {"coordinates": [[98.67, 3.59], [98.69, 3.62], [98.72, 3.65], [98.67, 3.59]]},
                }],
            },
        )

    client = OSRMRoadGeometryClient(
        base_url="https://road.test",
        transport=httpx.MockTransport(handler),
    )
    result = client.compute_route([(3.59, 98.67), (3.65, 98.72), (3.59, 98.67)])

    assert result["route_geometry_source"] == "OSRM_ROAD_GEOMETRY"
    assert result["distance_meters"] == 25_100
    assert result["duration_seconds"] == 2_415
    assert result["route_geometry"][1] == {"latitude": 3.62, "longitude": 98.69}


def test_phase7_map_geometry_uses_geometry_only_provider_then_cache() -> None:
    class StubRoadGeometryClient:
        def __init__(self) -> None:
            self.calls = 0

        def compute_route(self, coordinates):
            self.calls += 1
            assert coordinates == [(3.59, 98.67), (3.65, 98.72), (3.59, 98.67)]
            return {
                "distance_meters": 25_100,
                "duration_seconds": 2_415,
                "route_geometry": [
                    {"latitude": 3.59, "longitude": 98.67},
                    {"latitude": 3.62, "longitude": 98.69},
                    {"latitude": 3.65, "longitude": 98.72},
                    {"latitude": 3.59, "longitude": 98.67},
                ],
                "route_geometry_source": "OSRM_ROAD_GEOMETRY",
            }

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        depot = MasterDepot(depot_id="D1", depot_name="Depot", latitude=3.59, longitude=98.67)
        spbu = MasterSPBU(spbu_id="S1", spbu_code="S1", spbu_name="SPBU", latitude=3.65, longitude=98.72)
        db.add_all([depot, spbu])
        db.commit()

        service = RouteMatrixService(db)
        service.client = None
        road_client = StubRoadGeometryClient()
        service.road_geometry_client = road_client
        departure = datetime(2026, 8, 28, 5, 0, tzinfo=timezone.utc)

        first = service.build_map_road_geometry(depot=depot, ordered_spbus=[spbu], departure=departure)
        second = service.build_map_road_geometry(depot=depot, ordered_spbus=[spbu], departure=departure)

        assert first["route_geometry_source"] == "OSRM_ROAD_GEOMETRY"
        assert first["road_geometry"] is True
        assert first["cache_hit"] is False
        assert second["route_geometry_source"] == "OSRM_ROAD_GEOMETRY"
        assert second["cache_hit"] is True
        assert second["external_request_count"] == 0
        assert road_client.calls == 1
        assert db.scalar(select(func.count()).select_from(RouteMatrixCache)) == 1
