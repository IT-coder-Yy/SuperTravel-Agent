from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha1
import math
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Tuple

from pydantic import ValidationError

from schemas.trip_models import TripPlan, TripValidationResult
from schemas.trip_v3_models import TravelPlanDocumentV3
from services.candidate_schedule_service import (
    refresh_candidate_schedule_options,
    select_candidate_insertion_option,
)
from services.trip_plan_repair_service import repair_trip_plan_once
from services.trip_plan_validator import validate_trip_plan
from services.trip_draft_validation_service import validate_trip_draft


SUPPORTED_OPERATIONS = {
    "add_activity",
    "remove_activity",
    "replace_activity",
    "move_activity",
    "update_activity_time",
    "update_day_preference",
}

V3_SUPPORTED_OPERATIONS = {
    "add_activity",
    "replace_activity",
    "update_activity_time",
    "update_trip_note",
    "update_day_note",
    "delete_activity",
    "optimize_day_route",
    "move_activity",
    "move_activity_to_candidate",
    "insert_candidate",
    "shift_activity_time",
    "set_activity_fixed_time",
    "update_activity_note",
    "select_transport_option",
    "select_lodging_option",
}

_TIME_FORMAT = "%H:%M"
_ROUTE_REVALIDATION_ISSUE_CODE = "ROUTE_REVALIDATION_REQUIRED"

LOCAL_REPAIRABLE_ISSUE_CODES = {
    "MISSING_TRANSPORT",
    "POSSIBLE_ROUTE_BACKTRACK",
    "UNVERIFIED_REALTIME_CLAIM",
}


class TripEditError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_v3_document(value: Mapping[str, Any]) -> bool:
    return value.get("schema_version") == "3.0"


def _validate_v3_identity(document: Mapping[str, Any], operation: Mapping[str, Any]) -> None:
    plan_id = document.get("plan_id")
    operation_plan_id = operation.get("plan_id")
    if not plan_id or not operation_plan_id or plan_id != operation_plan_id:
        raise TripEditError("PLAN_ID_MISMATCH", "operation plan_id does not match the document")

    revision = document.get("revision")
    base_version = operation.get("base_version")
    if isinstance(revision, bool) or isinstance(base_version, bool):
        raise TripEditError("INVALID_VERSION", "document revision and base_version must be integers")
    if not isinstance(revision, int) or not isinstance(base_version, int):
        raise TripEditError("INVALID_VERSION", "document revision and base_version must be integers")
    if revision != base_version:
        raise TripEditError("VERSION_CONFLICT", "base_version does not match the current document")


def _v3_days(document: MutableMapping[str, Any]) -> List[MutableMapping[str, Any]]:
    itinerary = document.get("itinerary")
    if not isinstance(itinerary, MutableMapping) or not isinstance(itinerary.get("days"), list):
        raise TripEditError("INVALID_PLAN", "document.itinerary.days must be a list")
    days = itinerary["days"]
    if any(not isinstance(day, MutableMapping) for day in days):
        raise TripEditError("INVALID_PLAN", "document itinerary days must be mappings")
    return days


def _v3_find_day(document: MutableMapping[str, Any], day_number: int) -> MutableMapping[str, Any]:
    for day in _v3_days(document):
        if day.get("day") == day_number:
            return day
    raise TripEditError("INVALID_PAYLOAD", "payload.target_day is outside the current itinerary")


def _v3_find_activity(
    document: MutableMapping[str, Any], activity_id: Any
) -> Tuple[MutableMapping[str, Any], int, MutableMapping[str, Any]]:
    for day in _v3_days(document):
        activities = day.get("activities")
        if not isinstance(activities, list) or any(not isinstance(item, MutableMapping) for item in activities):
            raise TripEditError("INVALID_PLAN", "document day activities must be mappings")
        for index, activity in enumerate(activities):
            if activity.get("activity_id") == activity_id:
                return day, index, activity
    raise TripEditError("ACTIVITY_NOT_FOUND", f"activity {activity_id!r} was not found")


def _v3_find_candidate(document: MutableMapping[str, Any], activity_id: Any) -> Tuple[int, MutableMapping[str, Any]]:
    candidates = document.get("candidate_pool")
    if not isinstance(candidates, list) or any(not isinstance(item, MutableMapping) for item in candidates):
        raise TripEditError("INVALID_PLAN", "document.candidate_pool must be a list of mappings")
    for index, candidate in enumerate(candidates):
        if candidate.get("activity_id") == activity_id:
            return index, candidate
    raise TripEditError("CANDIDATE_NOT_FOUND", f"candidate {activity_id!r} was not found")


def _v3_normalize_day_orders(day: MutableMapping[str, Any]) -> None:
    activities = day.get("activities")
    if not isinstance(activities, list):
        raise TripEditError("INVALID_PLAN", "document day activities must be a list")
    for index, activity in enumerate(activities, start=1):
        if not isinstance(activity, MutableMapping):
            raise TripEditError("INVALID_PLAN", "document day activities must be mappings")
        activity["day"] = day.get("day")
        activity["order"] = index


def _v3_insert_activity(
    day: MutableMapping[str, Any], activity: MutableMapping[str, Any], position: Any
) -> None:
    activities = day.get("activities")
    if not isinstance(activities, list):
        raise TripEditError("INVALID_PLAN", "document day activities must be a list")
    if position is None:
        activities.append(activity)
    else:
        if isinstance(position, bool):
            raise TripEditError("INVALID_PAYLOAD", "payload.position must be a non-negative integer")
        try:
            parsed_position = int(position)
        except (TypeError, ValueError):
            raise TripEditError("INVALID_PAYLOAD", "payload.position must be a non-negative integer")
        if parsed_position < 0:
            raise TripEditError("INVALID_PAYLOAD", "payload.position must be a non-negative integer")
        activities.insert(min(parsed_position, len(activities)), activity)
    _v3_normalize_day_orders(day)


def _v3_parse_time(value: Any, key: str) -> datetime:
    if not isinstance(value, str):
        raise TripEditError("TIME_NOT_SET", f"{key} must be set before adjusting by 15 minutes")
    try:
        return datetime.strptime(value, _TIME_FORMAT)
    except ValueError:
        raise TripEditError("INVALID_PAYLOAD", f"{key} must use HH:MM format")


def _v3_format_time(value: datetime) -> str:
    return value.strftime(_TIME_FORMAT)


