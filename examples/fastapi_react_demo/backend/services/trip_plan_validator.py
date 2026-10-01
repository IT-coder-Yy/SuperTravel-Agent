import math
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from schemas.trip_models import TripActivity, TripPlan, TripValidationIssue, TripValidationResult
from services.destination_catalog_service import canonical_destination, classic_places_for_city, is_invalid_poi_name


PACE_ACTIVITY_LIMITS = {
    "relaxed": 4,
    "balanced": 5,
    "intensive": 7,
}

UNVERIFIED_CLAIM_PATTERN = re.compile(
    r"(已预订|已下单|(?:保证|确认|确保)(?:有票|有房)|"
    r"(?:库存|余票)(?:充足|可订|有票)|"
    r"(?:实时价格|实时票价|现价|门票|票价)(?:为|是|[:：])?\s*\d+(?:\.\d+)?\s*元?|"
    r"(?:营业时间|开放时间)(?:为|是|[:：])?\s*\d{1,2}[:：]\d{2}(?:\s*[-至到]\s*\d{1,2}[:：]\d{2})?|"
    r"(?:车次|航班)(?:为|是|[:：])?\s*[A-Z]{1,3}\d{2,5})",
    re.IGNORECASE,
)
BOOKING_COMPLETION_PATTERN = re.compile(r"(已预订|已下单)")
REALTIME_DISCLAIMER_PATTERN = re.compile(r"(以.+为准|需.+确认|需要.+确认|仅供参考|估算|参考价|可能变动)")
HOTEL_PATTERN = re.compile(r"(酒店|住宿|客栈|民宿|hotel)", re.IGNORECASE)
FOOD_PATTERN = re.compile(r"(餐厅|餐馆|饭店|小吃|美食|restaurant|cafe|咖啡)", re.IGNORECASE)
HOTEL_CATEGORY_PATTERN = re.compile(r"(酒店|住宿|hotel|hostel)", re.IGNORECASE)
FOOD_CATEGORY_PATTERN = re.compile(r"(餐厅|美食|小吃|restaurant|food|cafe)", re.IGNORECASE)
WEATHER_PATTERN = re.compile(r"(天气|气温|降雨|下雨|雨具|防晒|防寒|季节|实时预报|weather)", re.IGNORECASE)
RETURN_TRANSPORT_PATTERN = re.compile(r"(返程|回程|离开|返回|返航|回到|返校|返京|返沪|返深|返广)", re.IGNORECASE)
MISSING_REQUIRED_MEAL_PATTERN = re.compile(r"第(?P<day>\d+)天缺少可核验的(?P<meal>早餐|午餐|晚餐)地点")
DATA_CONFIDENCE_TYPES = {"confirmed_live_data", "reference_data", "estimated_data"}
RELIABLE_COORDINATE_SOURCE_PATTERN = re.compile(
    r"(confirmed_live_data|verified|official|官方|地图|map|amap|高德|baidu|百度|google|openstreetmap|osm)",
    re.IGNORECASE,
)
HOTEL_ROUTE_MISMATCH_KM = 40.0


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _text_values(record: Any, keys: Iterable[str]) -> str:
    if record is None:
        return ""
    if isinstance(record, str):
        return record
    return " ".join(str(_value(record, key, "") or "") for key in keys)


def _source_text(source: Any) -> str:
    return _text_values(
        source,
        (
            "type",
            "data_type",
            "source_type",
            "confidence",
            "verification_status",
            "provider",
            "source",
            "title",
            "url",
        ),
    )


def _source_is_classified(source: Any) -> bool:
    if source is None:
        return False
    values = {
        str(_value(source, key, "") or "").strip().lower()
        for key in ("type", "data_type", "source_type", "confidence", "verification_status")
    }
    if values & DATA_CONFIDENCE_TYPES:
        return True
    return any(value in {"high", "medium", "low", "verified", "official", "estimated"} for value in values)


def _source_is_reliable_for_coordinates(source: Any) -> bool:
    if source is None:
        return False
    if _value(source, "verified", False) is True:
        return True
    source_text = _source_text(source)
    confidence = str(_value(source, "confidence", "") or "").strip().lower()
    data_type = str(
        _value(source, "data_type", _value(source, "source_type", _value(source, "type", ""))) or ""
    ).strip().lower()
    if data_type == "confirmed_live_data":
        return True
    return confidence in {"high", "verified"} and bool(RELIABLE_COORDINATE_SOURCE_PATTERN.search(source_text)) or bool(
        RELIABLE_COORDINATE_SOURCE_PATTERN.search(source_text)
    )


