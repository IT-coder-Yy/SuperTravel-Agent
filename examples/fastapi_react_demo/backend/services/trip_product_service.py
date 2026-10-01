import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import ValidationError

from schemas.trip_models import TravelPlanDocumentV2, TripPlan
from services.destination_catalog_service import (
    INTERNATIONAL_DESTINATION_META,
    INTERNATIONAL_DESTINATIONS,
    canonical_destination,
    domestic_city_names,
)
from services.file_service import get_output_root_path
from services.text_sanitizer_service import sanitize_user_visible_payload
from services.trip_plan_validator import validate_trip_plan
from services.travel_date_service import is_flexible_date_range


SCHEMA_VERSION = "2.0"
SHARE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,96}$")
ALLOWED_SHARE_SCOPES = {"itinerary", "budget", "sources", "checklist", "notes"}

TRIP_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "classic-three-day",
        "name": "经典三日游",
        "audience": ["首次到访", "朋友", "情侣"],
        "budget_level": "中等",
        "pace": "balanced",
        "default_days": 3,
        "preferences": ["经典景点", "城市文化", "地方美食"],
        "prompt": "使用经典三日游模板规划行程，优先覆盖代表性景点、城市文化和地方美食。模板仅作为偏好，请继续逐项澄清缺失条件。",
    },
    {
        "id": "family-slow",
        "name": "亲子慢旅行",
        "audience": ["亲子家庭", "低龄儿童"],
        "budget_level": "中等",
        "pace": "relaxed",
        "default_days": 3,
        "preferences": ["少步行", "亲子设施", "午休", "室内备选"],
        "prompt": "使用亲子慢旅行模板，减少连续步行和频繁换乘，保留午休及天气备选。模板仅作为偏好，请继续逐项澄清缺失条件。",
    },
    {
        "id": "food-route",
        "name": "美食路线",
        "audience": ["美食爱好者", "朋友"],
        "budget_level": "灵活",
        "pace": "balanced",
        "default_days": 2,
        "preferences": ["本地菜", "小吃", "市场", "餐厅错峰"],
        "prompt": "使用美食路线模板，按区域串联本地菜、小吃与市场，并检查饮食禁忌。模板仅作为偏好，请继续逐项澄清缺失条件。",
    },
    {
        "id": "low-budget",
        "name": "低预算旅行",
        "audience": ["学生", "独行旅客", "预算敏感"],
        "budget_level": "低",
        "pace": "balanced",
        "default_days": 3,
        "preferences": ["公共交通", "免费景点", "经济住宿", "费用透明"],
        "prompt": "使用低预算旅行模板，优先公共交通、免费景点和经济住宿，明确未知费用。模板仅作为偏好，请继续逐项澄清缺失条件。",
    },
    {
        "id": "photography",
        "name": "摄影路线",
        "audience": ["摄影爱好者", "情侣", "独行旅客"],
        "budget_level": "中等",
        "pace": "balanced",
        "default_days": 3,
        "preferences": ["日出日落", "城市机位", "自然风景", "光线时段"],
        "prompt": "使用摄影路线模板，按光线时段安排机位并减少跨区域折返。模板仅作为偏好，请继续逐项澄清缺失条件。",
    },
]

_BLOCKED_KEYS = {
    "profile", "user_profile", "api_key", "messages", "conversation", "prompt",
    "tool_calls", "tool_records", "internal_context", "logs", "diagnostics",
}