def reschedule_moved_activity(document: MutableMapping[str, Any], operation: Mapping[str, Any], diff: MutableMapping[str, Any]) -> None:
    """取得拖动后新相邻路段，再顺延日程；保留用户活动、时长和固定时间。"""
    from services.candidate_schedule_service import _opening_windows
    payload = operation["payload"]
    affected_days = diff.get("affected_days") or [_require_day(payload, "target_day", "to_day")]
    for day_number in sorted(set(affected_days)):
        day = _v3_find_day(document, day_number)
        activities = day["activities"]
        timed = [item for item in activities if item.get("start_at") and item.get("end_at")]
        if not timed:
            continue
        route = next((item for item in document["map_guidance"]["day_routes"] if item["day"] == day["day"]), {})
        legs = {(leg["from_id"], leg["to_id"]): leg for leg in route.get("legs", [])}
        cursor = min(_v3_parse_time(item["start_at"], "start_at") for item in timed)
        arrival = next((item for item in day.get("anchors", []) if item["kind"] == "arrival_hub"), None)
        if arrival:
            section = document["outbound_transport"]
            selected = next((item for item in section["options"] if item["option_id"] == section.get("selected_option_id")), {})
            stamp = (selected.get("arrival_time") or {}).get("local_iso")
            if stamp:
                arrival_time = datetime.fromisoformat(stamp).strftime(_TIME_FORMAT)
                leg = legs.get((arrival["anchor_id"], timed[0]["activity_id"]), {})
                minutes = leg.get("duration_minutes") if leg.get("status") == "ready" else 15
                cursor = max(cursor, _v3_parse_time(arrival_time, "arrival_time") + timedelta(minutes=minutes or 0))
        previous = None
        for activity in timed:
            before = {"start_at": activity["start_at"], "end_at": activity["end_at"]}
            old_start = _v3_parse_time(activity["start_at"], "start_at")
            old_end = _v3_parse_time(activity["end_at"], "end_at")
            if previous:
                leg = legs.get((previous["activity_id"], activity["activity_id"]), {})
                # 缺失路线仍由校验报告待确认，不能将预留值标成真实交通时间。
                minutes = leg.get("duration_minutes") if leg.get("status") == "ready" else 15
                cursor = _v3_parse_time(previous["end_at"], "end_at") + timedelta(minutes=minutes or 0)
            if activity.get("fixed_time"):
                previous = activity
                continue
            start = cursor if activity["activity_id"] == payload["activity_id"] and not activity.get("meal_type") else max(cursor, old_start)
            start += timedelta(minutes=(-start.minute) % 15)
            duration = int((old_end - old_start).total_seconds() / 60)
            windows = _opening_windows((activity.get("place") or {}).get("opening_hours"), day.get("date"))
            start_minutes = start.hour * 60 + start.minute
            feasible = [math.ceil(max(start_minutes, left) / 15) * 15 for left, right in windows
                        if math.ceil(max(start_minutes, left) / 15) * 15 + duration <= right]
            if feasible and start.date() == old_start.date():
                start = old_start.replace(hour=0, minute=0) + timedelta(minutes=min(feasible))
            end = start + (old_end - old_start)
            # 无法排入当天时仍保留原活动，让路线/时间校验指出冲突，不删活动或吞掉编辑。
            if end.date() == old_start.date():
                activity.update(start_at=_v3_format_time(start), end_at=_v3_format_time(end))
            after = {"start_at": activity["start_at"], "end_at": activity["end_at"]}
            if before != after:
                diff["time_changes"].append({"activity_id": activity["activity_id"], "before": before, "after": after})
            previous = activity
    refresh_candidate_schedule_options(document)


def _v3_note_id(document: Mapping[str, Any], activity_id: str) -> str:
    notes = document.get("notes")
    existing_ids = {
        item.get("note_id")
        for item in notes
        if isinstance(item, Mapping) and isinstance(item.get("note_id"), str)
    } if isinstance(notes, list) else set()
    base = f"note_{activity_id}"
    note_id = base
    suffix = 2
    while note_id in existing_ids:
        note_id = f"{base}_{suffix}"
        suffix += 1
    return note_id


def _v3_is_mappable_place(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if not isinstance(value.get("poi_id"), str) or not value["poi_id"].strip():
        return False
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, Mapping):
        return False
    try:
        latitude = float(coordinates.get("latitude"))
        longitude = float(coordinates.get("longitude"))
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _v3_selected_transport_hub(section: Any, field: str) -> Dict[str, Any] | None:
    if not isinstance(section, Mapping):
        return None
    selected_id = section.get("selected_option_id")
    options = section.get("options")
    if not isinstance(options, list):
        return None
    selected = next(
        (
            option for option in options
            if isinstance(option, Mapping) and option.get("option_id") == selected_id
        ),
        None,
    )
    hub = selected.get(field) if isinstance(selected, Mapping) else None
    return deepcopy(dict(hub)) if _v3_is_mappable_place(hub) else None


def _v3_anchor_id(plan_id: Any, kind: str, day: int, poi_id: str) -> str:
    digest = sha1(f"{plan_id}:{kind}:{day}:{poi_id}".encode("utf-8")).hexdigest()[:16]
    return f"anchor_{digest}"


def _v3_refresh_anchors(document: MutableMapping[str, Any]) -> None:
    """Rebuild only derived city-side anchors after a lodging or transport choice changes."""
    days = _v3_days(document)
    lodging_plan = document.get("lodging_plan")
    lodging_options = lodging_plan.get("options", []) if isinstance(lodging_plan, Mapping) else []
    lodging_id = lodging_plan.get("planning_lodging_id") if isinstance(lodging_plan, Mapping) else None
    selected_lodging = next(
        (
            option for option in lodging_options
            if isinstance(option, Mapping) and option.get("lodging_id") == lodging_id
        ),
        None,
    )
    lodging_place = selected_lodging.get("place") if isinstance(selected_lodging, Mapping) else None
    planning_lodging = deepcopy(dict(lodging_place)) if _v3_is_mappable_place(lodging_place) else None
    arrival_hub = _v3_selected_transport_hub(document.get("outbound_transport"), "arrival_hub")
    departure_hub = _v3_selected_transport_hub(document.get("return_transport"), "departure_hub")
    def existing_transport_hub(section_name: str, kind: str, field: str):
        section = document.get(section_name) or {}
        selected = next((item for item in section.get("options", []) if item.get("option_id") == section.get("selected_option_id")), {})
        name = str(selected.get(field) or "").strip()
        return next((deepcopy(anchor["place"]) for day in days for anchor in day.get("anchors", [])
                     if anchor["kind"] == kind and name and (anchor.get("place") or {}).get("name") == name), None)
    arrival_hub = arrival_hub or existing_transport_hub("outbound_transport", "arrival_hub", "arrival_place")
    departure_hub = departure_hub or existing_transport_hub("return_transport", "departure_hub", "departure_place")
    has_lodging = len(days) > 1 and planning_lodging is not None
    anchor_ids: List[str] = []
    existing_ids = {(day["day"], anchor["kind"], (anchor.get("place") or {}).get("poi_id")): anchor["anchor_id"]
                    for day in days for anchor in day.get("anchors", [])}

    def add_anchor(day: MutableMapping[str, Any], kind: str, place: Mapping[str, Any], label: str) -> None:
        anchors = day.setdefault("anchors", [])
        if not isinstance(anchors, list):
            raise TripEditError("INVALID_PLAN", "document day anchors must be a list")
        anchor_id = existing_ids.get((day["day"], kind, place["poi_id"])) or _v3_anchor_id(document.get("plan_id"), kind, int(day["day"]), str(place["poi_id"]))
        anchors.append({
            "anchor_id": anchor_id,
            "kind": kind,
            "day": day["day"],
            "place": deepcopy(dict(place)),
            "display_label": label,
        })
        anchor_ids.append(anchor_id)

    for index, day in enumerate(days):
        day["anchors"] = []
        activities = day.get("activities")
        if not isinstance(activities, list) or not activities:
            # An arrival/departure-only day still needs its real city transfer.
            # Empty middle days must not create a lodging-to-itself route.
            has_empty_day_transfer = has_lodging and (
                (index == 0 and arrival_hub is not None)
                or (index == len(days) - 1 and departure_hub is not None)
            )
            if not has_empty_day_transfer:
                continue
        if index == 0 and arrival_hub:
            add_anchor(day, "arrival_hub", arrival_hub, f"从{arrival_hub['name']}抵达")
        elif has_lodging and planning_lodging:
            add_anchor(day, "lodging_departure", planning_lodging, "从规划住宿点出发")
        if index == len(days) - 1:
            if departure_hub:
                add_anchor(day, "departure_hub", departure_hub, f"前往{departure_hub['name']}离开")
        elif has_lodging and planning_lodging:
            add_anchor(day, "lodging_return", planning_lodging, "返回规划住宿点")

    map_guidance = document.get("map_guidance")
    if not isinstance(map_guidance, MutableMapping):
        raise TripEditError("INVALID_PLAN", "document.map_guidance must be a mapping")
    map_guidance["formal_location_ids"] = [
        activity.get("activity_id")
        for day in days
        for activity in day.get("activities", [])
        if isinstance(activity, Mapping) and activity.get("place") is not None
    ] + anchor_ids


