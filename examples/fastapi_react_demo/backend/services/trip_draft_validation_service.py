"""提交前草稿校验：区分必须修正的结构问题与可降级的外部数据问题。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import ValidationError

try:
    from backend.schemas.trip_v3_models import TravelPlanDocumentV3
except ModuleNotFoundError:  # Runtime entrypoint executes from backend/.
    from schemas.trip_v3_models import TravelPlanDocumentV3


DraftIssue = Dict[str, Any]


def _issue(code: str, message: str, *, target_id: Optional[str] = None, day: Optional[int] = None) -> DraftIssue:
    result: DraftIssue = {"code": code, "message": message}
    if target_id:
        result["target_id"] = target_id
    if day is not None:
        result["day"] = day
    return result


def _append_unique(issues: List[DraftIssue], issue: DraftIssue) -> None:
    key = (issue["code"], issue.get("target_id"), issue.get("day"))
    if not any((item["code"], item.get("target_id"), item.get("day")) == key for item in issues):
        issues.append(issue)


def _minutes(value: str) -> int:
    hours, minutes = value.split(":", 1)
    parsed_hours, parsed_minutes = int(hours), int(minutes)
    if not 0 <= parsed_hours <= 23 or not 0 <= parsed_minutes <= 59:
        raise ValueError("time must be within one day")
    return parsed_hours * 60 + parsed_minutes


def _soft_validation_issues(document: TravelPlanDocumentV3) -> Iterable[DraftIssue]:
    for issue in document.validation.issues:
        if issue.severity != "error":
            yield _issue(issue.code, issue.message, target_id=issue.target_id)

    section_labels = {
        "outbound_transport": "去程交通",
        "lodging_plan": "住宿",
        "return_transport": "返程交通",
        "map_guidance": "地图路线",
    }
    for field, label in section_labels.items():
        section = getattr(document, field)
        if section.status in {"degraded", "unavailable"}:
            yield _issue(
                f"{field.upper()}_PENDING",
                section.status_reason or f"{label}信息待确认。",
            )

    if document.budget.unknown_cost_count:
        yield _issue(
            "REFERENCE_PRICE_PENDING",
            f"有 {document.budget.unknown_cost_count} 项参考价格待确认。",
        )

    for route in document.map_guidance.day_routes:
        if route.status != "ready":
            yield _issue(
                "ROUTE_DATA_PENDING",
                f"Day {route.day} 的真实路线待确认。",
                day=route.day,
            )

    if document.status == "degraded" and not document.validation.issues:
        yield _issue("EXTERNAL_DATA_PENDING", "部分外部信息待确认。")


def _raw_time_validation_issues(document: Any) -> List[DraftIssue]:
    """在 Pydantic 丢失字段上下文前，返回前端可操作的时间错误。"""
    if not isinstance(document, Mapping):
        return []
    itinerary = document.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, Mapping) else None
    if not isinstance(days, list):
        return []

    issues: List[DraftIssue] = []
    timed_by_day: Dict[int, List[tuple[Mapping[str, Any], int, int]]] = {}
    for raw_day in days:
        if not isinstance(raw_day, Mapping):
            continue
        try:
            day_number = int(raw_day.get("day"))
        except (TypeError, ValueError):
            continue
        activities = raw_day.get("activities")
        if not isinstance(activities, list):
            continue
        for raw_activity in activities:
            if not isinstance(raw_activity, Mapping):
                continue
            target_id = str(raw_activity.get("activity_id") or "") or None
            start_at = raw_activity.get("start_at")
            end_at = raw_activity.get("end_at")
            fixed_time = raw_activity.get("fixed_time") is True
            if fixed_time and (not start_at or not end_at):
                _append_unique(
                    issues,
                    _issue("FIXED_TIME_INCOMPLETE", "固定时间活动必须同时提供开始和结束时间。", target_id=target_id, day=day_number),
                )
                continue
            if bool(start_at) != bool(end_at):
                _append_unique(
                    issues,
                    _issue("INCOMPLETE_ACTIVITY_TIME", "活动时间必须同时提供开始和结束时间。", target_id=target_id, day=day_number),
                )
                continue
            if not start_at or not end_at:
                continue
            try:
                start_minutes = _minutes(str(start_at))
                end_minutes = _minutes(str(end_at))
            except (AttributeError, TypeError, ValueError):
                _append_unique(
                    issues,
                    _issue("INVALID_ACTIVITY_TIME", "活动时间必须是当天有效的 HH:MM。", target_id=target_id, day=day_number),
                )
                continue
            if end_minutes <= start_minutes:
                _append_unique(
                    issues,
                    _issue("INVALID_ACTIVITY_TIME", "活动结束时间必须晚于开始时间。", target_id=target_id, day=day_number),
                )
                continue
            duration = raw_activity.get("duration_minutes")
            if duration is not None:
                try:
                    valid_duration = int(duration) > 0
                except (TypeError, ValueError):
                    valid_duration = False
                if not valid_duration:
                    _append_unique(
                        issues,
                        _issue("INVALID_ACTIVITY_DURATION", "已排期活动时长必须大于 0。", target_id=target_id, day=day_number),
                    )
            timed_by_day.setdefault(day_number, []).append((raw_activity, start_minutes, end_minutes))

    for day_number, activities in timed_by_day.items():
        ordered = sorted(activities, key=lambda item: (item[1], item[2]))
        for index, (left, left_start, left_end) in enumerate(ordered):
            for right, right_start, right_end in ordered[index + 1:]:
                if right_start >= left_end:
                    break
                if left_start >= right_end:
                    continue
                fixed = left if left.get("fixed_time") is True else right if right.get("fixed_time") is True else None
                target = fixed or right
                code = "FIXED_TIME_CONFLICT" if fixed else "ACTIVITY_TIME_CONFLICT"
                message = (
                    f"固定时间“{target.get('title') or '活动'}”与当天其他活动冲突。"
                    if fixed
                    else f"“{target.get('title') or '活动'}”与当天其他活动时间重叠。"
                )
                _append_unique(
                    issues,
                    _issue(code, message, target_id=str(target.get("activity_id") or "") or None, day=day_number),
                )

    def selected_clock(section_name: str, field: str) -> Optional[int]:
        section = document.get(section_name)
        if not isinstance(section, Mapping):
            return None
        selected_id = str(section.get("selected_option_id") or "").strip()
        options = section.get("options")
        if not selected_id or not isinstance(options, list):
            return None
        option = next(
            (
                item for item in options
                if isinstance(item, Mapping) and str(item.get("option_id") or "").strip() == selected_id
            ),
            None,
        )
        time_value = option.get(field) if isinstance(option, Mapping) else None
        if not isinstance(time_value, Mapping):
            return None
        local_iso = str(time_value.get("local_iso") or "").strip()
        if "T" not in local_iso:
            return None
        try:
            return _minutes(local_iso.split("T", 1)[1][:5])
        except (IndexError, ValueError):
            return None

    ordered_days = [item for item in days if isinstance(item, Mapping)]
    if ordered_days:
        first_day_number = int(ordered_days[0].get("day") or 1)
        arrival = selected_clock("outbound_transport", "arrival_time")
        if arrival is not None:
            for activity, start_minutes, _end_minutes in timed_by_day.get(first_day_number, []):
                if start_minutes < arrival:
                    _append_unique(
                        issues,
                        _issue(
                            "OUTBOUND_ARRIVAL_CONFLICT",
                            "首日活动不能早于所选去程到达时间。",
                            target_id=str(activity.get("activity_id") or "") or None,
                            day=first_day_number,
                        ),
                    )
        last_day_number = int(ordered_days[-1].get("day") or len(ordered_days))
        departure = selected_clock("return_transport", "departure_time")
        if departure is not None:
            for activity, _start_minutes, end_minutes in timed_by_day.get(last_day_number, []):
                if end_minutes > departure:
                    _append_unique(
                        issues,
                        _issue(
                            "RETURN_DEPARTURE_CONFLICT",
                            "末日活动不能晚于所选返程出发时间。",
                            target_id=str(activity.get("activity_id") or "") or None,
                            day=last_day_number,
                        ),
                    )
    return issues


def validate_trip_draft(document: Any) -> Dict[str, Any]:
    """返回可持久化草稿的提交前校验结果，不修改传入文档。"""
    raw_time_errors = _raw_time_validation_issues(document)
    if raw_time_errors:
        return {
            "can_apply": False,
            "status": "blocked",
            "hard_errors": raw_time_errors,
            "soft_warnings": [],
        }
    try:
        parsed = TravelPlanDocumentV3.model_validate(document, context={"draft": True})
    except (ValidationError, TypeError, ValueError):
        hard_errors = [_issue("INVALID_TRIP_DOCUMENT", "行程结构不完整或日期无效，请修正后再应用。")]
        return {
            "can_apply": False,
            "status": "blocked",
            "hard_errors": hard_errors,
            "soft_warnings": [],
        }

    hard_errors: List[DraftIssue] = []
    from services.formal_consistency_service import schedule_issues
    for issue in schedule_issues(parsed.model_dump(mode="json")):
        _append_unique(hard_errors, issue)
    timed_by_day: Dict[int, List[tuple[Any, int, int]]] = {}
    for day in parsed.itinerary.days:
        for activity in day.activities:
            start_at, end_at = activity.start_at, activity.end_at
            target_id = activity.activity_id
            if activity.fixed_time and (not start_at or not end_at):
                _append_unique(
                    hard_errors,
                    _issue("FIXED_TIME_INCOMPLETE", "固定时间活动必须同时提供开始和结束时间。", target_id=target_id, day=day.day),
                )
                continue
            if bool(start_at) != bool(end_at):
                _append_unique(
                    hard_errors,
                    _issue("INCOMPLETE_ACTIVITY_TIME", "活动时间必须同时提供开始和结束时间。", target_id=target_id, day=day.day),
                )
                continue
            if not start_at or not end_at:
                continue
            try:
                start_minutes, end_minutes = _minutes(start_at), _minutes(end_at)
            except ValueError:
                _append_unique(
                    hard_errors,
                    _issue("INVALID_ACTIVITY_TIME", "活动时间必须是当天有效的 HH:MM。", target_id=target_id, day=day.day),
                )
                continue
            if end_minutes <= start_minutes:
                _append_unique(
                    hard_errors,
                    _issue("INVALID_ACTIVITY_TIME", "活动结束时间必须晚于开始时间。", target_id=target_id, day=day.day),
                )
                continue
            timed_by_day.setdefault(day.day, []).append((activity, start_minutes, end_minutes))

    for day, activities in timed_by_day.items():
        for index, (left, left_start, left_end) in enumerate(activities):
            for right, right_start, right_end in activities[index + 1:]:
                if not (left.fixed_time or right.fixed_time):
                    continue
                if left_start < right_end and right_start < left_end:
                    fixed = left if left.fixed_time else right
                    _append_unique(
                        hard_errors,
                        _issue(
                            "FIXED_TIME_CONFLICT",
                            f"固定时间“{fixed.title}”与当天其他活动冲突。",
                            target_id=fixed.activity_id,
                            day=day,
                        ),
                    )

    for issue in parsed.validation.issues:
        if issue.severity == "error":
            _append_unique(hard_errors, _issue(issue.code, issue.message, target_id=issue.target_id))

    soft_warnings: List[DraftIssue] = []
    for issue in _soft_validation_issues(parsed):
        _append_unique(soft_warnings, issue)
    return {
        "can_apply": not hard_errors,
        "status": "blocked" if hard_errors else "degraded" if soft_warnings else "ready",
        "hard_errors": hard_errors,
        "soft_warnings": soft_warnings,
    }
