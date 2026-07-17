import math
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from schemas.trip_models import (
    TripActivity,
    TripPlan,
    TripPlanRepairResult,
    TripValidationResult,
)
from services.trip_plan_validator import UNVERIFIED_CLAIM_PATTERN, validate_trip_plan
from services.destination_catalog_service import canonical_destination, is_invalid_poi_name


REPAIRABLE_ISSUE_CODES = {
    "MISSING_TRANSPORT",
    "MISSING_WEATHER_REMINDER",
    "MISSING_RETURN_TRANSPORT",
    "UNVERIFIED_REALTIME_CLAIM",
    "DAY_OVERLOADED",
    "POSSIBLE_ROUTE_BACKTRACK",
    "INVALID_POI",
    "DUPLICATE_POI",
    "CITY_MISMATCH",
}

_REALTIME_FALLBACK_PATTERN = re.compile(
    r"已预订|已下单|有库存|余票|保证有票|"
    r"(?:实时价格|实时票价|现价)(?:为|是|[:：])?\s*\d+(?:\.\d+)?\s*元?"
)
_REALTIME_DISCLAIMER = "价格、票务、库存和预订状态需以官方渠道实时查询结果为准"
_WEATHER_REMINDER = "出发前请查询目的地最新天气，并按降雨、气温和季节调整穿着与行程。"
_RETURN_REMINDER = "请预留返程时间，并在出发前核验车站或机场、班次及行李寄存安排。"
_TRANSPORT_REMINDER = "步行或公共交通前往下一站，具体路线与耗时请在出发前通过地图工具核验。"


def _issue_codes(validation: TripValidationResult) -> Set[str]:
    return {issue.code for issue in validation.issues}


def _activities_by_day(plan: TripPlan) -> Dict[int, List[TripActivity]]:
    grouped: Dict[int, List[TripActivity]] = defaultdict(list)
    for activity in plan.activities:
        grouped[activity.day].append(activity)
    return grouped


def _repair_missing_transport(plan: TripPlan) -> bool:
    changed = False
    for activities in _activities_by_day(plan).values():
        for activity in activities[:-1]:
            if not (activity.transport_to_next or "").strip():
                activity.transport_to_next = _TRANSPORT_REMINDER
                changed = True
    return changed


def _append_warning_once(plan: TripPlan, warning: str) -> bool:
    if warning in plan.warnings:
        return False
    plan.warnings.append(warning)
    return True


def _repair_return_transport(plan: TripPlan) -> bool:
    if not plan.activities:
        return False
    last_day = max(activity.day for activity in plan.activities)
    last_activity = next(
        activity for activity in reversed(plan.activities) if activity.day == last_day
    )
    if _RETURN_REMINDER in last_activity.notes:
        return False
    last_activity.notes.append(_RETURN_REMINDER)
    return True


def _sanitize_realtime_text(value: str) -> Tuple[str, bool]:
    if not value:
        return value, False
    sanitized, count = UNVERIFIED_CLAIM_PATTERN.subn(_REALTIME_DISCLAIMER, value)
    sanitized, fallback_count = _REALTIME_FALLBACK_PATTERN.subn(_REALTIME_DISCLAIMER, sanitized)
    if count + fallback_count == 0:
        return value, False
    sanitized = re.sub(
        rf"(?:{re.escape(_REALTIME_DISCLAIMER)})(?:[，、；; ]*{re.escape(_REALTIME_DISCLAIMER)})+",
        _REALTIME_DISCLAIMER,
        sanitized,
    )
    return sanitized, True


def _repair_unverified_realtime_claims(plan: TripPlan) -> bool:
    changed = False
    for activity in plan.activities:
        activity.title, title_changed = _sanitize_realtime_text(activity.title)
        sanitized_notes: List[str] = []
        notes_changed = False
        for note in activity.notes:
            sanitized_note, note_changed = _sanitize_realtime_text(note)
            sanitized_notes.append(sanitized_note)
            notes_changed = notes_changed or note_changed
        if notes_changed:
            activity.notes = sanitized_notes
        if title_changed or notes_changed:
            activity.data_type = "estimated_data"
            changed = True
    return changed


def _activity_limit(plan: TripPlan) -> int:
    return {"relaxed": 4, "balanced": 5, "intensive": 7}.get(
        plan.intent.pace or "balanced",
        5,
    )


def _balance_overloaded_days(plan: TripPlan) -> bool:
    limit = _activity_limit(plan)
    grouped = _activities_by_day(plan)
    positions = {id(activity): index for index, activity in enumerate(plan.activities)}
    changed = False

    for source_day in range(1, plan.days + 1):
        source = grouped[source_day]
        while len(source) > limit:
            target_days = sorted(
                (
                    day
                    for day in range(1, plan.days + 1)
                    if day != source_day and len(grouped[day]) < limit
                ),
                key=lambda day: (abs(day - source_day), day),
            )
            if not target_days:
                break
            activity = source.pop()
            target_day = target_days[0]
            activity.day = target_day
            grouped[target_day].append(activity)
            changed = True

    if changed:
        plan.activities.sort(key=lambda activity: (activity.day, positions[id(activity)]))
    return changed


def _coordinates(activity: TripActivity) -> Optional[Tuple[float, float]]:
    if not activity.place or activity.place.lat is None or activity.place.lng is None:
        return None
    return float(activity.place.lat), float(activity.place.lng)


