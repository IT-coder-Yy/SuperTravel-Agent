import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.route_geometry_service import build_day_route


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