def _activity_limit(plan: TripPlan) -> int:
    pace = plan.intent.pace or "balanced"
    return PACE_ACTIVITY_LIMITS.get(pace, PACE_ACTIVITY_LIMITS["balanced"])


def _budget_limit(plan: TripPlan) -> Optional[float]:
    if plan.intent.budget_total:
        return float(plan.intent.budget_total)
    if plan.intent.budget_per_person and plan.intent.people_count:
        return float(plan.intent.budget_per_person) * int(plan.intent.people_count)
    return None


def _activities_by_day(plan: TripPlan) -> Dict[int, List[TripActivity]]:
    grouped: Dict[int, List[TripActivity]] = defaultdict(list)
    for activity in plan.activities:
        grouped[activity.day].append(activity)
    return grouped


def _activity_text(activity: TripActivity) -> str:
    parts = [activity.title, activity.transport_to_next or ""]
    if activity.place:
        parts.extend([activity.place.name, activity.place.category])
    parts.extend(activity.notes)
    return " ".join(parts)


def _has_unverified_realtime_claim(activity: TripActivity) -> bool:
    text = _activity_text(activity)
    if BOOKING_COMPLETION_PATTERN.search(text):
        return True
    if not UNVERIFIED_CLAIM_PATTERN.search(text):
        return False
    if activity.data_type == "confirmed_live_data":
        return False
    return not bool(REALTIME_DISCLAIMER_PATTERN.search(text))


def _plan_text(plan: TripPlan) -> str:
    parts = [plan.title, *plan.warnings]
    for activity in plan.activities:
        parts.append(_activity_text(activity))
    for location in plan.map_locations:
        if isinstance(location, dict):
            parts.append(" ".join(str(location.get(key, "")) for key in ["name", "category", "description"]))
    for source in plan.source_references:
        parts.append(_text_values(source, ("title", "source", "snippet")))
    return " ".join(parts)


def _has_hotel_signal(plan: TripPlan) -> bool:
    for activity in plan.activities:
        if HOTEL_PATTERN.search(_activity_text(activity)):
            return True
    for location in plan.map_locations:
        if HOTEL_PATTERN.search(" ".join(str(location.get(key, "")) for key in ["name", "category", "description"])):
            return True
    return False


def _is_hotel_activity(activity: TripActivity) -> bool:
    return bool(HOTEL_PATTERN.search(_activity_text(activity)))


def _coordinates(activity: TripActivity) -> Optional[tuple[float, float]]:
    if not activity.place or activity.place.lat is None or activity.place.lng is None:
        return None
    return float(activity.place.lat), float(activity.place.lng)


