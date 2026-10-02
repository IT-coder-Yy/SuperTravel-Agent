import re
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping


OFFICIAL_RAIL_QUERY_URL = "https://www.12306.cn/index/"


def transport_query_guidance(origin: str = "", destination: str = "") -> str:
    route = f"“{origin} → {destination}”的" if origin and destination else ""
    return (
        f"暂无可靠实时票务数据。请通过官方交通查询渠道核验{route}可用交通方式、"
        "班次、出发与到达地点、时间及票价，并为两端接驳预留时间。"
    )


def _number(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _ticket_price(value: Any) -> float | None:
    # 票务接口用 0 表示未取得报价，不能把它展示或排序为免费车票。
    price = _number(value)
    return price if price is not None and price > 0 else None


def _planning_option_rank(option: Mapping[str, Any], direction: str, travel_date: str) -> tuple:
    """优先保留白天游览窗口，再在适合的班次间比较价格和耗时。"""
    field = "arrival_time" if direction == "outbound" else "departure_time"
    try:
        clock = datetime.fromisoformat(str(option.get(field)))
        minute = (clock.date() - datetime.fromisoformat(travel_date).date()).days * 1440 + clock.hour * 60 + clock.minute
        if direction == "outbound":
            departure = datetime.fromisoformat(str(option.get("departure_time")))
            # 过早出发或午后抵达都会压缩可用的旅行时间。
            penalty = max(0, 6 * 60 - departure.hour * 60 - departure.minute) + max(0, minute - 11 * 60)
        else:
            penalty = max(0, 19 * 60 - minute) + max(0, minute - 23 * 60)
    except (TypeError, ValueError):
        penalty = float("inf")
    price = option.get("price")
    return penalty, price is None, price if price is not None else float("inf"), option.get("duration_minutes") or float("inf")


def _duration_minutes(value: Any) -> int | None:
    text = str(value or "")
    hours = re.search(r"(\d+)\s*(?:小时|h)", text, re.I)
    minutes = re.search(r"(\d+)\s*(?:分钟?|min)", text, re.I)
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
    remaining = re.search(r"余票\s*[:：]?\s*(\d+)", text)
    if remaining:
        return "limited" if int(remaining.group(1)) <= 5 else "available"
    if re.search(r"有票|可预订|available", text, re.I):
        return "available"
    return "unknown"


def _data_type(source: str) -> str:
    normalized = source.casefold()
    live_markers = (
        "12306",
        "get-tickets",
        "query_12306",
        "official api",
        "official_api",
        "provider api",
        "provider_api",
    )
    reference_markers = ("web", "网页", "ctrip", "携程", "search", "搜索")
    if any(marker in normalized for marker in reference_markers):
        return "reference_data"
    return "confirmed_live_data" if any(marker in normalized for marker in live_markers) else "reference_data"


def _option_id(row: Mapping[str, Any], mode: str, direction: str) -> str:
    identity = "|".join(str(row.get(key) or "") for key in ("trip_no", "train_code", "route", "depart", "arrive"))
    digest = hashlib.sha1(f"{direction}|{mode}|{identity}".encode("utf-8")).hexdigest()[:16]
    return f"transport_{digest}"


def _seat_availability(remaining: Any) -> str:
    text = str(remaining or "").strip()
    if not text:
        return "unknown"
    if re.search(r"无|售罄|候补|不可订|^0$", text, re.I):
        return "unavailable"
    count = _number(text)
    if count is not None:
        return "limited" if count <= 5 else "available"
    if re.search(r"紧张|少量|limited", text, re.I):
        return "limited"
    if re.search(r"有票|可订|available|充足", text, re.I):
        return "available"
    return "unknown"


def _seat_options(row: Mapping[str, Any], *, source_is_live: bool) -> list[Dict[str, Any]]:
    """Keep provider-returned seat/cabin choices without inventing availability."""
    if not source_is_live:
        return []

    raw_options = row.get("seat_options")
    if not isinstance(raw_options, list):
        seat = str(row.get("seat") or "").strip()
        note = str(row.get("note") or "")
        remaining_match = re.search(r"余票\s*[:：]?\s*([^，,；;\s]+)", note)
        raw_options = ([{
            "name": seat,
            "remaining_text": remaining_match.group(1) if remaining_match else "",
            "price": row.get("price"),
        }] if seat and seat != "-" else [])

    normalized: list[Dict[str, Any]] = []
    seen = set()
    for item in raw_options:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or item.get("seat") or item.get("seat_name") or "").strip()
        if not name or name == "-" or name in seen:
            continue
        seen.add(name)
        remaining_text = str(item.get("remaining_text") or item.get("remaining") or item.get("left") or "").strip()
        price = _ticket_price(item.get("price"))
        normalized.append({
            "name": name,
            "availability": _seat_availability(remaining_text),
            "remaining_text": remaining_text or None,
            "price": price,
            "currency": "CNY",
        })
    return normalized[:12]


