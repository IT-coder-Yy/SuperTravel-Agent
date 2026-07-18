import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx

from agents.tool.baidu_request_dispatcher import BaiduRequestDispatcher


class BaiduRouteProvider:
    name = "baidu_directionlite"
    coordinate_system = "BD09LL"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        dispatcher: BaiduRequestDispatcher | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("BAIDU_MAP_API_KEY", "")
        configured_timeout = float(os.getenv("BAIDU_MAP_NETWORK_TIMEOUT_SECONDS", "35"))
        self.timeout_seconds = (
            max(0.01, timeout_seconds)
            if timeout_seconds is not None
            else max(35.0, configured_timeout)
        )
        self.dispatcher = dispatcher or BaiduRequestDispatcher()

    @staticmethod
    def _endpoint(mode: str) -> str:
        normalized = {
            "walk": "walking",
            "walking": "walking",
            "cycle": "riding",
            "cycling": "riding",
            "drive": "driving",
            "driving": "driving",
            "transit": "transit",
            "public_transit": "transit",
        }
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

        async def request() -> Dict[str, Any]:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"https://api.map.baidu.com/directionlite/v1/{endpoint}",
                    params=params,
                )
                response.raise_for_status()
                return response.json()

        payload = await self.dispatcher.execute_async(
            request,
            provider=self.name,
            operation=f"route:{endpoint}",
            arguments={
                "origin": params["origin"],
                "destination": params["destination"],
                "coord_type": params["coord_type"],
            },
            priority="formal",
            cache_ttl_seconds=900,
        )
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
