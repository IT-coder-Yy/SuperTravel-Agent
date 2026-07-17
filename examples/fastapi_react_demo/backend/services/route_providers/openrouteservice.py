import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx


class OpenRouteServiceProvider:
    name = "openrouteservice"
    coordinate_system = "WGS84"

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 12.0) -> None:
        self.api_key = api_key or os.getenv("OPENROUTESERVICE_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _profile(mode: str) -> str:
        return {
            "walk": "foot-walking", "walking": "foot-walking", "cycle": "cycling-regular",
            "cycling": "cycling-regular", "drive": "driving-car", "driving": "driving-car",
        }.get(mode, "foot-walking")

    async def route(self, origin: List[float], destination: List[float], mode: str) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENROUTESERVICE_API_KEY_NOT_CONFIGURED")
        headers = {"Authorization": self.api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"https://api.openrouteservice.org/v2/directions/{self._profile(mode)}/geojson",
                headers=headers,
                json={"coordinates": [origin, destination]},
            )
            response.raise_for_status()
            payload = response.json()
        features = payload.get("features") or []
        if not features:
            raise RuntimeError("OPENROUTESERVICE_ROUTE_UNAVAILABLE")
        feature = features[0]
        summary = feature.get("properties", {}).get("summary", {})
        geometry = feature.get("geometry")
        if not geometry or len(geometry.get("coordinates") or []) < 2:
            raise RuntimeError("OPENROUTESERVICE_GEOMETRY_MISSING")
        return {
            "distance_meters": round(float(summary.get("distance") or 0)),
            "duration_minutes": round(float(summary.get("duration") or 0) / 60),
            "geometry": geometry,
            "provider": self.name,
            "coordinate_system": self.coordinate_system,
            "calculated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
