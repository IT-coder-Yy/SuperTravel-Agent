"""真实旅行请求的专业任务执行图，复用现有工具与确定性领域服务。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from copy import deepcopy
import re
from typing import Any, AsyncIterator, Dict

from services.planning_orchestrator import PlanningTaskGraph, build_default_task_graph
from services.formal_consistency_service import (
    finalize_initial_schedule, recalculate_formal_budget, refresh_schedule_validation,
)
from services.travel_document_service import adapt_v2_to_v3
from schemas.trip_v3_models import TravelPlanDocumentV3
from services.meal_schedule_service import prepare_early_departure_breakfast, early_breakfast_preparation_covers


async def stream_production_plan(*, intent: Any, query: str, tool_manager: Any, message_history: list,
                                 session_id: str, allow_web_search: bool, selected_skill_ids: list | None = None,
                                 profile: Any = None, selected_knowledge_context: list | None = None) -> AsyncIterator[Dict[str, Any]]:
    from services import chat_service as c
    query = f"从{intent.origin}到{intent.destination}{intent.days}天旅行，日期{intent.date_range}，" + "、".join(intent.interests or [])
    bundle: Dict[str, Any] = {"query": query, "trip_intent": intent.model_dump(), "destination_city": intent.destination,
        "origin_city": intent.origin, "kinds": ["旅行规划"], "date_sensitive": not c.is_flexible_date_range(intent.date_range),
        "travel_date": intent.date_range, "xhs_rows": [], "xhs_table": "", "user_profile": profile or {},
        "selected_knowledge_context": selected_knowledge_context or []}
    context: Dict[str, Any] = {}
    queue: asyncio.Queue = asyncio.Queue()
    base = build_default_task_graph()
    # 百度仍使用工具管理器共享的同一个账户队列。并行的是专业任务。
    # 完整百度核验允许 360 秒，避免正常账户排队在 180 秒触发重复任务。
    # 整体软/硬时限仍由外层运行管理器执行。
    map_timeout = 360 if getattr(tool_manager, "baidu_request_dispatcher", None) is not None else 180
    tasks = [replace(task, timeout_seconds=map_timeout if task.task_id in {"research", "lodging", "dining", "route", "verification"} else 120)
             for task in base.tasks.values()]
    graph = PlanningTaskGraph(tasks)
    common = {"tool_manager": tool_manager, "message_history": message_history, "session_id": session_id}

    async def requirements() -> dict:
        return intent.model_dump()

    async def lookup_role(role: str) -> tuple:
        return await asyncio.to_thread(c._run_map_search_for_travel, user_query=query,
            destination_city=intent.destination, research_role=role, **common)

    async def lookup_web(terms: str) -> tuple:
        if not allow_web_search:
            return [], "", "已关闭网页检索"
        return await asyncio.to_thread(c._run_web_search_for_travel,
            user_query=f"{intent.destination} {terms}", **common)

    async def research() -> dict:
        locations, tool, error, _ = await lookup_role("research")
        if not locations:
            raise ValueError(f"{intent.destination}真实景点检索暂未返回可用地点，请稍后重试。")
        context["attractions"] = locations
        rows, web_tool, web_error = await lookup_web("旅行 景点 历史人文 官方 开放时间")
        bundle.update(web_rows=c._filter_destination_reference_rows(rows, intent.destination), web_tool=web_tool,
                      web_error=web_error, rag_context=c.maybe_prepare_travel_rag_context(query), map_tool=tool, map_error=error)
        return {"places": locations, "references": bundle["web_rows"], "warnings": [value for value in (error, web_error) if value]}

    async def transport() -> dict:
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", intent.date_range or "")
        if not bundle["date_sensitive"] or len(dates) < 2:
            return {"date_mode": "flexible", "options": [], "reason": "日期暂未确定，未查询具体往返班次"}
        async def lookup(origin: str, destination: str, day: str):
            return await asyncio.to_thread(c.maybe_prepare_train_ticket_bundle,
                user_query=f"{origin}到{destination} {day} 火车票 机票 大巴票", selected_skill_ids=selected_skill_ids, **common)
        outbound, returning = await asyncio.gather(lookup(intent.origin, intent.destination, dates[0]), lookup(intent.destination, intent.origin, dates[-1]))
        bundle.update(ticket_bundle=outbound, return_ticket_bundle=returning)
        return {"outbound": outbound or {}, "return": returning or {}}

    async def lodging() -> dict:
        locations, tool, error, verified = await lookup_role("lodging")
        if intent.days > 1 and not verified:
            raise ValueError(f"{intent.destination}住宿地点尚未核验成功，无法建立每日住宿路线。")
        bundle["lodging_candidates"] = verified
        rows, web_tool, web_error = await lookup_web("住宿 酒店 区域 价格参考")
        bundle.update(lodging_web_rows=rows, lodging_web_tool=web_tool, lodging_web_error=web_error,
                      lodging_reference_rows=[{**row, "reference_type": "web"} for row in rows[:3]])
        return {"places": verified, "references": rows, "warnings": [value for value in (error, web_error) if value]}

    async def dining() -> dict:
        locations, tool, error, _ = await lookup_role("dining")
        if not locations:
            raise ValueError(f"{intent.destination}餐饮地点尚未核验成功，无法生成完整餐饮日程。")
        context["dining"] = locations
        return {"places": locations, "provider": tool, "warning": error}

    async def route() -> dict:
        bundle["map_locations"] = c._merge_map_locations(context.get("attractions", []), context.get("dining", []), max_rows=65)
        # 资料齐备后只构建一次；路线、预算与质量校验依次更新同一份文档。
        bundle["cover_image"] = await asyncio.to_thread(c.maybe_prepare_destination_cover, destination=intent.destination, **common)
        await asyncio.to_thread(c.maybe_prepare_activity_images, bundle, **common)
        legacy = await asyncio.to_thread(c.build_travel_document, bundle)
        context["document"] = adapt_v2_to_v3(legacy).model_dump(mode="json")
        return {"itinerary": legacy["itinerary"], "candidate_places": legacy.get("candidate_places", [])}

    async def verification() -> dict:
        # 失败的路线核验不得污染下次重试的原始排期。
        document = deepcopy(context["document"])
        document["title"] = f"{intent.destination}{intent.days}日旅行方案"
        from services.transport_hub_service import enrich_selected_transport_hubs
        await enrich_selected_transport_hubs(document, tool_manager)
        prepare_early_departure_breakfast(document)
        dispatcher = getattr(tool_manager, "baidu_request_dispatcher", None)
        await finalize_initial_schedule(document, dispatcher=dispatcher)
        if prepare_early_departure_breakfast(document, after_routes=True):
            await finalize_initial_schedule(document, dispatcher=dispatcher)
        context["document"] = document
        return {"formal_location_ids": document["map_guidance"]["formal_location_ids"], "routes": document["map_guidance"]["day_routes"],
                "issues": document["validation"]["issues"]}

    async def budget() -> dict:
        recalculate_formal_budget(context["document"])
        return context["document"]["budget"]

    async def quality() -> dict:
        document = context["document"]
        refresh_schedule_validation(document)
        if not document["validation"]["valid"]:
            raise ValueError("；".join(item["message"] for item in document["validation"]["issues"] if item["severity"] == "error"))
        from services.meal_schedule_service import meal_targets_by_day
        def clock(section, field):
            option = next((item for item in section["options"] if item["option_id"] == section.get("selected_option_id")), {})
            return (option.get(field) or {}).get("local_iso")
        targets = meal_targets_by_day(days=document["intent"]["days"], date_mode=document["intent"]["date_mode"],
            outbound_arrival=clock(document["outbound_transport"], "arrival_time"), return_departure=clock(document["return_transport"], "departure_time"))
        labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
        missing = [f"第 {day['day']} 天{labels[meal]}" for day in document["itinerary"]["days"]
                   for meal in targets[day["day"]] if meal not in {item.get("meal_type") for item in day["activities"]}
                   and not (meal == "breakfast" and early_breakfast_preparation_covers(document, day["day"]))]
        if missing:
            raise ValueError("最终排期缺少必需餐饮：" + "、".join(missing))
        if document["budget"]["estimated_total"]["amount"] <= 0:
            raise ValueError("最终正式活动缺少非零费用估算，请补充可靠价格后重试。")
        validated = TravelPlanDocumentV3.model_validate(document)
        context["document"] = validated.model_dump(mode="json")
        return {"valid": validated.validation.valid, "issues": document["validation"]["issues"], "status": validated.status}

    async def observe(task, status, attempt, result):
        output = result.value if result is not None and isinstance(result.value, dict) else None
        # 交通原始返回可能含内部工具上下文，只展示规范化交通章节。
        if task.task_id == "transport" and output is not None:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", intent.date_range or "")
            scope = "international" if c.canonical_destination(intent.destination) in c.INTERNATIONAL_DESTINATIONS else "domestic"
            output = {"outbound": c.transport_section_from_bundle(bundle.get("ticket_bundle"), "outbound", scope, dates[0] if dates else None),
                      "return": c.transport_section_from_bundle(bundle.get("return_ticket_bundle"), "return", scope, dates[-1] if dates else None)}
        await queue.put({"type": "planning_task", "payload": {"task_id": task.task_id, "stage": task.stage,
            "agent_name": task.agent_name, "task": task.task, "data_sources": list(task.data_sources), "status": status,
            "attempt": attempt, "duration_ms": result.duration_ms if result is not None else None,
            "summary": f"{task.task}已完成" if result else "正在独立重试" if status == "retrying" else None,
            "result": c.sanitize_user_visible_payload(output) if output is not None else None}})

    async def run():
        try:
            await graph.execute({"requirements": requirements, "research": research, "transport": transport, "lodging": lodging,
                                 "dining": dining, "route": route, "verification": verification, "budget": budget, "quality": quality}, on_event=observe)
            await queue.put({"type": "trip_plan", "document": context["document"], "plan": {}, "task_graph_executed": True})
        except Exception as error:
            await queue.put(error)
        finally:
            await queue.put(None)

    worker = asyncio.create_task(run())
    try:
        while True:
            event = await queue.get()
            if event is None:
                return
            if isinstance(event, Exception):
                raise event
            yield event
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