def _v3_mark_routes_for_revalidation(
    document: MutableMapping[str, Any], affected_days: Sequence[int]
) -> None:
    """Discard stale geometry rather than implying a route survived an itinerary edit."""
    affected = set(affected_days)
    for day in _v3_days(document):
        if day.get("day") not in affected:
            continue
        for activity in day.get("activities", []):
            if isinstance(activity, MutableMapping):
                activity["route_to_next"] = None

    map_guidance = document.get("map_guidance")
    if not isinstance(map_guidance, MutableMapping):
        raise TripEditError("INVALID_PLAN", "document.map_guidance must be a mapping")
    existing_routes = map_guidance.get("day_routes")
    remaining_routes = [
        route for route in existing_routes
        if isinstance(route, Mapping) and route.get("day") not in affected
    ] if isinstance(existing_routes, list) else []
    map_guidance["day_routes"] = sorted(
        [*remaining_routes, *({"day": day, "status": "unavailable", "legs": []} for day in affected)],
        key=lambda route: route["day"],
    )
    map_guidance["status"] = "degraded"
    map_guidance["status_reason"] = "日程已编辑，相关真实路线待重新核验"
    document["status"] = "degraded"
    validation = document.get("validation")
    if not isinstance(validation, MutableMapping):
        raise TripEditError("INVALID_PLAN", "document.validation must be a mapping")
    validation["degraded"] = True
    issues = validation.setdefault("issues", [])
    if not isinstance(issues, list):
        raise TripEditError("INVALID_PLAN", "document.validation.issues must be a list")
    if not any(
        isinstance(issue, Mapping) and issue.get("code") == _ROUTE_REVALIDATION_ISSUE_CODE
        for issue in issues
    ):
        issues.append(
            {
                "code": _ROUTE_REVALIDATION_ISSUE_CODE,
                "message": "日程调整后，相关真实路线待重新核验。",
                "severity": "warning",
            }
        )


def _v3_route_segment_count(document: Mapping[str, Any], day_number: int) -> int:
    """Count the persisted route segments that must be discarded for one itinerary day."""
    segment_ids: set[Tuple[str, str]] = set()
    for day in _v3_days(document):
        if day.get("day") != day_number:
            continue
        for activity in day.get("activities", []):
            route = activity.get("route_to_next") if isinstance(activity, Mapping) else None
            if isinstance(route, Mapping):
                from_id, to_id = route.get("from_id"), route.get("to_id")
                if isinstance(from_id, str) and isinstance(to_id, str):
                    segment_ids.add((from_id, to_id))

    map_guidance = document.get("map_guidance")
    day_routes = map_guidance.get("day_routes") if isinstance(map_guidance, Mapping) else []
    if isinstance(day_routes, list):
        for route in day_routes:
            if not isinstance(route, Mapping) or route.get("day") != day_number:
                continue
            for leg in route.get("legs", []):
                if not isinstance(leg, Mapping):
                    continue
                from_id, to_id = leg.get("from_id"), leg.get("to_id")
                if isinstance(from_id, str) and isinstance(to_id, str):
                    segment_ids.add((from_id, to_id))
    return len(segment_ids)


def get_activity_delete_impact(plan: Any, activity_id: Any) -> Dict[str, Any]:
    """Return the server-authoritative cascade preview for deleting one scheduled V3 activity."""
    document = _as_dict(plan, "document")
    if not _is_v3_document(document):
        raise TripEditError("INVALID_PLAN", "delete impact is only available for V3 documents")
    try:
        validated_document = TravelPlanDocumentV3.model_validate(document, context={"draft": True}).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise TripEditError("INVALID_PLAN", f"invalid V3 document: {exc}") from None

    day, _, activity = _v3_find_activity(validated_document, activity_id)
    deleted_note_id = activity.get("note_id")
    notes = validated_document.get("notes", [])
    note_count = sum(
        1
        for note in notes
        if isinstance(note, Mapping)
        and (
            note.get("note_id") == deleted_note_id
            or (note.get("scope") == "activity" and note.get("target_id") == activity_id)
        )
    )
    action_items = validated_document.get("action_items", [])
    related_actions = [
        item
        for item in action_items
        if isinstance(item, Mapping)
        and item.get("scope") == "activity"
        and item.get("target_id") == activity_id
    ]
    images = activity.get("images", [])
    image_count = len(images) if isinstance(images, list) else 0
    map_guidance = validated_document.get("map_guidance", {})
    formal_location_ids = (
        map_guidance.get("formal_location_ids", []) if isinstance(map_guidance, Mapping) else []
    )
    marker_count = int(isinstance(formal_location_ids, list) and activity_id in formal_location_ids)
    reminder_count = sum(item.get("kind") == "reservation_reminder" for item in related_actions)
    checklist_count = sum(item.get("kind") == "checklist" for item in related_actions)
    route_segment_count = _v3_route_segment_count(validated_document, int(day["day"]))
    return {
        "activity_id": activity_id,
        "activity_title": activity.get("title") or "该活动",
        "day": int(day["day"]),
        "notes": note_count,
        "reservation_reminders": reminder_count,
        "checklist_items": checklist_count,
        "image_references": image_count,
        "route_segments": route_segment_count,
        "map_markers": marker_count,
        "total_related_items": note_count + reminder_count + checklist_count + image_count + route_segment_count + marker_count,
    }


def _v3_activity_coordinates(activity: Any) -> Tuple[float, float] | None:
    place = activity.get("place") if isinstance(activity, Mapping) else None
    if not _v3_is_mappable_place(place):
        return None
    coordinates = place.get("coordinates") if isinstance(place, Mapping) else None
    if not isinstance(coordinates, Mapping):
        return None
    try:
        latitude = float(coordinates.get("latitude"))
        longitude = float(coordinates.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    return latitude, longitude


def _v3_distance_meters(
    left: Tuple[float, float], right: Tuple[float, float]
) -> float:
    latitude_delta = math.radians(right[0] - left[0])
    longitude_delta = math.radians(right[1] - left[1])
    latitude_left = math.radians(left[0])
    latitude_right = math.radians(right[0])
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_left) * math.cos(latitude_right) * math.sin(longitude_delta / 2) ** 2
    )
    return 6_371_000 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _v3_nearest_activity_order(
    activities: Sequence[MutableMapping[str, Any]], start_activity: Any = None
) -> List[MutableMapping[str, Any]]:
    """Use a deterministic nearest-neighbour route only when every place has verified coordinates."""
    if len(activities) < 2:
        return list(activities)
    coordinates = [_v3_activity_coordinates(activity) for activity in activities]
    if any(point is None for point in coordinates):
        return list(activities)
    typed_coordinates = [point for point in coordinates if point is not None]
    start_coordinates = _v3_activity_coordinates(start_activity)
    starting_indices = (
        [min(range(len(activities)), key=lambda index: (_v3_distance_meters(start_coordinates, typed_coordinates[index]), index))]
        if start_coordinates is not None
        else list(range(len(activities)))
    )
    candidates: List[Tuple[float, Tuple[int, ...], List[int]]] = []
    for starting_index in starting_indices:
        remaining = set(range(len(activities)))
        order = [starting_index]
        remaining.remove(starting_index)
        total_distance = (
            _v3_distance_meters(start_coordinates, typed_coordinates[starting_index])
            if start_coordinates is not None else 0.0
        )
        current_index = starting_index
        while remaining:
            next_index = min(
                remaining,
                key=lambda index: (
                    _v3_distance_meters(typed_coordinates[current_index], typed_coordinates[index]),
                    index,
                ),
            )
            total_distance += _v3_distance_meters(typed_coordinates[current_index], typed_coordinates[next_index])
            order.append(next_index)
            remaining.remove(next_index)
            current_index = next_index
        candidates.append((total_distance, tuple(order), order))
    return [activities[index] for _, _, order in [min(candidates)] for index in order]