def _distance_km(left: TripActivity, right: TripActivity) -> float:
    left_coord = _coordinates(left)
    right_coord = _coordinates(right)
    if left_coord is None or right_coord is None:
        return math.inf
    lat1, lng1 = left_coord
    lat2, lng2 = right_coord
    radius_km = 6371.0
    lat_delta = math.radians(lat2 - lat1)
    lng_delta = math.radians(lng2 - lng1)
    value = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(lng_delta / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))


def _route_distance(activities: Sequence[TripActivity]) -> float:
    return sum(_distance_km(left, right) for left, right in zip(activities, activities[1:]))


def _nearest_neighbor_route(
    activities: Sequence[TripActivity],
    start_index: int,
) -> List[TripActivity]:
    remaining = list(enumerate(activities))
    _, current = remaining.pop(start_index)
    route = [current]
    while remaining:
        next_position = min(
            range(len(remaining)),
            key=lambda index: (
                _distance_km(current, remaining[index][1]),
                remaining[index][0],
            ),
        )
        _, current = remaining.pop(next_position)
        route.append(current)
    return route


def _reorder_long_distance_routes(plan: TripPlan) -> bool:
    changed = False
    grouped = _activities_by_day(plan)
    for day, activities in grouped.items():
        if len(activities) < 3 or any(_coordinates(activity) is None for activity in activities):
            continue
        original_distance = _route_distance(activities)
        candidates = [
            _nearest_neighbor_route(activities, start_index)
            for start_index in range(len(activities))
        ]
        best = min(candidates, key=lambda route: (_route_distance(route), [activity.title for activity in route]))
        if _route_distance(best) + 0.001 >= original_distance:
            continue
        grouped[day] = best
        changed = True

    if changed:
        plan.activities = [
            activity
            for day in sorted(grouped)
            for activity in grouped[day]
        ]
    return changed


def _remove_low_quality_activities(plan: TripPlan) -> bool:
    destination = canonical_destination(plan.intent.destination or "") or (plan.intent.destination or "")
    seen = set()
    kept: List[TripActivity] = []
    removed_names: Set[str] = set()
    for activity in plan.activities:
        if not activity.place:
            kept.append(activity)
            continue
        name = activity.place.name.strip()
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", name).casefold()
        explicit_city = canonical_destination(activity.place.city or "")
        should_remove = (
            is_invalid_poi_name(name)
            or (normalized and normalized in seen)
            or bool(destination and explicit_city and explicit_city != destination)
        )
        if should_remove:
            removed_names.add(name)
            continue
        seen.add(normalized)
        kept.append(activity)
    if not removed_names:
        return False
    plan.activities = kept
    plan.map_locations = [
        location for location in plan.map_locations
        if not isinstance(location, dict) or str(location.get("name") or "").strip() not in removed_names
    ]
    _append_warning_once(plan, f"已移除低质量或重复地点：{', '.join(sorted(removed_names)[:5])}；如当天内容不足，请从同城可靠候选补充。")
    return True


def _ordered_supported_codes(validation: TripValidationResult) -> Iterable[str]:
    seen: Set[str] = set()
    for issue in validation.issues:
        if issue.code in REPAIRABLE_ISSUE_CODES and issue.code not in seen:
            seen.add(issue.code)
            yield issue.code


def repair_trip_plan_once(
    plan: TripPlan,
    validation: TripValidationResult,
) -> TripPlanRepairResult:
    repaired_plan = plan.model_copy(deep=True)
    attempted_issue_codes: List[str] = []
    notes: List[str] = []
    changed = False

    repair_actions = {
        "MISSING_TRANSPORT": _repair_missing_transport,
        "MISSING_WEATHER_REMINDER": lambda value: _append_warning_once(value, _WEATHER_REMINDER),
        "MISSING_RETURN_TRANSPORT": _repair_return_transport,
        "UNVERIFIED_REALTIME_CLAIM": _repair_unverified_realtime_claims,
        "DAY_OVERLOADED": _balance_overloaded_days,
        "POSSIBLE_ROUTE_BACKTRACK": _reorder_long_distance_routes,
        "INVALID_POI": _remove_low_quality_activities,
        "DUPLICATE_POI": _remove_low_quality_activities,
        "CITY_MISMATCH": _remove_low_quality_activities,
    }
    for issue_code in _ordered_supported_codes(validation):
        attempted_issue_codes.append(issue_code)
        issue_changed = repair_actions[issue_code](repaired_plan)
        changed = issue_changed or changed
        if not issue_changed:
            notes.append(f"{issue_code} 无法在不编造信息的前提下自动修复，已保留为待确认项。")

    remaining_validation = validate_trip_plan(repaired_plan)
    remaining_codes = _issue_codes(remaining_validation)
    resolved_issue_codes = [
        code for code in attempted_issue_codes if code not in remaining_codes
    ]
    return TripPlanRepairResult(
        repaired=changed,
        plan=repaired_plan,
        attempted_issue_codes=attempted_issue_codes,
        resolved_issue_codes=resolved_issue_codes,
        remaining_issue_codes=sorted(remaining_codes),
        notes=notes,
        remaining_validation=remaining_validation,
    )
