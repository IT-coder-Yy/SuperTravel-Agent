"""Deterministic meal placement for formal travel itineraries."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional

from backend.schemas.trip_models import TripActivity, TripPlan


_MEAL_ORDER = ("breakfast", "lunch", "dinner")
_MEAL_LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
_DOMESTIC_WINDOWS = {
    "breakfast": (7 * 60, 90),
    "lunch": (11 * 60 + 30, 90),
    "dinner": (17 * 60 + 30, 90),
}
_INTERNATIONAL_PLANNING_WINDOWS = {
    "breakfast": (7 * 60 + 30, 90),
    "lunch": (12 * 60, 90),
    "dinner": (18 * 60 + 30, 90),
}


def _early_departure_context(document: dict) -> Optional[tuple[dict, int, str]]:
    days = document.get("itinerary", {}).get("days", [])
    if not days or document.get("intent", {}).get("date_mode") != "fixed":
        return None
    section = document.get("return_transport", {})
    option = next((item for item in section.get("options", [])
                   if item["option_id"] == section.get("selected_option_id")), {})
    departure = (option.get("departure_time") or {}).get("local_iso")
    minute = _clock_minutes(departure)
    if minute is None or minute >= 8 * 60 or str(departure)[:10] != str(days[-1].get("date")):
        return None
    return days[-1], minute, f"early-breakfast-{days[-1]['day']}-{minute}"


def early_breakfast_preparation_covers(document: dict, day_number: int) -> bool:
    context = _early_departure_context(document)
    return bool(context and context[0]["day"] == day_number and any(
        item.get("action_id") == context[2] and item.get("scope") == "day"
        and item.get("target_id") == str(day_number) and item.get("text")
        for item in document.get("action_items", [])) and any(
        item.get("code") == "EARLY_BREAKFAST_PREPARATION_REQUIRED" and item.get("target_id") == context[2]
        for item in document.get("validation", {}).get("issues", [])))


def prepare_early_departure_breakfast(document: dict, *, after_routes: bool = False) -> bool:
    """早班先尝试已核验营业餐馆；无法在真实窗口用餐时明确要求提前准备。"""
    context = _early_departure_context(document)
    if context is None:
        return False
    day, departure, action_id = context
    breakfast = next((item for item in day["activities"] if item.get("meal_type") == "breakfast"), None)
    issues = document["validation"]["issues"]
    if after_routes:
        if breakfast is None or not any(item.get("severity") == "error"
                and item.get("code") == "INITIAL_MEAL_WINDOW_CONFLICT"
                and item.get("target_id") == breakfast["activity_id"] for item in issues):
            return False
        # 路线、营业或离开窗口不成立时，撤回这餐，不能清除其他活动的错误。
        day["activities"].remove(breakfast)
        candidate = deepcopy(breakfast)
        candidate.update(day=None, order=None, start_at=None, end_at=None, fixed_time=False, route_to_next=None)
        pool = document["candidate_pool"]
        if len(pool) >= 15:
            moved_ids = {item.get("target_id") for item in issues if item.get("code") == "ACTIVITY_WINDOW_UNAVAILABLE"}
            replace_index = min(range(len(pool)), key=lambda index: (
                pool[index]["activity_id"] in moved_ids,
                bool((pool[index].get("place") or {}).get("opening_hours")), -index))
            pool.pop(replace_index)
        pool.append(candidate)
        document["validation"]["issues"] = [item for item in issues
            if not (item.get("code") == "INITIAL_MEAL_WINDOW_CONFLICT" and item.get("target_id") == breakfast["activity_id"])]
    elif breakfast is not None:
        return False
    else:
        from backend.services.candidate_schedule_service import _closed_day, _opening_windows
        for candidate in list(document["candidate_pool"]):
            place = candidate.get("place") or {}
            if candidate.get("kind") != "food" or not place.get("poi_id") or not place.get("coordinates"):
                continue
            hours = place.get("opening_hours")
            duration = candidate.get("duration_minutes") or 90
            end = departure - 30
            start = end - duration
            if start < 0 or _closed_day(hours, day.get("date")) or not any(
                    left <= start and end <= right for left, right in _opening_windows(hours, day.get("date"))):
                continue
            activity = deepcopy(candidate)
            activity.update(day=day["day"], order=1, meal_type="breakfast", start_at=_format_clock(start),
                            end_at=_format_clock(end), duration_minutes=duration)
            day["activities"].insert(0, activity)
            document["candidate_pool"].remove(candidate)
            from backend.services.trip_edit_service import _v3_refresh_anchors
            _v3_refresh_anchors(document)
            return True
    if not early_breakfast_preparation_covers(document, day["day"]):
        document.setdefault("action_items", []).append({"action_id": action_id, "kind": "checklist",
            "scope": "day", "target_id": str(day["day"]), "status": "pending",
            "text": f"第{day['day']}天返程于{_format_clock(departure)}出发，尚无同时满足营业及接驳时间的已核验早餐餐馆。请提前一晚准备可携带早餐，或向住宿方确认早餐打包；出发前自行确认食品携带要求。"})
        document["validation"]["issues"].append({"code": "EARLY_BREAKFAST_PREPARATION_REQUIRED",
            "severity": "warning", "target_id": action_id,
            "message": f"第{day['day']}天早班交通前没有可行的已核验早餐排期，已加入提前准备早餐的待办，未计作已确认餐饮。"})
    document["status"] = "degraded"
    document["validation"]["degraded"] = True
    if after_routes:
        from backend.services.trip_edit_service import _v3_refresh_anchors
        _v3_refresh_anchors(document)
    return True


def _clock_minutes(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    match = re.search(r"(?:T|\s|^)([01]?\d|2[0-3]):([0-5]\d)(?::\d{2})?", text)
    if match is None:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _format_clock(minutes: int) -> str:
    normalized = max(0, min(minutes, 23 * 60 + 59))
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def _opening_interval(value: Any) -> Optional[tuple[int, int]]:
    text = str(value or "")
    match = re.search(r"([01]?\d|2[0-3]):([0-5]\d)\s*(?:-|–|—|至|到)\s*([01]?\d|2[0-3]):([0-5]\d)", text)
    if match is None:
        return None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    return (start, end) if end > start else None


def _is_verified_food(activity: TripActivity) -> bool:
    place = activity.place
    return bool(
        activity.activity_type == "food"
        and place is not None
        and place.poi_id
        and place.lat is not None
        and place.lng is not None
    )


def _target_meals(
    *,
    days: int,
    date_mode: str,
    outbound_arrival: Any,
    return_departure: Any,
    breakfast_requested: bool,
    lodging_breakfast_included: Optional[bool],
) -> Dict[int, List[str]]:
    targets = {day: [] for day in range(1, days + 1)}
    if date_mode != "fixed":
        for day in range(1, days + 1):
            targets[day] = ["lunch", "dinner"]
        if breakfast_requested or lodging_breakfast_included is False:
            for day in range(1, days + 1):
                targets[day].insert(0, "breakfast")
        return {day: [meal for meal in _MEAL_ORDER if meal in targets[day]] for day in targets}

    arrival_minutes = _clock_minutes(outbound_arrival)
    departure_minutes = _clock_minutes(return_departure)
    for day in range(1, days + 1):
        if 1 < day < days:
            targets[day] = ["lunch", "dinner"]

    if arrival_minutes is None:
        targets[1].extend(["lunch", "dinner"])
    else:
        if arrival_minutes <= 13 * 60 + 30:
            targets[1].append("lunch")
        if arrival_minutes <= 20 * 60 + 30:
            targets[1].append("dinner")
    if departure_minutes is None:
        targets[days].extend(["lunch", "dinner"])
    else:
        last_day = targets[days]
        if departure_minutes >= 14 * 60:
            last_day.append("lunch")
        if departure_minutes >= 20 * 60 + 30:
            last_day.append("dinner")

    if days == 1:
        # 一日游必须同时满足抵达和离开窗口，不能把两端的餐次取并集。
        if (arrival_minutes is not None and arrival_minutes > 13 * 60 + 30) or (departure_minutes is not None and departure_minutes < 14 * 60):
            targets[1] = [meal for meal in targets[1] if meal != "lunch"]
        if (arrival_minutes is not None and arrival_minutes > 20 * 60 + 30) or (departure_minutes is not None and departure_minutes < 20 * 60 + 30):
            targets[1] = [meal for meal in targets[1] if meal != "dinner"]
    wants_breakfast = breakfast_requested or lodging_breakfast_included is False
    if wants_breakfast:
        for day in range(2, days + 1):
            if "breakfast" not in targets[day]:
                targets[day].insert(0, "breakfast")
    if departure_minutes is not None and departure_minutes < 8 * 60 and "breakfast" not in targets[days]:
        targets[days].insert(0, "breakfast")

    return {day: [meal for meal in _MEAL_ORDER if meal in targets[day]] for day in targets}


def meal_targets_by_day(
    *,
    days: int,
    date_mode: str,
    outbound_arrival: Any = None,
    return_departure: Any = None,
    breakfast_requested: bool = False,
    lodging_breakfast_included: Optional[bool] = None,
) -> Dict[int, List[str]]:
    """Return only the meals justified by dates, transport windows and breakfast facts."""

    return _target_meals(
        days=days,
        date_mode=date_mode,
        outbound_arrival=outbound_arrival,
        return_departure=return_departure,
        breakfast_requested=breakfast_requested,
        lodging_breakfast_included=lodging_breakfast_included,
    )


def _scheduled_window(
    activity: TripActivity,
    meal_type: str,
    *,
    domestic: bool,
) -> Optional[tuple[int, int]]:
    return meal_window_for_opening_hours(
        activity.place.opening_hours if activity.place else None,
        meal_type,
        domestic=domestic,
    )


def meal_window_for_opening_hours(
    opening_hours: Any,
    meal_type: str,
    *,
    domestic: bool,
) -> Optional[tuple[int, int]]:
    """Return a feasible local meal window without claiming provider hours."""

    windows = _DOMESTIC_WINDOWS if domestic else _INTERNATIONAL_PLANNING_WINDOWS
    if meal_type not in windows:
        return None
    start, duration = (
        windows
    )[meal_type]
    planned_start = start
    opening = _opening_interval(opening_hours)
    if opening is not None:
        open_start, open_end = opening
        start = max(start, open_start)
        if start - planned_start > 90:
            return None
        if start + duration > open_end:
            return None
    return start, start + duration


def apply_meal_schedule(
    plan: TripPlan,
    *,
    targets_by_day: Mapping[int, Iterable[str]],
    domestic: bool,
) -> List[str]:
    """Keep only real restaurant POIs, assign meal periods and sort days by time."""

    warnings: List[str] = []
    verified_food_ids = {
        activity.activity_id
        for activity in plan.activities
        if _is_verified_food(activity)
    }
    dropped_food = [
        activity.title
        for activity in plan.activities
        if activity.activity_type == "food" and activity.activity_id not in verified_food_ids
    ]
    plan.activities = [
        activity
        for activity in plan.activities
        if activity.activity_type != "food" or activity.activity_id in verified_food_ids
    ]
    if dropped_food:
        warnings.append("未将缺少稳定 POI 或坐标的餐饮候选写入正式日程：" + "、".join(dropped_food[:3]))

    scheduled_food_ids = set()
    for day in range(1, plan.days + 1):
        expected = list(targets_by_day.get(day, []))
        food_activities = [
            activity for activity in plan.activities
            if activity.day == day and activity.activity_type == "food"
        ]
        used_ids = set()
        for meal_type in expected:
            selected: Optional[TripActivity] = None
            selected_window: Optional[tuple[int, int]] = None
            for activity in food_activities:
                if activity.activity_id in used_ids:
                    continue
                candidate_window = _scheduled_window(activity, meal_type, domestic=domestic)
                if candidate_window is None:
                    continue
                selected, selected_window = activity, candidate_window
                break
            if selected is None or selected_window is None:
                warnings.append(f"第{day}天缺少可核验的{_MEAL_LABELS[meal_type]}地点，未强行补入餐饮活动")
                continue
            used_ids.add(selected.activity_id)
            scheduled_food_ids.add(selected.activity_id)
            selected.meal_type = meal_type
            selected.start_time = _format_clock(selected_window[0])
            selected.end_time = _format_clock(selected_window[1])
            selected.duration_minutes = selected_window[1] - selected_window[0]
            if selected.place and selected.place.opening_hours in (None, "", []):
                selected.notes = [
                    *selected.notes,
                    "用餐时段为目的地当地规划窗口；该餐厅营业时间仍需在出发前核验。",
                ]

    plan.activities = [
        activity
        for activity in plan.activities
        if activity.activity_type != "food" or activity.activity_id in scheduled_food_ids
    ]

    def activity_sort_key(activity: TripActivity) -> tuple[int, int, str]:
        return (activity.day, _clock_minutes(activity.start_time) or 24 * 60, activity.activity_id)

    plan.activities.sort(key=activity_sort_key)
    return list(dict.fromkeys(warnings))
