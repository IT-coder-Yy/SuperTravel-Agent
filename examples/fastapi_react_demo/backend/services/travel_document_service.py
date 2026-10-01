from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.schemas.trip_models import TravelPlanDocumentV2
from backend.schemas.trip_v3_models import (
    BudgetCategoryV3,
    BudgetSummaryV3,
    CoordinatesV3,
    DayRouteV3,
    DeliveryV3,
    DestinationOverviewV3,
    ImageAssetV3,
    ItineraryV3,
    LodgingOptionV3,
    LodgingPlanV3,
    MapGuidanceV3,
    MoneyV3,
    PlanningEventEnvelope,
    ReservationInfoV3,
    RouteLegV3,
    SourceStatus,
    TransportOptionV3,
    TransportSeatOptionV3,
    TransportSectionV3,
    TransportTimeV3,
    TravelerCountV3,
    TravelPlanDocumentV3,
    TripActionItemV3,
    TripActivityV3,
    TripAnchorV3,
    TripDayV3,
    TripIntentV3,
    TravelerBudgetCostV3,
    TripNoteV3,
    TripPlaceV3,
    TripSourceV3,
    ValidationIssueV3,
    ValidationSummaryV3,
    is_valid_route_geometry,
)
from backend.services.image_asset_service import build_unsplash_cover_asset, displayable_images
from backend.services.candidate_schedule_service import refresh_candidate_schedule_options
from backend.services.destination_catalog_service import INTERNATIONAL_DESTINATION_META, canonical_destination
from backend.services.travel_date_service import resolve_planning_date_contract


_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_LOCATION_KINDS = {"attraction", "food", "transport", "hotel", "shopping"}
_CATEGORY_MAP = {
    "attraction": "attraction",
    "food": "food",
    "transport": "transport",
    "hotel": "hotel",
    "shopping": "shopping",
    "free_time": "free_time",
    "other": "other",
    "景点": "attraction",
    "吃喝": "food",
    "餐饮": "food",
    "餐厅": "food",
    "交通": "transport",
    "住宿": "hotel",
    "酒店": "hotel",
    "购物": "shopping",
    "休息": "free_time",
    "自由活动": "free_time",
}


class FormalPlanValidationError(ValueError):
    """Business validation failure for a document that must not be published."""

    def __init__(self, issues: List[ValidationIssueV3]):
        blocking = [issue for issue in issues if issue.severity == "error"]
        self.issues = blocking
        self.diagnostics = [
            {
                "location": f"validation.{issue.code}",
                "type": "business_validation_error",
                "message": issue.message,
            }
            for issue in blocking[:8]
        ]
        summary = "；".join(issue.message for issue in blocking[:4]) or "正式方案未通过业务校验"
        super().__init__(summary)


def _formal_completeness_issues(
    *,
    itinerary: ItineraryV3,
    candidates: List[TripActivityV3],
    budget: BudgetSummaryV3,
    outbound_transport: TransportSectionV3,
    return_transport: TransportSectionV3,
    lodging_plan: LodgingPlanV3,
    destination_currency: str,
) -> List[ValidationIssueV3]:
    issues: List[ValidationIssueV3] = []
    scheduled = [activity for day in itinerary.days for activity in day.activities]
    verified_places = [
        activity.place
        for activity in scheduled
        if activity.place is not None
        and activity.place.coordinates is not None
        and not activity.place.poi_id.startswith("catalog_")
    ]
    if not scheduled:
        issues.append(ValidationIssueV3(
            code="EMPTY_ITINERARY",
            message="正式方案没有任何已排期活动，不能标记为完成。",
            severity="error",
        ))
    if not verified_places:
        issues.append(ValidationIssueV3(
            code="MISSING_VERIFIED_POI",
            message="正式方案至少需要一个带可信坐标的真实 POI。",
            severity="error",
        ))
    estimated_total = budget.estimated_total.amount if budget.estimated_total is not None else 0
    if estimated_total <= 0:
        issues.append(ValidationIssueV3(
            code="EMPTY_BUDGET_ESTIMATE",
            message="正式方案缺少非零费用估算，不能标记为完成。",
            severity="error",
        ))
    if destination_currency != "CNY":
        foreign_amounts = [
            amount
            for amount in [
                *(activity.estimated_cost for activity in scheduled),
                *(category.amount for category in budget.categories),
            ]
            if amount is not None and amount.currency == destination_currency
        ]
        if not any(
            amount.cny_reference_amount is not None and amount.exchange_rate_as_of is not None
            for amount in foreign_amounts
        ):
            issues.append(ValidationIssueV3(
                code="MISSING_FOREIGN_CURRENCY_REFERENCE",
                message=f"国际方案缺少 {destination_currency} 原币金额及带日期的人民币参考换算。",
                severity="error",
            ))
    for code, label, section in (
        ("OUTBOUND_DEGRADATION_REASON_MISSING", "去程交通", outbound_transport),
        ("RETURN_DEGRADATION_REASON_MISSING", "返程交通", return_transport),
        ("LODGING_DEGRADATION_REASON_MISSING", "住宿", lodging_plan),
    ):
        if section.status != "ready" and not str(section.status_reason or "").strip():
            issues.append(ValidationIssueV3(
                code=code,
                message=f"{label}降级时必须说明原因和后续核验方式。",
                severity="error",
            ))
    invalid_candidates = [
        candidate
        for candidate in candidates
        if candidate.place is None
        or candidate.place.coordinates is None
        or candidate.place.poi_id.startswith("catalog_")
    ]
    if invalid_candidates:
        issues.append(ValidationIssueV3(
            code="UNVERIFIED_CANDIDATES_EXCLUDED",
            message=f"{len(invalid_candidates)} 个候选地点尚未完成真实 POI 与坐标核验，不得视为可用候选。",
            severity="warning",
        ))
    return issues


def _clock_sort_value(value: Any) -> int:
    match = _TIME_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return 24 * 60
    hour, minute = int(match.group(1)), int(match.group(2))
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else 24 * 60


def _clock_text(minutes: int) -> str:
    bounded = max(0, min(int(minutes), 24 * 60 - 1))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def safe_delivery_filename(
    value: Any,
    *,
    fallback_title: str,
    revision: int,
    suffix: str,
    max_utf8_bytes: int = 120,
) -> str:
    """Return a cross-platform filename with a bounded UTF-8 component size."""
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    raw = str(value or "").strip()
    raw_name = Path(raw).name if raw else ""
    raw_stem = Path(raw_name).stem if raw_name else ""
    fallback = f"{fallback_title or '旅行规划'}-v{max(1, int(revision))}"
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", raw_stem or fallback).strip(" .-") or "旅行规划"
    if stem.casefold() in {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }:
        stem = f"{stem}-旅行规划"
    candidate = f"{stem}{normalized_suffix}"
    if len(candidate.encode("utf-8")) <= max_utf8_bytes:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    trailer = f"-{digest}{normalized_suffix}"
    allowed = max_utf8_bytes - len(trailer.encode("utf-8"))
    kept: List[str] = []
    used = 0
    for character in stem:
        size = len(character.encode("utf-8"))
        if used + size > allowed:
            break
        kept.append(character)
        used += size
    bounded_stem = "".join(kept).rstrip(" .-") or "旅行规划"
    return f"{bounded_stem}{trailer}"


def _normalize_legacy_activity_window(
    start_at: Any,
    end_at: Any,
    duration_minutes: Any,
    *,
    cursor: int,
) -> Tuple[Optional[str], Optional[str], Optional[int], int, bool]:
    """把旧文档时间迁移成不重叠、正时长的单日时间段。"""
    raw_start = str(start_at or "").strip()
    raw_end = str(end_at or "").strip()
    if not raw_start and not raw_end:
        try:
            duration = int(duration_minutes) if duration_minutes not in (None, "") else None
        except (TypeError, ValueError):
            duration = None
        return None, None, duration if duration and duration > 0 else None, cursor, False

    parsed_start = _clock_sort_value(raw_start)
    parsed_end = _clock_sort_value(raw_end)
    valid_pair = parsed_start < 24 * 60 and parsed_end < 24 * 60 and parsed_end > parsed_start
    try:
        declared_duration = int(duration_minutes) if duration_minutes not in (None, "") else None
    except (TypeError, ValueError):
        declared_duration = None
    if declared_duration is not None and declared_duration <= 0:
        declared_duration = None
    duration = declared_duration or (parsed_end - parsed_start if valid_pair else 60)
    normalized_start = max(parsed_start if parsed_start < 24 * 60 else 9 * 60, cursor)
    normalized_end = normalized_start + duration
    if normalized_end >= 24 * 60:
        return None, None, None, cursor, True
    normalized = (_clock_text(normalized_start), _clock_text(normalized_end))
    changed = not valid_pair or normalized != (raw_start, raw_end) or declared_duration != duration_minutes
    return normalized[0], normalized[1], duration, normalized_end, changed


def _currency_code(value: Any, default: str = "XXX") -> str:
    normalized = str(value or "").strip().upper()
    return normalized if len(normalized) == 3 else default


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _parse_aware_datetime(value: Any, default_timezone: str = "Asia/Shanghai") -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(default_timezone))
    return parsed


def _parse_date_range(intent: Any) -> Tuple[str, Optional[date], Optional[date], int]:
    return resolve_planning_date_contract(
        getattr(intent, "date_range", ""),
        getattr(intent, "days", 0),
    )


