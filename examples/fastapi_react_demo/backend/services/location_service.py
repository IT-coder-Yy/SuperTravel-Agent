import os
from typing import Any, Dict

import httpx

from agents.tool.baidu_request_dispatcher import BaiduRequestDispatcher


class LocationLookupUnavailable(RuntimeError):
    pass


def _city_from_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    if int(payload.get("status", -1)) != 0:
        raise LocationLookupUnavailable("BAIDU_REVERSE_GEOCODE_UNAVAILABLE")

    result = payload.get("result") or {}
    component = result.get("addressComponent") or {}
    city = str(component.get("city") or component.get("province") or component.get("district") or "").strip()
    if city.endswith("市") and len(city) > 1:
        city = city[:-1]
    if not city:
        raise LocationLookupUnavailable("BAIDU_REVERSE_GEOCODE_CITY_MISSING")

    return {
        "city": city,
        "province": str(component.get("province") or "").strip(),
        "district": str(component.get("district") or "").strip(),
        "formatted_address": str(result.get("formatted_address") or "").strip(),
        "coordinate_system": "WGS84",
        "source": "baidu_reverse_geocode",
    }


async def reverse_geocode_current_location(
    latitude: float,
    longitude: float,
    dispatcher: BaiduRequestDispatcher,
    api_key: str | None = None,
) -> Dict[str, str]:
    key = (api_key or os.getenv("BAIDU_MAP_API_KEY", "")).strip()
    if not key:
        raise LocationLookupUnavailable("BAIDU_MAP_API_KEY_NOT_CONFIGURED")

    params = {
        "location": f"{latitude:.6f},{longitude:.6f}",
        "output": "json",
        "coordtype": "wgs84ll",
        "extensions_poi": "0",
        "ak": key,
    }
    timeout_seconds = max(10.0, float(os.getenv("BAIDU_MAP_NETWORK_TIMEOUT_SECONDS", "35")))

    async def request() -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(
                "https://api.map.baidu.com/reverse_geocoding/v3/",
                params=params,
            )
            response.raise_for_status()
            return response.json()

    try:
        payload = await dispatcher.execute_async(
            request,
            provider="baidu_reverse_geocode",
            operation="current_location",
            arguments={
                "latitude": round(latitude, 4),
                "longitude": round(longitude, 4),
                "coordtype": params["coordtype"],
            },
            priority="supplemental",
            cache_ttl_seconds=86400,
        )
    except LocationLookupUnavailable:
        raise
    except Exception as exc:
        raise LocationLookupUnavailable("BAIDU_REVERSE_GEOCODE_REQUEST_FAILED") from exc

    return _city_from_payload(payload)