def _distance_km(left: TripActivity, right: TripActivity) -> Optional[float]:
    left_coord = _coordinates(left)
    right_coord = _coordinates(right)
    if not left_coord or not right_coord:
        return None
    lat1, lng1 = left_coord
    lat2, lng2 = right_coord
    radius_km = 6371.0
    lat_delta = math.radians(lat2 - lat1)
    lng_delta = math.radians(lng2 - lng1)
    a = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(lng_delta / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _hotel_route_mismatch_count(plan: TripPlan) -> tuple[int, int]:
    hotels = [
        activity
        for activity in plan.activities
        if _is_hotel_activity(activity) and _coordinates(activity) is not None
    ]
    visits = [
        activity
        for activity in plan.activities
        if not _is_hotel_activity(activity) and _coordinates(activity) is not None
    ]
    if not hotels or len(visits) < 2:
        return 0, len(visits)

    distant_visits = 0
    for visit in visits:
        distances = [distance for hotel in hotels if (distance := _distance_km(hotel, visit)) is not None]
        if distances and min(distances) > HOTEL_ROUTE_MISMATCH_KM:
            distant_visits += 1
    return distant_visits, len(visits)


def validate_trip_plan(plan: TripPlan) -> TripValidationResult:
    issues: List[TripValidationIssue] = []
    limit = _activity_limit(plan)

    normalized_places: Dict[str, List[str]] = defaultdict(list)
    invalid_places: List[str] = []
    type_mismatches: List[str] = []
    city_mismatches: List[str] = []
    destination = canonical_destination(plan.intent.destination or "") or (plan.intent.destination or "")
    for activity in plan.activities:
        if not activity.place:
            continue
        name = activity.place.name.strip()
        normalized_name = re.sub(r"[^\w\u4e00-\u9fff]+", "", name).casefold()
        normalized_places[normalized_name].append(name)
        if is_invalid_poi_name(name):
            invalid_places.append(name)
        category = activity.place.category or ""
        if HOTEL_PATTERN.search(name) and not HOTEL_CATEGORY_PATTERN.search(category):
            type_mismatches.append(f"{name}（应为酒店）")
        if FOOD_PATTERN.search(name) and not FOOD_CATEGORY_PATTERN.search(category):
            type_mismatches.append(f"{name}（应为餐厅）")
        explicit_city = canonical_destination(activity.place.city or "")
        if destination and explicit_city and explicit_city != destination:
            city_mismatches.append(f"{name}（{explicit_city}）")

    duplicates = [names[0] for key, names in normalized_places.items() if key and len(names) > 1]
    if invalid_places:
        issues.append(TripValidationIssue(code="INVALID_POI", message=f"发现非旅游地点：{', '.join(invalid_places[:5])}。", severity="error", repair_hint="移除道路、导航结果或无法识别的地点，并使用同城可靠 POI 替换。"))
    if duplicates:
        issues.append(TripValidationIssue(code="DUPLICATE_POI", message=f"发现重复地点：{', '.join(duplicates[:5])}。", severity="warning", repair_hint="同一地点仅保留一次，除非用户明确要求重访。"))
    if type_mismatches:
        issues.append(TripValidationIssue(code="POI_TYPE_MISMATCH", message=f"地点分类不一致：{', '.join(type_mismatches[:5])}。", severity="warning", repair_hint="根据地点名称和地图分类重新归类。"))
    if city_mismatches:
        issues.append(TripValidationIssue(code="CITY_MISMATCH", message=f"发现不属于目的地{destination}的地点：{', '.join(city_mismatches[:5])}。", severity="error", repair_hint="删除异地地点，并替换为目的地同类 POI。"))

    classic_places = [name for name, _ in classic_places_for_city(destination)]
    plan_place_text = " ".join(activity.place.name for activity in plan.activities if activity.place)
    if classic_places and not any(name in plan_place_text for name in classic_places):
        issues.append(TripValidationIssue(code="MISSING_CLASSIC_POI", message=f"行程尚未覆盖{destination}的代表性经典地点。", severity="warning", repair_hint=f"在不违背用户偏好的前提下，从以下地点补充：{', '.join(classic_places[:5])}。"))

    missing_must_visit = [name for name in plan.intent.must_visit if name not in plan_place_text]
    missing_interests = [interest for interest in plan.intent.interests if interest and interest not in _plan_text(plan)]
    if missing_must_visit or missing_interests:
        missing = [*missing_must_visit, *missing_interests]
        issues.append(TripValidationIssue(code="PREFERENCE_MISMATCH", message=f"行程未充分匹配用户偏好：{', '.join(missing[:5])}。", severity="warning", repair_hint="优先替换低价值候选，补入用户明确要求的地点或主题。"))

    for day, activities in sorted(_activities_by_day(plan).items()):
        sightseeing_activities = [activity for activity in activities if activity.activity_type != "food"]
        if len(sightseeing_activities) > limit:
            issues.append(
                TripValidationIssue(
                    code="DAY_OVERLOADED",
                    message=f"第{day}天安排了{len(sightseeing_activities)}个游览活动，超过当前节奏建议的{limit}个。",
                    severity="warning",
                    repair_hint=f"减少到{limit}个以内，或把部分地点移动到其他日期。",
                )
            )

        if len(activities) > 1:
            missing_transport_count = sum(
                1
                for activity in activities[:-1]
                if not (activity.transport_to_next or "").strip()
            )
            if missing_transport_count:
                issues.append(
                    TripValidationIssue(
                        code="MISSING_TRANSPORT",
                        message=f"第{day}天有{missing_transport_count}段活动之间缺少交通衔接说明。",
                        severity="info",
                        repair_hint="补充步行、地铁、打车或公共交通衔接时间。",
                    )
                )

        long_jump_count = 0
        for left, right in zip(activities, activities[1:]):
            distance_km = _distance_km(left, right)
            if distance_km is not None and distance_km > 30:
                long_jump_count += 1
        if long_jump_count:
            issues.append(
                TripValidationIssue(
                    code="POSSIBLE_ROUTE_BACKTRACK",
                    message=f"第{day}天存在{long_jump_count}段超过30公里的地点跳转，可能导致明显绕路或通勤过长。",
                    severity="warning",
                    repair_hint="按地理相邻区域重新排序，或把远距离地点拆到不同日期。",
                )
            )

    missing_coordinate_places = [
        activity.place.name
        for activity in plan.activities
        if activity.place and (activity.place.lat is None or activity.place.lng is None)
    ]
    if missing_coordinate_places:
        issues.append(
            TripValidationIssue(
                code="MISSING_COORDINATES",
                message=f"{len(missing_coordinate_places)}个地点缺少地图坐标：{', '.join(missing_coordinate_places[:5])}。",
                severity="warning",
                repair_hint="用地图工具补齐坐标，无法核验时不要在地图中标注。",
            )
        )

    budget_limit = _budget_limit(plan)
    estimated_total = sum(float(activity.estimated_cost or 0) for activity in plan.activities)
    if budget_limit is not None and estimated_total > budget_limit:
        issues.append(
            TripValidationIssue(
                code="BUDGET_EXCEEDED",
                message=f"当前可统计预算约{estimated_total:.0f}元，超过预算上限{budget_limit:.0f}元。",
                severity="warning",
                repair_hint="降低酒店/餐厅档位，减少收费项目，或明确该费用仅为估算。",
            )
        )

    if plan.days > 1 and not _has_hotel_signal(plan):
        issues.append(
            TripValidationIssue(
                code="MISSING_HOTEL_AREA",
                message="多日行程缺少住宿区域或酒店建议。",
                severity="info",
                repair_hint="补充建议住宿区域，并说明与每日路线的匹配关系。",
            )
        )

    distant_visits, coordinate_visit_count = _hotel_route_mismatch_count(plan)
    if coordinate_visit_count and distant_visits > coordinate_visit_count / 2:
        issues.append(
            TripValidationIssue(
                code="HOTEL_ROUTE_MISMATCH",
                message=(
                    f"有{distant_visits}个主要地点距离住宿点超过{HOTEL_ROUTE_MISMATCH_KM:.0f}公里，"
                    "住宿区域可能与多数日程不匹配。"
                ),
                severity="warning",
                repair_hint="重新选择靠近主要活动区域的住宿，或把远距离地点明确拆为独立日程。",
            )
        )

    plan_text = _plan_text(plan)
    if plan.days > 1 and not RETURN_TRANSPORT_PATTERN.search(plan_text):
        issues.append(
            TripValidationIssue(
                code="MISSING_RETURN_TRANSPORT",
                message="多日行程缺少返程或离开目的地的交通提醒。",
                severity="info",
                repair_hint="补充最后一天返程时间窗口、车站/机场选择和行李寄存建议。",
            )
        )

    if not WEATHER_PATTERN.search(plan_text):
        issues.append(
            TripValidationIssue(
                code="MISSING_WEATHER_REMINDER",
                message="行程缺少天气、季节或穿搭雨具提醒。",
                severity="info",
                repair_hint="补充目的地天气核验提醒，并说明雨天/高温/低温时的替代安排。",
            )
        )

    unverified_claims = [
        activity.title
        for activity in plan.activities
        if _has_unverified_realtime_claim(activity)
    ]
    if unverified_claims:
        issues.append(
            TripValidationIssue(
                code="UNVERIFIED_REALTIME_CLAIM",
                message=f"发现{len(unverified_claims)}处可能需要实时工具确认的价格、库存或预订表述。",
                severity="error",
                repair_hint="删除确定性库存/预订表述，或改为“需以官方/实时工具确认为准”。",
            )
        )

    missing_required_meals = [
        f"第{match.group('day')}天{match.group('meal')}"
        for warning in plan.warnings
        if (match := MISSING_REQUIRED_MEAL_PATTERN.search(str(warning or "")))
    ]
    if missing_required_meals:
        issues.append(
            TripValidationIssue(
                code="MISSING_REQUIRED_MEAL",
                message=f"完整旅行日缺少可核验餐饮安排：{'、'.join(missing_required_meals[:10])}。",
                severity="error",
                repair_hint="继续检索并核验真实餐厅 POI；不得只生成缺餐警告后发布正式方案。",
            )
        )

    return TripValidationResult(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )
