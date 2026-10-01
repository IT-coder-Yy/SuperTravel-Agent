import asyncio
import sys
import time
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


def test_baidu_route_timeout_releases_dispatcher_without_waiting_for_stuck_cancellation():
    release_cancellation = asyncio.Event()

    class StuckClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def get(self, *args, **kwargs):
            del args, kwargs
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release_cancellation.wait()

    dispatcher = BaiduRequestDispatcher(min_interval_seconds=0, queue_timeout_seconds=1)
    provider = BaiduRouteProvider(
        api_key="test-key",
        timeout_seconds=0.01,
        dispatcher=dispatcher,
    )

    async def verify_timeout():
        async def release_later():
            await asyncio.sleep(0.2)
            release_cancellation.set()

        release_task = asyncio.create_task(release_later())
        started_at = time.monotonic()
        try:
            await provider.route([114.30, 30.58], [114.31, 30.59], "walking")
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("stuck Baidu route request did not time out")
        elapsed = time.monotonic() - started_at
        await release_task
        return elapsed

    with patch("services.route_providers.baidu.httpx.AsyncClient", StuckClient):
        elapsed = asyncio.run(verify_timeout())

    assert elapsed < 0.1
    assert dispatcher.metrics_snapshot()["active_executions"] == 0


class FakeProvider:
    name = "fake"
    coordinate_system = "WGS84"

    async def route(self, origin, destination, mode):
        return {"distance_meters": 1200, "duration_minutes": 18,
                "geometry": {"type": "LineString", "coordinates": [origin, destination]},
                "provider": self.name, "coordinate_system": self.coordinate_system, "calculated_at": "2026-07-15T00:00:00Z"}


class InvalidGeometryProvider(FakeProvider):
    async def route(self, origin, destination, mode):
        return {
            "distance_meters": 1200,
            "duration_minutes": 18,
            "geometry": {"type": "LineString", "coordinates": [origin]},
            "provider": self.name,
            "coordinate_system": self.coordinate_system,
        }


class PartiallyInvalidProvider(FakeProvider):
    def __init__(self):
        self.calls = 0

    async def route(self, origin, destination, mode):
        self.calls += 1
        if self.calls == 1:
            return await super().route(origin, destination, mode)
        return {
            "distance_meters": 1200,
            "duration_minutes": 18,
            "geometry": {"type": "LineString", "coordinates": [origin, destination]},
            "provider": self.name,
            "coordinate_system": "BD09LL",
        }


def test_day_route_uses_activity_order_and_real_geometry():
    activities = [
        {"activity_id": "a1", "place": {"lng": 139.7, "lat": 35.6}, "route_to_next": {"mode": "walking"}},
        {"activity_id": "a2", "place": {"lng": 139.8, "lat": 35.7}},
    ]
    route = asyncio.run(build_day_route(day=1, plan_version=4, activities=activities, scope="international", international_provider=FakeProvider()))
    assert route["status"] == "ready"
    assert route["legs"][0]["from_activity_id"] == "a1"
    assert route["legs"][0]["geometry"]["coordinates"] == [[139.7, 35.6], [139.8, 35.7]]


def test_day_route_orders_activities_by_time_and_uses_supported_distance_mode():
    class RecordingProvider(FakeProvider):
        def __init__(self):
            self.modes = []

        async def route(self, origin, destination, mode):
            self.modes.append(mode)
            return await super().route(origin, destination, mode)

    provider = RecordingProvider()
    activities = [
        {
            "activity_id": "lunch",
            "start_time": "11:30",
            "place": {"lng": 120.18, "lat": 30.25},
            "transport_to_next": "步行或公共交通前往下一站，具体路线与耗时请复核",
        },
        {"activity_id": "morning", "start_time": "09:00", "place": {"lng": 120.10, "lat": 30.25}},
        {"activity_id": "museum", "start_time": "10:30", "place": {"lng": 120.11, "lat": 30.25}},
    ]

    route = asyncio.run(build_day_route(
        day=1,
        plan_version=1,
        activities=activities,
        scope="domestic",
        baidu_provider=provider,
    ))

    assert [(leg["from_activity_id"], leg["to_activity_id"]) for leg in route["legs"]] == [
        ("morning", "museum"),
        ("museum", "lunch"),
    ]
    assert provider.modes == ["walking", "transit"]


def test_day_route_overrides_impractical_explicit_walking_mode():
    class RecordingProvider(FakeProvider):
        def __init__(self):
            self.modes = []

        async def route(self, origin, destination, mode):
            self.modes.append(mode)
            return await super().route(origin, destination, mode)

    provider = RecordingProvider()
    activities = [
        {
            "activity_id": "far-a",
            "start_time": "09:00",
            "place": {"lng": 114.2995, "lat": 30.5529},
            "route_to_next": {"mode": "walking"},
        },
        {
            "activity_id": "far-b",
            "start_time": "10:30",
            "place": {"lng": 114.3718, "lat": 30.5678},
        },
    ]

    route = asyncio.run(build_day_route(
        day=1,
        plan_version=1,
        activities=activities,
        scope="domestic",
        baidu_provider=provider,
    ))

    assert route["status"] == "ready"
    assert provider.modes == ["transit"]


def test_day_route_does_not_claim_real_geometry_for_catalog_approximate_points():
    activities = [
        {
            "activity_id": "catalog-a1",
            "place": {
                "lng": 120.15,
                "lat": 30.27,
                "coordinate_source": "catalog_city_approximate",
                "coordinates_trusted": False,
            },
        },
        {
            "activity_id": "catalog-a2",
            "place": {
                "lng": 120.16,
                "lat": 30.28,
                "coordinate_source": "catalog_city_approximate",
                "coordinates_trusted": False,
            },
        },
    ]

    route = asyncio.run(build_day_route(
        day=1,
        plan_version=1,
        activities=activities,
        scope="international",
        international_provider=FakeProvider(),
    ))

    assert route["status"] == "unavailable"
    assert route["legs"] == []


def test_invalid_provider_geometry_is_marked_unavailable_without_fallback_line():
    activities = [
        {"activity_id": "a1", "place": {"lng": 139.7, "lat": 35.6}},
        {"activity_id": "a2", "place": {"lng": 139.8, "lat": 35.7}},
    ]

    route = asyncio.run(build_day_route(
        day=1,
        plan_version=4,
        activities=activities,
        scope="international",
        international_provider=InvalidGeometryProvider(),
    ))

    assert route["status"] == "unavailable"
    assert route["legs"][0]["status"] == "unavailable"
    assert route["legs"][0]["geometry"] is None


def test_coordinate_system_mismatch_keeps_only_verified_legs():
    activities = [
        {"activity_id": "a1", "place": {"lng": 139.7, "lat": 35.6}},
        {"activity_id": "a2", "place": {"lng": 139.8, "lat": 35.7}},
        {"activity_id": "a3", "place": {"lng": 139.9, "lat": 35.8}},
    ]

    route = asyncio.run(build_day_route(
        day=1,
        plan_version=4,
        activities=activities,
        scope="international",
        international_provider=PartiallyInvalidProvider(),
    ))

    assert route["status"] == "partial"
    assert [leg["status"] for leg in route["legs"]] == ["ready", "unavailable"]
    assert route["legs"][1]["geometry"] is None


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


def test_baidu_route_provider_accepts_nested_transit_steps():
    payload = {
        "status": 0,
        "result": {
            "routes": [
                {
                    "steps": [
                        [{"path": "114.3000,30.5000;114.3100,30.5100"}],
                        [{"path": "114.3100,30.5100;114.3200,30.5200"}],
                    ]
                }
            ]
        },
    }

    assert BaiduRouteProvider._path_points(payload) == [
        [114.3, 30.5],
        [114.31, 30.51],
        [114.32, 30.52],
    ]