def _strip_private(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    return {
        key: _strip_private(item)
        for key, item in value.items()
        if str(key).strip().lower() not in _BLOCKED_KEYS
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_document(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("旅行文档必须是对象")
    cleaned = sanitize_user_visible_payload(_strip_private(dict(value)))
    for key in list(cleaned):
        if str(key).lower() in _BLOCKED_KEYS:
            cleaned.pop(key, None)
    cleaned["schema_version"] = SCHEMA_VERSION
    return cleaned


def _dates_from_intent(intent: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", str(intent.get("date_range") or ""))
    if not dates:
        return None, None
    return dates[0], dates[-1]


def _scope_for_destination(destination: str) -> str:
    canonical = canonical_destination(destination) or destination
    return "international" if canonical in INTERNATIONAL_DESTINATIONS else "domestic"


def _filename(title: str, version: int) -> str:
    safe_title = re.sub(r"[\\/:*?\"<>|]+", "-", str(title or "旅行规划")).strip(" .-")
    stem = safe_title or "旅行规划"
    candidate = f"{stem}-v{version}.md"
    if len(candidate.encode("utf-8")) <= 120:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    trailer = f"-v{version}-{digest}.md"
    allowed = 120 - len(trailer.encode("utf-8"))
    kept = []
    used = 0
    for character in stem:
        size = len(character.encode("utf-8"))
        if used + size > allowed:
            break
        kept.append(character)
        used += size
    return f"{''.join(kept).rstrip(' .-') or '旅行规划'}{trailer}"


def _legacy_plan_from_v2(document: Mapping[str, Any]) -> Dict[str, Any]:
    itinerary = document.get("itinerary") if isinstance(document.get("itinerary"), Mapping) else {}
    days = itinerary.get("days") if isinstance(itinerary.get("days"), list) else []
    activities = [
        activity
        for day in days
        if isinstance(day, Mapping)
        for activity in day.get("activities", [])
        if isinstance(activity, Mapping)
    ]
    overview = document.get("destination_overview") if isinstance(document.get("destination_overview"), Mapping) else {}
    reminders = document.get("friendly_reminders") if isinstance(document.get("friendly_reminders"), Mapping) else {}
    destination = str(overview.get("name_zh") or document.get("intent", {}).get("destination") or "旅行")
    return {
        "plan_id": document.get("plan_id"),
        "version": document.get("version"),
        "title": document.get("title") or f"{destination}{len(days) or document.get('intent', {}).get('days') or ''}日旅行规划",
        "intent": document.get("intent") or {},
        "days": len(days) or document.get("intent", {}).get("days") or 1,
        "activities": activities,
        "trip_days": days,
        "budget_summary": document.get("budget") or {},
        "map_locations": _derive_map_locations(days),
        "source_references": document.get("sources") or [],
        "warnings": [
            str(item.get("content")) for item in reminders.get("items", [])
            if isinstance(item, Mapping) and item.get("content")
        ],
    }


def _derive_map_locations(days: Iterable[Any]) -> List[Dict[str, Any]]:
    locations: List[Dict[str, Any]] = []
    for day in days:
        if not isinstance(day, Mapping):
            continue
        for order, activity in enumerate(day.get("activities", []), start=1):
            if not isinstance(activity, Mapping) or activity.get("map_visible", True) is False:
                continue
            place = activity.get("place")
            if not isinstance(place, Mapping) or place.get("lat") is None or place.get("lng") is None:
                continue
            locations.append({
                **dict(place),
                "id": place.get("poi_id") or activity.get("activity_id"),
                "day": day.get("day"),
                "order": order,
                "activity_id": activity.get("activity_id"),
            })
    return locations


def adapt_v1_document_to_v2(document: Mapping[str, Any]) -> Dict[str, Any]:
    raw_plan = document.get("plan") if isinstance(document.get("plan"), Mapping) else document
    plan = TripPlan.model_validate(raw_plan)
    plan_payload = plan.model_dump(mode="json")
    days = plan_payload.get("trip_days") or []
    if not days:
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for activity in plan_payload.get("activities", []):
            grouped.setdefault(int(activity.get("day") or 1), []).append(activity)
        days = [{"day": day, "activities": values} for day, values in sorted(grouped.items())]
    destination = canonical_destination(plan.intent.destination or "") or plan.intent.destination or "待确认"
    scope = _scope_for_destination(destination)
    supported_modes = ["flight"] if scope == "international" else ["train", "intercity_bus", "flight"]
    start_date, end_date = _dates_from_intent(plan_payload.get("intent") or {})
    location_ids = [str(item.get("id")) for item in _derive_map_locations(days)]
    country = INTERNATIONAL_DESTINATIONS.get(destination, {}).get("country") if scope == "international" else "中国"
    destination_meta = INTERNATIONAL_DESTINATION_META.get(destination, {})
    destination_currency = str(destination_meta.get("currency") or ("CNY" if scope == "domestic" else "XXX"))
    checklist = _normalize_user_items(document.get("checklist"), "checklist")
    notes = _normalize_user_items(document.get("notes"), "notes")
    validation = validate_trip_plan(plan).model_dump(mode="json")
    raw_sources = document.get("sources") or plan_payload.get("source_references") or []
    source_payload = []
    for index, item in enumerate(raw_sources, start=1):
        if not isinstance(item, Mapping):
            continue
        normalized_source = dict(item)
        normalized_source["reference_id"] = str(item.get("reference_id") or f"source_{index}")
        source_payload.append(normalized_source)
    cover_image = dict(document.get("cover_image")) if isinstance(document.get("cover_image"), Mapping) else None
    if cover_image:
        cover_reference_id = str(cover_image.get("source_reference_id") or "source_cover_unsplash")
        cover_image["source_reference_id"] = cover_reference_id
        if not any(item.get("reference_id") == cover_reference_id for item in source_payload):
            source_payload.append({
                "reference_id": cover_reference_id,
                "type": "image",
                "title": f"{destination}目的地封面",
                "source": "Unsplash",
                "url": cover_image.get("unsplash_url") or "https://unsplash.com",
                "snippet": f"摄影者：{cover_image.get('photographer_name') or 'Unsplash photographer'}",
                "data_type": "reference_data",
                "updated_at": str(document.get("generated_at") or _now_iso()),
                "confidence": 0.8,
                "related_fields": ["destination_overview.cover_image"],
                "related_places": [destination],
            })
    destination_sources = [
        item for item in source_payload
        if isinstance(item, Mapping) and str(item.get("type") or "") in {"local_knowledge", "selected_knowledge"}
    ]
    hotel_items = []
    for day in days:
        for activity in day.get("activities", []):
            place = activity.get("place") if isinstance(activity, Mapping) else None
            if not isinstance(place, Mapping):
                continue
            category = str(place.get("category") or "")
            if activity.get("activity_type") != "hotel" and not re.search(r"酒店|住宿|hotel|hostel", category, re.I):
                continue
            hotel_name = place.get("name") or activity.get("title") or "待确认"
            hotel_source = next((
                source for source in source_payload
                if hotel_name in (source.get("related_places") or [])
            ), None)
            hotel_items.append({
                "hotel_id": place.get("poi_id") or activity.get("activity_id"),
                "area": place.get("city") or place.get("address") or "待确认",
                "name": hotel_name,
                "place": dict(place),
                "nightly_price": activity.get("estimated_cost"), "total_price": None,
                "currency": destination_currency, "rating": place.get("rating"),
                "reasons": [str(item) for item in activity.get("notes", []) if item],
                "booking_url": None, "source_reference_id": hotel_source.get("reference_id") if hotel_source else None,
                "data_type": place.get("data_type") or "reference_data", "updated_at": place.get("updated_at"),
            })
    reminder_items = [
        {"category": "other", "content": warning, "data_type": "estimated_data"}
        for warning in plan_payload.get("warnings", []) if warning
    ]
    for source in source_payload:
        if source.get("type") == "weather" and source.get("snippet"):
            reminder_items.append({
                "category": "weather", "content": source["snippet"],
                "source_reference_id": source["reference_id"], "data_type": source.get("data_type") or "reference_data",
                "updated_at": source.get("updated_at"),
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "version": plan.version,
        "title": plan.title,
        "generated_at": str(document.get("generated_at") or _now_iso()),
        "locale": str(document.get("locale") or "zh-CN"),
        "intent": plan_payload.get("intent") or {},
        "destination_overview": {
            "status": "ready" if destination_sources else "needs_confirmation",
            "status_reason": None if destination_sources else "目的地背景资料待可靠来源核验",
            "name_zh": destination,
            "name_en": destination_meta.get("name_en"),
            "country_code": destination_meta.get("country_code") or ("CN" if scope == "domestic" else None),
            "country_name": destination_meta.get("country") or country,
            "timezone": destination_meta.get("timezone"),
            "currency": destination_currency,
            "languages": destination_meta.get("languages") or (["中文"] if scope == "domestic" else []),
            "themes": list(plan.intent.interests),
            "area_overview": str(destination_sources[0].get("snippet") or "") if destination_sources else None,
            "cover_image": cover_image,
            "source_reference_ids": [str(item.get("reference_id")) for item in destination_sources if item.get("reference_id")],
            "updated_at": max((str(item.get("updated_at") or "") for item in destination_sources), default="") or None,
        },
        "outbound_transport": {
            "direction": "outbound", "scope": scope, "travel_date": start_date,
            "supported_modes": supported_modes, "options": [],
            "status": "needs_confirmation" if start_date else "needs_date",
            "status_reason": "暂无可靠实时票务数据" if start_date else "需要明确出发日期",
        },
        "hotel_recommendations": {
            "status": "ready" if hotel_items else "needs_confirmation",
            "status_reason": "酒店为参考推荐，暂无可靠实时库存" if hotel_items else "暂无可靠实时库存，酒店仅作参考",
            "recommendations": hotel_items,
        },
        "itinerary": {"status": "ready" if days else "needs_confirmation", "days": days},
        "return_transport": {
            "direction": "return", "scope": scope, "travel_date": end_date,
            "supported_modes": supported_modes, "options": [],
            "status": "needs_confirmation" if end_date else "needs_date",
            "status_reason": "暂无可靠实时票务数据" if end_date else "需要明确返程日期",
        },
        "friendly_reminders": {
            "status": "ready" if any(item.get("source_reference_id") for item in reminder_items) else "needs_confirmation",
            "status_reason": None if any(item.get("source_reference_id") for item in reminder_items) else "动态旅行提醒待联网核验",
            "items": reminder_items,
        },
        "map_guidance": {
            "status": "needs_confirmation", "status_reason": "真实路线待计算",
            "location_ids": location_ids, "day_routes": [], "reminders": [], "unavailable_segments": [],
        },
        "delivery": {
            "status": "ready", "markdown_filename": _filename(plan.title, plan.version),
            "share_status": "private", "share_scopes": [],
        },
        "budget": document.get("budget") or plan_payload.get("budget_summary") or {},
        "sources": source_payload,
        "validation": validation,
        "checklist": checklist,
        "notes": notes,
    }


def _normalize_user_items(items: Any, item_type: str) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(items[:500], start=1):
        if not isinstance(item, Mapping):
            continue
        text_key = "text" if item_type == "checklist" else "content"
        text = str(item.get(text_key) or "").strip()
        if not text:
            continue
        normalized = {
            "id": str(item.get("id") or f"{item_type}_{index}"),
            text_key: text[:1000],
            "day": item.get("day"),
            "activity_id": str(item.get("activity_id") or ""),
            "poi_id": str(item.get("poi_id") or ""),
        }
        if item_type == "checklist":
            normalized["completed"] = item.get("completed") is True
        else:
            normalized["target_type"] = str(item.get("target_type") or "trip")
            normalized["target_id"] = str(item.get("target_id") or "")
        result.append(normalized)
    return result


class TripDocumentValidationError(ValueError):
    """A safe, field-level V2 validation failure for logs and planning events."""

    def __init__(self, error: ValidationError):
        self.diagnostics = []
        for item in error.errors(include_url=False, include_input=False)[:8]:
            message = str(item.get("msg") or "文档字段校验失败")
            location = ".".join(str(part) for part in item.get("loc", ()))
            if not location:
                tokens = re.findall(r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", message)
                location = next((token for token in tokens if "." in token or "_" in token), "document")
            self.diagnostics.append({
                "location": location,
                "type": str(item.get("type") or "validation_error"),
                "message": message,
            })
        summary = "；".join(
            f"{item['location']}: {item['message']}"
            for item in self.diagnostics
        )
        super().__init__(f"旅行文档结构无效：{summary}")


def validate_trip_document(document: Any) -> Dict[str, Any]:
    cleaned = _safe_document(document)
    try:
        if str(document.get("schema_version") or "") == SCHEMA_VERSION and "itinerary" in cleaned:
            candidate = dict(cleaned)
            candidate.pop("plan", None)
            candidate.pop("imported_at", None)
        else:
            raw_plan = cleaned.get("plan")
            if not isinstance(raw_plan, Mapping):
                raise ValueError("旅行文档缺少结构化 plan")
            candidate = adapt_v1_document_to_v2(cleaned)
        candidate["checklist"] = _normalize_user_items(candidate.get("checklist"), "checklist")
        candidate["notes"] = _normalize_user_items(candidate.get("notes"), "notes")
        parsed = TravelPlanDocumentV2.model_validate(candidate)
    except ValidationError as exc:
        raise TripDocumentValidationError(exc) from exc
    normalized = parsed.model_dump(mode="json")
    normalized["plan"] = _legacy_plan_from_v2(normalized)
    normalized["imported_at"] = _now_iso()
    return normalized


def _money(value: Any, currency: str = "CNY") -> str:
    try:
        return f"{currency} {float(value):.2f}"
    except (TypeError, ValueError):
        return "待确认"


def export_trip_markdown(document: Any) -> str:
    normalized = validate_trip_document(document)
    plan = normalized["plan"]
    overview = normalized["destination_overview"]
    destination_label = str(overview.get("name_zh") or "待确认")
    if overview.get("name_en"):
        destination_label += f" / {overview['name_en']}"
    lines = [
        f"# {plan.get('title') or '旅行行程'}", "",
        f"> 方案：{normalized['plan_id']} · 版本：v{normalized['version']} · Schema：{SCHEMA_VERSION}", "",
        "## 1. 目的地介绍", "",
        f"- 目的地：{destination_label}",
        f"- 国家/地区：{overview.get('country_name') or '待确认'}",
        f"- 时区：{overview.get('timezone') or '待确认'}",
        f"- 货币：{overview.get('currency') or '待确认'}",
        f"- 语言：{'、'.join(overview.get('languages') or []) or '待确认'}",
        f"- 适合季节：{'、'.join(overview.get('best_seasons') or []) or '待确认'}",
        f"- 区域概览：{overview.get('area_overview') or overview.get('status_reason') or '待确认'}",
    ]
    flexible_dates = is_flexible_date_range((normalized.get("intent") or {}).get("date_range"))
    if flexible_dates:
        lines.extend([
            "",
            "> 当前为日期待定参考方案：按第 1 天至第 N 天规划；具体日期确定后再补充往返班次、票价、余票、天气和时效性预约信息。",
        ])
    cover = overview.get("cover_image") if isinstance(overview.get("cover_image"), Mapping) else None
    if cover:
        lines.extend(["", f"![{cover.get('alt') or overview.get('name_zh')}]({cover.get('url')})", "",
                      f"图片：[{cover.get('photographer_name')}]({cover.get('photographer_url')}) / [Unsplash]({cover.get('unsplash_url')})"])

    def append_transport(title: str, section: Mapping[str, Any]) -> None:
        lines.extend(["", title, ""])
        if not section.get("options"):
            lines.append(f"- {section.get('status_reason') or '待确认'}")
            return
        for option in section.get("options", []):
            lines.append(
                f"- {option.get('mode')} {option.get('service_number') or ''}："
                f"{option.get('departure_station') or '待确认'} → {option.get('arrival_station') or '待确认'}，"
                f"{option.get('departure_time') or '待确认'}-{option.get('arrival_time') or '待确认'}，"
                f"{_money(option.get('price'), option.get('currency') or 'CNY')}，余量：{option.get('availability') or 'unknown'}"
            )
            lines.append(f"  - 来源：{option.get('source_reference_id') or '暂无可靠来源'}；查询时间：{option.get('queried_at') or '待确认'}")

    if not flexible_dates:
        append_transport("## 2. 出发地到目的地的车票", normalized["outbound_transport"])

    hotels = normalized["hotel_recommendations"]
    lines.extend(["", "## 3. 酒店推荐", ""])
    if not hotels.get("recommendations"):
        lines.append(f"- {hotels.get('status_reason') or '待确认'}")
    for hotel in hotels.get("recommendations", []):
        hotel_summary = f"- {hotel.get('name') or '待确认'}（{hotel.get('area') or '区域待确认'}）"
        if not flexible_dates:
            hotel_summary += f"：每晚 {_money(hotel.get('nightly_price'), hotel.get('currency') or 'CNY')}，评分 {hotel.get('rating') or '待确认'}"
        elif hotel.get("reasons"):
            hotel_summary += f"：{'；'.join(str(item) for item in hotel.get('reasons') if item)}"
        lines.append(hotel_summary)
        lines.append(f"  - 来源：{hotel.get('source_reference_id') or '暂无可靠来源'}；更新：{hotel.get('updated_at') or '待确认'}")

    lines.extend(["", "## 4. 日程规划（景点与美食）", ""])
    days = normalized["itinerary"].get("days") or []
    for day in days:
        if not isinstance(day, dict):
            continue
        lines.extend([f"### Day {day.get('day', '')} {day.get('theme') or ''}".rstrip(), ""])
        for activity in day.get("activities", []):
            if not isinstance(activity, dict):
                continue
            place = activity.get("place") if isinstance(activity.get("place"), dict) else {}
            title = activity.get("title") or place.get("name") or "待确认活动"
            time_range = "-".join(filter(None, [activity.get("start_time"), activity.get("end_time")]))
            suffix = f"（{time_range}）" if time_range else ""
            lines.append(f"- {title}{suffix}")
            if place.get("address"):
                lines.append(f"  - 地址：{place['address']}")
            if activity.get("estimated_cost") is not None:
                lines.append(f"  - 费用：{_money(activity['estimated_cost'])}（估算）")
        lines.append("")

    budget = normalized.get("budget") or {}
    lines.extend(["### 预算", ""])
    for key, label in [
        ("total", "总预算"), ("per_person", "人均预算"), ("transport", "交通"),
        ("accommodation", "住宿"), ("food", "餐饮"), ("tickets", "门票"),
        ("known_total", "可统计费用"), ("unknown_count", "未知费用项"),
    ]:
        value = budget.get(key, budget.get({"total": "budget_total", "per_person": "budget_per_person", "known_total": "estimated_total"}.get(key, key)))
        lines.append(f"- {label}：{_money(value, budget.get('currency', 'CNY')) if key != 'unknown_count' else value or 0}")

    checklist = normalized.get("checklist", [])
    if checklist:
        lines.extend(["", "### 行程清单", ""])
        lines.extend(f"- [{'x' if item.get('completed') else ' '}] {item['text']}" for item in checklist)
    notes = normalized.get("notes", [])
    if notes:
        lines.extend(["", "### 便签", ""])
        lines.extend(f"- {item['content']}" for item in notes)

    if not flexible_dates:
        append_transport("## 5. 目的地返回出发地的车票", normalized["return_transport"])

    reminders = normalized["friendly_reminders"]
    lines.extend(["", "## 6. 友情提醒", ""])
    if not reminders.get("items"):
        lines.append(f"- {reminders.get('status_reason') or '待确认'}")
    for item in reminders.get("items", []):
        if flexible_dates and item.get("category") in {"reservation", "weather"}:
            continue
        lines.append(f"- [{item.get('category') or 'other'}] {item.get('content')}")
        lines.append(f"  - 来源：{item.get('source_reference_id') or '暂无可靠来源'}；更新：{item.get('updated_at') or '待确认'}")

    map_guidance = normalized["map_guidance"]
    lines.extend(["", "## 7. 景点和地图提醒", ""])
    lines.append(f"- 地图地点：{len(map_guidance.get('location_ids') or [])} 个，仅来自当前版本日程。")
    lines.append(f"- 路线状态：{map_guidance.get('status_reason') or map_guidance.get('status') or '待确认'}")
    lines.extend(f"- {item}" for item in map_guidance.get("reminders", []))

    sources = normalized.get("sources") or []
    lines.extend(["", "### 数据来源", ""])
    if sources:
        for source in sources:
            if not isinstance(source, dict):
                continue
            title = source.get("title") or "未命名来源"
            url = source.get("url") or ""
            updated = source.get("updated_at") or "待确认"
            lines.append(f"- [{title}]({url})，更新时间：{updated}" if url else f"- {title}，更新时间：{updated}")
    else:
        lines.append("- 暂无可靠来源")
    delivery = normalized["delivery"]
    lines.extend(["", "## 8. 下载与分享", "",
                  f"- Markdown 文件：{delivery.get('markdown_filename')}",
                  f"- 分享状态：{delivery.get('share_status')}",
                  f"- 分享范围：{'、'.join(delivery.get('share_scopes') or []) or '未开启分享'}"])
    return "\n".join(lines).strip() + "\n"


def import_trip_markdown(content: str) -> Dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        raise ValueError("Markdown 内容为空")
    title_match = re.search(r"(?m)^#\s+(.+)$", text)
    title = title_match.group(1).strip() if title_match else "导入的旅行行程"
    day_matches = list(re.finditer(r"(?m)^#{2,3}[ \t]+Day[ \t]+(\d+)(?:[ \t]+(.+))?$", text, re.IGNORECASE))
    activities: List[Dict[str, Any]] = []
    trip_days: List[Dict[str, Any]] = []
    for index, match in enumerate(day_matches):
        day_number = int(match.group(1))
        end = day_matches[index + 1].start() if index + 1 < len(day_matches) else len(text)
        next_section = re.search(r"(?m)^#{2,3}\s+(?!Day\b)", text[match.end():end], re.IGNORECASE)
        if next_section:
            end = match.end() + next_section.start()
        section = text[match.end():end]
        day_activities: List[Dict[str, Any]] = []
        for item_index, item in enumerate(re.findall(r"(?m)^-\s+(?!\[[ xX]\])(.+)$", section), start=1):
            item_text = re.sub(r"（[^）]*）$", "", item).strip()
            activity = {
                "activity_id": f"imported_{day_number}_{item_index}", "day": day_number,
                "title": item_text, "notes": ["由 Markdown 导入，地点详情待确认"],
                "data_type": "estimated_data",
            }
            activities.append(activity)
            day_activities.append(activity)
        trip_days.append({"day": day_number, "theme": (match.group(2) or "").strip() or None, "activities": day_activities})
    if not trip_days:
        raise ValueError("Markdown 中未找到“## Day N”行程段落")
    checklist = [
        {"id": f"imported_check_{index}", "text": label.strip(), "completed": mark.lower() == "x"}
        for index, (mark, label) in enumerate(re.findall(r"(?m)^-\s+\[([ xX])\]\s+(.+)$", text), start=1)
    ]
    notes: List[Dict[str, Any]] = []
    notes_match = re.search(r"(?m)^###\s+便签\s*$", text)
    if notes_match:
        notes_end_match = re.search(r"(?m)^#{2,3}\s+", text[notes_match.end():])
        notes_end = notes_match.end() + notes_end_match.start() if notes_end_match else len(text)
        notes = [
            {"id": f"imported_note_{index}", "content": item.strip(), "scope": "trip"}
            for index, item in enumerate(re.findall(r"(?m)^-\s+(.+)$", text[notes_match.end():notes_end]), start=1)
        ]
    budget: Dict[str, Any] = {}
    budget_match = re.search(r"(?m)^###\s+预算\s*$", text)
    if budget_match:
        budget_end_match = re.search(r"(?m)^#{2,3}\s+", text[budget_match.end():])
        budget_end = budget_match.end() + budget_end_match.start() if budget_end_match else len(text)
        budget_section = text[budget_match.end():budget_end]
        budget_keys = {
            "总预算": "budget_total", "人均预算": "budget_per_person", "交通": "transport",
            "住宿": "accommodation", "餐饮": "food", "门票": "tickets",
            "可统计费用": "estimated_total", "未知费用项": "unknown_count",
        }
        for label, raw_value in re.findall(r"(?m)^-\s+([^：]+)：(.+)$", budget_section):
            key = budget_keys.get(label.strip())
            number_match = re.search(r"-?\d+(?:\.\d+)?", raw_value.replace(",", ""))
            if key and number_match:
                value = float(number_match.group())
                budget[key] = int(value) if value.is_integer() else value
        category_keys = ("transport", "accommodation", "food", "tickets")
        budget["categories"] = {key: budget[key] for key in category_keys if key in budget}
        budget["source_label"] = "由 Markdown 导入的估算值，需重新核验"
    document = {
        "schema_version": SCHEMA_VERSION,
        "plan": {
            "title": title, "intent": {}, "days": max(day["day"] for day in trip_days),
            "activities": activities, "trip_days": trip_days, "warnings": ["Markdown 导入无法恢复的实时字段已标记待确认"],
        },
        "budget": budget,
        "checklist": checklist,
        "notes": notes,
    }
    return validate_trip_document(document)


def import_trip_document(file_format: str, content: Any) -> Dict[str, Any]:
    normalized_format = str(file_format or "").strip().lower()
    if normalized_format == "markdown":
        return import_trip_markdown(str(content or ""))
    if normalized_format != "json":
        raise ValueError("仅支持 json 或 markdown")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 格式无效") from exc
    return validate_trip_document(content)


def build_share_snapshot(document: Any, scopes: Iterable[str]) -> Dict[str, Any]:
    normalized = validate_trip_document(document)
    selected = {str(scope) for scope in scopes} & ALLOWED_SHARE_SCOPES
    snapshot: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "title": normalized["plan"].get("title") or "只读旅行行程",
        "plan_id": normalized["plan_id"],
        "version": normalized["version"],
        "shared_at": _now_iso(),
        "scopes": sorted(selected),
    }
    if "itinerary" in selected:
        snapshot["document"] = {
            key: normalized[key]
            for key in (
                "schema_version", "plan_id", "version", "title", "generated_at", "locale", "intent",
                "destination_overview", "outbound_transport", "hotel_recommendations", "itinerary",
                "return_transport", "friendly_reminders", "map_guidance", "delivery", "validation",
            )
        }
        snapshot["plan"] = normalized["plan"]
    if "budget" in selected:
        snapshot["budget"] = normalized.get("budget") or normalized["plan"].get("budget_summary", {})
    if "sources" in selected:
        snapshot["sources"] = normalized.get("sources") or normalized["plan"].get("source_references", [])
    if "checklist" in selected:
        snapshot["checklist"] = normalized.get("checklist", [])
    if "notes" in selected:
        snapshot["notes"] = normalized.get("notes", [])
    return sanitize_user_visible_payload(snapshot)


class ShareRepository:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or (get_output_root_path() / "shares")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, token: str) -> Path:
        if not SHARE_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("分享令牌无效")
        return self.root / f"{token}.json"

    def create(self, document: Any, scopes: Iterable[str]) -> Dict[str, str]:
        token = secrets.token_urlsafe(24)
        management_key = secrets.token_urlsafe(32)
        payload = {
            "management_hash": hashlib.sha256(management_key.encode("utf-8")).hexdigest(),
            "snapshot": build_share_snapshot(document, scopes),
        }
        target = self._path(token)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return {"token": token, "management_key": management_key}

    def read(self, token: str) -> Dict[str, Any]:
        target = self._path(token)
        if not target.exists():
            raise FileNotFoundError(token)
        payload = json.loads(target.read_text(encoding="utf-8"))
        return sanitize_user_visible_payload(payload.get("snapshot", {}))

    def delete(self, token: str, management_key: str) -> None:
        target = self._path(token)
        if not target.exists():
            raise FileNotFoundError(token)
        payload = json.loads(target.read_text(encoding="utf-8"))
        supplied = hashlib.sha256(str(management_key or "").encode("utf-8")).hexdigest()
        if not secrets.compare_digest(supplied, str(payload.get("management_hash") or "")):
            raise PermissionError("管理密钥无效")
        target.unlink()


share_repository = ShareRepository()