def _source_status(data_type: Any, source_name: str = "") -> SourceStatus:
    normalized = str(data_type or "").lower()
    source_lower = source_name.lower()
    if normalized == "confirmed_live_data":
        return "realtime_verified"
    if "official" in source_lower or "官网" in source_name or "官方" in source_name:
        return "official_reference"
    if "map" in source_lower or "地图" in source_name:
        return "map_reference"
    if normalized == "reference_data":
        return "guide_reference"
    return "user_confirmation_required"


def _activity_kind(value: Any) -> str:
    return _CATEGORY_MAP.get(str(value or "").strip(), "other")


def _money(
    amount: Any,
    currency: Any = "CNY",
    *,
    source_status: SourceStatus = "user_confirmation_required",
    cny_reference_amount: Any = None,
    exchange_rate_as_of: Any = None,
) -> Optional[MoneyV3]:
    if amount in (None, ""):
        return None
    try:
        numeric_amount = max(float(amount), 0.0)
    except (TypeError, ValueError):
        return None
    cny_value: Optional[float]
    try:
        cny_value = None if cny_reference_amount in (None, "") else max(float(cny_reference_amount), 0.0)
    except (TypeError, ValueError):
        cny_value = None
    try:
        exchange_date = (
            exchange_rate_as_of
            if isinstance(exchange_rate_as_of, date)
            else date.fromisoformat(str(exchange_rate_as_of))
        ) if exchange_rate_as_of else None
    except (TypeError, ValueError):
        exchange_date = None
    return MoneyV3(
        amount=numeric_amount,
        currency=_currency_code(currency, "CNY"),
        cny_reference_amount=cny_value,
        exchange_rate_as_of=exchange_date,
        source_status=source_status,
    )


def _transport_time(
    raw_value: Any,
    *,
    travel_date: Optional[date],
    timezone_name: Optional[str],
) -> Optional[TransportTimeV3]:
    if raw_value in (None, ""):
        return None
    display = str(raw_value).strip()
    if not timezone_name:
        return TransportTimeV3(display_text=display)
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return TransportTimeV3(display_text=display)

    parsed = _parse_aware_datetime(display, timezone_name)
    if parsed is None and travel_date and timezone_name:
        match = _TIME_PATTERN.match(display)
        if match:
            parsed = datetime.combine(
                travel_date,
                time(hour=int(match.group(1)), minute=int(match.group(2))),
                local_zone,
            )
    if parsed is None:
        return TransportTimeV3(display_text=display)
    local = parsed.astimezone(local_zone)
    beijing = local.astimezone(ZoneInfo("Asia/Shanghai"))
    return TransportTimeV3(
        display_text=display,
        utc=local.astimezone(timezone.utc),
        local_iso=local,
        timezone=timezone_name,
        beijing_iso=beijing,
    )


def _has_complete_transport_time(value: Optional[TransportTimeV3]) -> bool:
    return bool(
        value
        and value.utc is not None
        and value.local_iso is not None
        and value.beijing_iso is not None
        and value.timezone
    )


def _is_time_only(value: Any) -> bool:
    return bool(_TIME_PATTERN.match(str(value or "").strip()))


def _raw_transport_time(value: Optional[TransportTimeV3]) -> Optional[TransportTimeV3]:
    return TransportTimeV3(display_text=value.display_text) if value else None


