import asyncio
import copy
import json
import os
import time
from typing import Any, Dict, Iterable, List, Mapping

import httpx

from services.route_providers import BaiduRouteProvider, OpenRouteServiceProvider


_ROUTE_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_LEG_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_ROUTE_CACHE_TTL_SECONDS = max(60, int(os.getenv("ROUTE_CACHE_TTL_SECONDS", "900")))


def _coordinates(activity: Mapping[str, Any]) -> List[float] | None:
    place = activity.get("place")
    if not isinstance(place, Mapping):
        return None
    try:
        lng, lat = float(place["lng"]), float(place["lat"])
    except (KeyError, TypeError, ValueError):
        return None
    return [lng, lat]


async def build_day_route(
    *, day: int, plan_version: int, activities: Iterable[Mapping[str, Any]], scope: str = "domestic",
    baidu_provider: BaiduRouteProvider | None = None,
    international_provider: OpenRouteServiceProvider | None = None,
) -> Dict[str, Any]:
    ordered = [item for item in activities if _coordinates(item) is not None and item.get("map_visible", True) is not False]
    provider = (
        international_provider or OpenRouteServiceProvider()
        if scope == "international"
        else baidu_provider or BaiduRouteProvider()
    )
    use_cache = baidu_provider is None and international_provider is None
    cache_key = json.dumps({
        "day": day,
        "plan_version": plan_version,
        "scope": scope,
        "provider": provider.name,
        "activities": [
            [item.get("activity_id") or item.get("id"), _coordinates(item), item.get("transport_to_next")]
            for item in ordered
        ],
    }, ensure_ascii=True, sort_keys=True)
    cached = _ROUTE_CACHE.get(cache_key)
    if use_cache and cached and time.monotonic() - cached[0] < _ROUTE_CACHE_TTL_SECONDS:
        return copy.deepcopy(cached[1])
    legs: List[Dict[str, Any]] = []
    for current, following in zip(ordered, ordered[1:]):
        mode = str((current.get("route_to_next") or {}).get("mode") if isinstance(current.get("route_to_next"), Mapping) else current.get("transport_to_next") or "walking")
        base = {
            "from_activity_id": str(current.get("activity_id") or current.get("id") or ""),
            "to_activity_id": str(following.get("activity_id") or following.get("id") or ""),
            "mode": mode,
        }
        leg_cache_key = json.dumps(
            {
                "provider": provider.name,
                "origin": _coordinates(current),
                "destination": _coordinates(following),
                "mode": mode,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        cached_leg = _LEG_CACHE.get(leg_cache_key) if use_cache else None
        if cached_leg and time.monotonic() - cached_leg[0] < _ROUTE_CACHE_TTL_SECONDS:
            legs.append({**base, **copy.deepcopy(cached_leg[1]), "status": "ready"})
            continue
        try:
            result = await provider.route(_coordinates(current), _coordinates(following), mode)  # type: ignore[arg-type]
            legs.append({**base, **result, "status": "ready"})
            if use_cache:
                _LEG_CACHE[leg_cache_key] = (time.monotonic(), copy.deepcopy(result))
                if len(_LEG_CACHE) > 512:
                    oldest_leg = min(_LEG_CACHE, key=lambda key: _LEG_CACHE[key][0])
                    _LEG_CACHE.pop(oldest_leg, None)
        except (httpx.HTTPError, RuntimeError, asyncio.TimeoutError, ValueError, TypeError):
            legs.append({
                **base, "distance_meters": None, "duration_minutes": None, "geometry": None,
                "provider": provider.name, "coordinate_system": provider.coordinate_system,
                "calculated_at": None, "status": "unavailable",
            })
    ready_count = sum(leg["status"] == "ready" for leg in legs)
    status = "ready" if legs and ready_count == len(legs) else "partial" if ready_count else "unavailable"
    response = {
        "day": day, "plan_version": plan_version, "provider": provider.name,
        "coordinate_system": provider.coordinate_system, "legs": legs, "bbox": None, "status": status,
    }
    if use_cache:
        _ROUTE_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(response))
        if len(_ROUTE_CACHE) > 256:
            oldest_key = min(_ROUTE_CACHE, key=lambda key: _ROUTE_CACHE[key][0])
            _ROUTE_CACHE.pop(oldest_key, None)
    return response
