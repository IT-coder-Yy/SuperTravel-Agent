"""用户提交搜索后，按国家与城市核验国际 OpenStreetMap 地点。"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import math
import os
import re
import threading
import time
from typing import Any

import requests

try:
    from services.destination_catalog_service import (
        DESTINATION_APPROXIMATE_CENTERS, INTERNATIONAL_DESTINATIONS,
        INTERNATIONAL_DESTINATION_META, canonical_destination,
    )
except ModuleNotFoundError:
    from backend.services.destination_catalog_service import (
        DESTINATION_APPROXIMATE_CENTERS, INTERNATIONAL_DESTINATIONS,
        INTERNATIONAL_DESTINATION_META, canonical_destination,
    )

_request_lock = threading.Lock()
_last_request_at = 0.0


def request_nominatim_places(query: str, city: str, limit: int = 10) -> tuple[dict, ...]:
    """国际编辑、餐饮和酒店查询共用节流；异常由外层处理，不能缓存成空结果。"""
    global _last_request_at
    meta = INTERNATIONAL_DESTINATION_META[city]
    with _request_lock:
        time.sleep(max(0.0, 1.0 - (time.monotonic() - _last_request_at)))
        _last_request_at = time.monotonic()
        response = requests.get(
            os.getenv("NOMINATIM_SEARCH_URL", "").strip() or "https://nominatim.openstreetmap.org/search",
            params={"q": f"{query} {meta['name_en']}", "format": "jsonv2",
                    "addressdetails": 1, "extratags": 1, "namedetails": 1, "limit": min(20, max(1, limit)),
                    "countrycodes": str(meta["country_code"]).lower(), "accept-language": "zh-CN,en"},
            headers={"User-Agent": "SuperTravelAgent/1.0 (local personal travel planner)"},
            timeout=max(5.0, min(float(os.getenv("NOMINATIM_TIMEOUT_SECONDS", "20")), 35.0)),
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Nominatim 响应格式无效")
    return tuple(deepcopy(row) for row in payload if isinstance(row, dict))


@lru_cache(maxsize=128)
def _fetch_places(query: str, city: str) -> tuple[dict, ...]:
    return request_nominatim_places(query, city)


def _locality_name(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^city of\s+|\s+(?:metropolis|prefecture|city)$|[市都]$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def matches_osm_destination(raw: dict, city: str) -> bool:
    address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    expected = str(INTERNATIONAL_DESTINATION_META[city]["country_code"]).lower()
    if str(address.get("country_code") or "").lower() != expected:
        return False
    aliases = {_locality_name(name) for name in [city, *INTERNATIONAL_DESTINATIONS[city]["aliases"]]}
    localities = [address.get(key) for key in ("city", "town", "municipality", "county", "state", "region")]
    # Nominatim 某些国家仅在 display_name 中保留上级城市；排除首段地点名称。
    localities.extend(str(raw.get("display_name") or "").split(",")[1:])
    if not any(_locality_name(part) in aliases for value in localities for part in str(value or "").split("/")):
        return False
    try:
        latitude, longitude = float(raw["lat"]), float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    if not (math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        return False
    # 城市中心只用于排除错误城市坐标，绝不作为 POI 坐标返回。
    center_lat, center_lng = DESTINATION_APPROXIMATE_CENTERS[city]
    lat_delta, lng_delta = math.radians(latitude - center_lat), math.radians(longitude - center_lng)
    a = math.sin(lat_delta / 2) ** 2 + math.cos(math.radians(center_lat)) * math.cos(math.radians(latitude)) * math.sin(lng_delta / 2) ** 2
    distance_km = 6371 * 2 * math.asin(min(1.0, math.sqrt(a)))
    return distance_km <= 100


def _matches_kind(raw: dict, kind: str) -> bool:
    category = str(raw.get("category") or raw.get("class") or "").lower()
    place_type = str(raw.get("type") or "").lower()
    tags = raw.get("extratags") if isinstance(raw.get("extratags"), dict) else {}
    if tags.get("disused") == "yes" or tags.get("abandoned") == "yes":
        return False
    lodging = {"hotel", "motel", "hostel", "guest_house", "apartment", "resort"}
    if kind == "food":
        return place_type in {"restaurant", "cafe", "fast_food", "food_court", "bar", "pub"}
    if kind == "hotel":
        return place_type in lodging
    if kind == "shopping":
        return category == "shop" or place_type == "marketplace"
    if kind == "transport":
        return category in {"railway", "aeroway"} or place_type in {"bus_station", "ferry_terminal"}
    if kind == "attraction":
        return (category == "historic" or place_type in {
            "attraction", "museum", "gallery", "viewpoint", "zoo", "aquarium", "theme_park",
            "park", "garden", "nature_reserve", "place_of_worship", "theatre", "arts_centre",
            "beach", "peak", "waterfall",
        } or str(tags.get("tourism") or "") == "attraction")
    return kind == "other" and category not in {"", "boundary", "place"}


def search_international_places(query: str, destination: str, kind: str) -> list[dict]:
    city = canonical_destination(destination)
    if city not in INTERNATIONAL_DESTINATION_META or len(query.strip()) < 2:
        return []
    if os.getenv("OSM_PLACE_SEARCH_ENABLED", "true").lower() in {"0", "false", "off", "no"}:
        return []
    try:
        raw_rows = _fetch_places(query.strip(), city)
    except (requests.RequestException, TypeError, ValueError):
        return []
    results: list[dict] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not matches_osm_destination(raw, city) or not _matches_kind(raw, kind):
            continue
        osm_type, osm_id = str(raw.get("osm_type") or ""), str(raw.get("osm_id") or "")
        if osm_type not in {"node", "way", "relation"} or not osm_id.isdigit():
            continue
        poi_id = f"osm_{osm_type}_{osm_id}"
        if poi_id in seen:
            continue
        names = raw.get("namedetails") if isinstance(raw.get("namedetails"), dict) else {}
        name = query.strip() if query.strip() in names.values() else names.get("name:zh-Hans") or names.get("name:zh") or raw.get("name")
        if not str(name or "").strip():
            continue
        tags = raw.get("extratags") if isinstance(raw.get("extratags"), dict) else {}
        source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
        reference_id = f"src_{poi_id}"
        results.append({
            "id": poi_id, "poi_id": poi_id, "place_id": poi_id, "provider_place_id": poi_id,
            "name": str(name).strip(), "category": kind, "city": city,
            "country_code": INTERNATIONAL_DESTINATION_META[city]["country_code"],
            "address": raw.get("display_name"), "lat": float(raw["lat"]), "lng": float(raw["lon"]),
            "coordinate_system": "WGS84", "coordinates_trusted": True, "coordinate_source": "provider_poi",
            "provider_latitude": float(raw["lat"]), "provider_longitude": float(raw["lon"]),
            "provider_coordinate_system": "WGS84", "requested_destination": city, "destination_bound": True,
            "opening_hours": tags.get("opening_hours"), "source": "OpenStreetMap",
            "source_provider": "openstreetmap", "source_url": source_url,
            "source_reference_id": reference_id, "data_type": "reference_data",
            "sources": [{"source_reference_id": reference_id}], "evidence_refs": [reference_id],
            "field_evidence": {"coordinates": {"source_reference_id": reference_id}},
            "summary": f"地点及坐标来源：OpenStreetMap（{source_url}）。营业时间与营业状态请在出发前复核。",
        })
        seen.add(poi_id)
    return results
