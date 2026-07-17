from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Tuple

from pydantic import ValidationError

from schemas.trip_models import TripPlan, TripValidationResult
from services.trip_plan_repair_service import repair_trip_plan_once
from services.trip_plan_validator import validate_trip_plan


SUPPORTED_OPERATIONS = {
    "add_activity",
    "remove_activity",
    "replace_activity",
    "move_activity",
    "update_activity_time",
    "update_day_preference",
}

LOCAL_REPAIRABLE_ISSUE_CODES = {
    "MISSING_TRANSPORT",
    "POSSIBLE_ROUTE_BACKTRACK",
    "UNVERIFIED_REALTIME_CLAIM",
}


class TripEditError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
