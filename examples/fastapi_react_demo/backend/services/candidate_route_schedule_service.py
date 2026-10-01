"""候选插入取得真实路线后，只在原空档内重新安排新增活动。"""
from __future__ import annotations

import math
from typing import Any, Mapping, MutableMapping

from services.candidate_schedule_service import (
    _ceil_slot, _closed_day, _day_operating_bounds, _last_admission,
    _opening_windows, refresh_candidate_schedule_options,
)
from services.formal_consistency_service import _clock, _selected, _time, daily_route_nodes
from services.trip_edit_service import TripEditError, _v3_find_activity


def reschedule_inserted_candidate(
    document: MutableMapping[str, Any], operation: Mapping[str, Any], diff: MutableMapping[str, Any],
) -> None:
    """不移动既有活动；无法容纳真实往返通勤时拒绝本次插入。"""
    def reject() -> None:
        raise TripEditError("CANDIDATE_NO_FEASIBLE_SLOT", "目标空档无法容纳活动、真实接驳及营业和抵离时间")

    day, index, candidate = _v3_find_activity(document, operation["payload"]["activity_id"])
    activities = day["activities"]
    old_start, old_end = _clock(candidate.get("start_at")), _clock(candidate.get("end_at"))
    if old_start is None or old_end is None or old_end <= old_start:
        reject()
    duration = old_end - old_start
    lower, upper = _day_operating_bounds(document, day["day"])
    # display_text 可能带日期或文字，真实抵离时间以结构化 local_iso 为准。
    for kind, section, field in (("arrival_hub", "outbound_transport", "arrival_time"),
                                  ("departure_hub", "return_transport", "departure_time")):
        boundary_day = 1 if kind == "arrival_hub" else document.get("intent", {}).get("days")
        if day["day"] == boundary_day or any(anchor["kind"] == kind for anchor in day.get("anchors", [])):
            stamp = _clock((_selected(document, section).get(field) or {}).get("local_iso"))
            if stamp is not None:
                if kind == "arrival_hub":
                    lower = max(lower, stamp)
                else:
                    upper = min(upper, stamp)
    nodes = [node["activity_id"] for node in daily_route_nodes(day)]
    if candidate["activity_id"] not in nodes:
        reject()
    node_index = nodes.index(candidate["activity_id"])
    route = next((route for route in document.get("map_guidance", {}).get("day_routes", [])
                  if route["day"] == day["day"]), {})
    legs = {(leg["from_id"], leg["to_id"]): leg for leg in route.get("legs", [])}

    def travel_minutes(path: list[str]) -> int:
        total = 0.0
        for left, right in zip(path, path[1:]):
            leg = legs.get((left, right), {})
            minutes = leg.get("duration_minutes")
            if (leg.get("status") != "ready" or isinstance(minutes, bool)
                    or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes < 0):
                reject()
            total += minutes
        return math.ceil(total)

    left_index, right_index = 0, len(nodes) - 1
    if index:
        previous = activities[index - 1]
        previous_end = _clock(previous.get("end_at"))
        if previous_end is None or previous["activity_id"] not in nodes:
            reject()
        lower = max(lower, previous_end)
        left_index = nodes.index(previous["activity_id"])
    if index + 1 < len(activities):
        following = activities[index + 1]
        following_start = _clock(following.get("start_at"))
        if following_start is None or following["activity_id"] not in nodes:
            reject()
        upper = min(upper, following_start)
        right_index = nodes.index(following["activity_id"])
    lower += travel_minutes(nodes[left_index:node_index + 1])
    upper -= travel_minutes(nodes[node_index:right_index + 1])
    hours = (candidate.get("place") or {}).get("opening_hours")
    if _closed_day(hours, day.get("date")):
        reject()
    last_entry = _last_admission(hours, day.get("date"))
    starts = [_ceil_slot(max(lower, left)) for left, right in _opening_windows(hours, day.get("date"))
              if _ceil_slot(max(lower, left)) + duration <= min(upper, right)
              and (last_entry is None or _ceil_slot(max(lower, left)) <= last_entry)]
    if not starts:
        reject()
    start = min(starts)
    before = {"start_at": candidate["start_at"], "end_at": candidate["end_at"]}
    candidate.update(start_at=_time(start), end_at=_time(start + duration))
    after = {"start_at": candidate["start_at"], "end_at": candidate["end_at"]}
    if before != after:
        diff["time_changes"].append({"activity_id": candidate["activity_id"], "day": day["day"],
                                     "before": before, "after": after})
    refresh_candidate_schedule_options(document)
