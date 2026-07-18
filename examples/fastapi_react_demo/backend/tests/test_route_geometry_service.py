import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.route_geometry_service import build_day_route
from services.route_providers.baidu import BaiduRouteProvider
from agents.tool.baidu_request_dispatcher import BaiduRequestDispatcher


class FakeProvider:
    name = "fake"
    coordinate_system = "WGS84"

    async def route(self, origin, destination, mode):
        return {"distance_meters": 1200, "duration_minutes": 18,
                "geometry": {"type": "LineString", "coordinates": [origin, destination]},
                "provider": self.name, "coordinate_system": self.coordinate_system, "calculated_at": "2026-07-15T00:00:00Z"}


def test_day_route_uses_activity_order_and_real_geometry():
    activities = [
        {"activity_id": "a1", "place": {"lng": 139.7, "lat": 35.6}, "route_to_next": {"mode": "walking"}},
        {"activity_id": "a2", "place": {"lng": 139.8, "lat": 35.7}},
    ]
    route = asyncio.run(build_day_route(day=1, plan_version=4, activities=activities, scope="international", international_provider=FakeProvider()))
    assert route["status"] == "ready"
    assert route["legs"][0]["from_activity_id"] == "a1"
    assert route["legs"][0]["geometry"]["coordinates"] == [[139.7, 35.6], [139.8, 35.7]]


def test_baidu_route_provider_reuses_dispatcher_cache():
    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": 0,
                "result": {
                    "routes": [
                        {
                            "distance": 1200,
                            "duration": 600,
                            "steps": [{"path": "116.3972,39.9163;116.4072,39.9263"}],
                        }
                    ]
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params):
            nonlocal calls
            calls += 1
            return FakeResponse()

    dispatcher = BaiduRequestDispatcher(min_interval_seconds=0)
    provider = BaiduRouteProvider(api_key="test-key", dispatcher=dispatcher)

    async def run_twice():
        first = await provider.route([116.3972, 39.9163], [116.4072, 39.9263], "walking")
        second = await provider.route([116.3972, 39.9163], [116.4072, 39.9263], "walking")
        return first, second

    with patch("services.route_providers.baidu.httpx.AsyncClient", return_value=FakeClient()):
        first, second = asyncio.run(run_twice())

    assert first["geometry"] == second["geometry"]
    assert first["distance_meters"] == second["distance_meters"]
    assert calls == 1
