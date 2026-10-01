"""Bounded OpenStreetMap restaurant lookup for international itineraries."""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Any, Dict, List

import requests

try:
    from services.destination_catalog_service import (
        INTERNATIONAL_DESTINATION_META,
        canonical_destination,
    )
    from services.international_place_search_service import matches_osm_destination, request_nominatim_places
except ModuleNotFoundError:  # Package import used by focused tests.
    from backend.services.destination_catalog_service import (
        INTERNATIONAL_DESTINATION_META,
        canonical_destination,
    )
    from backend.services.international_place_search_service import matches_osm_destination, request_nominatim_places


_FOOD_TYPES = {"restaurant", "cafe", "fast_food", "food_court"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=64)
def _fetch_nominatim_payload(city: str, limit: int) -> tuple[Dict[str, Any], ...]:
    # lru_cache 不缓存抛出的异常，下一次专业任务重试会重新请求 Provider。
    return request_nominatim_places("restaurant", city, limit)


def fetch_osm_restaurants(destination: str, *, limit: int) -> List[Dict[str, Any]]:
    """Return real restaurant POIs from one cached, user-triggered Nominatim query.

    Successful responses are cached by destination/limit in a process,
    carries an identifying User-Agent and never performs autocomplete or grid
    downloads. Results are accepted only when their country and OSM amenity
    classification, locality and coordinates match the requested destination.
    """

    city = canonical_destination(destination) or _text(destination)
    meta = INTERNATIONAL_DESTINATION_META.get(city)
    if not meta:
        return []
    if os.getenv("OSM_RESTAURANT_SEARCH_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return []
    expected_country_code = _text(meta.get("country_code")).lower()
    rows: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        payload = _fetch_nominatim_payload(city, min(20, max(1, limit)))
    except (requests.RequestException, TypeError, ValueError):
        return []
    for raw in payload:
        address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
        if not matches_osm_destination(raw, city):
            continue
        if _text(raw.get("type")).lower() not in _FOOD_TYPES:
            continue
        lat = _number(raw.get("lat"))
        lng = _number(raw.get("lon"))
        osm_type = _text(raw.get("osm_type")).lower()
        osm_id = _text(raw.get("osm_id"))
        if lat is None or lng is None or osm_type not in {"node", "way", "relation"} or not osm_id.isdigit():
            continue
        poi_id = f"osm_{osm_type}_{osm_id}"
        if poi_id in seen_ids:
            continue
        namedetails = raw.get("namedetails") if isinstance(raw.get("namedetails"), dict) else {}
        name = _text(namedetails.get("name:zh")) or _text(raw.get("name")) or _text(address.get("amenity"))
        if not name:
            continue
        extratags = raw.get("extratags") if isinstance(raw.get("extratags"), dict) else {}
        source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
        reference_id = f"src_{poi_id}"
        rows.append({
            "id": poi_id,
            "poi_id": poi_id,
            "place_id": poi_id,
            "provider_place_id": poi_id,
            "name": name,
            "category": "餐厅",
            "city": city,
            "country_code": expected_country_code.upper(),
            "address": _text(raw.get("display_name")) or None,
            "lat": lat,
            "lng": lng,
            "provider_latitude": lat,
            "provider_longitude": lng,
            "provider_coordinate_system": "WGS84",
            "coordinate_system": "WGS84",
            "coordinates_trusted": True,
            "coordinate_source": "provider_poi",
            "source": "OpenStreetMap Nominatim",
            "source_provider": "openstreetmap",
            "source_url": source_url,
            "source_reference_id": reference_id,
            "data_type": "reference_data",
            "requested_destination": city,
            "destination_bound": True,
            "opening_hours": _text(extratags.get("opening_hours")) or None,
            "summary": "餐厅地点与坐标来自 OpenStreetMap；营业时间、菜单、价格及营业状态需在出发前复核。",
            "evidence_refs": [reference_id],
            "sources": [{"source_reference_id": reference_id}],
            "field_evidence": {"coordinates": {"source_reference_id": reference_id}},
        })
        seen_ids.add(poi_id)
        if len(rows) >= limit:
            break
    return rows
