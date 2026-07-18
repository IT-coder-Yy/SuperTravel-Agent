from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable, Callable, Dict, Iterable, List, Optional

from pydantic import ValidationError

try:
    from backend.schemas.trip_v3_models import (
        AgentStagePayload,
        PlanCompletedPayload,
        PlanSectionPayload,
        PlanningErrorPayload,
        PlanningEventEnvelope,
        ProviderQueueWaitingPayload,
        RunCancelledPayload,
        RunStartedPayload,
        SoftTimeoutPayload,
        TravelPlanDocumentV3,
    )
    from backend.services.travel_document_service import (
        adapt_v2_to_v3,
        calculate_document_checksum,
    )
except ModuleNotFoundError:
    from schemas.trip_v3_models import (
        AgentStagePayload,
        PlanCompletedPayload,
        PlanSectionPayload,
        PlanningErrorPayload,
        PlanningEventEnvelope,
        ProviderQueueWaitingPayload,
        RunCancelledPayload,
        RunStartedPayload,
        SoftTimeoutPayload,
        TravelPlanDocumentV3,
    )
    from services.travel_document_service import adapt_v2_to_v3, calculate_document_checksum


STAGE_ORDER = (
    "requirements_analysis",
    "research",
    "route_planning",
    "realtime_verification",
    "validation_completed",
)
STRUCTURED_EVENT_TYPES = {
    "trip_plan_delta",
    "trip_day_upsert",
    "trip_locations",
    "trip_budget",
    "trip_sources",
    "trip_plan_repair",
    "trip_validation",
    "trip_plan",
}


@dataclass(frozen=True)
class PlanningTask:
    task_id: str
    agent_name: str
    stage: str
    task: str
    data_sources: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    timeout_seconds: float = 90.0
    max_attempts: int = 2


@dataclass
class PlanningTaskResult:
    task_id: str
    value: Any
    attempts: int
    duration_ms: int
    status: str = "completed"


