"""正式方案的派生事实：预算、锚点路线与最终时间约束。"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Dict, Mapping, Sequence

from services.candidate_schedule_service import _opening_windows, _last_admission, _closed_day, refresh_candidate_schedule_options


def _clock(value: Any) -> int | None:
    match = re.search(r"(?:T|^)(\d{2}):(\d{2})", str(value or ""))
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        return None
    return int(match[1]) * 60 + int(match[2])


def _time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _selected(document: Mapping[str, Any], section: str) -> Dict[str, Any]:
    value = document.get(section, {})
    key = "planning_lodging_id" if section == "lodging_plan" else "selected_option_id"
    id_key = "lodging_id" if section == "lodging_plan" else "option_id"
    return next((item for item in value.get("options", []) if item.get(id_key) == value.get(key)), {})


def recalculate_formal_budget(document: Dict[str, Any]) -> None:
    """只按当前正式活动和已选项计算，缺价不以零元冒充已知价格。"""
    budget = document["budget"]
    travelers = document["intent"]["travelers"]
    counts = {key: int(travelers.get(key, 0)) for key in ("adults", "children", "seniors")}
    people = sum(counts.values())
    categories: Dict[tuple, Dict[str, Any]] = {}
    unknown: list[str] = []
    warnings: list[str] = []
    person_totals = {"adult": 0.0, "child": 0.0, "senior": 0.0}
    assumed = set()
    discounts = set()

    def add(category: str, money: Any, multiplier: int, label: str, traveler: str | None = None) -> None:
        if not isinstance(money, dict) or money.get("amount") is None:
            unknown.append(label)
            return
        amount = float(money["amount"]) * multiplier
        currency = money.get("currency", "CNY")
        reference = amount if currency == "CNY" else (
            float(money["cny_reference_amount"]) * multiplier if money.get("cny_reference_amount") is not None else None
        )
        if reference is None:
            unknown.append(f"{label}人民币换算")
        key = (category, currency, money.get("exchange_rate_as_of"), money.get("source_status", "user_confirmation_required"))
        if key not in categories:
            categories[key] = {"category": category, "amount": {**deepcopy(money), "amount": 0.0}}
            if currency != "CNY":
                categories[key]["amount"]["cny_reference_amount"] = 0.0 if reference is not None else None
        result = categories[key]["amount"]
        result["amount"] = round(result["amount"] + amount, 2)
        if currency != "CNY":
            if result.get("cny_reference_amount") is not None and reference is not None:
                result["cny_reference_amount"] = round(result["cny_reference_amount"] + reference, 2)
            else:
                result["cny_reference_amount"] = None
        if reference is not None:
            if traveler:
                person_totals[traveler] += reference
            else:
                for kind, key in (("adult", "adults"), ("child", "children"), ("senior", "seniors")):
                    person_totals[kind] += reference * counts[key] / people

    def add_travelers(category: str, money: Any, label: str, official: Mapping[str, Any] | None = None) -> None:
        if not isinstance(money, dict):
            unknown.append(label)
            return
        for kind, key in (("adult", "adults"), ("child", "children"), ("senior", "seniors")):
            if not counts[key]:
                continue
            price = (official or {}).get(kind)
            if kind != "adult" and money.get("amount", 0) > 0:
                (discounts if price else assumed).add(kind)
            add(category, price or money, counts[key], label, kind)

    category_map = {"attraction": "activities", "food": "food", "shopping": "shopping", "transport": "transport", "hotel": "lodging"}
    for day in document["itinerary"]["days"]:
        for activity in day["activities"]:
            if activity["kind"] in {"free_time", "other"} and activity.get("estimated_cost") is None:
                continue
            per_person = activity.get("estimated_cost_unit", "per_traveler") == "per_traveler"
            category = category_map.get(activity["kind"], "other")
            if per_person:
                add_travelers(category, activity.get("estimated_cost"), activity["title"], activity.get("official_traveler_prices"))
            else:
                add(category, activity.get("estimated_cost"), 1, activity["title"])
    for key, label in (("outbound_transport", "去程交通"), ("return_transport", "返程交通")):
        option = _selected(document, key)
        add_travelers("transport", option.get("price"), label)
    nights = max(0, int(document["intent"]["days"]) - 1)
    if nights:
        lodging = _selected(document, "lodging_plan")
        if lodging.get("total_price"):
            add("lodging", lodging["total_price"], 1, "住宿")
        elif lodging.get("nightly_price"):
            rooms = math.ceil(people / 2)
            add("lodging", lodging["nightly_price"], rooms * nights, "住宿")
            warnings.append(f"住宿按每间最多 2 人、{rooms} 间 × {nights} 晚估算，房型与实际入住人数待确认。")
        else:
            unknown.append("住宿")
    routes = document.get("map_guidance", {}).get("day_routes", [])
    for day in document["itinerary"]["days"]:
        route = next((item for item in routes if item["day"] == day["day"]), {})
        legs = route.get("legs", [])
        if not legs and (len(day["activities"]) > 1 or day.get("anchors")):
            unknown.append(f"第 {day['day']} 天市内交通")
        for leg in legs:
            if leg.get("mode") in {"walking", "walk", "步行"}:
                continue
            names = {item.get("activity_id") or item.get("anchor_id"): item.get("title") or (item.get("place") or {}).get("name") or "接驳地点" for item in [*day["activities"], *day.get("anchors", [])]}
            add("transport", leg.get("estimated_cost"), 1, f"第 {day['day']} 天接驳 {names.get(leg['from_id'], '起点')} → {names.get(leg['to_id'], '终点')}")
    if assumed:
        warnings.append("部分儿童或老人优惠未获官方确认，相关费用按成人价估算；已核验优惠保持原价。")
    if unknown:
        warnings.append(f"{len(unknown)} 项费用待确认：" + "、".join(unknown))
    total = round(sum(item["amount"].get("amount", 0) if item["amount"].get("currency") == "CNY" else item["amount"].get("cny_reference_amount") or 0 for item in categories.values()), 2)
    cny = lambda value: {"amount": round(value, 2), "currency": "CNY", "source_status": "user_confirmation_required"}
    target = budget["total_budget"]
    target_cny = target["amount"] if target["currency"] == "CNY" else target.get("cny_reference_amount")
    overrun = max(0, total - target_cny) if target_cny is not None else 0
    budget.update(categories=list(categories.values()), estimated_total=cny(total), unknown_cost_count=len(unknown), warnings=warnings, over_budget=overrun > 0, overrun_amount=cny(overrun) if overrun else None)
    budget["traveler_costs"] = [
        {"traveler_type": kind, "count": counts[key], "estimated_total": cny(person_totals[kind]) if counts[key] else None,
         "pricing_status": "not_applicable" if not counts[key] else "adult_price_assumed" if kind in assumed else "official_discount_verified" if kind in discounts else "standard_price"}
        for kind, key in (("adult", "adults"), ("child", "children"), ("senior", "seniors"))
    ]


def daily_route_nodes(day: Mapping[str, Any]) -> list[Dict[str, Any]]:
    anchors = day.get("anchors", [])
    start = [a for a in anchors if a["kind"] in {"arrival_hub", "lodging_departure"}]
    end = [a for a in anchors if a["kind"] in {"lodging_return", "departure_hub"}]
    nodes = []
    for item in [*start, *day.get("activities", []), *end]:
        place = item.get("place") or {}
        coords = place.get("coordinates") or {}
        if not place.get("poi_id") or not coords:
            continue
        nodes.append({"activity_id": item.get("activity_id") or item["anchor_id"], "route_order": len(nodes),
                      "place": {"lat": coords["latitude"], "lng": coords["longitude"]}, "route_to_next": item.get("route_to_next")})
    return nodes


def schedule_issues(document: Mapping[str, Any]) -> list[Dict[str, Any]]:
    issues = []
    routes = document.get("map_guidance", {}).get("day_routes", [])
    for day in document.get("itinerary", {}).get("days", []):
        by_id = {a["activity_id"]: a for a in day["activities"]}
        for anchor in day.get("anchors", []):
            section, field = ("outbound_transport", "arrival_time") if anchor["kind"] == "arrival_hub" else ("return_transport", "departure_time")
            if anchor["kind"] in {"arrival_hub", "departure_hub"}:
                stamp = (_selected(document, section).get(field) or {}).get("local_iso")
                by_id[anchor["anchor_id"]] = {"activity_id": anchor["anchor_id"], "title": anchor.get("display_label") or anchor["place"]["name"], "start_at": stamp, "end_at": stamp}
        def issue(code: str, message: str, activity: Mapping[str, Any]) -> None:
            issues.append({"code": code, "message": message, "target_id": activity["activity_id"], "day": day["day"]})
        for activity in day["activities"]:
            start, end = _clock(activity.get("start_at")), _clock(activity.get("end_at"))
            if start is None or end is None:
                continue
            hours = (activity.get("place") or {}).get("opening_hours")
            windows = _opening_windows(hours, day.get("date"))
            if windows and not any(left <= start and end <= right for left, right in windows):
                issue("OPENING_HOURS_CONFLICT", f"“{activity['title']}”的活动时间不在已取得的营业窗口内（{hours}）。", activity)
            last_entry = _last_admission(hours, day.get("date"))
            if last_entry is not None and start > last_entry:
                issue("LAST_ADMISSION_CONFLICT", f"“{activity['title']}”已超过最晚入场时间。", activity)
            if _closed_day(hours, day.get("date")):
                issue("CLOSED_DAY_CONFLICT", f"“{activity['title']}”与已知闭馆规则冲突，节假日例外需核验。", activity)
        route = next((r for r in routes if r["day"] == day["day"]), {})
        for leg in route.get("legs", []):
            if leg.get("status") != "ready" or leg.get("duration_minutes") is None:
                continue
            left, right = by_id.get(leg["from_id"]), by_id.get(leg["to_id"])
            if left and right:
                end, start = _clock(left.get("end_at")), _clock(right.get("start_at"))
                if end is not None and start is not None and start - end < leg["duration_minutes"]:
                    issue("ROUTE_TIME_CONFLICT", f"“{left['title']}”至“{right['title']}”预留 {start-end} 分钟，真实路线需要 {leg['duration_minutes']} 分钟。", right)
    return issues


async def rebuild_formal_routes(
    document: Dict[str, Any], *, dispatcher: Any = None,
    affected_days: Sequence[int] | None = None,
) -> None:
    """初次生成重建全部日；局部编辑复用节点链仍一致的未改日路线。"""
    from services.route_geometry_service import build_day_route
    routes = []
    scope = document["outbound_transport"]["scope"]
    affected = set(affected_days) if affected_days is not None else None
    existing = {route["day"]: route for route in document["map_guidance"].get("day_routes", [])}
    for day in document["itinerary"]["days"]:
        nodes = daily_route_nodes(day)
        previous = existing.get(day["day"])
        expected_edges = [(left["activity_id"], right["activity_id"]) for left, right in zip(nodes, nodes[1:])]
        reusable = (
            affected is not None and day["day"] not in affected and previous is not None
            and [(leg.get("from_id"), leg.get("to_id")) for leg in previous.get("legs", [])] == expected_edges
        )
        if reusable:
            route = deepcopy(previous)
        else:
            raw = await build_day_route(day=day["day"], plan_version=document["revision"], activities=nodes, scope=scope, baidu_dispatcher=dispatcher) if len(nodes) >= 2 else {"status": "unavailable", "legs": []}
            legs = [{**{key: value for key, value in leg.items() if key not in {"from_activity_id", "to_activity_id"}},
                     "from_id": leg["from_activity_id"], "to_id": leg["to_activity_id"]} for leg in raw["legs"]]
            route = {"day": day["day"], "status": raw["status"], "legs": legs}
        routes.append(route)
        legs = route["legs"]
        by_id = {leg["from_id"]: leg for leg in legs}
        for activity in day["activities"]:
            activity["route_to_next"] = deepcopy(by_id.get(activity["activity_id"]))
    ready = sum(route["status"] == "ready" for route in routes)
    document["map_guidance"].update(day_routes=routes, status="ready" if ready == len(routes) else "degraded",
        status_reason=f"已核验 {ready}/{len(routes)} 天完整路线（含规划住宿与抵离接驳）。" if ready == len(routes) else f"已核验 {ready}/{len(routes)} 天完整路线，其余路段待确认。")
    recalculate_formal_budget(document)
    refresh_candidate_schedule_options(document)


async def finalize_initial_schedule(document: Dict[str, Any], *, dispatcher: Any = None) -> None:
    """移除活动会生成新的相邻路段；取得新路线后继续修复直到日程稳定。"""
    activity_count = sum(len(day["activities"]) for day in document["itinerary"]["days"])
    initial_times = {activity["activity_id"]: (activity.get("start_at"), activity.get("end_at"))
                     for day in document["itinerary"]["days"] for activity in day["activities"]}
    # 删除绕行景点后重新使用原建议时段，不能把上一轮累积的延误永久留下。
    for _ in range(activity_count + 2):
        await rebuild_formal_routes(document, dispatcher=dispatcher)
        if not repair_initial_schedule(document, initial_times=initial_times):
            break
    refresh_schedule_validation(document)


def repair_initial_schedule(document: Dict[str, Any], *, initial_times: Mapping[str, tuple] | None = None) -> bool:
    """生成时按已取得的路线和营业窗口修复；不挪动用户固定时间。"""
    changed = False
    routes = document["map_guidance"]["day_routes"]
    document["validation"]["issues"] = [item for item in document["validation"]["issues"]
                                         if item["code"] != "INITIAL_MEAL_WINDOW_CONFLICT"]
    warnings = document["validation"]["issues"]
    from services.meal_schedule_service import meal_window_for_opening_hours

    def to_candidate(activity: Dict[str, Any], day_number: int, reason: str) -> None:
        candidate = deepcopy(activity)
        candidate.update(day=None, order=None, start_at=None, end_at=None, fixed_time=False, route_to_next=None)
        if len(document["candidate_pool"]) >= 15:
            # 多次移出活动时不能反复 pop 新追加的上一项，丢掉已核验营业信息。
            moved_ids = {item.get("target_id") for item in warnings if item["code"] == "ACTIVITY_WINDOW_UNAVAILABLE"}
            pool = document["candidate_pool"]
            replace_index = min(range(len(pool)), key=lambda index: (
                pool[index]["activity_id"] in moved_ids,
                bool((pool[index].get("place") or {}).get("opening_hours")),
                -index,
            ))
            pool.pop(replace_index)
        document["candidate_pool"].append(candidate)
        warnings.append({"code": "ACTIVITY_WINDOW_UNAVAILABLE", "message": f"第 {day_number} 天“{activity['title']}”{reason}，已保留在候选池。", "severity": "warning", "target_id": activity["activity_id"]})
    for day in document["itinerary"]["days"]:
        route = next((r for r in routes if r["day"] == day["day"]), {})
        legs = {(leg["from_id"], leg["to_id"]): leg for leg in route.get("legs", [])}
        previous = None
        kept = []
        for position, activity in enumerate(day["activities"]):
            proposed = (initial_times or {}).get(activity["activity_id"], (activity.get("start_at"), activity.get("end_at")))
            start, end = map(_clock, proposed)
            if start is None or end is None or activity.get("fixed_time"):
                kept.append(activity)
                previous = activity
                continue
            duration = end - start
            if not previous:
                arrival_anchor = next((item for item in day.get("anchors", []) if item["kind"] == "arrival_hub"), None)
                arrival_clock = _clock((_selected(document, "outbound_transport").get("arrival_time") or {}).get("local_iso"))
                if arrival_anchor and arrival_clock is not None:
                    arrival_leg = legs.get((arrival_anchor["anchor_id"], activity["activity_id"]), {})
                    start = max(start, arrival_clock + (arrival_leg.get("duration_minutes") or 0))
            if previous:
                leg = legs.get((previous["activity_id"], activity["activity_id"]), {})
                previous_end = _clock(previous.get("end_at"))
                if previous_end is not None:
                    start = max(start, previous_end + (leg.get("duration_minutes") or 15))
            start = math.ceil(start / 15) * 15
            hours = (activity.get("place") or {}).get("opening_hours")
            windows = _opening_windows(hours, day.get("date"))
            if windows:
                feasible = [max(start, left) for left, right in windows if max(start, left) + duration <= right]
                start = math.ceil(min(feasible) / 15) * 15 if feasible else 24 * 60
                if not any(left <= start and start + duration <= right for left, right in windows):
                    start = 24 * 60
            last_entry = _last_admission(hours, day.get("date"))
            if _closed_day(hours, day.get("date")) or (last_entry is not None and start > last_entry):
                start = 24 * 60
            departure_anchor = next((item for item in day.get("anchors", []) if item["kind"] == "departure_hub"), None)
            departure_clock = _clock((_selected(document, "return_transport").get("departure_time") or {}).get("local_iso"))
            if departure_anchor and departure_clock is not None:
                departure_leg = legs.get((activity["activity_id"], departure_anchor["anchor_id"]), {})
                if start + duration + (departure_leg.get("duration_minutes") or 0) > departure_clock:
                    start = 24 * 60
            meal = activity.get("meal_type") if activity.get("kind") == "food" else None
            # 营业时间已按当天的全部窗口校验；这里仅取得目的地的建议用餐时段。
            meal_window = meal_window_for_opening_hours(None, meal,
                domestic=document["outbound_transport"]["scope"] == "domestic") if meal else None
            meal_deadline = meal_window[0] + 90 if meal_window else None
            if meal_deadline is not None and previous is None and arrival_anchor and arrival_clock is not None:
                # 晚抵达后直接用餐，以真实到达时刻加接驳为最早可行时间。
                # 不能因晚于默认饭点就删除首日必需餐次。
                arrival_leg = legs.get((arrival_anchor["anchor_id"], activity["activity_id"]), {})
                arrival_ready = arrival_clock + (arrival_leg.get("duration_minutes") or 0)
                meal_deadline = max(meal_deadline, math.ceil(arrival_ready / 15) * 15)
            meal_conflict = meal and (meal_deadline is None or start > meal_deadline or start + duration >= 24 * 60)
            if meal_conflict:
                # 必需餐饮优先于可调整景点。一次只移出一个，下一轮必须取得新的相邻真实路线。
                blocker = next((item for item in reversed(kept)
                                if not item.get("fixed_time") and not item.get("meal_type")), None)
                if blocker is not None and meal_window is not None and not _closed_day(hours, day.get("date")):
                    kept.remove(blocker)
                    to_candidate(blocker, day["day"], f"与“{activity['title']}”的用餐和交通窗口冲突")
                    kept.extend(day["activities"][position:])
                    changed = True
                    break
                # 无可移出的景点时保留餐饮和明确错误，由质量检查拒绝不完整方案。
                warnings.append({"code": "INITIAL_MEAL_WINDOW_CONFLICT", "message": f"第 {day['day']} 天“{activity['title']}”无法在用餐、营业及交通窗口内安排。", "severity": "error", "target_id": activity["activity_id"]})
                kept.append(activity)
                previous = activity
                continue
            if start + duration >= 24 * 60:
                to_candidate(activity, day["day"], "无法满足营业和交通时间")
                changed = True
                continue
            if start != _clock(activity["start_at"]):
                activity.update(start_at=_time(start), end_at=_time(start + duration), duration_minutes=duration)
                changed = True
            kept.append(activity)
            previous = activity
        day["activities"] = kept
        for index, activity in enumerate(kept, 1):
            activity["order"] = index
    if changed:
        from services.trip_edit_service import _v3_refresh_anchors
        _v3_refresh_anchors(document)
        document["status"] = "degraded"
        document["validation"]["degraded"] = True
    return changed


def refresh_schedule_validation(document: Dict[str, Any]) -> None:
    codes = {"OPENING_HOURS_CONFLICT", "LAST_ADMISSION_CONFLICT", "CLOSED_DAY_CONFLICT", "ROUTE_TIME_CONFLICT"}
    validation = document["validation"]
    validation["issues"] = [item for item in validation["issues"] if item["code"] not in codes]
    for issue in schedule_issues(document):
        validation["issues"].append({key: value for key, value in {**issue, "severity": "error"}.items() if key != "day"})
    validation["valid"] = not any(issue["severity"] == "error" for issue in validation["issues"])
    if validation["issues"] or document["budget"]["unknown_cost_count"] or document["map_guidance"]["status"] != "ready":
        validation["degraded"] = True
        document["status"] = "degraded"
