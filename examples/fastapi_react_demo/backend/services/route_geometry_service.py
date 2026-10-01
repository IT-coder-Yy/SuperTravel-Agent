import asyncio
import copy
import json
import math
import os
import time
from typing import Any, Dict, Iterable, List, Mapping

import httpx

from agents.tool.baidu_request_dispatcher import BaiduRequestDispatcher
from services.route_providers import BaiduRouteProvider, OpenRouteServiceProvider


_ROUTE_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_LEG_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_ROUTE_CACHE_TTL_SECONDS = max(60, int(os.getenv("ROUTE_CACHE_TTL_SECONDS", "900")))
_SUPPORTED_COORDINATE_SYSTEMS = {"WGS84", "BD09LL"}


def _is_valid_coordinate(value: Any, *, minimum: float, maximum: float) -> bool:
    """Return true only for finite longitude/latitude numbers in their legal range."""
    if isinstance(value, bool):
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and minimum <= numeric <= maximum


def is_valid_line_string_geometry(geometry: Any) -> bool:
    """Validate provider GeoJSON before it can be exposed as a real route."""
    if not isinstance(geometry, Mapping) or geometry.get("type") != "LineString":
        return False
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return False
    return all(
        isinstance(point, (list, tuple))
        and len(point) >= 2
        and _is_valid_coordinate(point[0], minimum=-180, maximum=180)
        and _is_valid_coordinate(point[1], minimum=-90, maximum=90)
        for point in coordinates
    )


def _is_valid_provider_result(result: Any, provider: Any) -> bool:
    """A ready leg must carry geometry from the selected provider and coordinate system."""
    return bool(
        isinstance(result, Mapping)
        and result.get("provider") == provider.name
        and result.get("coordinate_system") == provider.coordinate_system
        and provider.coordinate_system in _SUPPORTED_COORDINATE_SYSTEMS
        and is_valid_line_string_geometry(result.get("geometry"))
    )


def _coordinates(activity: Mapping[str, Any]) -> List[float] | None:
    place = activity.get("place")
    if not isinstance(place, Mapping):
        return None
    if place.get("coordinates_trusted") is False or place.get("coordinate_source") == "catalog_city_approximate":
        return None
    try:
        lng, lat = float(place["lng"]), float(place["lat"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (
        _is_valid_coordinate(lng, minimum=-180, maximum=180)
        and _is_valid_coordinate(lat, minimum=-90, maximum=90)
    ):
        return None
    return [lng, lat]


def _clock_minutes(value: Any) -> int:
    text = str(value or "").strip()
    parts = text.split(":", 1)
    if len(parts) != 2:
        return 24 * 60
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return 24 * 60
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else 24 * 60


def _straight_line_distance_meters(origin: List[float], destination: List[float]) -> float:
    lng1, lat1 = origin
    lng2, lat2 = destination
    radius = 6_371_000.0
    lat_delta = math.radians(lat2 - lat1)
    lng_delta = math.radians(lng2 - lng1)
    value = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(lng_delta / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def _route_mode(current: Mapping[str, Any], following: Mapping[str, Any], *, scope: str) -> str:
    """Choose a provider-supported mode instead of passing user-facing prose as a mode."""
    raw = (
        (current.get("route_to_next") or {}).get("mode")
        if isinstance(current.get("route_to_next"), Mapping)
        else current.get("transport_to_next")
    )
    normalized = str(raw or "").strip().casefold()
    aliases = {
        "步行": "walking", "walk": "walking", "walking": "walking",
        "骑行": "cycling", "cycle": "cycling", "cycling": "cycling",
        "驾车": "driving", "打车": "driving", "drive": "driving", "driving": "driving",
        "公交": "transit", "地铁": "transit", "公共交通": "transit",
        "transit": "transit", "public_transit": "transit",
    }
    origin, destination = _coordinates(current), _coordinates(following)
    if origin is None or destination is None:
        return aliases.get(normalized, "walking")
    distance = _straight_line_distance_meters(origin, destination)
    requested_mode = aliases.get(normalized)
    if requested_mode == "walking" and distance <= 1_200:
        return requested_mode
    if requested_mode == "cycling" and distance <= 5_000:
        return requested_mode
    if requested_mode in {"transit", "driving"}:
        return requested_mode
    if distance <= 1_200:
        return "walking"
    if scope == "domestic" and distance <= 40_000:
        return "transit"
    return "driving"


async def build_day_route(
    *, day: int, plan_version: int, activities: Iterable[Mapping[str, Any]], scope: str = "domestic",
    baidu_provider: BaiduRouteProvider | None = None,
    international_provider: OpenRouteServiceProvider | None = None,
    baidu_dispatcher: BaiduRequestDispatcher | None = None,
    baidu_priority: str = "formal",
) -> Dict[str, Any]:
    ordered = sorted(
        (
            item
            for item in activities
            if _coordinates(item) is not None and item.get("map_visible", True) is not False
        ),
        key=lambda item: (
            item.get("route_order", 0),
            item.get("route_order", 0),
            _clock_minutes(item.get("start_time") or item.get("start_at")),
            _clock_minutes(item.get("end_time") or item.get("end_at")),
            str(item.get("activity_id") or item.get("id") or ""),
        ),
    )
    provider = (
        international_provider or OpenRouteServiceProvider()
        if scope == "international"
        else baidu_provider or BaiduRouteProvider(
            dispatcher=baidu_dispatcher,
            request_priority=baidu_priority,
        )
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
        mode = _route_mode(current, following, scope=scope)
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
        if (
            cached_leg
            and time.monotonic() - cached_leg[0] < _ROUTE_CACHE_TTL_SECONDS
            and _is_valid_provider_result(cached_leg[1], provider)
        ):
            legs.append({**base, **copy.deepcopy(cached_leg[1]), "status": "ready"})
            continue
        try:
            result = await provider.route(_coordinates(current), _coordinates(following), mode)  # type: ignore[arg-type]
            if not _is_valid_provider_result(result, provider):
                raise ValueError("ROUTE_PROVIDER_GEOMETRY_INVALID")
            legs.append({**base, **result, "status": "ready"})
            if use_cache:
                _LEG_CACHE[leg_cache_key] = (time.monotonic(), copy.deepcopy(result))
                if len(_LEG_CACHE) > 512:
                    oldest_leg = min(_LEG_CACHE, key=lambda key: _LEG_CACHE[key][0])
                    _LEG_CACHE.pop(oldest_leg, None)
        except (
            httpx.HTTPError,
            RuntimeError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
        ):
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