def _with_transport_day_offset(
    departure: Optional[TransportTimeV3],
    arrival: Optional[TransportTimeV3],
    *,
    arrival_is_time_only: bool,
    duration_minutes: Optional[int],
) -> Tuple[Optional[TransportTimeV3], Optional[TransportTimeV3]]:
    """Set a verifiable arrival day offset without inventing missing timezone context."""
    if not (_has_complete_transport_time(departure) and _has_complete_transport_time(arrival)):
        return departure, arrival

    observed_minutes = int((arrival.utc - departure.utc).total_seconds() // 60)
    day_shift = 0
    if arrival_is_time_only:
        if duration_minutes is not None:
            target_shift = round((duration_minutes - observed_minutes) / (24 * 60))
            if target_shift >= 0 and abs(observed_minutes + target_shift * 24 * 60 - duration_minutes) <= 60:
                day_shift = target_shift
        while observed_minutes + day_shift * 24 * 60 < 0:
            day_shift += 1
        if day_shift > 2:
            return _raw_transport_time(departure), _raw_transport_time(arrival)
        if day_shift:
            local = arrival.local_iso + timedelta(days=day_shift)
            arrival = TransportTimeV3(
                display_text=arrival.display_text,
                utc=local.astimezone(timezone.utc),
                local_iso=local,
                timezone=arrival.timezone,
                beijing_iso=local.astimezone(ZoneInfo("Asia/Shanghai")),
                day_offset=day_shift,
            )

    if arrival.utc < departure.utc:
        return _raw_transport_time(departure), _raw_transport_time(arrival)
    day_offset = max((arrival.local_iso.date() - departure.local_iso.date()).days, 0)
    if day_offset > 2:
        return _raw_transport_time(departure), _raw_transport_time(arrival)
    return departure, arrival.model_copy(update={"day_offset": day_offset})


def _build_sources(document: TravelPlanDocumentV2) -> Tuple[List[TripSourceV3], Dict[str, str]]:
    sources: List[TripSourceV3] = []
    lookup: Dict[str, str] = {}
    for index, legacy in enumerate(document.sources, start=1):
        source_id = _stable_id("src", document.plan_id, index, legacy.title, legacy.url)
        source_name = legacy.source or legacy.title or "参考资料"
        source = TripSourceV3(
            source_id=source_id,
            title=legacy.title or source_name,
            source_name=source_name,
            status=_source_status(legacy.data_type, source_name),
            url=legacy.url or None,
            updated_at=_parse_aware_datetime(legacy.updated_at),
            related_fields=list(legacy.related_fields),
            related_place_ids=list(legacy.related_places),
        )
        sources.append(source)
        for key in (str(index), getattr(legacy, "reference_id", None), legacy.title, legacy.url, legacy.source):
            if key:
                lookup[str(key)] = source_id
    return sources, lookup


def _map_ref(value: Any, lookup: Dict[str, str]) -> Optional[str]:
    if value in (None, ""):
        return None
    # IDs, URLs and legacy numeric references must match exactly. In particular,
    # a digit in an unknown image hash is not a reference to that source index.
    return lookup.get(str(value))


def _map_refs(values: Iterable[Any], lookup: Dict[str, str]) -> List[str]:
    mapped = [_map_ref(value, lookup) for value in values]
    return list(dict.fromkeys(value for value in mapped if value))


def _map_place(legacy: Any, kind: str, lookup: Dict[str, str]) -> Optional[TripPlaceV3]:
    if legacy is None or not legacy.poi_id:
        return None
    raw_coordinate_system = str(getattr(legacy, "coordinate_system", "") or "").upper()
    if raw_coordinate_system not in {"WGS84", "BD09LL"}:
        source_text = str(getattr(legacy, "source", "") or "")
        raw_coordinate_system = (
            "BD09LL"
            if str(legacy.poi_id).startswith("baidu_geo_") or "百度" in source_text
            else "WGS84"
        )
    coordinates = (
        CoordinatesV3(
            latitude=legacy.lat,
            longitude=legacy.lng,
            coordinate_system=raw_coordinate_system,
        )
        if legacy.lat is not None and legacy.lng is not None
        else None
    )
    return TripPlaceV3(
        poi_id=legacy.poi_id,
        name=legacy.name,
        category=kind,
        city=legacy.city,
        address=legacy.address,
        opening_hours=str(legacy.opening_hours) if legacy.opening_hours not in (None, "") else None,
        coordinates=coordinates,
        summary=legacy.summary,
        evidence_refs=_map_refs(
            [item.get("source_reference_id") for item in legacy.sources if isinstance(item, dict)],
            lookup,
        ),
    )


def _candidate_place_cost(place: Any, currency: str) -> Optional[MoneyV3]:
    """Keep a provider/reference price only when the map record exposes one."""
    raw_price = getattr(place, "price", None)
    if raw_price in (None, ""):
        return None
    if isinstance(raw_price, dict):
        value = raw_price.get("amount") or raw_price.get("price")
    else:
        value = raw_price
    if isinstance(value, str) and any(marker in value for marker in ("免费", "free")):
        value = 0
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        value = match.group(0) if match else None
    return _money(value, currency, source_status=_source_status(getattr(place, "data_type", None)))


def _map_verified_candidate_places(
    document: TravelPlanDocumentV2,
    *,
    lookup: Dict[str, str],
    currency: str,
    existing_ids: set[str],
    existing_poi_ids: set[str],
    migration_issues: List[ValidationIssueV3],
) -> List[TripActivityV3]:
    mapped: List[TripActivityV3] = []
    for index, legacy_place in enumerate(document.candidate_places):
        kind = _activity_kind(legacy_place.category)
        place = _map_place(legacy_place, kind, lookup)
        has_trusted_coordinates = getattr(legacy_place, "coordinates_trusted", None) is not False
        if (
            kind not in _LOCATION_KINDS
            or place is None
            or place.poi_id in existing_poi_ids
        ):
            continue
        activity_id = _stable_id("candidate", document.plan_id, place.poi_id)
        if activity_id in existing_ids:
            continue
        existing_ids.add(activity_id)
        existing_poi_ids.add(place.poi_id)
        mapped.append(
            TripActivityV3(
                activity_id=activity_id,
                kind=kind,
                title=place.name,
                duration_minutes=legacy_place.suggested_duration_minutes,
                place=(
                    place
                    if has_trusted_coordinates
                    else place.model_copy(update={"coordinates": None})
                ),
                estimated_cost=_candidate_place_cost(legacy_place, currency),
                evidence_refs=list(place.evidence_refs),
            )
        )
        if len(mapped) >= 15:
            break
    if document.candidate_places and len(mapped) < min(7, len(document.candidate_places)):
        migration_issues.append(
            ValidationIssueV3(
                code="CANDIDATE_POOL_VERIFICATION_INSUFFICIENT",
                message="部分候选地点缺少稳定 POI 或坐标，未进入可排程候选池",
                severity="warning",
            )
        )
    return mapped


def _map_route_leg(
    legacy: Any,
    *,
    from_id: str,
    to_id: str,
) -> Optional[RouteLegV3]:
    if legacy is None:
        return None
    return RouteLegV3(
        from_id=from_id,
        to_id=to_id,
        mode=legacy.mode or "待确认",
        distance_meters=legacy.distance_meters,
        duration_minutes=legacy.duration_minutes,
        estimated_cost=_money(legacy.estimated_cost),
        provider=legacy.provider,
        calculated_at=_parse_aware_datetime(legacy.calculated_at),
        status="unavailable",
    )


def _map_transport_seat_options(
    raw_options: Any,
    *,
    currency: str,
    source_status: SourceStatus,
) -> List[TransportSeatOptionV3]:
    if source_status != "realtime_verified" or not isinstance(raw_options, list):
        return []

    mapped: List[TransportSeatOptionV3] = []
    seen = set()
    for raw in raw_options[:12]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("seat") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        remaining_text = str(raw.get("remaining_text") or raw.get("remaining") or "").strip() or None
        availability = str(raw.get("availability") or "unknown")
        if availability not in {"available", "limited", "unavailable", "unknown"}:
            availability = "unknown"
        if availability != "unknown" and not remaining_text:
            availability = "unknown"
        mapped.append(
            TransportSeatOptionV3(
                name=name,
                availability=availability,
                remaining_text=remaining_text,
                price=_money(raw.get("price"), currency, source_status=source_status),
            )
        )
    return mapped


def _map_transport_section(
    legacy: Any,
    *,
    lookup: Dict[str, str],
    departure_timezone: Optional[str] = None,
    arrival_timezone: Optional[str] = None,
) -> TransportSectionV3:
    try:
        travel_date = date.fromisoformat(legacy.travel_date) if legacy.travel_date else None
    except ValueError:
        travel_date = None
    options: List[TransportOptionV3] = []
    for option in legacy.options[:3]:
        source_ref = _map_ref(option.source_reference_id, lookup)
        status = _source_status(option.data_type)
        availability = option.availability if status == "realtime_verified" else "unknown"
        service_number = option.service_number if status == "realtime_verified" else None
        departure_time = _transport_time(
            option.departure_time,
            travel_date=travel_date,
            timezone_name=departure_timezone or ("Asia/Shanghai" if legacy.scope == "domestic" else None),
        ) if status == "realtime_verified" else None
        arrival_time = _transport_time(
            option.arrival_time,
            travel_date=travel_date,
            timezone_name=arrival_timezone or ("Asia/Shanghai" if legacy.scope == "domestic" else None),
        ) if status == "realtime_verified" else None
        departure_time, arrival_time = _with_transport_day_offset(
            departure_time,
            arrival_time,
            arrival_is_time_only=_is_time_only(option.arrival_time),
            duration_minutes=option.duration_minutes,
        )
        options.append(
            TransportOptionV3(
                option_id=option.option_id,
                mode=option.mode,
                service_number=service_number,
                departure_place=option.departure_station,
                arrival_place=option.arrival_station,
                departure_hub=_map_place(getattr(option, "departure_hub", None), "transport", lookup),
                arrival_hub=_map_place(getattr(option, "arrival_hub", None), "transport", lookup),
                departure_time=departure_time,
                arrival_time=arrival_time,
                duration_minutes=option.duration_minutes,
                transfers=option.transfers,
                price=_money(option.price, option.currency, source_status=status),
                availability=availability,
                seat_options=_map_transport_seat_options(
                    option.seat_options,
                    currency=option.currency,
                    source_status=status,
                ),
                booking_url=option.booking_url,
                source_status=status,
                source_refs=[source_ref] if source_ref else [],
                verified_at=_parse_aware_datetime(option.queried_at),
            )
        )
    selected = legacy.recommended_option_id
    if selected not in {option.option_id for option in options}:
        selected = None
    status_map = {
        "ready": "ready",
        "needs_date": "user_confirmation_required",
        "needs_confirmation": "user_confirmation_required",
        "unavailable": "unavailable",
    }
    return TransportSectionV3(
        direction=legacy.direction,
        scope=legacy.scope,
        status=status_map.get(legacy.status, "degraded"),
        status_reason=legacy.status_reason,
        selected_option_id=selected,
        options=options,
        official_query_url=getattr(legacy, "official_query_url", None),
    )


def _map_budget(document: TravelPlanDocumentV2, budget_total: MoneyV3) -> BudgetSummaryV3:
    payload = document.budget if isinstance(document.budget, dict) else {}
    category_map = {
        "transport": "transport",
        "transportation": "transport",
        "lodging": "lodging",
        "accommodation": "lodging",
        "hotel": "lodging",
        "food": "food",
        "dining": "food",
        "activities": "activities",
        "tickets": "activities",
        "attractions": "activities",
        "shopping": "shopping",
        "other": "other",
    }
    source_statuses = {
        "realtime_verified",
        "official_reference",
        "map_reference",
        "guide_reference",
        "user_confirmation_required",
    }

    def money_from_value(value: Any, fallback_currency: str) -> Optional[MoneyV3]:
        if isinstance(value, dict):
            source_status = value.get("source_status")
            return _money(
                value.get("amount"),
                value.get("currency", fallback_currency),
                source_status=(
                    source_status
                    if source_status in source_statuses
                    else "user_confirmation_required"
                ),
                cny_reference_amount=value.get("cny_reference_amount"),
                exchange_rate_as_of=value.get("exchange_rate_as_of"),
            )
        return _money(value, fallback_currency)

    estimated = payload.get("estimated_total")
    if estimated is None:
        estimated = payload.get("known_total")
    if estimated is None:
        estimated = payload.get("total_estimated")
    estimated_money = money_from_value(
        estimated,
        _currency_code(payload.get("currency"), budget_total.currency),
    )
    categories: List[BudgetCategoryV3] = []
    currency_breakdown = payload.get("currency_breakdown")
    raw_currency_categories = (
        currency_breakdown.get("categories")
        if isinstance(currency_breakdown, dict)
        else None
    )
    if isinstance(raw_currency_categories, list):
        for item in raw_currency_categories:
            if not isinstance(item, dict):
                continue
            money = money_from_value(item.get("amount"), budget_total.currency)
            if money:
                categories.append(
                    BudgetCategoryV3(
                        category=category_map.get(str(item.get("category") or ""), "other"),
                        amount=money,
                    )
                )
    else:
        raw_categories = payload.get("categories")
        if isinstance(raw_categories, dict):
            for key, value in raw_categories.items():
                money = money_from_value(value, budget_total.currency)
                if money:
                    categories.append(
                        BudgetCategoryV3(category=category_map.get(str(key), "other"), amount=money)
                    )

    traveler_costs: List[TravelerBudgetCostV3] = []
    raw_traveler_costs = (
        currency_breakdown.get("traveler_costs")
        if isinstance(currency_breakdown, dict)
        else None
    )
    if isinstance(raw_traveler_costs, list):
        allowed_types = {"adult", "child", "senior"}
        allowed_statuses = {
            "standard_price",
            "official_discount_verified",
            "adult_price_assumed",
            "not_applicable",
        }
        for item in raw_traveler_costs:
            if not isinstance(item, dict) or item.get("traveler_type") not in allowed_types:
                continue
            try:
                count = max(int(item.get("count", 0)), 0)
            except (TypeError, ValueError):
                count = 0
            traveler_costs.append(
                TravelerBudgetCostV3(
                    traveler_type=item["traveler_type"],
                    count=count,
                    estimated_total=money_from_value(item.get("estimated_total"), "CNY"),
                    pricing_status=(
                        item.get("pricing_status")
                        if item.get("pricing_status") in allowed_statuses
                        else "not_applicable"
                    ),
                )
            )

    overrun = money_from_value(payload.get("overrun_amount"), budget_total.currency)
    return BudgetSummaryV3(
        total_budget=budget_total,
        estimated_total=estimated_money,
        categories=categories,
        unknown_cost_count=int(payload.get("unknown_count", 0) or 0),
        over_budget=bool(payload.get("over_budget", False)),
        overrun_amount=overrun,
        traveler_costs=traveler_costs,
        warnings=list(payload.get("warnings") or []),
    )


def _map_actions(
    document: TravelPlanDocumentV2,
    *,
    lookup: Dict[str, str],
    include_date_sensitive: bool = True,
) -> List[TripActionItemV3]:
    actions: List[TripActionItemV3] = []
    for index, item in enumerate(document.checklist):
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        actions.append(
            TripActionItemV3(
                action_id=str(item.get("id") or _stable_id("action", document.plan_id, "checklist", index)),
                kind="checklist",
                scope="trip",
                text=str(item["text"]).strip(),
                status="completed" if item.get("completed") else "pending",
            )
        )
    for index, reminder in enumerate(document.friendly_reminders.items):
        if not include_date_sensitive and reminder.category in {"reservation", "weather"}:
            continue
        reminder_text = str(reminder.content or "").strip()
        if not reminder_text:
            continue
        source_ref = _map_ref(reminder.source_reference_id, lookup)
        actions.append(
            TripActionItemV3(
                action_id=_stable_id("action", document.plan_id, "reminder", index, reminder_text),
                kind="reservation_reminder" if reminder.category == "reservation" else "checklist",
                scope="trip",
                text=reminder_text,
                status="pending",
                source_ref=source_ref,
            )
        )
    return actions


def _map_existing_notes(
    document: TravelPlanDocumentV2,
    generated_at: datetime,
) -> List[TripNoteV3]:
    notes: List[TripNoteV3] = []
    for index, item in enumerate(document.notes):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            continue
        raw_scope = str(item.get("scope") or "trip")
        scope = raw_scope if raw_scope in {"trip", "day", "activity"} else "trip"
        notes.append(
            TripNoteV3(
                note_id=str(item.get("id") or _stable_id("note", document.plan_id, index, content)),
                scope=scope,
                target_id=str(item.get("target_id")) if item.get("target_id") else None,
                content=content,
                updated_at=_parse_aware_datetime(item.get("updated_at")) or generated_at,
            )
        )
    return notes


def _map_itinerary(
    document: TravelPlanDocumentV2,
    *,
    start_date: Optional[date],
    days: int,
    destination_timezone: str,
    lookup: Dict[str, str],
    generated_at: datetime,
    notes: List[TripNoteV3],
    migration_issues: List[ValidationIssueV3],
    destination_currency: str,
    include_date_sensitive: bool = True,
) -> Tuple[ItineraryV3, List[TripActivityV3]]:
    legacy_days = {day.day: day for day in document.itinerary.days}
    mapped_days: List[TripDayV3] = []
    candidates: List[TripActivityV3] = []
    for day_number in range(1, days + 1):
        legacy_day = legacy_days.get(day_number)
        mapped_activities: List[TripActivityV3] = []
        if legacy_day:
            legacy_activities = sorted(
                legacy_day.activities,
                key=lambda activity: (
                    _clock_sort_value(activity.start_time),
                    _clock_sort_value(activity.end_time),
                    activity.activity_id,
                ),
            )
            schedule_cursor = 0
            for index, legacy_activity in enumerate(legacy_activities, start=1):
                kind = _activity_kind(legacy_activity.activity_type)
                place = _map_place(legacy_activity.place, kind, lookup)
                refs = _map_refs(legacy_activity.evidence_refs, lookup)
                start_at, end_at, duration_minutes, next_cursor, time_adjusted = _normalize_legacy_activity_window(
                    legacy_activity.start_time,
                    legacy_activity.end_time,
                    legacy_activity.duration_minutes,
                    cursor=schedule_cursor,
                )
                if start_at and end_at:
                    schedule_cursor = next_cursor
                if time_adjusted:
                    migration_issues.append(
                        ValidationIssueV3(
                            code="LEGACY_ACTIVITY_TIME_NORMALIZED",
                            message=f"{legacy_activity.title} 的旧版时间已调整为不重叠的有效时段",
                            severity="warning",
                            target_id=legacy_activity.activity_id,
                        )
                    )
                raw_image_assets = (
                    getattr(legacy_activity.place, "image_assets", [])
                    if legacy_activity.place is not None
                    else []
                )
                mapped_images: List[ImageAssetV3] = []
                for raw_image in raw_image_assets if isinstance(raw_image_assets, list) else []:
                    if not isinstance(raw_image, dict):
                        continue
                    source_ref = _map_ref(raw_image.get("source_ref"), lookup)
                    if not source_ref:
                        continue
                    try:
                        mapped_images.append(ImageAssetV3.model_validate({
                            **raw_image,
                            "source_ref": source_ref,
                            "checked_at": _parse_aware_datetime(raw_image.get("checked_at")) or generated_at,
                        }))
                    except (TypeError, ValueError):
                        continue
                mapped_images = displayable_images(mapped_images, maximum=3)
                note_id: Optional[str] = None
                if legacy_activity.notes:
                    note_id = _stable_id("note", document.plan_id, legacy_activity.activity_id)
                    notes.append(
                        TripNoteV3(
                            note_id=note_id,
                            scope="activity",
                            target_id=legacy_activity.activity_id,
                            content="\n".join(str(value) for value in legacy_activity.notes if str(value).strip()),
                            updated_at=generated_at,
                        )
                    )
                if kind in _LOCATION_KINDS and place is None:
                    candidates.append(
                        TripActivityV3(
                            activity_id=legacy_activity.activity_id,
                            kind=kind,
                            title=legacy_activity.title,
                            evidence_refs=refs,
                            note_id=note_id,
                            estimated_cost=_money(
                                legacy_activity.estimated_cost,
                                legacy_activity.estimated_cost_currency or "CNY",
                                source_status=_source_status(legacy_activity.data_type),
                                cny_reference_amount=legacy_activity.estimated_cost_cny_reference_amount,
                                exchange_rate_as_of=legacy_activity.estimated_cost_exchange_rate_as_of,
                            ),
                        )
                    )
                    migration_issues.append(
                        ValidationIssueV3(
                            code="LEGACY_PLACE_MOVED_TO_CANDIDATE",
                            message=f"{legacy_activity.title} 缺少稳定 POI 或坐标，迁移后保留在候选池",
                            severity="warning",
                            target_id=legacy_activity.activity_id,
                        )
                    )
                    continue
                mapped_activities.append(
                    TripActivityV3(
                        activity_id=legacy_activity.activity_id,
                        kind=kind,
                        title=legacy_activity.title,
                        day=day_number,
                        order=len(mapped_activities) + 1,
                        start_at=start_at,
                        end_at=end_at,
                        duration_minutes=duration_minutes,
                        estimated_cost_unit=legacy_activity.estimated_cost_unit,
                        official_traveler_prices={kind: MoneyV3(
                            amount=amount, currency=legacy_activity.estimated_cost_currency or "CNY",
                            cny_reference_amount=(legacy_activity.estimated_cost_cny_reference_amount * amount / legacy_activity.estimated_cost)
                            if legacy_activity.estimated_cost and legacy_activity.estimated_cost_cny_reference_amount is not None else None,
                            exchange_rate_as_of=legacy_activity.estimated_cost_exchange_rate_as_of,
                            source_status="official_reference",
                        ) for kind, amount in legacy_activity.official_traveler_prices.items()},
                        meal_type=legacy_activity.meal_type,
                        place=place,
                        images=mapped_images,
                        cover_image_id=mapped_images[0].image_id if mapped_images else None,
                        estimated_cost=_money(
                            legacy_activity.estimated_cost,
                            legacy_activity.estimated_cost_currency or "CNY",
                            source_status=_source_status(legacy_activity.data_type),
                            cny_reference_amount=legacy_activity.estimated_cost_cny_reference_amount,
                            exchange_rate_as_of=legacy_activity.estimated_cost_exchange_rate_as_of,
                        ),
                        reservation=(
                            ReservationInfoV3(
                                required=bool(legacy_activity.reservation),
                                status="needs_confirmation" if legacy_activity.reservation else "not_required",
                            )
                            if include_date_sensitive
                            else None
                        ),
                        note_id=note_id,
                        evidence_refs=refs,
                        route_to_next=None,
                    )
                )
        mapped_activities.sort(
            key=lambda activity: (
                _clock_sort_value(activity.start_at),
                _clock_sort_value(activity.end_at),
                activity.activity_id,
            )
        )
        for order, activity in enumerate(mapped_activities, start=1):
            activity.order = order
        mapped_days.append(
            TripDayV3(
                day=day_number,
                date=(start_date + timedelta(days=day_number - 1)) if start_date else None,
                timezone=destination_timezone,
                theme=legacy_day.theme if legacy_day else None,
                activities=mapped_activities,
                anchors=[],
            )
        )
    existing_ids = {
        activity.activity_id
        for day in mapped_days
        for activity in day.activities
    } | {candidate.activity_id for candidate in candidates}
    existing_poi_ids = {
        activity.place.poi_id
        for day in mapped_days
        for activity in day.activities
        if activity.place is not None
    }
    candidates.extend(
        _map_verified_candidate_places(
            document,
            lookup=lookup,
            currency=destination_currency,
            existing_ids=existing_ids,
            existing_poi_ids=existing_poi_ids,
            migration_issues=migration_issues,
        )
    )
    return ItineraryV3(days=mapped_days), candidates[:15]


def _map_day_routes(document: TravelPlanDocumentV2) -> List[DayRouteV3]:
    routes: List[DayRouteV3] = []
    for legacy_day_route in document.map_guidance.day_routes:
        legs: List[RouteLegV3] = []
        for leg in legacy_day_route.legs:
            ready = bool(
                legacy_day_route.status in {"ready", "partial"}
                and leg.status == "ready"
                and leg.provider
                and leg.coordinate_system == legacy_day_route.coordinate_system
                and is_valid_route_geometry(leg.geometry)
            )
            legs.append(
                RouteLegV3(
                    from_id=leg.from_activity_id,
                    to_id=leg.to_activity_id,
                    mode=leg.mode,
                    distance_meters=leg.distance_meters,
                    duration_minutes=leg.duration_minutes,
                    provider=leg.provider if ready else None,
                    coordinate_system=leg.coordinate_system if ready else None,
                    geometry=leg.geometry if ready else None,
                    calculated_at=_parse_aware_datetime(leg.calculated_at),
                    status="ready" if ready else "unavailable",
                )
            )
        ready_count = sum(leg.status == "ready" for leg in legs)
        status = (
            "ready" if legs and ready_count == len(legs)
            else "partial" if ready_count
            else "unavailable"
        )
        routes.append(
            DayRouteV3(
                day=legacy_day_route.day,
                status=status,
                legs=legs,
            )
        )
    return routes


def _is_mappable_anchor_place(place: Optional[TripPlaceV3]) -> bool:
    return bool(place and place.poi_id and place.coordinates)


def _selected_transport_hub(
    section: TransportSectionV3,
    *,
    field: str,
) -> Optional[TripPlaceV3]:
    if not section.selected_option_id:
        return None
    option = next((item for item in section.options if item.option_id == section.selected_option_id), None)
    place = getattr(option, field, None) if option else None
    return place if _is_mappable_anchor_place(place) else None


def _apply_v3_transport_windows(
    itinerary: ItineraryV3,
    candidates: List[TripActivityV3],
    *,
    outbound: TransportSectionV3,
    returning: TransportSectionV3,
    migration_issues: List[ValidationIssueV3],
    transfer_minutes: int = 30,
) -> None:
    """Reconcile migrated activities with the selected arrival/departure window."""
    if not itinerary.days:
        return

    def selected(section: TransportSectionV3) -> Optional[TransportOptionV3]:
        return next(
            (option for option in section.options if option.option_id == section.selected_option_id),
            None,
        )

    first_day = itinerary.days[0]
    last_day = itinerary.days[-1]
    outbound_option = selected(outbound)
    return_option = selected(returning)
    lower = 0
    upper = 24 * 60 - 1
    if (
        outbound_option
        and outbound_option.arrival_time
        and outbound_option.arrival_time.local_iso
        and first_day.date == outbound_option.arrival_time.local_iso.date()
    ):
        arrival = outbound_option.arrival_time.local_iso
        lower = arrival.hour * 60 + arrival.minute + transfer_minutes
    if (
        return_option
        and return_option.departure_time
        and return_option.departure_time.local_iso
        and last_day.date == return_option.departure_time.local_iso.date()
    ):
        departure = return_option.departure_time.local_iso
        upper = departure.hour * 60 + departure.minute - transfer_minutes

    candidate_ids = {candidate.activity_id for candidate in candidates}

    def normalize_day(day_item: TripDayV3, *, day_lower: int, day_upper: int) -> None:
        ordered = sorted(
            day_item.activities,
            key=lambda item: (_clock_sort_value(item.start_at), item.activity_id),
        )
        # 先为已有的正餐留出时间。只移出可调整的游览活动；餐馆身份、
        # 用餐时长与交通边界均保持不变，真实路线仍由后续核验计算。
        removed_ids: set[str] = set()
        while True:
            cursor = max(0, day_lower)
            preceding: List[TripActivityV3] = []
            blocker = None
            for activity in ordered:
                if activity.activity_id in removed_ids:
                    continue
                start = _clock_sort_value(activity.start_at)
                end = _clock_sort_value(activity.end_at)
                duration = activity.duration_minutes or (end - start if end > start and end < 24 * 60 else 60)
                earliest_start = max(start if start < 24 * 60 else day_lower, day_lower)
                normalized_start = max(earliest_start, cursor)
                if (
                    activity.meal_type
                    and earliest_start + duration <= day_upper
                    and (normalized_start + duration > day_upper or normalized_start > earliest_start + 90)
                ):
                    blocker = next((item for item in reversed(preceding)
                                    if not item.fixed_time and not item.meal_type), None)
                    if blocker is not None:
                        break
                if normalized_start + duration <= day_upper:
                    cursor = normalized_start + duration
                    preceding.append(activity)
            if blocker is None:
                break
            removed_ids.add(blocker.activity_id)

        cursor = max(0, day_lower)
        kept: List[TripActivityV3] = []
        for activity in ordered:
            start = _clock_sort_value(activity.start_at)
            end = _clock_sort_value(activity.end_at)
            duration = activity.duration_minutes or (end - start if end > start and end < 24 * 60 else 60)
            normalized_start = max(start if start < 24 * 60 else cursor, cursor)
            normalized_end = normalized_start + max(1, duration)
            if activity.activity_id in removed_ids or normalized_start >= day_upper or normalized_end > day_upper:
                if activity.activity_id not in candidate_ids:
                    candidates.append(activity.model_copy(update={
                        "day": None,
                        "order": None,
                        "start_at": None,
                        "end_at": None,
                        "fixed_time": False,
                        "route_to_next": None,
                    }))
                    candidate_ids.add(activity.activity_id)
                migration_issues.append(
                    ValidationIssueV3(
                        code="TRANSPORT_WINDOW_ACTIVITY_MOVED_TO_CANDIDATE",
                        message=f"{activity.title} 无法放入所选交通时间窗口，已移入候选池",
                        severity="warning",
                        target_id=activity.activity_id,
                    )
                )
                continue
            activity.start_at = _clock_text(normalized_start)
            activity.end_at = _clock_text(normalized_end)
            activity.duration_minutes = max(1, duration)
            activity.order = len(kept) + 1
            kept.append(activity)
            cursor = normalized_end
        day_item.activities = kept

    if first_day is last_day:
        normalize_day(first_day, day_lower=lower, day_upper=upper)
    else:
        normalize_day(first_day, day_lower=lower, day_upper=24 * 60 - 1)
        normalize_day(last_day, day_lower=0, day_upper=upper)


def _append_trip_anchors(
    itinerary: ItineraryV3,
    *,
    plan_id: str,
    planning_lodging: Optional[TripPlaceV3],
    arrival_hub: Optional[TripPlaceV3],
    departure_hub: Optional[TripPlaceV3],
) -> List[str]:
    """Add only verifiable city-side anchors; route geometry remains server-provided."""
    mapped_anchor_ids: List[str] = []
    days = itinerary.days
    has_lodging = len(days) > 1 and _is_mappable_anchor_place(planning_lodging)

    def add_anchor(day: TripDayV3, kind: str, place: TripPlaceV3, label: str) -> None:
        anchor_id = _stable_id("anchor", plan_id, kind, day.day, place.poi_id)
        day.anchors.append(
            TripAnchorV3(
                anchor_id=anchor_id,
                kind=kind,
                day=day.day,
                place=place.model_copy(deep=True),
                display_label=label,
            )
        )
        mapped_anchor_ids.append(anchor_id)

    for index, day in enumerate(days):
        is_first_day = index == 0
        is_last_day = index == len(days) - 1
        if not day.activities:
            # A transport-only boundary day can still have a verified transfer.
            # Do not create a hotel round trip on an otherwise empty middle day.
            has_empty_day_transfer = has_lodging and (
                (is_first_day and arrival_hub is not None)
                or (is_last_day and departure_hub is not None)
            )
            if not has_empty_day_transfer:
                continue

        if is_first_day and arrival_hub:
            add_anchor(day, "arrival_hub", arrival_hub, f"从{arrival_hub.name}抵达")
        elif has_lodging and planning_lodging:
            add_anchor(day, "lodging_departure", planning_lodging, "从规划住宿点出发")

        if is_last_day:
            if departure_hub:
                add_anchor(day, "departure_hub", departure_hub, f"前往{departure_hub.name}离开")
        elif has_lodging and planning_lodging:
            add_anchor(day, "lodging_return", planning_lodging, "返回规划住宿点")

    return mapped_anchor_ids


def adapt_v2_to_v3(
    value: Union[TravelPlanDocumentV2, Dict[str, Any]],
) -> TravelPlanDocumentV3:
    """Read a legacy V2 snapshot and return one validated, deterministic V3 snapshot."""
    document = value if isinstance(value, TravelPlanDocumentV2) else TravelPlanDocumentV2.model_validate(value)
    date_mode, start_date, end_date, days = _parse_date_range(document.intent)
    generated_at = _parse_aware_datetime(document.generated_at) or datetime.combine(
        start_date or date.today(),
        time.min,
        ZoneInfo("Asia/Shanghai"),
    )
    sources, source_lookup = _build_sources(document)
    destination_timezone = document.destination_overview.timezone or (
        "Asia/Shanghai" if document.outbound_transport.scope == "domestic" else "Etc/UTC"
    )
    destination_currency = _currency_code(
        document.destination_overview.currency,
        "CNY" if document.outbound_transport.scope == "domestic" else "XXX",
    )
    migration_issues: List[ValidationIssueV3] = []
    raw_traveler_counts = {
        "adults": document.intent.adult_count,
        "children": document.intent.child_count,
        "seniors": document.intent.senior_count,
    }
    if any(value is not None for value in raw_traveler_counts.values()):
        normalized_counts = {
            key: max(int(value or 0), 0)
            for key, value in raw_traveler_counts.items()
        }
        if sum(normalized_counts.values()) < 1:
            normalized_counts = {"adults": max(int(document.intent.people_count or 1), 1), "children": 0, "seniors": 0}
            migration_issues.append(
                ValidationIssueV3(
                    code="TRAVELER_COUNT_INVALID",
                    message="成人、儿童和老人拆分未包含有效人数，已按历史总人数暂作成人计",
                    severity="warning",
                )
            )
        travelers = TravelerCountV3(**normalized_counts)
        people_count = travelers.total
        if document.intent.people_count not in (None, people_count):
            migration_issues.append(
                ValidationIssueV3(
                    code="TRAVELER_COUNT_TOTAL_MISMATCH",
                    message="三类出行人数与历史总人数不一致，已以成人、儿童、老人拆分为准",
                    severity="warning",
                )
            )
    else:
        people_count = max(int(document.intent.people_count or 1), 1)
        travelers = TravelerCountV3(adults=people_count)
        migration_issues.append(
            ValidationIssueV3(
                code="LEGACY_TRAVELER_BREAKDOWN_MISSING",
                message="V2 未保存成人、儿童和老人拆分，迁移后暂按成人计并等待用户确认",
                severity="warning",
            )
        )
    derived_budget_total = (
        float(document.intent.budget_per_person) * people_count
        if document.intent.budget_total is None and document.intent.budget_per_person is not None
        else None
    )
    raw_budget = document.budget if isinstance(document.budget, dict) else {}
    budget_total = _money(
        document.intent.budget_total if document.intent.budget_total is not None else (derived_budget_total or 0),
        _currency_code(raw_budget.get("currency"), destination_currency),
    ) or MoneyV3(amount=0, currency=_currency_code(raw_budget.get("currency"), destination_currency))
    if document.intent.budget_total is None and document.intent.budget_per_person is None:
        migration_issues.append(
            ValidationIssueV3(
                code="LEGACY_BUDGET_MISSING",
                message="V2 未保存明确总预算，迁移后预算待用户确认",
                severity="warning",
            )
        )

    notes = _map_existing_notes(document, generated_at)
    itinerary, candidates = _map_itinerary(
        document,
        start_date=start_date,
        days=days,
        destination_timezone=destination_timezone,
        lookup=source_lookup,
        generated_at=generated_at,
        notes=notes,
        migration_issues=migration_issues,
        destination_currency=destination_currency,
        include_date_sensitive=date_mode == "fixed",
    )
    if len(candidates) < 7:
        migration_issues.append(
            ValidationIssueV3(
                code="CANDIDATE_POOL_INSUFFICIENT",
                message=f"当前仅有 {len(candidates)} 个可用候选地点，少于产品要求的 7 个，建议补充地图核验后再排程",
                severity="warning",
            )
        )
    for day_item in itinerary.days:
        attraction_count = sum(activity.kind == "attraction" for activity in day_item.activities)
        if attraction_count < 4:
            migration_issues.append(
                ValidationIssueV3(
                    code="DAILY_ATTRACTION_COVERAGE_INSUFFICIENT",
                    message=f"第 {day_item.day} 天仅有 {attraction_count} 个已核验游览活动，少于默认 4 个",
                    severity="warning",
                    target_id=str(day_item.day),
                )
            )
    destination_ref_ids = _map_refs(document.destination_overview.source_reference_ids, source_lookup)
    cover = document.destination_overview.cover_image
    cover_image = None
    if cover:
        cover_source_ref = _map_ref(cover.source_reference_id, source_lookup)
        cover_image = build_unsplash_cover_asset(
            image_id=_stable_id("img", document.plan_id, "cover", cover.url),
            url=cover.url,
            alt=cover.alt,
            photographer_name=cover.photographer_name,
            photographer_url=cover.photographer_url,
            unsplash_url=cover.unsplash_url,
            download_location=cover.download_location,
            source_ref=cover_source_ref,
            checked_at=generated_at,
        )

    lodging_options: List[LodgingOptionV3] = []
    for legacy in document.hotel_recommendations.recommendations:
        source_refs = _map_refs(
            [legacy.source_reference_id, *legacy.source_reference_ids],
            source_lookup,
        )
        status = _source_status(legacy.data_type)
        lodging_options.append(
            LodgingOptionV3(
                lodging_id=legacy.hotel_id,
                name=legacy.name,
                area=legacy.area,
                place=_map_place(getattr(legacy, "place", None), "hotel", source_lookup),
                nightly_price=(
                    _money(legacy.nightly_price, legacy.currency, source_status=status)
                    if date_mode == "fixed"
                    else None
                ),
                total_price=(
                    _money(legacy.total_price, legacy.currency, source_status=status)
                    if date_mode == "fixed"
                    else None
                ),
                rating=legacy.rating,
                reasons=list(legacy.reasons),
                booking_url=legacy.booking_url if date_mode == "fixed" else None,
                source_status=status,
                source_refs=source_refs,
            )
        )

    planning_lodging = lodging_options[0] if lodging_options else None
    canonical_origin = canonical_destination(document.intent.origin or "")
    origin_meta = INTERNATIONAL_DESTINATION_META.get(canonical_origin or "", {})
    origin_timezone = str(origin_meta.get("timezone") or "Asia/Shanghai")
    outbound_transport = (
        _map_transport_section(
            document.outbound_transport,
            lookup=source_lookup,
            departure_timezone=origin_timezone,
            arrival_timezone=destination_timezone,
        )
        if date_mode == "fixed"
        else TransportSectionV3(
            direction="outbound",
            scope=document.outbound_transport.scope,
            status="unavailable",
            status_reason="日期暂未确定，未查询具体往程班次、票价与余票。",
        )
    )
    return_transport = (
        _map_transport_section(
            document.return_transport,
            lookup=source_lookup,
            departure_timezone=destination_timezone,
            arrival_timezone=origin_timezone,
        )
        if date_mode == "fixed"
        else TransportSectionV3(
            direction="return",
            scope=document.return_transport.scope,
            status="unavailable",
            status_reason="日期暂未确定，未查询具体返程班次、票价与余票。",
        )
    )
    _apply_v3_transport_windows(
        itinerary,
        candidates,
        outbound=outbound_transport,
        returning=return_transport,
        migration_issues=migration_issues,
    )
    del candidates[15:]
    anchor_ids = _append_trip_anchors(
        itinerary,
        plan_id=document.plan_id,
        planning_lodging=(
            planning_lodging.place
            if planning_lodging and _is_mappable_anchor_place(planning_lodging.place)
            else None
        ),
        arrival_hub=_selected_transport_hub(outbound_transport, field="arrival_hub"),
        departure_hub=_selected_transport_hub(return_transport, field="departure_hub"),
    )
    formal_location_ids = [
        activity.activity_id
        for day_item in itinerary.days
        for activity in day_item.activities
        if activity.place is not None
    ] + anchor_ids

    section_statuses = [
        document.destination_overview.status,
        document.outbound_transport.status,
        document.hotel_recommendations.status,
        document.itinerary.status,
        document.return_transport.status,
        document.friendly_reminders.status,
        document.map_guidance.status,
    ]
    degraded = bool(migration_issues or any(status != "ready" for status in section_statuses))
    legacy_validation_issues = document.validation.get("issues", []) if isinstance(document.validation, dict) else []
    for index, issue in enumerate(legacy_validation_issues):
        if not isinstance(issue, dict):
            continue
        raw_severity = str(issue.get("severity") or "warning")
        severity = raw_severity if raw_severity in {"info", "warning", "error"} else "warning"
        migration_issues.append(
            ValidationIssueV3(
                code=str(issue.get("code") or f"LEGACY_VALIDATION_{index + 1}"),
                message=str(issue.get("message") or "V2 校验提示待确认"),
                severity=severity,
                target_id=issue.get("target_id"),
            )
        )
    lodging_plan = LodgingPlanV3(
        status={
            "ready": "ready",
            "needs_confirmation": "user_confirmation_required",
            "needs_date": "user_confirmation_required",
            "unavailable": "unavailable",
        }.get(document.hotel_recommendations.status, "degraded"),
        status_reason=document.hotel_recommendations.status_reason,
        planning_lodging_id=planning_lodging.lodging_id if planning_lodging else None,
        options=lodging_options,
    )
    budget_summary = _map_budget(document, budget_total)
    migration_issues.extend(_formal_completeness_issues(
        itinerary=itinerary,
        candidates=candidates,
        budget=budget_summary,
        outbound_transport=outbound_transport,
        return_transport=return_transport,
        lodging_plan=lodging_plan,
        destination_currency=destination_currency,
    ))
    validation_valid = not any(issue.severity == "error" for issue in migration_issues)
    degraded = degraded or bool(migration_issues)
    if not validation_valid:
        raise FormalPlanValidationError(migration_issues)

    day_routes = _map_day_routes(document)
    formal_location_id_set = set(formal_location_ids)
    chronological_pairs_by_day = {
        day_item.day: {
            (left.activity_id, right.activity_id)
            for left, right in zip(day_item.activities, day_item.activities[1:])
        }
        for day_item in itinerary.days
    }
    for day_route in day_routes:
        day_route.legs = [
            leg
            for leg in day_route.legs
            if leg.from_id in formal_location_id_set and leg.to_id in formal_location_id_set
            and (leg.from_id, leg.to_id) in chronological_pairs_by_day.get(day_route.day, set())
        ]
    activities_by_id = {
        activity.activity_id: activity
        for day_item in itinerary.days
        for activity in day_item.activities
    }
    for day_route in day_routes:
        for leg in day_route.legs:
            activity = activities_by_id.get(leg.from_id)
            if activity is not None:
                activity.route_to_next = leg.model_copy(deep=True)

    adapted = TravelPlanDocumentV3(
        plan_id=document.plan_id,
        revision=document.version,
        status="degraded" if degraded else "formal",
        title=document.title,
        generated_at=generated_at,
        locale=document.locale,
        intent=TripIntentV3(
            origin=document.intent.origin or "出发地待确认",
            destination=document.intent.destination or document.destination_overview.name_zh,
            date_mode=date_mode,
            start_date=start_date,
            end_date=end_date,
            days=days,
            travelers=travelers,
            budget=budget_total,
            preferences=[
                value
                for value in [document.intent.travel_style, *document.intent.interests]
                if value
            ],
            dietary_preferences=list(document.intent.dietary_preferences),
            pace=document.intent.pace or "balanced",
        ),
        destination_overview=DestinationOverviewV3(
            name_zh=document.destination_overview.name_zh,
            name_en=document.destination_overview.name_en,
            country_code=document.destination_overview.country_code,
            country_name=document.destination_overview.country_name,
            timezone=destination_timezone,
            currency=destination_currency,
            summary=document.destination_overview.area_overview,
            themes=list(document.destination_overview.themes),
            cover_image=cover_image,
            evidence_refs=destination_ref_ids,
        ),
        outbound_transport=outbound_transport,
        lodging_plan=lodging_plan,
        itinerary=itinerary,
        candidate_pool=candidates,
        action_items=_map_actions(
            document,
            lookup=source_lookup,
            include_date_sensitive=date_mode == "fixed",
        ),
        notes=notes,
        budget=budget_summary,
        return_transport=return_transport,
        map_guidance=MapGuidanceV3(
            status={
                "ready": "ready",
                "needs_confirmation": "user_confirmation_required",
                "needs_date": "user_confirmation_required",
                "unavailable": "unavailable",
            }.get(document.map_guidance.status, "degraded"),
            status_reason=document.map_guidance.status_reason,
            formal_location_ids=formal_location_ids,
            day_routes=day_routes,
        ),
        sources=sources,
        delivery=DeliveryV3(
            markdown_filename=safe_delivery_filename(
                document.delivery.markdown_filename,
                fallback_title=document.title,
                revision=document.version,
                suffix=".md",
            ),
            pdf_filename=safe_delivery_filename(
                document.delivery.markdown_filename,
                fallback_title=document.title,
                revision=document.version,
                suffix=".pdf",
            ),
        ),
        validation=ValidationSummaryV3(
            valid=validation_valid,
            degraded=degraded,
            issues=migration_issues,
        ),
    )
    adapted_payload = adapted.model_dump(mode="json")
    from services.formal_consistency_service import recalculate_formal_budget
    recalculate_formal_budget(adapted_payload)
    refresh_candidate_schedule_options(adapted_payload)
    return TravelPlanDocumentV3.model_validate(adapted_payload)


def calculate_document_checksum(document: TravelPlanDocumentV3) -> str:
    payload = document.model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def export_travel_plan_v3_markdown(document: TravelPlanDocumentV3) -> str:
    """Render the validated V3 snapshot without falling back to legacy V2 fields."""

    def money_text(value: Optional[MoneyV3]) -> str:
        if value is None:
            return "费用待确认"
        original = f"{value.currency} {value.amount:.2f}"
        if value.currency == "CNY":
            return original
        if value.cny_reference_amount is None:
            return f"{original}（人民币参考待确认）"
        as_of = (
            f"，汇率参考日期 {value.exchange_rate_as_of.isoformat()}"
            if value.exchange_rate_as_of
            else ""
        )
        return f"{original}（约 CNY {value.cny_reference_amount:.2f}{as_of}）"

    def transport_time_text(
        value: Optional[TransportTimeV3],
        *,
        scope: str,
        label: str,
    ) -> str:
        if value is None:
            return ""
        has_complete_context = bool(
            value.utc is not None
            and value.local_iso is not None
            and value.beijing_iso is not None
            and value.timezone
        )
        if not has_complete_context:
            return f"{label}：来源原始时刻 {value.display_text}（时区待确认）"
        local_time = value.local_iso.strftime("%Y-%m-%d %H:%M")
        beijing_time = value.beijing_iso.strftime("%Y-%m-%d %H:%M")
        if scope == "domestic":
            detail = f"北京时间 {beijing_time}"
        else:
            detail = f"当地时间 {local_time}（{value.timezone}） / 北京时间 {beijing_time}"
        if label == "到达" and value.day_offset == 1:
            detail += "（次日抵达）"
        elif label == "到达" and value.day_offset > 1:
            detail += f"（{value.day_offset} 日后抵达）"
        return f"{label}：{detail}"

    def transport_lines(section: TransportSectionV3, label: str) -> List[str]:
        lines = [f"### {label}"]
        if not section.options:
            lines.append(f"- {section.status_reason or '暂无可核验的具体交通方案'}")
            return lines
        availability_labels = {
            "available": "有余量",
            "limited": "余量紧张",
            "unknown": "余量待确认",
        }
        for option in section.options:
            route = " → ".join(
                value for value in [option.departure_place, option.arrival_place] if value
            ) or "路线待确认"
            time_parts = [
                transport_time_text(option.departure_time, scope=section.scope, label="出发"),
                transport_time_text(option.arrival_time, scope=section.scope, label="到达"),
            ]
            service = f" {option.service_number}" if option.service_number else ""
            detail = "；".join(
                item
                for item in [
                    route,
                    "；".join(item for item in time_parts if item),
                    money_text(option.price),
                    availability_labels[option.availability],
                ]
                if item
            )
            mode_label = {"train": "火车", "flight": "航班", "intercity_bus": "长途巴士"}.get(option.mode, "交通方案")
            lines.append(f"- {mode_label}{service}：{detail}")
        return lines

    intent = document.intent
    traveler_total = intent.travelers.total
    lines = [
        f"# {document.title}",
        "",
        f"> 方案：{document.plan_id} · 版本：v{document.revision} · Schema：3.0",
        "",
        "## 1. 目的地与旅行条件",
        "",
        f"- 行程：{intent.origin} → {intent.destination}",
        f"- 人数：{traveler_total} 人（成人 {intent.travelers.adults}、儿童 {intent.travelers.children}、老人 {intent.travelers.seniors}）",
        f"- 预算：{money_text(intent.budget)}",
    ]
    if intent.date_mode == "flexible":
        lines.extend([
            f"- 日期：暂未确定，共 {intent.days} 天",
            "",
            "> 当前为日期待定参考方案：按 Day 1 至 Day N 展示；日期确定后再补充具体班次、票价、余票、天气和时效性预约信息。",
        ])
    else:
        lines.append(f"- 日期：{intent.start_date} 至 {intent.end_date}，共 {intent.days} 天")
    if document.destination_overview.summary:
        lines.extend(["", document.destination_overview.summary])

    lines.extend(["", "## 2. 去返程交通", ""])
    lines.extend(transport_lines(document.outbound_transport, "去程"))
    lines.append("")
    lines.extend(transport_lines(document.return_transport, "返程"))

    lines.extend(["", "## 3. 住宿建议", ""])
    if document.lodging_plan.options:
        for index, lodging in enumerate(document.lodging_plan.options, start=1):
            details = [lodging.area or "区域待确认"]
            if lodging.rating is not None:
                details.append(f"评分 {lodging.rating:.1f}")
            if lodging.nightly_price is not None:
                details.append(f"参考每晚 {money_text(lodging.nightly_price)}")
            lines.append(f"{index}. {lodging.name}（{'；'.join(details)}）")
            for reason in lodging.reasons:
                lines.append(f"   - {reason}")
    else:
        lines.append(f"- {document.lodging_plan.status_reason or '暂无通过地点核验的住宿候选，不编造酒店名称。'}")

    lines.extend(["", "## 4. 分日行程", ""])
    meal_labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
    for day in document.itinerary.days:
        date_suffix = f" · {day.date}" if day.date else ""
        theme_suffix = f"｜{day.theme}" if day.theme else ""
        lines.append(f"### Day {day.day}{date_suffix}{theme_suffix}")
        if not day.activities:
            lines.append("- 当天暂无通过地点核验的活动，待补充。")
        for activity in day.activities:
            time_range = ""
            if activity.start_at or activity.end_at:
                time_range = f"（{activity.start_at or '待定'}–{activity.end_at or '待定'}）"
            place = f" · {activity.place.name}" if activity.place else ""
            meal_prefix = f"{meal_labels[activity.meal_type]}：" if activity.meal_type else ""
            lines.append(f"- {meal_prefix}{activity.title}{time_range}{place}")
        lines.append("")

    lines.extend([
        "## 5. 预算与提醒",
        "",
        f"- 总预算：{money_text(document.budget.total_budget)}",
        f"- 当前可估算：{money_text(document.budget.estimated_total)}",
        f"- 待确认费用项：{document.budget.unknown_cost_count}",
    ])
    for category in document.budget.categories:
        labels = {
            "transport": "交通",
            "lodging": "住宿",
            "food": "餐饮",
            "activities": "活动与门票",
            "shopping": "购物",
            "other": "其他",
        }
        lines.append(f"- {labels[category.category]}：{money_text(category.amount)}")
    status_labels = {
        "standard_price": "按成人标准价计算",
        "official_discount_verified": "已按官方优惠价计算",
        "adult_price_assumed": "优惠待确认，暂按成人价计算",
        "not_applicable": "暂无可按人数拆分的费用",
    }
    traveler_labels = {"adult": "成人", "child": "儿童", "senior": "老人"}
    for traveler_cost in document.budget.traveler_costs:
        if traveler_cost.count == 0:
            continue
        amount_text = money_text(traveler_cost.estimated_total)
        lines.append(
            f"- {traveler_labels[traveler_cost.traveler_type]} {traveler_cost.count} 人："
            f"{amount_text}（{status_labels[traveler_cost.pricing_status]}）"
        )
    if document.budget.over_budget and document.budget.overrun_amount:
        lines.append(f"- 预算偏差：超出 {money_text(document.budget.overrun_amount)}")
    for warning in document.budget.warnings:
        lines.append(f"- {warning}")
    for action in document.action_items:
        lines.append(f"- [ ] {action.text}")

    lines.extend([
        "",
        "## 6. 地图与路线",
        "",
        f"- {document.map_guidance.status_reason or ('已核验正式地点与路线' if document.map_guidance.status == 'ready' else '地图与路线仍需确认')}",
        "",
        "## 7. 下载",
        "",
        f"- Markdown：{document.delivery.markdown_filename}",
        f"- PDF：{document.delivery.pdf_filename}",
        "",
        "## 8. 参考来源",
        "",
    ])
    if not document.sources:
        lines.append("- 暂无外部参考来源")
    for source in document.sources:
        link = f"（{source.url}）" if source.url else ""
        lines.append(f"- {source.title} · {source.source_name}{link}")
    return "\n".join(lines).strip()


def export_travel_plan_v3_plain_markdown(document: TravelPlanDocumentV3) -> str:
    """Render one formal V3 revision as a self-contained, text-only Markdown file.

    The online plan renderer above intentionally keeps sources for in-product reading.
    A downloaded Markdown file has a different contract: it contains only the
    accepted itinerary content and must not expose images, sources, drafts,
    candidates, validation details, or application-only task states.
    """

    def money_text(value: Optional[MoneyV3]) -> str:
        if value is None:
            return "费用待确认"
        original = f"{value.currency} {value.amount:.2f}"
        if value.currency == "CNY":
            return original
        if value.cny_reference_amount is None:
            return f"{original}（人民币参考待确认）"
        as_of = (
            f"，汇率参考日期 {value.exchange_rate_as_of.isoformat()}"
            if value.exchange_rate_as_of
            else ""
        )
        return f"{original}（约 CNY {value.cny_reference_amount:.2f}{as_of}）"

    def transport_time_text(value: Optional[TransportTimeV3], *, scope: str, label: str) -> str:
        if value is None:
            return ""
        if not all((value.utc, value.local_iso, value.beijing_iso, value.timezone)):
            return f"{label}：来源原始时刻 {value.display_text}（时区待确认）"
        local_time = value.local_iso.strftime("%Y-%m-%d %H:%M")
        beijing_time = value.beijing_iso.strftime("%Y-%m-%d %H:%M")
        if scope == "domestic":
            detail = f"北京时间 {beijing_time}"
        else:
            detail = f"当地时间 {local_time}（{value.timezone}） / 北京时间 {beijing_time}"
        if label == "到达" and value.day_offset == 1:
            detail += "（次日抵达）"
        elif label == "到达" and value.day_offset > 1:
            detail += f"（{value.day_offset} 日后抵达）"
        return f"{label}：{detail}"

    availability_labels = {
        "available": "有余量",
        "limited": "余量紧张",
        "unknown": "余量待确认",
    }
    meal_labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
    budget_labels = {
        "transport": "交通",
        "lodging": "住宿",
        "food": "餐饮",
        "activities": "活动与门票",
        "shopping": "购物",
        "other": "其他",
    }
    traveler_labels = {"adult": "成人", "child": "儿童", "senior": "老人"}
    pricing_labels = {
        "standard_price": "按成人标准价计算",
        "official_discount_verified": "已按官方优惠价计算",
        "adult_price_assumed": "优惠待确认，暂按成人价计算",
        "not_applicable": "暂无可按人数拆分的费用",
    }
    note_by_id = {note.note_id: note for note in document.notes}

    def note_text(note_id: Optional[str]) -> Optional[str]:
        note = note_by_id.get(note_id or "")
        return note.content if note else None

    def append_transport(section: TransportSectionV3, label: str) -> None:
        lines.extend([f"### {label}", ""])
        if not section.options:
            lines.append(f"- {section.status_reason or '暂无可核验的具体交通方案'}")
            return
        for option in section.options:
            selection = "规划采用" if option.option_id == section.selected_option_id else "备选"
            route = " → ".join(
                value for value in (option.departure_place, option.arrival_place) if value
            ) or "路线待确认"
            time_parts = [
                transport_time_text(option.departure_time, scope=section.scope, label="出发"),
                transport_time_text(option.arrival_time, scope=section.scope, label="到达"),
            ]
            details = [route, *[item for item in time_parts if item], money_text(option.price)]
            if option.availability:
                details.append(availability_labels[option.availability])
            service = f" {option.service_number}" if option.service_number else ""
            mode_label = {"train": "火车", "flight": "航班", "intercity_bus": "长途巴士"}.get(option.mode, "交通方案")
            lines.append(f"- {selection}：{mode_label}{service}；{'；'.join(details)}")

    lines = [
        f"# {document.title}",
        "",
        "## 旅行摘要",
        "",
        f"- 行程：{document.intent.origin} → {document.intent.destination}",
        f"- 人数：{document.intent.travelers.total} 人（成人 {document.intent.travelers.adults}、儿童 {document.intent.travelers.children}、老人 {document.intent.travelers.seniors}）",
        f"- 预算：{money_text(document.intent.budget)}",
        f"- 目的地时区：{document.destination_overview.timezone}",
    ]
    if document.intent.date_mode == "flexible":
        lines.append(f"- 日期：暂未确定，共 {document.intent.days} 天")
    else:
        lines.append(f"- 日期：{document.intent.start_date} 至 {document.intent.end_date}，共 {document.intent.days} 天")
    if document.destination_overview.summary:
        lines.extend(["", document.destination_overview.summary])
    trip_notes = [note.content for note in document.notes if note.scope == "trip"]
    if trip_notes:
        lines.extend(["", "### 整体备注", ""])
        lines.extend(f"- {content}" for content in trip_notes)

    lines.extend(["", "## 去返程交通", ""])
    append_transport(document.outbound_transport, "去程")
    lines.append("")
    append_transport(document.return_transport, "返程")

    lines.extend(["", "## 住宿建议", ""])
    if document.lodging_plan.options:
        for lodging in document.lodging_plan.options:
            selection = "规划住宿点" if lodging.lodging_id == document.lodging_plan.planning_lodging_id else "备选"
            details = [lodging.area or "区域待确认"]
            if lodging.rating is not None:
                details.append(f"评分 {lodging.rating:.1f}")
            if lodging.nightly_price is not None:
                details.append(f"参考每晚 {money_text(lodging.nightly_price)}")
            if lodging.total_price is not None:
                details.append(f"参考总价 {money_text(lodging.total_price)}")
            lines.append(f"- {selection}：{lodging.name}（{'；'.join(details)}）")
            lines.extend(f"  - {reason}" for reason in lodging.reasons)
    else:
        lines.append(f"- {document.lodging_plan.status_reason or '暂无通过地点核验的住宿候选。'}")

    lines.extend(["", "## 分日行程", ""])
    reservation_lines: List[str] = []
    for day in document.itinerary.days:
        day_title = f"### 第 {day.day} 天"
        if day.date:
            day_title += f"｜{day.date}"
        if day.theme:
            day_title += f"｜{day.theme}"
        lines.extend([day_title, ""])
        if note := note_text(day.note_id):
            lines.append(f"- 当日备注：{note}")
        if not day.activities:
            lines.append("- 当天暂无已排期活动。")
        for activity in day.activities:
            time_range = ""
            if activity.start_at or activity.end_at:
                time_range = f"（{activity.start_at or '待定'}–{activity.end_at or '待定'}）"
            meal_prefix = f"{meal_labels[activity.meal_type]}：" if activity.meal_type else ""
            details = []
            if activity.duration_minutes is not None:
                details.append(f"停留约 {activity.duration_minutes} 分钟")
            if activity.estimated_cost is not None:
                details.append(f"参考费用 {money_text(activity.estimated_cost)}")
            if activity.fixed_time:
                details.append("固定时间")
            detail_suffix = f"（{'；'.join(details)}）" if details else ""
            lines.append(f"- {meal_prefix}{activity.title}{time_range}{detail_suffix}")
            if note := note_text(activity.note_id):
                lines.append(f"  - 活动备注：{note}")
            if activity.reservation and activity.reservation.guidance:
                reservation_lines.append(f"{activity.title}：{activity.reservation.guidance}")
        lines.append("")

    lines.extend(["## 预算", ""])
    lines.extend([
        f"- 总预算：{money_text(document.budget.total_budget)}",
        f"- 当前可估算：{money_text(document.budget.estimated_total)}",
        f"- 待确认费用项：{document.budget.unknown_cost_count}",
    ])
    for category in document.budget.categories:
        lines.append(f"- {budget_labels[category.category]}：{money_text(category.amount)}")
    for traveler_cost in document.budget.traveler_costs:
        if traveler_cost.count == 0:
            continue
        lines.append(
            f"- {traveler_labels[traveler_cost.traveler_type]} {traveler_cost.count} 人："
            f"{money_text(traveler_cost.estimated_total)}（{pricing_labels[traveler_cost.pricing_status]}）"
        )
    if document.budget.over_budget and document.budget.overrun_amount:
        lines.append(f"- 预算偏差：超出 {money_text(document.budget.overrun_amount)}")
    lines.extend(f"- {warning}" for warning in document.budget.warnings)

    checklist_items = [item.text for item in document.action_items if item.kind == "checklist"]
    if checklist_items:
        lines.extend(["", "## 准备清单", ""])
        lines.extend(f"- {text}" for text in checklist_items)

    reminder_items = [item.text for item in document.action_items if item.kind == "reservation_reminder"]
    if reminder_items or reservation_lines:
        lines.extend(["", "## 预约提醒", ""])
        lines.extend(f"- {text}" for text in [*reminder_items, *reservation_lines])

    pending_items = [
        f"去程交通：{document.outbound_transport.status_reason}"
        if document.outbound_transport.status_reason else None,
        f"住宿：{document.lodging_plan.status_reason}"
        if document.lodging_plan.status_reason else None,
        f"返程交通：{document.return_transport.status_reason}"
        if document.return_transport.status_reason else None,
    ]
    pending_items = [item for item in pending_items if item]
    if pending_items:
        lines.extend(["", "## 待确认事项", ""])
        lines.extend(f"- {item}" for item in pending_items)

    return "\n".join(lines).strip() + "\n"


def travel_plan_v3_json_schema() -> Dict[str, Any]:
    return TravelPlanDocumentV3.model_json_schema()


def planning_event_json_schema() -> Dict[str, Any]:
    return PlanningEventEnvelope.model_json_schema()