class PlanningTaskGraph:
    def __init__(self, tasks: Iterable[PlanningTask]) -> None:
        self.tasks = {task.task_id: task for task in tasks}
        if not self.tasks:
            raise ValueError("任务图不能为空")
        for task in self.tasks.values():
            missing = set(task.dependencies) - set(self.tasks)
            if missing:
                raise ValueError(f"任务 {task.task_id} 存在未知依赖: {sorted(missing)}")
        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("任务图不能包含循环依赖")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in self.tasks[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in self.tasks:
            visit(task_id)

    async def execute(
        self,
        executors: Dict[str, Callable[[], Awaitable[Any]]],
    ) -> Dict[str, PlanningTaskResult]:
        pending = set(self.tasks)
        results: Dict[str, PlanningTaskResult] = {}
        while pending:
            ready = [
                task_id
                for task_id in pending
                if all(dependency in results for dependency in self.tasks[task_id].dependencies)
            ]
            if not ready:
                raise RuntimeError("任务图无法继续执行")
            batch = await asyncio.gather(
                *(self._execute_task(self.tasks[task_id], executors[task_id]) for task_id in ready)
            )
            for result in batch:
                results[result.task_id] = result
                pending.remove(result.task_id)
        return results

    @staticmethod
    async def _execute_task(
        task: PlanningTask,
        executor: Callable[[], Awaitable[Any]],
    ) -> PlanningTaskResult:
        started = time.monotonic()
        last_error: Optional[BaseException] = None
        for attempt in range(1, task.max_attempts + 1):
            try:
                value = await asyncio.wait_for(executor(), timeout=task.timeout_seconds)
                return PlanningTaskResult(
                    task_id=task.task_id,
                    value=value,
                    attempts=attempt,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            except BaseException as error:
                last_error = error
        assert last_error is not None
        raise last_error


def build_default_task_graph() -> PlanningTaskGraph:
    return PlanningTaskGraph(
        [
            PlanningTask("requirements", "需求分析 Agent", "requirements_analysis", "整理日期、人数、预算与偏好", timeout_seconds=45),
            PlanningTask("research", "资料研究 Agent", "research", "检索目的地资料", ("网页/攻略", "本地知识库"), ("requirements",)),
            PlanningTask("transport", "交通 Agent", "research", "核验往返交通", ("官方实时交通",), ("requirements",)),
            PlanningTask("lodging", "住宿 Agent", "research", "筛选住宿区域", ("地图", "可信网页"), ("requirements",)),
            PlanningTask("dining", "美食 Agent", "research", "筛选真实餐饮地点", ("地图", "可信攻略"), ("requirements",)),
            PlanningTask("route", "路线 Agent", "route_planning", "编排日程与路线", ("地图",), ("research", "transport", "lodging", "dining")),
            PlanningTask("budget", "预算 Agent", "route_planning", "汇总预算与未知费用", dependencies=("route",)),
            PlanningTask("verification", "实时核验 Agent", "realtime_verification", "核验正式地点、路线与强实时事实", ("官方实时交通", "百度地图"), ("route",)),
            PlanningTask("quality", "质量 Agent", "validation_completed", "执行完整 V3 Schema 与业务校验", dependencies=("budget", "verification")),
        ]
    )


@dataclass
class _EventFactory:
    run_id: str
    request_id: str
    sequence: int = 0

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def legacy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event = dict(payload)
        event["request_id"] = self.request_id
        event["sequence"] = self.next_sequence()
        return event

    def planning(self, event_type: str, payload: Any) -> Dict[str, Any]:
        sequence = self.next_sequence()
        event = PlanningEventEnvelope(
            event_id=f"evt_{self.run_id}_{sequence}",
            run_id=self.run_id,
            request_id=self.request_id,
            sequence=sequence,
            occurred_at=datetime.now(timezone.utc),
            type=event_type,
            payload=payload,
        )
        return event.model_dump(mode="json")


def encode_sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def decode_sse(chunk: Any) -> Optional[Dict[str, Any]]:
    text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def build_cancelled_event(
    *,
    run_id: str,
    request_id: str,
    sequence: int,
    reason: str,
) -> Dict[str, Any]:
    return PlanningEventEnvelope(
        event_id=f"evt_{run_id}_{sequence}",
        run_id=run_id,
        request_id=request_id,
        sequence=sequence,
        occurred_at=datetime.now(timezone.utc),
        type="run_cancelled",
        payload=RunCancelledPayload(reason=reason),
    ).model_dump(mode="json")


class PlanningOrchestrator:
    """Turns the legacy chat stream into a validated, observable planning pipeline."""

    def __init__(
        self,
        *,
        baidu_dispatcher: Any = None,
        ordinary_soft_timeout_seconds: float = 180,
        ordinary_hard_timeout_seconds: float = 300,
        baidu_soft_timeout_seconds: float = 300,
        baidu_hard_timeout_seconds: float = 480,
    ) -> None:
        self.baidu_dispatcher = baidu_dispatcher
        self.ordinary_soft_timeout_seconds = ordinary_soft_timeout_seconds
        self.ordinary_hard_timeout_seconds = ordinary_hard_timeout_seconds
        self.baidu_soft_timeout_seconds = baidu_soft_timeout_seconds
        self.baidu_hard_timeout_seconds = baidu_hard_timeout_seconds
        self.task_graph = build_default_task_graph()

    @staticmethod
    def _stage_payload(
        stage: str,
        *,
        status: str,
        summary: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> AgentStagePayload:
        definitions = {
            "requirements_analysis": ("需求分析 Agent", "整理日期、人数、预算与偏好", ["用户输入", "用户画像"]),
            "research": ("资料研究 Agent", "并行研究交通、住宿、美食与目的地资料", ["官方实时交通", "网页/攻略", "本地知识库"]),
            "route_planning": ("路线 Agent", "编排每日活动、餐饮、预算与路线", ["研究结果", "地图"]),
            "realtime_verification": ("实时核验 Agent", "核验正式地点、路线与强实时事实", ["官方实时交通", "百度地图"]),
            "validation_completed": ("质量 Agent", "执行完整 V3 Schema 与业务校验", ["V3 Schema", "来源冲突规则"]),
        }
        agent_name, task, sources = definitions[stage]
        return AgentStagePayload(
            stage=stage,
            agent_name=agent_name,
            task=task,
            data_sources=sources,
            summary=summary,
            duration_ms=duration_ms,
            status=status,
        )

    @staticmethod
    def _document_sections(document: TravelPlanDocumentV3) -> List[tuple[str, Dict[str, Any]]]:
        payload = document.model_dump(mode="json")
        return [
            (
                "destination_overview",
                {
                    "title": payload["title"],
                    "intent": payload["intent"],
                    "destination_overview": payload["destination_overview"],
                },
            ),
            (
                "outbound_transport",
                {
                    "outbound_transport": payload["outbound_transport"],
                    "return_transport": payload["return_transport"],
                },
            ),
            ("lodging_plan", payload["lodging_plan"]),
            ("itinerary", payload["itinerary"]),
            (
                "budget_and_reminders",
                {
                    "budget": payload["budget"],
                    "candidate_pool": payload["candidate_pool"],
                    "action_items": payload["action_items"],
                    "notes": payload["notes"],
                },
            ),
            ("map_guidance", payload["map_guidance"]),
            ("delivery", payload["delivery"]),
            ("sources", {"sources": payload["sources"]}),
        ]

    @staticmethod
    async def _invoke_document_callback(
        callback: Optional[Callable[[Dict[str, Any]], Any]],
        document: Dict[str, Any],
    ) -> None:
        if callback is None:
            return
        result = callback(document)
        if inspect.isawaitable(result):
            await result

    async def orchestrate(
        self,
        source: AsyncIterator[Any],
        *,
        run_id: str,
        request_id: str,
        target_revision: int = 1,
        existing_plan_id: Optional[str] = None,
        requires_baidu_verification: bool = True,
        on_validated_document: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AsyncGenerator[str, None]:
        factory = _EventFactory(run_id=run_id, request_id=request_id)
        soft_timeout = (
            self.baidu_soft_timeout_seconds
            if requires_baidu_verification
            else self.ordinary_soft_timeout_seconds
        )
        hard_timeout = (
            self.baidu_hard_timeout_seconds
            if requires_baidu_verification
            else self.ordinary_hard_timeout_seconds
        )
        started = time.monotonic()
        stage_started_at: Dict[str, float] = {"requirements_analysis": started}
        active_stage = "requirements_analysis"
        soft_timeout_emitted = False
        formal_document_emitted = False
        clarification_finished = False
        buffered_structured: List[Dict[str, Any]] = []
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        async def consume_source() -> None:
            try:
                async for chunk in source:
                    await queue.put(("chunk", chunk))
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                await queue.put(("error", error))
            finally:
                await queue.put(("done", None))

        def provider_listener(event: Dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ("provider", event))

        unsubscribe = (
            self.baidu_dispatcher.add_event_listener(provider_listener)
            if self.baidu_dispatcher is not None
            else lambda: None
        )
        producer = asyncio.create_task(consume_source())

        def complete_stage(stage: str, summary: str, status: str = "completed") -> Dict[str, Any]:
            duration_ms = round((time.monotonic() - stage_started_at.get(stage, started)) * 1000)
            return factory.planning(
                "agent_stage_completed",
                self._stage_payload(stage, status=status, summary=summary, duration_ms=duration_ms),
            )

        def start_stage(stage: str) -> Dict[str, Any]:
            nonlocal active_stage
            active_stage = stage
            stage_started_at[stage] = time.monotonic()
            return factory.planning(
                "agent_stage_started",
                self._stage_payload(stage, status="running"),
            )

        async def fail_run(code: str, user_message: str, retryable: bool = True) -> AsyncGenerator[str, None]:
            yield encode_sse(
                factory.planning(
                    "error",
                    PlanningErrorPayload(
                        code=code,
                        user_message=user_message,
                        retryable=retryable,
                        actions=["retry"],
                    ),
                )
            )
            yield encode_sse(
                factory.legacy(
                    {
                        "type": "error",
                        "code": code,
                        "phase": active_stage,
                        "retryable": retryable,
                        "user_message": user_message,
                        "actions": ["retry"],
                    }
                )
            )
            yield encode_sse(
                factory.legacy(
                    {
                        "type": "chat_complete",
                        "message_id": "",
                        "finish_reason": "failed",
                    }
                )
            )

        try:
            yield encode_sse(
                factory.planning(
                    "run_started",
                    RunStartedPayload(message="开始规划"),
                )
            )
            yield encode_sse(start_stage("requirements_analysis"))

            while True:
                elapsed = time.monotonic() - started
                if elapsed >= hard_timeout:
                    producer.cancel()
                    async for failed_event in fail_run(
                        "RUN_HARD_TIMEOUT",
                        "本次规划已达到最长处理时间，未校验内容不会进入正式方案。",
                    ):
                        yield failed_event
                    return
                next_timeout = hard_timeout - elapsed
                if not soft_timeout_emitted:
                    next_timeout = min(next_timeout, max(0.0, soft_timeout - elapsed))
                try:
                    item_type, item = await asyncio.wait_for(queue.get(), timeout=next_timeout)
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - started
                    if not soft_timeout_emitted and elapsed >= soft_timeout:
                        soft_timeout_emitted = True
                        yield encode_sse(
                            factory.planning(
                                "run_soft_timeout",
                                SoftTimeoutPayload(
                                    elapsed_seconds=max(180, round(soft_timeout)),
                                    message="规划仍在继续，正在优先完成正式地点与方案校验。",
                                ),
                            )
                        )
                        continue
                    continue

                if item_type == "provider":
                    payload = item.get("payload") if isinstance(item, dict) else None
                    if isinstance(payload, dict):
                        yield encode_sse(
                            factory.planning(
                                "provider_queue_waiting",
                                ProviderQueueWaitingPayload.model_validate(payload),
                            )
                        )
                    continue
                if item_type == "error":
                    async for failed_event in fail_run(
                        "PLANNING_PIPELINE_FAILED",
                        "本次规划暂时中断，请重试。",
                    ):
                        yield failed_event
                    return
                if item_type == "done":
                    if buffered_structured and not formal_document_emitted:
                        async for failed_event in fail_run(
                            "FORMAL_DOCUMENT_MISSING",
                            "方案未完成完整校验，地图和工作台数据未发布。",
                            retryable=True,
                        ):
                            yield failed_event
                    elif active_stage == "requirements_analysis" and not clarification_finished:
                        yield encode_sse(complete_stage(active_stage, "需求处理结束", status="degraded"))
                    return

                payload = decode_sse(item)
                if payload is None:
                    continue
                event_type = str(payload.get("type") or "")

                if event_type == "trip_intent" and active_stage == "requirements_analysis":
                    yield encode_sse(complete_stage("requirements_analysis", "已整理行程约束"))
                    yield encode_sse(start_stage("research"))
                if event_type == "clarification_required":
                    clarification_finished = True
                    if active_stage == "requirements_analysis":
                        yield encode_sse(complete_stage("requirements_analysis", "仍需补充一个关键条件", status="degraded"))

                if event_type in STRUCTURED_EVENT_TYPES:
                    buffered_structured.append(payload)
                    if active_stage == "requirements_analysis":
                        yield encode_sse(complete_stage("requirements_analysis", "已整理行程约束"))
                        yield encode_sse(start_stage("research"))
                    if active_stage == "research":
                        yield encode_sse(complete_stage("research", "交通、住宿、美食与目的地资料已汇总"))
                        yield encode_sse(start_stage("route_planning"))

                    if event_type != "trip_plan":
                        continue
                    raw_document = payload.get("document")
                    if not isinstance(raw_document, dict):
                        async for failed_event in fail_run(
                            "FORMAL_DOCUMENT_MISSING",
                            "方案文档缺失，未发布地图和工作台数据。",
                        ):
                            yield failed_event
                        return
                    try:
                        adapted = adapt_v2_to_v3(raw_document)
                        document_payload = adapted.model_dump(mode="json")
                        document_payload["revision"] = max(1, int(target_revision))
                        if existing_plan_id:
                            document_payload["plan_id"] = existing_plan_id
                        document = TravelPlanDocumentV3.model_validate(document_payload)
                    except (ValidationError, ValueError, TypeError) as error:
                        async for failed_event in fail_run(
                            "FORMAL_DOCUMENT_VALIDATION_FAILED",
                            "方案未通过完整校验，地图和工作台数据未发布。",
                            retryable=True,
                        ):
                            yield failed_event
                        return

                    yield encode_sse(complete_stage("route_planning", "日程、餐饮、预算与路线编排完成"))
                    yield encode_sse(start_stage("realtime_verification"))
                    verification_status = "degraded" if document.status == "degraded" else "completed"
                    yield encode_sse(
                        complete_stage(
                            "realtime_verification",
                            "正式地点、路线和强实时事实核验完成",
                            status=verification_status,
                        )
                    )
                    yield encode_sse(start_stage("validation_completed"))
                    validated_payload = document.model_dump(mode="json")
                    try:
                        await self._invoke_document_callback(on_validated_document, validated_payload)
                    except Exception:
                        async for failed_event in fail_run(
                            "FORMAL_SNAPSHOT_COMMIT_FAILED",
                            "正式方案暂时无法保存，地图和工作台数据未发布，请重试。",
                            retryable=True,
                        ):
                            yield failed_event
                        return
                    yield encode_sse(
                        complete_stage(
                            "validation_completed",
                            "完整 V3 Schema 与业务规则校验通过",
                            status=verification_status,
                        )
                    )
                    for section_name, content in self._document_sections(document):
                        yield encode_sse(
                            factory.planning(
                                "final_plan_section",
                                PlanSectionPayload(
                                    plan_id=document.plan_id,
                                    revision=document.revision,
                                    section=section_name,
                                    content=content,
                                ),
                            )
                        )
                    checksum = calculate_document_checksum(document)
                    yield encode_sse(
                        factory.planning(
                            "trip_plan_completed",
                            PlanCompletedPayload(
                                plan_id=document.plan_id,
                                revision=document.revision,
                                status=(
                                    "completed_degraded"
                                    if document.status == "degraded"
                                    else "completed"
                                ),
                                checksum=checksum,
                            ),
                        )
                    )
                    formal_document_emitted = True
                    for buffered in buffered_structured:
                        yield encode_sse(factory.legacy(buffered))
                    buffered_structured.clear()
                    continue

                yield encode_sse(factory.legacy(payload))
        finally:
            current_task = asyncio.current_task()
            cancelling = getattr(current_task, "cancelling", None)
            cancellation_requested = bool(cancelling()) if callable(cancelling) else False
            unsubscribe()
            if not producer.done():
                producer.cancel()
            try:
                await producer
            except (asyncio.CancelledError, Exception):
                pass
            if cancellation_requested:
                raise asyncio.CancelledError