def _v3_time_minutes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, _TIME_FORMAT)
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def _v3_time_text(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _v3_reschedule_flexible_block(
    activities: Sequence[MutableMapping[str, Any]],
    previous_fixed: Mapping[str, Any] | None,
    next_fixed: Mapping[str, Any] | None,
) -> Dict[str, Tuple[str, str]]:
    """Keep each activity's duration while fitting a flexible block between fixed-time bounds."""
    if not activities:
        return {}
    time_ranges = [
        (_v3_time_minutes(activity.get("start_at")), _v3_time_minutes(activity.get("end_at")))
        for activity in activities
    ]
    if any(start is None or end is None or end <= start for start, end in time_ranges):
        return {}
    starts = [start for start, _ in time_ranges if start is not None]
    ends = [end for _, end in time_ranges if end is not None]
    previous_end = _v3_time_minutes(previous_fixed.get("end_at")) if previous_fixed else None
    next_start = _v3_time_minutes(next_fixed.get("start_at")) if next_fixed else None
    window_start = previous_end if previous_end is not None else min(starts)
    window_end = next_start if next_start is not None else max(ends)
    durations = {
        str(activity["activity_id"]): end - start
        for activity, (start, end) in zip(activities, time_ranges)
        if start is not None and end is not None
    }
    if window_start + sum(durations.values()) > window_end:
        return {}
    cursor = window_start
    updates: Dict[str, Tuple[str, str]] = {}
    for activity in activities:
        activity_id = str(activity["activity_id"])
        end = cursor + durations[activity_id]
        updates[activity_id] = (_v3_time_text(cursor), _v3_time_text(end))
        cursor = end
    return updates


def _build_v3_day_route_optimization(
    document: MutableMapping[str, Any], day_number: int
) -> Dict[str, Any]:
    day = _v3_find_day(document, day_number)
    activities = day.get("activities", [])
    if not isinstance(activities, list) or any(not isinstance(item, MutableMapping) for item in activities):
        raise TripEditError("INVALID_PLAN", "document day activities must be mappings")
    original_activities = list(activities)
    fixed_activity_count = sum(bool(activity.get("fixed_time")) for activity in original_activities)
    if len(original_activities) < 2:
        return {
            "day": day_number,
            "can_optimize": False,
            "reason": "当天活动不足两项，无需优化路线。",
            "current_order": [
                {"activity_id": item["activity_id"], "title": item.get("title") or "活动", "fixed_time": bool(item.get("fixed_time"))}
                for item in original_activities
            ],
            "optimized_order": [
                {"activity_id": item["activity_id"], "title": item.get("title") or "活动", "fixed_time": bool(item.get("fixed_time"))}
                for item in original_activities
            ],
            "fixed_activity_count": fixed_activity_count,
            "order_changes": [],
            "time_changes": [],
            "route_segments": _v3_route_segment_count(document, day_number),
        }

    optimized_activities: List[MutableMapping[str, Any]] = []
    time_updates: Dict[str, Tuple[str, str]] = {}
    index = 0
    while index < len(original_activities):
        if original_activities[index].get("fixed_time"):
            optimized_activities.append(original_activities[index])
            index += 1
            continue
        block_start = index
        while index < len(original_activities) and not original_activities[index].get("fixed_time"):
            index += 1
        flexible_block = original_activities[block_start:index]
        previous_fixed = original_activities[block_start - 1] if block_start > 0 and original_activities[block_start - 1].get("fixed_time") else None
        next_fixed = original_activities[index] if index < len(original_activities) and original_activities[index].get("fixed_time") else None
        start_activity = previous_fixed
        if start_activity is None:
            anchors = day.get("anchors", [])
            start_activity = anchors[0] if isinstance(anchors, list) and anchors else None
        optimized_block = _v3_nearest_activity_order(flexible_block, start_activity)
        optimized_activities.extend(optimized_block)
        time_updates.update(_v3_reschedule_flexible_block(optimized_block, previous_fixed, next_fixed))

    order_changes = [
        {
            "activity_id": activity["activity_id"],
            "title": activity.get("title") or "活动",
            "before_order": before_index + 1,
            "after_order": after_index + 1,
        }
        for after_index, activity in enumerate(optimized_activities)
        for before_index, original in enumerate(original_activities)
        if original.get("activity_id") == activity.get("activity_id") and before_index != after_index
    ]
    time_changes = [
        {
            "activity_id": activity["activity_id"],
            "title": activity.get("title") or "活动",
            "before": {"start_at": activity.get("start_at"), "end_at": activity.get("end_at")},
            "after": {"start_at": update[0], "end_at": update[1]},
        }
        for activity in optimized_activities
        if not activity.get("fixed_time")
        for update in [time_updates.get(str(activity["activity_id"]))]
        if update is not None and (activity.get("start_at"), activity.get("end_at")) != update
    ]
    current_order = [
        {"activity_id": item["activity_id"], "title": item.get("title") or "活动", "fixed_time": bool(item.get("fixed_time"))}
        for item in original_activities
    ]
    optimized_order = [
        {"activity_id": item["activity_id"], "title": item.get("title") or "活动", "fixed_time": bool(item.get("fixed_time"))}
        for item in optimized_activities
    ]
    can_optimize = bool(order_changes or time_changes)
    return {
        "day": day_number,
        "can_optimize": can_optimize,
        "reason": None if can_optimize else "当前顺序和非固定时间已无可优化空间。",
        "current_order": current_order,
        "optimized_order": optimized_order,
        "fixed_activity_count": fixed_activity_count,
        "order_changes": order_changes,
        "time_changes": time_changes,
        "route_segments": _v3_route_segment_count(document, day_number),
        "time_updates": time_updates,
    }


def get_day_route_optimization_preview(plan: Any, day_number: Any) -> Dict[str, Any]:
    """Preview a same-day optimization without mutating the draft document."""
    document = _as_dict(plan, "document")
    if not _is_v3_document(document):
        raise TripEditError("INVALID_PLAN", "route optimization is only available for V3 documents")
    try:
        validated_document = TravelPlanDocumentV3.model_validate(document, context={"draft": True}).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise TripEditError("INVALID_PLAN", f"invalid V3 document: {exc}") from None
    parsed_day = _require_day({"day": day_number}, "day")
    validation = validate_trip_draft(validated_document)
    if not validation["can_apply"]:
        raise TripEditError("ROUTE_OPTIMIZATION_BLOCKED", "resolve hard draft errors before optimizing a route")
    preview = _build_v3_day_route_optimization(validated_document, parsed_day)
    preview.pop("time_updates", None)
    return preview


def _empty_v3_diff() -> Dict[str, Any]:
    return {
        "affected_days": [],
        "candidate_changes": [],
        "time_changes": [],
        "fixed_time_changes": [],
        "note_changes": [],
        "transport_changes": [],
        "lodging_changes": [],
        "delete_impact": None,
        "route_optimization": None,
        "routes_revalidation_required": False,
    }


def _apply_v3_trip_edit(plan: Mapping[str, Any], operation: Mapping[str, Any]) -> Dict[str, Any]:
    _validate_v3_identity(plan, operation)
    operation_type = operation.get("type")
    if operation_type not in V3_SUPPORTED_OPERATIONS:
        raise TripEditError("UNSUPPORTED_OPERATION", f"unsupported V3 operation: {operation_type}")
    payload = operation.get("payload", {})
    if not isinstance(payload, Mapping):
        raise TripEditError("INVALID_PAYLOAD", "operation.payload must be a mapping")

    try:
        original_document = TravelPlanDocumentV3.model_validate(plan, context={"draft": True}).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise TripEditError("INVALID_PLAN", f"invalid V3 document: {exc}") from None
    document = deepcopy(original_document)
    diff = _empty_v3_diff()
    affected_days: List[int] = []
    refresh_anchors = False
    invalidate_routes = False

    if operation_type in {"add_activity", "replace_activity"}:
        from services.editable_place_service import resolve_place
        try:
            place = resolve_place(payload.get("selection_id"), document["intent"]["destination"])
        except ValueError as error:
            raise TripEditError("PLACE_SELECTION_INVALID", str(error)) from None
        if operation_type == "replace_activity":
            day, _, activity = _v3_find_activity(document, _require_non_empty(payload, "activity_id"))
            activity.update(place=place, title=place["name"], kind=place["category"], images=[], cover_image_id=None, estimated_cost=None, official_traveler_prices={}, reservation=None, evidence_refs=[], route_to_next=None)
            if place["category"] != "food":
                activity["meal_type"] = None
            affected_days = [int(day["day"])]
        else:
            target_day = _v3_find_day(document, _require_day(payload, "day"))
            start = _v3_parse_time(payload.get("start_at"), "start_at")
            end = _v3_parse_time(payload.get("end_at"), "end_at")
            if end <= start or start.minute % 15 or end.minute % 15:
                raise TripEditError("INVALID_PAYLOAD", "新增活动时间必须为 15 分钟粒度且结束晚于开始")
            activity = {"activity_id": f"act_{sha1(str(operation['operation_id']).encode()).hexdigest()[:20]}",
                "kind": place["category"], "title": place["name"], "day": target_day["day"], "order": len(target_day["activities"]) + 1,
                "start_at": payload["start_at"], "end_at": payload["end_at"], "duration_minutes": int((end-start).total_seconds()/60),
                "fixed_time": False, "place": place, "images": [], "evidence_refs": [], "estimated_cost": None}
            target_day["activities"].append(activity)
            target_day["activities"].sort(key=lambda item: item.get("start_at") or "23:59")
            _v3_normalize_day_orders(target_day)
            affected_days = [int(target_day["day"])]
        refresh_anchors = invalidate_routes = True

    elif operation_type == "update_activity_time":
        day, _, activity = _v3_find_activity(document, _require_non_empty(payload, "activity_id"))
        start = _v3_parse_time(payload.get("start_at"), "start_at")
        end = _v3_parse_time(payload.get("end_at"), "end_at")
        if end <= start or start.minute % 15 or end.minute % 15:
            raise TripEditError("INVALID_PAYLOAD", "时间必须为 15 分钟粒度且结束晚于开始")
        before = {"start_at": activity.get("start_at"), "end_at": activity.get("end_at")}
        activity.update(start_at=payload["start_at"], end_at=payload["end_at"], duration_minutes=int((end-start).total_seconds()/60))
        diff["time_changes"].append({"activity_id": activity["activity_id"], "before": before, "after": {"start_at": activity["start_at"], "end_at": activity["end_at"]}})
        day["activities"].sort(key=lambda item: item.get("start_at") or "23:59")
        _v3_normalize_day_orders(day)
        affected_days = [int(day["day"])]
        invalidate_routes = True

    elif operation_type in {"update_trip_note", "update_day_note"}:
        scope = "trip" if operation_type == "update_trip_note" else "day"
        target_day = _v3_find_day(document, _require_day(payload, "day")) if scope == "day" else None
        target_id = str(target_day["day"]) if target_day is not None else None
        content = payload.get("content")
        if not isinstance(content, str) or len(content) > 2000:
            raise TripEditError("INVALID_PAYLOAD", "备注最多 2000 字")
        note_key = "{}:{}:{}".format(document["plan_id"], scope, target_id)
        note_id = f"note_{sha1(note_key.encode()).hexdigest()[:20]}"
        previous = next((note for note in document["notes"] if note["scope"] == scope and note.get("target_id") == target_id), None)
        document["notes"] = [note for note in document["notes"] if not (note["scope"] == scope and note.get("target_id") == target_id)]
        if content.strip():
            document["notes"].append({"note_id": note_id, "scope": scope, "target_id": target_id, "content": content.strip(), "updated_at": datetime.now(timezone.utc).isoformat()})
        if target_day is not None:
            target_day["note_id"] = note_id if content.strip() else None
            affected_days = [int(target_day["day"])]
        diff["note_changes"].append({"scope": scope, "target_id": target_id, "before": previous.get("content") if previous else "", "after": content.strip()})

    elif operation_type == "delete_activity":
        if payload.get("confirmed") is not True:
            raise TripEditError("DELETE_CONFIRMATION_REQUIRED", "payload.confirmed must be true for permanent deletion")
        activity_id = _require_non_empty(payload, "activity_id")
        delete_impact = get_activity_delete_impact(document, activity_id)
        source_day, activity_index, activity = _v3_find_activity(document, activity_id)
        source_day_number = int(source_day["day"])
        deleted_note_id = activity.get("note_id")
        source_day["activities"].pop(activity_index)
        _v3_normalize_day_orders(source_day)
        document["notes"] = [
            note
            for note in document.get("notes", [])
            if not (
                isinstance(note, Mapping)
                and (
                    note.get("note_id") == deleted_note_id
                    or (note.get("scope") == "activity" and note.get("target_id") == activity_id)
                )
            )
        ]
        document["action_items"] = [
            item
            for item in document.get("action_items", [])
            if not (
                isinstance(item, Mapping)
                and item.get("scope") == "activity"
                and item.get("target_id") == activity_id
            )
        ]
        affected_days = [source_day_number]
        diff["delete_impact"] = delete_impact
        diff["candidate_changes"].append(
            {"activity_id": activity_id, "action": "permanently_deleted", "from_day": source_day_number}
        )
        refresh_anchors = True
        invalidate_routes = True

    elif operation_type == "optimize_day_route":
        if payload.get("confirmed") is not True:
            raise TripEditError("ROUTE_OPTIMIZATION_CONFIRMATION_REQUIRED", "payload.confirmed must be true for route optimization")
        target_day_number = _require_day(payload, "day")
        validation = validate_trip_draft(document)
        if not validation["can_apply"]:
            raise TripEditError("ROUTE_OPTIMIZATION_BLOCKED", "resolve hard draft errors before optimizing a route")
        optimization = _build_v3_day_route_optimization(document, target_day_number)
        if not optimization["can_optimize"]:
            raise TripEditError("ROUTE_OPTIMIZATION_UNAVAILABLE", optimization["reason"])
        target_day = _v3_find_day(document, target_day_number)
        activity_by_id = {
            str(activity["activity_id"]): activity
            for activity in target_day["activities"]
            if isinstance(activity, MutableMapping)
        }
        target_day["activities"] = [
            activity_by_id[str(item["activity_id"])]
            for item in optimization["optimized_order"]
        ]
        for activity in target_day["activities"]:
            update = optimization["time_updates"].get(str(activity["activity_id"]))
            if update and not activity.get("fixed_time"):
                activity["start_at"], activity["end_at"] = update
        _v3_normalize_day_orders(target_day)
        optimization.pop("time_updates", None)
        affected_days = [target_day_number]
        diff["route_optimization"] = optimization
        refresh_anchors = True
        invalidate_routes = True

    elif operation_type == "move_activity":
        activity_id = _require_non_empty(payload, "activity_id")
        target_day_number = _require_day(payload, "target_day", "to_day")
        source_day, activity_index, _ = _v3_find_activity(document, activity_id)
        target_day = _v3_find_day(document, target_day_number)
        moved = deepcopy(source_day["activities"].pop(activity_index))
        _v3_normalize_day_orders(source_day)
        _v3_insert_activity(target_day, moved, payload.get("position"))
        source_day_number = int(source_day["day"])
        affected_days = [source_day_number, target_day_number]
        diff["candidate_changes"].append(
            {"activity_id": activity_id, "action": "moved", "from_day": source_day_number, "to_day": target_day_number}
        )
        refresh_anchors = True
        invalidate_routes = True

    elif operation_type == "move_activity_to_candidate":
        activity_id = _require_non_empty(payload, "activity_id")
        candidate_pool = document.get("candidate_pool")
        if not isinstance(candidate_pool, list):
            raise TripEditError("INVALID_PLAN", "document.candidate_pool must be a list")
        if len(candidate_pool) >= 15:
            raise TripEditError("CANDIDATE_POOL_FULL", "candidate_pool cannot contain more than 15 activities")
        source_day, activity_index, _ = _v3_find_activity(document, activity_id)
        moved = deepcopy(source_day["activities"].pop(activity_index))
        source_day_number = int(source_day["day"])
        _v3_normalize_day_orders(source_day)
        moved.update({
            "day": None,
            "order": None,
            "start_at": None,
            "end_at": None,
            "fixed_time": False,
            "route_to_next": None,
            "insertion_options": [],
            "insertion_unavailable_reason": None,
        })
        candidate_pool.append(moved)
        affected_days = [source_day_number]
        diff["candidate_changes"].append(
            {"activity_id": activity_id, "action": "moved_to_candidate", "from_day": source_day_number}
        )
        refresh_anchors = True
        invalidate_routes = True

    elif operation_type == "insert_candidate":
        activity_id = _require_non_empty(payload, "activity_id")
        target_day_number = _require_day(payload, "target_day", "to_day")
        candidate_index, candidate = _v3_find_candidate(document, activity_id)
        insertion_option = select_candidate_insertion_option(document, candidate, target_day_number, payload.get("position"))
        if insertion_option is None:
            raise TripEditError(
                "CANDIDATE_NO_FEASIBLE_SLOT",
                candidate.get("insertion_unavailable_reason") or "目标日期没有可行时间空档",
            )
        candidate_pool = document["candidate_pool"]
        scheduled = deepcopy(candidate_pool.pop(candidate_index))
        scheduled.update({
            "day": target_day_number,
            "order": None,
            "start_at": insertion_option["start_at"],
            "end_at": insertion_option["end_at"],
            "fixed_time": False,
            "route_to_next": None,
            "insertion_options": [],
            "insertion_unavailable_reason": None,
        })
        target_day = _v3_find_day(document, target_day_number)
        _v3_insert_activity(target_day, scheduled, insertion_option["position"])
        affected_days = [target_day_number]
        diff["candidate_changes"].append(
            {"activity_id": activity_id, "action": "inserted", "to_day": target_day_number}
        )
        refresh_anchors = True
        invalidate_routes = True

    elif operation_type == "shift_activity_time":
        activity_id = _require_non_empty(payload, "activity_id")
        delta = payload.get("delta_minutes")
        if isinstance(delta, bool) or delta not in {-15, 15}:
            raise TripEditError("INVALID_PAYLOAD", "payload.delta_minutes must be exactly -15 or 15")
        day, _, activity = _v3_find_activity(document, activity_id)
        start = _v3_parse_time(activity.get("start_at"), "activity.start_at")
        end = _v3_parse_time(activity.get("end_at"), "activity.end_at")
        next_start = start + timedelta(minutes=delta)
        next_end = end + timedelta(minutes=delta)
        if next_start.date() != start.date() or next_end.date() != end.date() or next_end <= next_start:
            raise TripEditError("TIME_OUT_OF_RANGE", "15-minute adjustment must remain within the same day")
        before = {"start_at": activity.get("start_at"), "end_at": activity.get("end_at")}
        activity["start_at"] = _v3_format_time(next_start)
        activity["end_at"] = _v3_format_time(next_end)
        affected_days = [int(day["day"])]
        diff["time_changes"].append(
            {"activity_id": activity_id, "day": day["day"], "before": before, "after": {"start_at": activity["start_at"], "end_at": activity["end_at"]}}
        )

    elif operation_type == "set_activity_fixed_time":
        activity_id = _require_non_empty(payload, "activity_id")
        fixed_time = payload.get("fixed_time")
        if not isinstance(fixed_time, bool):
            raise TripEditError("INVALID_PAYLOAD", "payload.fixed_time must be a boolean")
        day, _, activity = _v3_find_activity(document, activity_id)
        if fixed_time and not activity.get("start_at"):
            raise TripEditError("TIME_NOT_SET", "an activity needs a start time before it can be fixed")
        before = bool(activity.get("fixed_time"))
        activity["fixed_time"] = fixed_time
        affected_days = [int(day["day"])]
        diff["fixed_time_changes"].append(
            {"activity_id": activity_id, "day": day["day"], "before": before, "after": fixed_time}
        )

    elif operation_type == "update_activity_note":
        activity_id = _require_non_empty(payload, "activity_id")
        content = payload.get("content")
        if not isinstance(content, str):
            raise TripEditError("INVALID_PAYLOAD", "payload.content must be a string")
        content = content.strip()
        if len(content) > 500:
            raise TripEditError("INVALID_PAYLOAD", "payload.content must contain at most 500 characters")
        day, _, activity = _v3_find_activity(document, activity_id)
        notes = document.get("notes")
        if not isinstance(notes, list):
            raise TripEditError("INVALID_PLAN", "document.notes must be a list")
        note_id = activity.get("note_id")
        note_index = next(
            (index for index, note in enumerate(notes) if isinstance(note, Mapping) and note.get("note_id") == note_id),
            None,
        )
        previous_content = notes[note_index].get("content") if note_index is not None else ""
        if not content:
            if note_index is not None:
                notes.pop(note_index)
            activity["note_id"] = None
            action = "cleared"
        elif note_index is not None:
            notes[note_index] = {
                **dict(notes[note_index]),
                "scope": "activity",
                "target_id": activity_id,
                "content": content,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            action = "updated"
        else:
            note_id = _v3_note_id(document, str(activity_id))
            notes.append({
                "note_id": note_id,
                "scope": "activity",
                "target_id": activity_id,
                "content": content,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            activity["note_id"] = note_id
            action = "created"
        affected_days = [int(day["day"])]
        diff["note_changes"].append(
            {"activity_id": activity_id, "day": day["day"], "action": action, "before": previous_content, "after": content}
        )

    elif operation_type == "select_transport_option":
        direction = payload.get("direction")
        if direction not in {"outbound", "return"}:
            raise TripEditError("INVALID_PAYLOAD", "payload.direction must be outbound or return")
        option_id = _require_non_empty(payload, "option_id")
        if document.get("intent", {}).get("date_mode") == "flexible":
            raise TripEditError("TRANSPORT_OPTION_UNAVAILABLE", "flexible-date trips cannot select a concrete transport option")
        section_key = "outbound_transport" if direction == "outbound" else "return_transport"
        section = document.get(section_key)
        if not isinstance(section, MutableMapping) or section.get("status") != "ready":
            raise TripEditError("TRANSPORT_OPTION_UNAVAILABLE", "transport options are not verified and ready")
        option_ids = {
            option.get("option_id") for option in section.get("options", []) if isinstance(option, Mapping)
        }
        if option_id not in option_ids:
            raise TripEditError("TRANSPORT_OPTION_NOT_FOUND", "payload.option_id must reference an existing transport option")
        previous_option_id = section.get("selected_option_id")
        section["selected_option_id"] = option_id
        affected_days = [1] if direction == "outbound" else [int(document["intent"]["days"])]
        diff["transport_changes"].append(
            {"direction": direction, "before": previous_option_id, "after": option_id}
        )
        refresh_anchors = True

    elif operation_type == "select_lodging_option":
        lodging_id = _require_non_empty(payload, "lodging_id")
        lodging_plan = document.get("lodging_plan")
        if not isinstance(lodging_plan, MutableMapping):
            raise TripEditError("INVALID_PLAN", "document.lodging_plan must be a mapping")
        lodging_ids = {
            option.get("lodging_id") for option in lodging_plan.get("options", []) if isinstance(option, Mapping)
        }
        if lodging_id not in lodging_ids:
            raise TripEditError("LODGING_OPTION_NOT_FOUND", "payload.lodging_id must reference an existing lodging option")
        previous_lodging_id = lodging_plan.get("planning_lodging_id")
        lodging_plan["planning_lodging_id"] = lodging_id
        affected_days = [int(day["day"]) for day in _v3_days(document)]
        diff["lodging_changes"].append({"before": previous_lodging_id, "after": lodging_id})
        refresh_anchors = True

    if refresh_anchors:
        invalidate_routes = True
        try:
            _v3_refresh_anchors(document)
            for old_day, new_day in zip(_v3_days(original_document), _v3_days(document)):
                if old_day.get("anchors") != new_day.get("anchors"):
                    affected_days.append(int(new_day["day"]))
        except (ValidationError, TypeError, ValueError) as exc:
            raise TripEditError("INVALID_PLAN", f"could not refresh V3 anchors: {exc}") from None
    if invalidate_routes:
        _v3_mark_routes_for_revalidation(document, affected_days)
        diff["routes_revalidation_required"] = True

    from services.formal_consistency_service import recalculate_formal_budget
    recalculate_formal_budget(document)
    refresh_candidate_schedule_options(document)

    document["revision"] = int(original_document["revision"]) + 1
    try:
        updated_document = TravelPlanDocumentV3.model_validate(document, context={"draft": True}).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise TripEditError("INVALID_PLAN", f"operation violates the V3 document contract: {exc}") from None
    diff["affected_days"] = sorted(set(affected_days))
    return {
        "operation_id": operation.get("operation_id"),
        "plan": updated_document,
        "previous_plan": original_document,
        "document": updated_document,
        "previous_document": original_document,
        "diff": diff,
        "draft_validation": validate_trip_draft(updated_document),
    }


def _as_dict(value: Any, label: str) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise TripEditError("INVALID_INPUT", f"{label} must be a mapping or Pydantic model")
    return deepcopy(dict(value))


def _require_non_empty(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise TripEditError("INVALID_PAYLOAD", f"payload.{key} is required")
    return value


def _require_day(payload: Mapping[str, Any], *keys: str) -> int:
    value = next((payload.get(key) for key in keys if payload.get(key) is not None), None)
    if isinstance(value, bool):
        value = None
    try:
        day = int(value)
    except (TypeError, ValueError):
        raise TripEditError("INVALID_PAYLOAD", f"payload.{keys[0]} must be a positive integer")
    if day < 1:
        raise TripEditError("INVALID_PAYLOAD", f"payload.{keys[0]} must be a positive integer")
    return day


def _activity_id(activity: Mapping[str, Any]) -> Any:
    return activity.get("activity_id", activity.get("id"))


def _find_activity(activities: Sequence[Mapping[str, Any]], activity_id: Any) -> int:
    for index, activity in enumerate(activities):
        if _activity_id(activity) == activity_id:
            return index
    raise TripEditError("ACTIVITY_NOT_FOUND", f"activity {activity_id!r} was not found")


def _insert_for_day(
    activities: List[Dict[str, Any]], activity: Dict[str, Any], day: int, position: Any
) -> None:
    day_indices = [index for index, item in enumerate(activities) if item.get("day") == day]
    if position is None:
        insert_at = day_indices[-1] + 1 if day_indices else len(activities)
    else:
        try:
            day_position = int(position)
        except (TypeError, ValueError):
            raise TripEditError("INVALID_PAYLOAD", "payload.position must be a non-negative integer")
        if day_position < 0:
            raise TripEditError("INVALID_PAYLOAD", "payload.position must be a non-negative integer")
        insert_at = (
            day_indices[day_position]
            if day_position < len(day_indices)
            else (day_indices[-1] + 1 if day_indices else len(activities))
        )
    activities.insert(insert_at, activity)


def _estimated_budget(plan: Mapping[str, Any]) -> float:
    total = 0.0
    for activity in plan.get("activities", []):
        value = activity.get("estimated_cost") if isinstance(activity, Mapping) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _materialize_flat_activities(plan: MutableMapping[str, Any]) -> None:
    activities = plan.get("activities")
    if isinstance(activities, list) and activities:
        return

    day_collections = []
    if isinstance(plan.get("days"), list):
        day_collections.append(plan["days"])
    if isinstance(plan.get("trip_days"), list):
        day_collections.append(plan["trip_days"])
    if not day_collections:
        if activities is None:
            plan["activities"] = []
        return

    flattened: List[Dict[str, Any]] = []
    for day_entry in day_collections[0]:
        if not isinstance(day_entry, Mapping):
            raise TripEditError("INVALID_PLAN", "plan days must contain mappings")
        day = _require_day(day_entry, "day")
        day_activities = day_entry.get("activities", [])
        if not isinstance(day_activities, list):
            raise TripEditError("INVALID_PLAN", "day activities must be a list")
        for activity in day_activities:
            item = _as_dict(activity, "day activity")
            item["day"] = day
            flattened.append(item)
    plan["activities"] = flattened


def _validation(plan: Mapping[str, Any]) -> Any:
    try:
        model = TripPlan.model_validate(plan)
    except (ValidationError, TypeError, ValueError):
        return None
    return model, validate_trip_plan(model)


def _issue_codes(validation: Any) -> set[str]:
    if validation is None:
        return set()
    return {issue.code for issue in validation.issues}


def _repair_new_local_issues_once(
    plan: MutableMapping[str, Any],
    affected_days: Sequence[int],
    before_codes: set[str],
) -> None:
    validated = _validation(plan)
    if validated is None:
        return
    model, validation = validated
    repairable_issues = [
        issue
        for issue in validation.issues
        if issue.code not in before_codes and issue.code in LOCAL_REPAIRABLE_ISSUE_CODES
    ]
    if not repairable_issues:
        return

    affected = set(affected_days)
    scoped_plan = model.model_copy(deep=True)
    scoped_plan.activities = [
        activity.model_copy(deep=True)
        for activity in model.activities
        if activity.day in affected
    ]
    scoped_validation = TripValidationResult(
        valid=not any(issue.severity == "error" for issue in repairable_issues),
        issues=repairable_issues,
    )
    repair = repair_trip_plan_once(scoped_plan, scoped_validation)
    if not repair.repaired or any(activity.day not in affected for activity in repair.plan.activities):
        return

    repaired_by_id = {
        activity.activity_id: activity.model_dump(mode="json")
        for activity in repair.plan.activities
    }
    current_ids = {
        _activity_id(activity)
        for activity in plan.get("activities", [])
        if isinstance(activity, Mapping) and activity.get("day") in affected
    }
    if set(repaired_by_id) != current_ids:
        return

    plan["activities"] = [
        repaired_by_id.get(_activity_id(activity), activity)
        if activity.get("day") in affected
        else activity
        for activity in plan["activities"]
    ]


def _mark_affected_routes_estimated(
    plan: MutableMapping[str, Any], affected_days: Sequence[int]
) -> None:
    affected = set(affected_days)
    calculated_at = datetime.now(timezone.utc).isoformat()
    grouped: Dict[int, List[MutableMapping[str, Any]]] = {}
    for activity in plan.get("activities", []):
        if isinstance(activity, MutableMapping) and activity.get("day") in affected:
            grouped.setdefault(int(activity["day"]), []).append(activity)

    for activities in grouped.values():
        for index, activity in enumerate(activities):
            if index == len(activities) - 1:
                if "route_to_next" in activity:
                    activity["route_to_next"] = None
                continue

            existing_route = activity.get("route_to_next")
            if isinstance(existing_route, Mapping):
                route = deepcopy(dict(existing_route))
                mode = route.get("mode") or activity.get("transport_to_next")
            elif isinstance(existing_route, str):
                route = {}
                mode = existing_route
            else:
                route = {}
                mode = activity.get("transport_to_next")
            if not mode:
                if "route_to_next" in activity:
                    activity["route_to_next"] = None
                continue
            route.update(
                {
                    "mode": mode,
                    "distance_meters": None,
                    "duration_minutes": None,
                    "data_type": "estimated_data",
                    "calculated_at": calculated_at,
                }
            )
            activity["route_to_next"] = route


def _sync_affected_day_collections(
    plan: MutableMapping[str, Any], affected_days: Sequence[int]
) -> None:
    affected = set(affected_days)
    activities_by_day = {
        day: [deepcopy(item) for item in plan.get("activities", []) if item.get("day") == day]
        for day in affected
    }
    for key in ("days", "trip_days"):
        collection = plan.get(key)
        if not isinstance(collection, list):
            continue
        seen = set()
        for day_entry in collection:
            if not isinstance(day_entry, MutableMapping):
                continue
            day = day_entry.get("day")
            if day in affected:
                day_entry["activities"] = deepcopy(activities_by_day[int(day)])
                seen.add(int(day))
        for day in sorted(affected - seen):
            collection.append({"day": day, "activities": deepcopy(activities_by_day[day])})


def _empty_diff(before_budget: float) -> Dict[str, Any]:
    return {
        "affected_days": [],
        "added": [],
        "removed": [],
        "moved": [],
        "time_changes": [],
        "budget_change": {
            "before": before_budget,
            "after": before_budget,
            "delta": 0.0,
        },
        "new_warnings": [],
    }


def _validate_identity(plan: Mapping[str, Any], operation: Mapping[str, Any]) -> None:
    plan_id = plan.get("plan_id")
    operation_plan_id = operation.get("plan_id")
    if not plan_id or not operation_plan_id or plan_id != operation_plan_id:
        raise TripEditError("PLAN_ID_MISMATCH", "operation plan_id does not match the plan")

    version = plan.get("version")
    base_version = operation.get("base_version")
    if isinstance(version, bool) or isinstance(base_version, bool):
        raise TripEditError("INVALID_VERSION", "plan version and base_version must be integers")
    if not isinstance(version, int) or not isinstance(base_version, int):
        raise TripEditError("INVALID_VERSION", "plan version and base_version must be integers")
    if version != base_version:
        raise TripEditError("VERSION_CONFLICT", "base_version does not match the current plan")


def _apply_activity_operation(
    plan: MutableMapping[str, Any], operation_type: str, payload: Mapping[str, Any], diff: Dict[str, Any]
) -> List[int]:
    activities = plan.setdefault("activities", [])
    if not isinstance(activities, list) or any(not isinstance(item, Mapping) for item in activities):
        raise TripEditError("INVALID_PLAN", "plan.activities must be a list of mappings")

    if operation_type == "add_activity":
        activity = _as_dict(_require_non_empty(payload, "activity"), "payload.activity")
        day = _require_day(activity, "day")
        _insert_for_day(activities, activity, day, payload.get("position"))
        diff["added"].append(deepcopy(activity))
        return [day]

    activity_id = _require_non_empty(payload, "activity_id")
    index = _find_activity(activities, activity_id)
    current = dict(activities[index])
    source_day = _require_day(current, "day")

    if operation_type == "remove_activity":
        removed = activities.pop(index)
        diff["removed"].append(deepcopy(removed))
        return [source_day]

    if operation_type == "replace_activity":
        replacement = _as_dict(_require_non_empty(payload, "activity"), "payload.activity")
        replacement["day"] = source_day
        if _activity_id(replacement) is None:
            id_key = "activity_id" if "activity_id" in current else "id"
            replacement[id_key] = activity_id
        activities[index] = replacement
        diff["removed"].append(deepcopy(current))
        diff["added"].append(deepcopy(replacement))
        return [source_day]

    if operation_type == "move_activity":
        target_day = _require_day(payload, "target_day", "to_day")
        moved = dict(activities.pop(index))
        moved["day"] = target_day
        _insert_for_day(activities, moved, target_day, payload.get("position"))
        diff["moved"].append(
            {"activity_id": activity_id, "from_day": source_day, "to_day": target_day}
        )
        return [source_day, target_day]

    if operation_type == "update_activity_time":
        if "start_time" not in payload and "end_time" not in payload:
            raise TripEditError(
                "INVALID_PAYLOAD", "start_time or end_time is required for update_activity_time"
            )
        before = {"start_time": current.get("start_time"), "end_time": current.get("end_time")}
        for key in ("start_time", "end_time"):
            if key in payload:
                activities[index][key] = payload[key]
        after = {
            "start_time": activities[index].get("start_time"),
            "end_time": activities[index].get("end_time"),
        }
        diff["time_changes"].append(
            {"activity_id": activity_id, "day": source_day, "before": before, "after": after}
        )
        return [source_day]

    raise TripEditError("UNSUPPORTED_OPERATION", f"unsupported operation: {operation_type}")


def _update_day_preference(
    plan: MutableMapping[str, Any], payload: Mapping[str, Any]
) -> Tuple[List[int], Dict[str, Any]]:
    day = _require_day(payload, "day")
    preference = payload.get("preference", payload.get("preferences"))
    if not isinstance(preference, Mapping):
        raise TripEditError("INVALID_PAYLOAD", "payload.preference must be a mapping")

    day_preferences = plan.setdefault("day_preferences", {})
    if not isinstance(day_preferences, MutableMapping):
        raise TripEditError("INVALID_PLAN", "plan.day_preferences must be a mapping")
    day_key = str(day)
    previous = day_preferences.get(day_key, {})
    if not isinstance(previous, Mapping):
        previous = {}
    day_preferences[day_key] = {**previous, **deepcopy(dict(preference))}
    return [day], {
        "day": day,
        "before": deepcopy(dict(previous)),
        "after": deepcopy(day_preferences[day_key]),
    }


def apply_trip_edit(plan: Any, operation: Any) -> Dict[str, Any]:
    """Apply one deterministic edit and return a JSON-serializable version result."""

    current_plan = _as_dict(plan, "plan")
    edit = _as_dict(operation, "operation")
    if _is_v3_document(current_plan):
        return _apply_v3_trip_edit(current_plan, edit)
    _validate_identity(current_plan, edit)

    operation_type = edit.get("type")
    if operation_type not in SUPPORTED_OPERATIONS:
        raise TripEditError("UNSUPPORTED_OPERATION", f"unsupported operation: {operation_type}")
    payload = edit.get("payload", {})
    if not isinstance(payload, Mapping):
        raise TripEditError("INVALID_PAYLOAD", "operation.payload must be a mapping")

    previous_plan = deepcopy(current_plan)
    updated_plan = deepcopy(current_plan)
    before_working_plan = deepcopy(previous_plan)
    _materialize_flat_activities(before_working_plan)
    _materialize_flat_activities(updated_plan)
    before_budget = _estimated_budget(before_working_plan)
    before_validation = _validation(before_working_plan)
    before_issue_codes = _issue_codes(before_validation[1]) if before_validation else set()
    diff = _empty_diff(before_budget)

    if operation_type == "update_day_preference":
        affected_days, preference_change = _update_day_preference(updated_plan, payload)
        diff["day_preference_change"] = preference_change
    else:
        affected_days = _apply_activity_operation(updated_plan, operation_type, payload, diff)

    _mark_affected_routes_estimated(updated_plan, affected_days)
    _repair_new_local_issues_once(updated_plan, affected_days, before_issue_codes)
    _mark_affected_routes_estimated(updated_plan, affected_days)
    _sync_affected_day_collections(updated_plan, affected_days)
    updated_plan["version"] = current_plan["version"] + 1
    diff["affected_days"] = sorted(set(affected_days))
    after_budget = _estimated_budget(updated_plan)
    diff["budget_change"] = {
        "before": before_budget,
        "after": after_budget,
        "delta": after_budget - before_budget,
    }
    previous_warnings = set(previous_plan.get("warnings", []))
    diff["new_warnings"] = [
        warning for warning in updated_plan.get("warnings", []) if warning not in previous_warnings
    ]
    after_validation = _validation(updated_plan)
    after_issue_codes = _issue_codes(after_validation[1]) if after_validation else set()
    diff["new_issue_codes"] = sorted(after_issue_codes - before_issue_codes)
    diff["resolved_issue_codes"] = sorted(before_issue_codes - after_issue_codes)

    return {
        "operation_id": edit.get("operation_id"),
        "plan": updated_plan,
        "previous_plan": previous_plan,
        "diff": diff,
    }


execute_trip_edit = apply_trip_edit
