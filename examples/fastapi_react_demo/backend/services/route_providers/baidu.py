import asyncio
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx


_MAX_CONCURRENCY = min(2, max(1, int(os.getenv("BAIDU_MAP_MAX_CONCURRENCY", "1"))))
_MIN_INTERVAL_SECONDS = max(0.0, float(os.getenv("BAIDU_MAP_MIN_INTERVAL_SECONDS", "0.45")))
_REQUEST_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENCY)
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_STARTED_AT = 0.0


class BaiduRouteProvider:
    name = "baidu_directionlite"
    coordinate_system = "BD09LL"

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 12.0) -> None:
        self.api_key = api_key or os.getenv("BAIDU_MAP_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _acquire_request_slot() -> None:
        global _LAST_REQUEST_STARTED_AT
        _REQUEST_SEMAPHORE.acquire()
        with _RATE_LOCK:
            now = time.monotonic()
            wait_seconds = max(0.0, _LAST_REQUEST_STARTED_AT + _MIN_INTERVAL_SECONDS - now)
            _LAST_REQUEST_STARTED_AT = now + wait_seconds
        if wait_seconds:
            time.sleep(wait_seconds)

    @staticmethod
    def _endpoint(mode: str) -> str:
        normalized = {"walk": "walking", "walking": "walking", "cycle": "riding", "cycling": "riding",
                      "drive": "driving", "driving": "driving", "transit": "transit", "public_transit": "transit"}
        return normalized.get(mode, "walking")

    @staticmethod
    def _path_points(payload: Dict[str, Any]) -> List[List[float]]:
        routes = payload.get("result", {}).get("routes", [])
        if not routes:
            return []
        points: List[List[float]] = []
        for step in routes[0].get("steps", []):
            for pair in str(step.get("path") or "").split(";"):
                try:
                    lng, lat = pair.split(",", 1)
                    coordinate = [float(lng), float(lat)]
                except (TypeError, ValueError):
                    continue
                if not points or points[-1] != coordinate:
                    points.append(coordinate)
        return points

    async def route(self, origin: List[float], destination: List[float], mode: str) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("BAIDU_MAP_API_KEY_NOT_CONFIGURED")
        endpoint = self._endpoint(mode)
        params = {
            "origin": f"{origin[1]},{origin[0]}",
            "destination": f"{destination[1]},{destination[0]}",
            "ak": self.api_key,
            "coord_type": "bd09ll",
        }
        await asyncio.to_thread(self._acquire_request_slot)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(f"https://api.map.baidu.com/directionlite/v1/{endpoint}", params=params)
                response.raise_for_status()
                payload = response.json()
        finally:
            _REQUEST_SEMAPHORE.release()
        if int(payload.get("status", -1)) != 0:
            raise RuntimeError("BAIDU_ROUTE_UNAVAILABLE")
        route = (payload.get("result", {}).get("routes") or [{}])[0]
        points = self._path_points(payload)
        if len(points) < 2:
            raise RuntimeError("BAIDU_ROUTE_GEOMETRY_MISSING")
        return {
            "distance_meters": route.get("distance"),
            "duration_minutes": round(float(route.get("duration") or 0) / 60),
            "geometry": {"type": "LineString", "coordinates": points},
            "provider": self.name,
            "coordinate_system": self.coordinate_system,
            "calculated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