def _options(rows: Iterable[Mapping[str, Any]], mode: str, source: str, queried_at: str, direction: str) -> list[Dict[str, Any]]:
    result = []
    data_type = _data_type(source)
    for row in rows:
        departure_station, arrival_station = _split_route(row.get("route"))
        result.append({
            "option_id": _option_id(row, mode, direction), "mode": mode,
            "service_number": row.get("trip_no") or row.get("train_code"),
            "departure_station": departure_station, "arrival_station": arrival_station,
            "departure_time": row.get("depart"), "arrival_time": row.get("arrive"),
            "duration_minutes": _duration_minutes(row.get("duration")), "transfers": 0,
            "price": _ticket_price(row.get("price")), "currency": "CNY",
            "availability": _availability(row.get("note")), "booking_url": None,
            "seat_options": _seat_options(row, source_is_live=data_type == "confirmed_live_data"),
            "source_reference_id": f"source_ticket_{direction}_{mode}",
            "data_type": data_type,
            "queried_at": queried_at,
        })
    return result


def transport_section_from_bundle(
    bundle: Mapping[str, Any] | None,
    direction: str,
    scope: str,
    travel_date: str | None,
    fallback_guidance: str | None = None,
) -> Dict[str, Any]:
    supported_modes = ["flight"] if scope == "international" else ["train", "intercity_bus", "flight"]
    official_query_url = OFFICIAL_RAIL_QUERY_URL if scope == "domestic" else None
    if not travel_date:
        return {"direction": direction, "scope": scope, "travel_date": None, "supported_modes": supported_modes,
                "recommended_option_id": None, "options": [], "status": "needs_date", "status_reason": "需要明确出行日期",
                "official_query_url": official_query_url}
    if not bundle:
        return {"direction": direction, "scope": scope, "travel_date": travel_date, "supported_modes": supported_modes,
                "recommended_option_id": None, "options": [], "status": "needs_confirmation",
                "status_reason": fallback_guidance or transport_query_guidance(),
                "official_query_url": official_query_url}
    queried_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    options = []
    if scope == "domestic":
        options += _options(bundle.get("direct_rows") or [], "train", str(bundle.get("direct_source") or ""), queried_at, direction)
        options += _options(bundle.get("bus_rows") or [], "intercity_bus", str(bundle.get("bus_source") or ""), queried_at, direction)
    options += _options(bundle.get("flight_rows") or [], "flight", str(bundle.get("flight_source") or ""), queried_at, direction)
    options = [item for item in options if str(item.get("departure_time") or "").startswith(travel_date)]
    options.sort(key=lambda item: (item.get("price") is None, item.get("price") or 0, item.get("duration_minutes") or 10**9))
    recommended = min(options, key=lambda option: _planning_option_rank(option, direction, travel_date)) if options else None
    return {
        "direction": direction, "scope": scope, "travel_date": travel_date, "supported_modes": supported_modes,
        "recommended_option_id": recommended["option_id"] if recommended else None, "options": options,
        "status": "ready" if options else "unavailable",
        "status_reason": None if options else (fallback_guidance or transport_query_guidance()),
        "official_query_url": official_query_url,
    }
