import re
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping


def _number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _duration_minutes(value: Any) -> int | None:
    text = str(value or "")
    hours = re.search(r"(\d+)\s*(?:小时|h)", text, re.I)
    minutes = re.search(r"(\d+)\s*(?:分钟|min)", text, re.I)
    if not hours and not minutes:
        return None
    total = int(hours.group(1)) * 60 if hours else 0
    total += int(minutes.group(1)) if minutes else 0
    return total


def _split_route(value: Any) -> tuple[str | None, str | None]:
    parts = re.split(r"\s*(?:->|→|到)\s*", str(value or ""), maxsplit=1)
    return (parts[0] or None, parts[1] or None) if len(parts) == 2 else (None, None)


def _availability(note: Any) -> str:
    text = str(note or "")
    if re.search(r"无票|售罄|不可订", text):
        return "unknown"
    if re.search(r"候补|紧张|少量|limited", text, re.I):
        return "limited"
    if re.search(r"有票|可预订|available", text, re.I):
        return "available"
    return "unknown"


def _data_type(source: str) -> str:
    normalized = source.casefold()
    live_markers = ("12306", "official api", "official_api", "provider api", "provider_api")
    reference_markers = ("web", "网页", "ctrip", "携程", "search", "搜索")
    if any(marker in normalized for marker in reference_markers):
        return "reference_data"
    return "confirmed_live_data" if any(marker in normalized for marker in live_markers) else "reference_data"


def _option_id(row: Mapping[str, Any], mode: str, direction: str) -> str:
    identity = "|".join(str(row.get(key) or "") for key in ("trip_no", "train_code", "route", "depart", "arrive"))
    digest = hashlib.sha1(f"{direction}|{mode}|{identity}".encode("utf-8")).hexdigest()[:16]
    return f"transport_{digest}"


def _options(rows: Iterable[Mapping[str, Any]], mode: str, source: str, queried_at: str, direction: str) -> list[Dict[str, Any]]:
    result = []
    for row in rows:
        departure_station, arrival_station = _split_route(row.get("route"))
        result.append({
            "option_id": _option_id(row, mode, direction), "mode": mode,
            "service_number": row.get("trip_no") or row.get("train_code"),
            "departure_station": departure_station, "arrival_station": arrival_station,
            "departure_time": row.get("depart"), "arrival_time": row.get("arrive"),
            "duration_minutes": _duration_minutes(row.get("duration")), "transfers": 0,
            "price": _number(row.get("price")), "currency": "CNY",
            "availability": _availability(row.get("note")), "booking_url": None,
            "source_reference_id": f"source_ticket_{direction}_{mode}",
            "data_type": _data_type(source),
            "queried_at": queried_at,
        })
    return result


def transport_section_from_bundle(bundle: Mapping[str, Any] | None, direction: str, scope: str, travel_date: str | None) -> Dict[str, Any]:
    supported_modes = ["flight"] if scope == "international" else ["train", "intercity_bus", "flight"]
    if not travel_date:
        return {"direction": direction, "scope": scope, "travel_date": None, "supported_modes": supported_modes,
                "recommended_option_id": None, "options": [], "status": "needs_date", "status_reason": "需要明确出行日期"}
    if not bundle:
        return {"direction": direction, "scope": scope, "travel_date": travel_date, "supported_modes": supported_modes,
                "recommended_option_id": None, "options": [], "status": "needs_confirmation", "status_reason": "暂无可靠实时票务数据"}
    queried_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    options = []
    if scope == "domestic":
        options += _options(bundle.get("direct_rows") or [], "train", str(bundle.get("direct_source") or ""), queried_at, direction)
        options += _options(bundle.get("bus_rows") or [], "intercity_bus", str(bundle.get("bus_source") or ""), queried_at, direction)
    options += _options(bundle.get("flight_rows") or [], "flight", str(bundle.get("flight_source") or ""), queried_at, direction)
    options = [item for item in options if str(item.get("departure_time") or "").startswith(travel_date)]
    options.sort(key=lambda item: (item.get("price") is None, item.get("price") or 0, item.get("duration_minutes") or 10**9))
    return {
        "direction": direction, "scope": scope, "travel_date": travel_date, "supported_modes": supported_modes,
        "recommended_option_id": options[0]["option_id"] if options else None, "options": options,
        "status": "ready" if options else "unavailable",
        "status_reason": None if options else "实时查询已结束，但没有可验证的有效结果",
    }
