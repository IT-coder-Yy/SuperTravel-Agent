"""Deterministic candidate-slot feasibility for V3 travel documents.

Candidate activities never create a route or move an existing activity.  A slot is
offered only when the candidate has a verified POI, a parsable opening window,
enough unscheduled time and an acceptable neighbouring-place distance.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple


DAY_START_MINUTES = 9 * 60
# Keep a bounded evening window so a verified night attraction or alternative
# restaurant can still expose a usable insertion option after the formal dinner.
DAY_END_MINUTES = 22 * 60
SLOT_GRANULARITY_MINUTES = 15
MAX_ADJACENT_DISTANCE_METERS = 20_000
_TIME_RANGE_PATTERN = re.compile(r"(\d{1,2})[:：](\d{2})\s*(?:-|–|—|至|到)\s*(\d{1,2})[:：](\d{2})")


def _minutes(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        return None
    hour, minute = value.split(":", 1)
    total = int(hour) * 60 + int(minute)
    return total if 0 <= total <= 24 * 60 else None


def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _ceil_slot(value: int) -> int:
    return int(math.ceil(value / SLOT_GRANULARITY_MINUTES) * SLOT_GRANULARITY_MINUTES)


def _opening_text(value: Any, day_date: Any = None) -> str:
    """按旅行日期筛选来源中的月日与星期规则，避免混用特殊日营业时间。"""
    text = str(value or "").strip()
    if not day_date:
        return text
    current = date.fromisoformat(str(day_date))
    month_day = (current.month, current.day)
    weekday_names = "一二三四五六日"
    selected = []
    text = re.sub(r"[，,。\s]+(?=(?:周|星期)[一二三四五六日天]\s*(?:闭馆|休馆|休息))", "；", text)
    for part in re.split(r"[；;\n]", text):
        dates = re.findall(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", part)
        if dates:
            bounds = [tuple(map(int, pair)) for pair in dates]
            left, right = bounds[0], bounds[1] if len(bounds) > 1 else bounds[0]
            if not (left <= month_day <= right if left <= right else month_day >= left or month_day <= right):
                continue
        weekdays = set()
        for match in re.finditer(r"(?:周|星期)([一二三四五六日天])(?:\s*[-–—至到]\s*(?:周|星期)?([一二三四五六日天]))?", part):
            left = weekday_names.index(match[1].replace("天", "日"))
            right = weekday_names.index(match[2].replace("天", "日")) if match[2] else left
            weekdays.update(range(left, right + 1) if left <= right else [*range(left, 7), *range(right + 1)])
        english_day = r"(?:Mo(?:n(?:day)?)?|Tu(?:e(?:sday)?)?|We(?:d(?:nesday)?)?|Th(?:u(?:rsday)?)?|Fr(?:i(?:day)?)?|Sa(?:t(?:urday)?)?|Su(?:n(?:day)?)?)"
        for match in re.finditer(r"\b(" + english_day + r")(?:\s*[-–—]\s*(" + english_day + r"))?\b", part, re.I):
            names = ["mo", "tu", "we", "th", "fr", "sa", "su"]
            left = names.index(match[1][:2].lower())
            right = names.index(match[2][:2].lower()) if match[2] else left
            weekdays.update(range(left, right + 1) if left <= right else [*range(left, 7), *range(right + 1)])
        if weekdays and current.weekday() not in weekdays:
            continue
        # 节假日专用窗口需官方日期证据，不能扩展到每个普通旅行日。
        if not dates and not weekdays and re.search(r"清明|端午|中秋|国庆|春节|元旦|劳动节|法定节假日", part) and _TIME_RANGE_PATTERN.search(part):
            continue
        selected.append(part)
    return "；".join(selected)


def _opening_windows(value: Any, day_date: Any = None) -> List[Tuple[int, int]]:
    text = _opening_text(value, day_date)
    if not text:
        return []
    windows: List[Tuple[int, int]] = []
    for match in _TIME_RANGE_PATTERN.finditer(text):
        start = int(match.group(1)) * 60 + int(match.group(2))
        end = int(match.group(3)) * 60 + int(match.group(4))
        if 0 <= start < end <= 24 * 60:
            windows.append((start, end))
    if windows:
        return windows
    if re.search(r"不开放|闭馆|休馆|休息|closed|off", text, re.I):
        return []
    if re.search(r"全天|24\s*(?:小时|h)|24/7", text, re.I):
        return [(0, 24 * 60)]
    return windows


def _last_admission(value: Any, day_date: Any = None) -> Optional[int]:
    matches = re.findall(r"(?:最晚|停止|截止).{0,8}?([0-2]?\d)[:：]([0-5]\d)", _opening_text(value, day_date))
    return min((int(hour) * 60 + int(minute) for hour, minute in matches), default=None)


def _closed_day(value: Any, day_date: Any = None) -> bool:
    text = _opening_text(value, day_date)
    if not day_date and _opening_windows(text):
        return False
    return any(re.search(r"不开放|闭馆|休馆|休息|\bclosed\b|\boff\b", part, re.I) and not _TIME_RANGE_PATTERN.search(part)
               for part in re.split(r"[；;\n]", text))


def _coordinates(place: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(place, Mapping):
        return None
    coordinates = place.get("coordinates")
    if not isinstance(coordinates, Mapping):
        return None
    try:
        latitude = float(coordinates.get("latitude"))
        longitude = float(coordinates.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _distance_meters(left: Optional[Tuple[float, float]], right: Optional[Tuple[float, float]]) -> Optional[float]:
    if left is None or right is None:
        return None
    lat1, lng1 = map(math.radians, left)
    lat2, lng2 = map(math.radians, right)
    sin_lat = math.sin((lat2 - lat1) / 2)
    sin_lng = math.sin((lng2 - lng1) / 2)
    arc = sin_lat * sin_lat + math.cos(lat1) * math.cos(lat2) * sin_lng * sin_lng
    return 6_371_000 * 2 * math.asin(min(1.0, math.sqrt(arc)))


def _activity_duration(candidate: Mapping[str, Any]) -> int:
    raw_duration = candidate.get("duration_minutes")
    if isinstance(raw_duration, int) and not isinstance(raw_duration, bool) and raw_duration > 0:
        return raw_duration
    return 90 if candidate.get("kind") == "food" else 120


def _money_amount(value: Any) -> Optional[float]:
    if not isinstance(value, Mapping):
        return None
    raw_amount = value.get("amount")
    if isinstance(raw_amount, bool):
        return None
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def _transport_local_minutes(value: Any) -> Optional[int]:
    if not isinstance(value, Mapping):
        return None
    local_iso = value.get("local_iso")
    if isinstance(local_iso, str) and re.search(r"[T ]\d{2}:\d{2}", local_iso):
        try:
            local = datetime.fromisoformat(local_iso)
            return local.hour * 60 + local.minute
        except ValueError:
            pass
    # Legacy labels may include the travel date and timezone; only a colon-
    # separated clock is a time, never the first digits of the year.
    match = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", str(value.get("display_text") or ""))
    return int(match[1]) * 60 + int(match[2]) if match else None


def _day_operating_bounds(document: Mapping[str, Any], day_number: int) -> Tuple[int, int]:
    start, end = DAY_START_MINUTES, DAY_END_MINUTES
    intent = document.get("intent") or {}
    if isinstance(intent, Mapping) and intent.get("date_mode") == "flexible":
        return start, end
    day_count = intent.get("days") if isinstance(intent, Mapping) else None
    outbound = document.get("outbound_transport")
    returning = document.get("return_transport")
    if day_number == 1 and isinstance(outbound, Mapping):
        selected = next(
            (
                option for option in outbound.get("options", [])
                if isinstance(option, Mapping) and option.get("option_id") == outbound.get("selected_option_id")
            ),
            None,
        )
        if isinstance(selected, Mapping):
            arrival = _transport_local_minutes(selected.get("arrival_time"))
            if arrival is not None:
                start = max(start, arrival)
    if day_number == day_count and isinstance(returning, Mapping):
        selected = next(
            (
                option for option in returning.get("options", [])
                if isinstance(option, Mapping) and option.get("option_id") == returning.get("selected_option_id")
            ),
            None,
        )
        if isinstance(selected, Mapping):
            departure = _transport_local_minutes(selected.get("departure_time"))
            if departure is not None:
                end = min(end, departure)
    return start, end


def _scheduled_intervals(day: Mapping[str, Any]) -> Tuple[Optional[List[Tuple[int, int, Mapping[str, Any]]]], Optional[str]]:
    intervals: List[Tuple[int, int, Mapping[str, Any]]] = []
    for activity in day.get("activities", []):
        if not isinstance(activity, Mapping):
            return None, "当天日程数据无效"
        start = _minutes(activity.get("start_at"))
        end = _minutes(activity.get("end_at"))
        if start is None or end is None or end <= start:
            return None, "当天存在未定时活动，暂无法可靠判断空档"
        intervals.append((start, end, activity))
    intervals.sort(key=lambda item: (item[0], item[1]))
    return intervals, None


def _budget_allows(document: Mapping[str, Any], candidate: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    budget = document.get("budget")
    if not isinstance(budget, Mapping):
        return True, None
    def cny(money: Any) -> Optional[float]:
        if not isinstance(money, Mapping):
            return None
        return _money_amount(money) if money.get("currency", "CNY") == "CNY" else _money_amount({"amount": money.get("cny_reference_amount")})
    def group_cost(activity: Mapping[str, Any]) -> Optional[float]:
        amount = cny(activity.get("estimated_cost"))
        if amount is None or activity.get("estimated_cost_unit", "per_traveler") == "group":
            return amount
        travelers = document.get("intent", {}).get("travelers") or {"adults": 1}
        official = activity.get("official_traveler_prices") or {}
        return sum((cny(official.get(kind)) if cny(official.get(kind)) is not None else amount) * travelers.get(key, 0)
            for kind, key in (("adult", "adults"), ("child", "children"), ("senior", "seniors")))
    total_budget = cny(budget.get("total_budget"))
    candidate_cost = group_cost(candidate)
    if total_budget is None or candidate_cost is None:
        return True, None
    planned_total = cny(budget.get("estimated_total"))
    scheduled_total = 0.0
    itinerary = document.get("itinerary")
    if isinstance(itinerary, Mapping):
        for day in itinerary.get("days", []):
            if not isinstance(day, Mapping):
                continue
            for activity in day.get("activities", []):
                if isinstance(activity, Mapping):
                    scheduled_total += group_cost(activity) or 0.0
    baseline_cost = planned_total if planned_total is not None else scheduled_total
    if baseline_cost + candidate_cost > total_budget:
        return False, "预算余量不足"
    return True, None


def _slot_distance_ok(
    candidate: Mapping[str, Any],
    before: Optional[Mapping[str, Any]],
    after: Optional[Mapping[str, Any]],
) -> Tuple[bool, float]:
    candidate_coordinates = _coordinates(candidate.get("place"))
    distances = [
        distance
        for distance in (
            _distance_meters(candidate_coordinates, _coordinates(before.get("place")) if before else None),
            _distance_meters(candidate_coordinates, _coordinates(after.get("place")) if after else None),
        )
        if distance is not None
    ]
    if any(distance > MAX_ADJACENT_DISTANCE_METERS for distance in distances):
        return False, max(distances)
    return True, sum(distances)


def _candidate_slots_for_day(
    document: Mapping[str, Any],
    candidate: Mapping[str, Any],
    day: Mapping[str, Any],
    opening_windows: Sequence[Tuple[int, int]],
    position: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    day_number = day.get("day")
    if not isinstance(day_number, int):
        return None, "日期数据无效"
    hours = (candidate.get("place") or {}).get("opening_hours")
    if _closed_day(hours, day.get("date")):
        return None, "当天闭馆规则需核验"
    opening_windows = _opening_windows(hours, day.get("date"))
    last_admission = _last_admission(hours, day.get("date"))
    intervals, interval_reason = _scheduled_intervals(day)
    if intervals is None:
        return None, interval_reason
    day_start, day_end = _day_operating_bounds(document, day_number)
    if day_end <= day_start:
        return None, "抵离时间未留下可用空档"
    duration = _activity_duration(candidate)
    boundaries = [(day_start, day_start, None)]
    boundaries.extend((start, end, activity) for start, end, activity in intervals)
    boundaries.append((day_end, day_end, None))
    options: List[Tuple[float, Dict[str, Any]]] = []
    distance_blocked = False
    for index in range(len(boundaries) - 1):
        if position is not None and index != position:
            continue
        left_start, left_end, before = boundaries[index]
        right_start, right_end, after = boundaries[index + 1]
        gap_start, gap_end = max(left_end, day_start), min(right_start, day_end)
        if gap_end - gap_start < duration:
            continue
        for open_start, open_end in opening_windows:
            start = _ceil_slot(max(gap_start, open_start))
            end = start + duration
            if last_admission is not None and start > last_admission:
                continue
            if end > gap_end or end > open_end:
                continue
            distance_ok, distance_score = _slot_distance_ok(candidate, before, after)
            if not distance_ok:
                distance_blocked = True
                continue
            options.append((
                distance_score,
                {
                    "day": day_number,
                    "start_at": _format_minutes(start),
                    "end_at": _format_minutes(end),
                    "position": sum(1 for interval_start, _, _ in intervals if interval_start < start),
                },
            ))
    if not options:
        return None, "与营业时间不匹配" if not distance_blocked else "相邻地点距离过远"
    options.sort(key=lambda item: (item[0], item[1]["start_at"]))
    return options[0][1], None


def refresh_candidate_schedule_options(document: MutableMapping[str, Any]) -> None:
    """Refresh each candidate's server-authoritative insertion options in place."""
    itinerary = document.get("itinerary")
    candidate_pool = document.get("candidate_pool")
    if not isinstance(itinerary, Mapping) or not isinstance(itinerary.get("days"), list) or not isinstance(candidate_pool, list):
        return
    days = [day for day in itinerary["days"] if isinstance(day, Mapping)]
    for candidate in candidate_pool:
        if not isinstance(candidate, MutableMapping):
            continue
        candidate["insertion_options"] = []
        candidate["insertion_unavailable_reason"] = None
        place = candidate.get("place")
        if not isinstance(place, Mapping) or not place.get("poi_id") or _coordinates(place) is None:
            candidate["insertion_unavailable_reason"] = "地点待确认，缺少稳定 POI 或坐标"
            continue
        opening_windows = _opening_windows(place.get("opening_hours"))
        if not opening_windows:
            candidate["insertion_unavailable_reason"] = "营业时间待核验，暂不自动排入"
            continue
        budget_allowed, budget_reason = _budget_allows(document, candidate)
        if not budget_allowed:
            candidate["insertion_unavailable_reason"] = budget_reason
            continue
        options: List[Dict[str, Any]] = []
        reasons: List[str] = []
        for day in days:
            option, reason = _candidate_slots_for_day(document, candidate, day, opening_windows)
            if option is not None:
                options.append(option)
            elif reason:
                reasons.append(f"Day {day.get('day')}: {reason}")
        candidate["insertion_options"] = options
        if not options:
            candidate["insertion_unavailable_reason"] = "；".join(reasons[:3]) or "当前没有可行时间空档"


def select_candidate_insertion_option(
    document: MutableMapping[str, Any],
    candidate: Mapping[str, Any],
    target_day: int,
    position: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return one recomputed option for the requested day, never trusting client time."""
    refresh_candidate_schedule_options(document)
    if position is not None:
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            return None
        day = next((day for day in document["itinerary"]["days"] if day["day"] == target_day), None)
        if day is None or not _budget_allows(document, candidate)[0]:
            return None
        return _candidate_slots_for_day(document, candidate, day, _opening_windows((candidate.get("place") or {}).get("opening_hours")), position)[0]
    for option in candidate.get("insertion_options", []):
        if isinstance(option, Mapping) and option.get("day") == target_day:
            return dict(option)
    return None
