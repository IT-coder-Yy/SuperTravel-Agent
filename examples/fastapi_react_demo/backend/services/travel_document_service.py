from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

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
    TransportSectionV3,
    TransportTimeV3,
    TravelerCountV3,
    TravelPlanDocumentV3,
    TripActionItemV3,
    TripActivityV3,
    TripDayV3,
    TripIntentV3,
    TripNoteV3,
    TripPlaceV3,
    TripSourceV3,
    ValidationIssueV3,
    ValidationSummaryV3,
)
from backend.services.travel_date_service import resolve_date_range


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
    "交通": "transport",
    "住宿": "hotel",
    "购物": "shopping",
    "休息": "free_time",
    "自由活动": "free_time",
}


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


def _parse_date_range(intent: Any) -> Tuple[date, date, int]:
    return resolve_date_range(
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
    return MoneyV3(
        amount=numeric_amount,
        currency=_currency_code(currency, "CNY"),
        cny_reference_amount=cny_value,
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
    parsed = _parse_aware_datetime(display, timezone_name or "Asia/Shanghai")
    if parsed is None and travel_date and timezone_name:
        match = _TIME_PATTERN.match(display)
        if match:
            parsed = datetime.combine(
                travel_date,
                time(hour=int(match.group(1)), minute=int(match.group(2))),
                ZoneInfo(timezone_name),
            )
    beijing = parsed.astimezone(ZoneInfo("Asia/Shanghai")) if parsed else None
    return TransportTimeV3(
        display_text=display,
        utc=parsed.astimezone(timezone.utc) if parsed else None,
        local_iso=parsed,
        timezone=timezone_name if parsed else None,
        beijing_iso=beijing,
    )


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
        for key in (str(index), legacy.title, legacy.url, legacy.source):
            if key:
                lookup[str(key)] = source_id
    return sources, lookup


def _map_ref(value: Any, lookup: Dict[str, str]) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value)
    if text in lookup:
        return lookup[text]
    for key, source_id in lookup.items():
        if key and key in text:
            return source_id
    return None


def _map_refs(values: Iterable[Any], lookup: Dict[str, str]) -> List[str]:
    mapped = [_map_ref(value, lookup) for value in values]
    return list(dict.fromkeys(value for value in mapped if value))


def _map_place(legacy: Any, kind: str, lookup: Dict[str, str]) -> Optional[TripPlaceV3]:
    if legacy is None or legacy.lat is None or legacy.lng is None or not legacy.poi_id:
        return None
    return TripPlaceV3(
        poi_id=legacy.poi_id,
        name=legacy.name,
        category=kind,
        city=legacy.city,
        address=legacy.address,
        opening_hours=str(legacy.opening_hours) if legacy.opening_hours not in (None, "") else None,
        coordinates=CoordinatesV3(
            latitude=legacy.lat,
            longitude=legacy.lng,
            coordinate_system="WGS84",
        ),
        summary=legacy.summary,
        evidence_refs=_map_refs(
            [item.get("source_reference_id") for item in legacy.sources if isinstance(item, dict)],
            lookup,
        ),
    )


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


def _map_transport_section(
    legacy: Any,
    *,
    destination_timezone: str,
    lookup: Dict[str, str],
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
        options.append(
            TransportOptionV3(
                option_id=option.option_id,
                mode=option.mode,
                service_number=service_number,
                departure_place=option.departure_station,
                arrival_place=option.arrival_station,
                departure_time=_transport_time(
                    option.departure_time,
                    travel_date=travel_date,
                    timezone_name="Asia/Shanghai" if legacy.scope == "domestic" else None,
                ) if status == "realtime_verified" else None,
                arrival_time=_transport_time(
                    option.arrival_time,
                    travel_date=travel_date,
                    timezone_name="Asia/Shanghai" if legacy.scope == "domestic" else destination_timezone,
                ) if status == "realtime_verified" else None,
                duration_minutes=option.duration_minutes,
                transfers=option.transfers,
                price=_money(option.price, option.currency, source_status=status),
                availability=availability,
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
    )


def _map_budget(document: TravelPlanDocumentV2, budget_total: MoneyV3) -> BudgetSummaryV3:
    payload = document.budget if isinstance(document.budget, dict) else {}
    estimated = (
        payload.get("estimated_total")
        or payload.get("known_total")
        or payload.get("total_estimated")
    )
    estimated_money = _money(estimated, budget_total.currency)
    categories: List[BudgetCategoryV3] = []
    raw_categories = payload.get("categories")
    if isinstance(raw_categories, dict):
        category_map = {
            "transport": "transport",
            "transportation": "transport",
            "lodging": "lodging",
            "hotel": "lodging",
            "food": "food",
            "dining": "food",
            "activities": "activities",
            "attractions": "activities",
            "shopping": "shopping",
            "other": "other",
        }
        for key, value in raw_categories.items():
            money = _money(value, budget_total.currency)
            if money:
                categories.append(
                    BudgetCategoryV3(category=category_map.get(str(key), "other"), amount=money)
                )
    overrun = _money(payload.get("overrun_amount"), budget_total.currency)
    return BudgetSummaryV3(
        total_budget=budget_total,
        estimated_total=estimated_money,
        categories=categories,
        unknown_cost_count=int(payload.get("unknown_count", 0) or 0),
        over_budget=bool(payload.get("over_budget", False)),
        overrun_amount=overrun,
        warnings=list(payload.get("warnings") or []),
    )


def _map_actions(
    document: TravelPlanDocumentV2,
    *,
    lookup: Dict[str, str],
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
        source_ref = _map_ref(reminder.source_reference_id, lookup)
        actions.append(
            TripActionItemV3(
                action_id=_stable_id("action", document.plan_id, "reminder", index, reminder.content),
                kind="reservation_reminder" if reminder.category == "reservation" else "checklist",
                scope="trip",
                text=reminder.content,
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
    start_date: date,
    days: int,
    destination_timezone: str,
    lookup: Dict[str, str],
    generated_at: datetime,
    notes: List[TripNoteV3],
    migration_issues: List[ValidationIssueV3],
) -> Tuple[ItineraryV3, List[TripActivityV3]]:
    legacy_days = {day.day: day for day in document.itinerary.days}
    mapped_days: List[TripDayV3] = []
    candidates: List[TripActivityV3] = []
    for day_number in range(1, days + 1):
        legacy_day = legacy_days.get(day_number)
        mapped_activities: List[TripActivityV3] = []
        if legacy_day:
            for index, legacy_activity in enumerate(legacy_day.activities, start=1):
                kind = _activity_kind(legacy_activity.activity_type)
                place = _map_place(legacy_activity.place, kind, lookup)
                refs = _map_refs(legacy_activity.evidence_refs, lookup)
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
                            estimated_cost=_money(legacy_activity.estimated_cost),
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
                        start_at=legacy_activity.start_time,
                        end_at=legacy_activity.end_time,
                        duration_minutes=legacy_activity.duration_minutes,
                        place=place,
                        estimated_cost=_money(legacy_activity.estimated_cost),
                        reservation=ReservationInfoV3(
                            required=bool(legacy_activity.reservation),
                            status="needs_confirmation" if legacy_activity.reservation else "not_required",
                        ),
                        note_id=note_id,
                        evidence_refs=refs,
                        route_to_next=None,
                    )
                )
        mapped_days.append(
            TripDayV3(
                day=day_number,
                date=start_date + timedelta(days=day_number - 1),
                timezone=destination_timezone,
                theme=legacy_day.theme if legacy_day else None,
                activities=mapped_activities,
                anchors=[],
            )
        )
    return ItineraryV3(days=mapped_days), candidates[:15]


def _map_day_routes(document: TravelPlanDocumentV2) -> List[DayRouteV3]:
    routes: List[DayRouteV3] = []
    for legacy_day_route in document.map_guidance.day_routes:
        legs: List[RouteLegV3] = []
        for leg in legacy_day_route.legs:
            ready = bool(leg.status == "ready" and leg.provider and leg.geometry)
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
        routes.append(
            DayRouteV3(
                day=legacy_day_route.day,
                status=legacy_day_route.status,
                legs=legs,
            )
        )
    return routes


def adapt_v2_to_v3(
    value: Union[TravelPlanDocumentV2, Dict[str, Any]],
) -> TravelPlanDocumentV3:
    """Read a legacy V2 snapshot and return one validated, deterministic V3 snapshot."""
    document = value if isinstance(value, TravelPlanDocumentV2) else TravelPlanDocumentV2.model_validate(value)
    start_date, end_date, days = _parse_date_range(document.intent)
    generated_at = _parse_aware_datetime(document.generated_at) or datetime.combine(
        start_date,
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
    people_count = max(int(document.intent.people_count or 1), 1)
    budget_total = _money(
        document.intent.budget_total or 0,
        destination_currency,
    ) or MoneyV3(amount=0, currency=destination_currency)
    migration_issues: List[ValidationIssueV3] = []
    migration_issues.append(
        ValidationIssueV3(
            code="LEGACY_TRAVELER_BREAKDOWN_MISSING",
            message="V2 未保存成人、儿童和老人拆分，迁移后暂按成人计并等待用户确认",
            severity="warning",
        )
    )
    if not document.intent.budget_total:
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
    )
    formal_location_ids = [
        activity.activity_id
        for day_item in itinerary.days
        for activity in day_item.activities
        if activity.place is not None
    ]
    destination_ref_ids = _map_refs(document.destination_overview.source_reference_ids, source_lookup)
    cover = document.destination_overview.cover_image
    cover_image = None
    if cover:
        cover_source_ref = _map_ref(cover.source_reference_id, source_lookup)
        cover_image = ImageAssetV3(
            image_id=_stable_id("img", document.plan_id, "cover", cover.url),
            url=cover.url,
            alt=cover.alt,
            display_allowed=True,
            export_allowed=False,
            attribution_required=True,
            attribution_text=cover.photographer_name,
            source_ref=cover_source_ref,
        )

    lodging_options: List[LodgingOptionV3] = []
    for legacy in document.hotel_recommendations.recommendations:
        source_ref = _map_ref(legacy.source_reference_id, source_lookup)
        status = _source_status(legacy.data_type)
        lodging_options.append(
            LodgingOptionV3(
                lodging_id=legacy.hotel_id,
                name=legacy.name,
                area=legacy.area,
                nightly_price=_money(legacy.nightly_price, legacy.currency, source_status=status),
                total_price=_money(legacy.total_price, legacy.currency, source_status=status),
                rating=legacy.rating,
                reasons=list(legacy.reasons),
                booking_url=legacy.booking_url,
                source_status=status,
                source_refs=[source_ref] if source_ref else [],
            )
        )

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
        if severity == "error":
            severity = "warning"
        migration_issues.append(
            ValidationIssueV3(
                code=str(issue.get("code") or f"LEGACY_VALIDATION_{index + 1}"),
                message=str(issue.get("message") or "V2 校验提示待确认"),
                severity=severity,
                target_id=issue.get("target_id"),
            )
        )
    degraded = degraded or bool(migration_issues)

    day_routes = _map_day_routes(document)
    formal_location_id_set = set(formal_location_ids)
    for day_route in day_routes:
        day_route.legs = [
            leg
            for leg in day_route.legs
            if leg.from_id in formal_location_id_set and leg.to_id in formal_location_id_set
        ]

    return TravelPlanDocumentV3(
        plan_id=document.plan_id,
        revision=document.version,
        status="degraded" if degraded else "formal",
        title=document.title,
        generated_at=generated_at,
        locale=document.locale,
        intent=TripIntentV3(
            origin=document.intent.origin or "出发地待确认",
            destination=document.intent.destination or document.destination_overview.name_zh,
            start_date=start_date,
            end_date=end_date,
            days=days,
            travelers=TravelerCountV3(adults=people_count),
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
        outbound_transport=_map_transport_section(
            document.outbound_transport,
            destination_timezone=destination_timezone,
            lookup=source_lookup,
        ),
        lodging_plan=LodgingPlanV3(
            status={
                "ready": "ready",
                "needs_confirmation": "user_confirmation_required",
                "needs_date": "user_confirmation_required",
                "unavailable": "unavailable",
            }.get(document.hotel_recommendations.status, "degraded"),
            status_reason=document.hotel_recommendations.status_reason,
            planning_lodging_id=None,
            options=lodging_options,
        ),
        itinerary=itinerary,
        candidate_pool=candidates,
        action_items=_map_actions(document, lookup=source_lookup),
        notes=notes,
        budget=_map_budget(document, budget_total),
        return_transport=_map_transport_section(
            document.return_transport,
            destination_timezone=destination_timezone,
            lookup=source_lookup,
        ),
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
            markdown_filename=document.delivery.markdown_filename,
            pdf_filename=str(Path(document.delivery.markdown_filename).with_suffix(".pdf")),
        ),
        validation=ValidationSummaryV3(
            valid=True,
            degraded=degraded,
            issues=migration_issues,
        ),
    )


def calculate_document_checksum(document: TravelPlanDocumentV3) -> str:
    payload = document.model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def travel_plan_v3_json_schema() -> Dict[str, Any]:
    return TravelPlanDocumentV3.model_json_schema()


def planning_event_json_schema() -> Dict[str, Any]:
    return PlanningEventEnvelope.model_json_schema()
