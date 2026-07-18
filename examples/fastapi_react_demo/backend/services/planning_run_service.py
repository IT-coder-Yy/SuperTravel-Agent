import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional

from .planning_event_store import PlanningEventStore
from .planning_orchestrator import (
    PlanningOrchestrator,
    build_cancelled_event,
    decode_sse,
    encode_sse,
)


def _decode_sse_payload(chunk: Any) -> Optional[Dict[str, Any]]:
    if isinstance(chunk, bytes):
        text = chunk.decode("utf-8", errors="replace")
    else:
        text = str(chunk)
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def attach_persisted_planning_run(
    response: Any,
    *,
    repository: Any,
    device_id: str,
    trip_id: str,
    run_id: str,
) -> Any:
    """Persist public SSE events and release the device run lock on every exit path."""
    original_iterator = response.body_iterator

    async def persisted_iterator():
        terminal_status = "cancelled"
        completed_normally = False
        try:
            async for chunk in original_iterator:
                payload = _decode_sse_payload(chunk)
                if payload:
                    sequence = int(payload.get("sequence") or 0)
                    if sequence > 0:
                        repository.append_run_event(
                            run_id,
                            sequence,
                            f"{run_id}:{sequence}",
                            payload,
                            datetime.now(timezone.utc).isoformat(),
                        )
                    if payload.get("type") == "error":
                        terminal_status = "failed"
                    elif payload.get("type") == "chat_complete":
                        finish_reason = str(payload.get("finish_reason") or "")
                        terminal_status = "failed" if finish_reason == "failed" else "completed"
                yield chunk
            completed_normally = True
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            raise
        except Exception:
            terminal_status = "failed"
            raise
        finally:
            if completed_normally and terminal_status == "cancelled":
                terminal_status = "failed"
            repository.finish_planning_run(device_id, run_id, terminal_status)

    response.body_iterator = persisted_iterator()
    return response


@dataclass
class _ManagedPlanningRun:
    run_id: str
    request_id: str
    device_id: str
    trip_id: str
    task: Optional[asyncio.Task] = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    last_sequence: int = 0
    terminal_status: str = "failed"
    cancel_reason: str = "用户停止规划"
    cancel_requested: bool = False


class PlanningRunManager:
    """Runs planning independently from an individual HTTP connection."""

    def __init__(
        self,
        *,
        repository: Any,
        orchestrator: PlanningOrchestrator,
        tool_manager: Any = None,
        provider_gateway: Any = None,
    ) -> None:
        self.repository = repository
        self.event_store = PlanningEventStore(repository)
        self.orchestrator = orchestrator
        self.tool_manager = tool_manager
        self.provider_gateway = provider_gateway
        self._runs: Dict[str, _ManagedPlanningRun] = {}

    def update_runtime_dependencies(self, *, tool_manager: Any = None) -> None:
        if tool_manager is not None:
            self.tool_manager = tool_manager

    def start(
        self,
        source: Any,
        *,
        run_id: str,
        request_id: str,
        device_id: str,
        trip_id: str,
        target_revision: int,
        existing_plan_id: Optional[str],
        requires_baidu_verification: bool,
    ) -> _ManagedPlanningRun:
        existing = self._runs.get(run_id)
        if existing is not None and existing.task is not None and not existing.task.done():
            return existing
        managed = _ManagedPlanningRun(
            run_id=run_id,
            request_id=request_id,
            device_id=device_id,
            trip_id=trip_id,
        )
        self._runs[run_id] = managed
        managed.task = asyncio.create_task(
            self._consume(
                managed,
                source,
                target_revision=target_revision,
                existing_plan_id=existing_plan_id,
                requires_baidu_verification=requires_baidu_verification,
            ),
            name=f"planning-run:{run_id}",
        )
        return managed

    async def _publish(self, managed: _ManagedPlanningRun, payload: Dict[str, Any]) -> None:
        if managed.cancel_requested and payload.get("type") != "run_cancelled":
            return
        sequence = int(payload.get("sequence") or 0)
        if sequence <= managed.last_sequence:
            return
        if self.event_store.append(managed.run_id, payload):
            managed.last_sequence = sequence
            for subscriber in list(managed.subscribers):
                await subscriber.put(payload)

    async def _consume(
        self,
        managed: _ManagedPlanningRun,
        source: Any,
        *,
        target_revision: int,
        existing_plan_id: Optional[str],
        requires_baidu_verification: bool,
    ) -> None:
        terminal_status = "failed"

        def persist_formal_document(document: Dict[str, Any]) -> None:
            self.repository.apply_formal_snapshot(
                managed.device_id,
                managed.trip_id,
                f"planning:{managed.run_id}",
                document,
            )

        try:
            async for chunk in self.orchestrator.orchestrate(
                source,
                run_id=managed.run_id,
                request_id=managed.request_id,
                target_revision=target_revision,
                existing_plan_id=existing_plan_id,
                requires_baidu_verification=requires_baidu_verification,
                on_validated_document=persist_formal_document,
            ):
                payload = decode_sse(chunk)
                if payload is None:
                    continue
                await self._publish(managed, payload)
                event_type = str(payload.get("type") or "")
                if event_type == "trip_plan_completed":
                    completion_status = str((payload.get("payload") or {}).get("status") or "")
                    terminal_status = "degraded" if completion_status == "completed_degraded" else "completed"
                elif event_type == "error":
                    terminal_status = "failed"
                elif event_type == "chat_complete" and terminal_status == "failed":
                    finish_reason = str(payload.get("finish_reason") or "")
                    if finish_reason in {"completed", "clarification_required"}:
                        terminal_status = "completed"
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            if not managed.cancel_requested:
                cancelled = build_cancelled_event(
                    run_id=managed.run_id,
                    request_id=managed.request_id,
                    sequence=managed.last_sequence + 1,
                    reason=managed.cancel_reason,
                )
                await self._publish(managed, cancelled)
        except Exception:
            terminal_status = "failed"
        finally:
            managed.terminal_status = terminal_status
            self.repository.finish_planning_run(managed.device_id, managed.run_id, terminal_status)
            if terminal_status == "cancelled" or managed.cancel_requested:
                self.event_store.clear_run(managed.device_id, managed.run_id)
            if self.provider_gateway is not None:
                self.provider_gateway.clear_scope(managed.run_id)
            if self.tool_manager is not None:
                cleanup_session = getattr(self.tool_manager, "cleanup_session", None)
                if callable(cleanup_session):
                    try:
                        await cleanup_session(managed.run_id)
                    except Exception:
                        pass
            for subscriber in list(managed.subscribers):
                await subscriber.put(None)

    async def stream(
        self,
        *,
        run_id: str,
        after_sequence: int = 0,
    ) -> AsyncGenerator[str, None]:
        cursor = max(0, int(after_sequence))
        managed = self._runs.get(run_id)
        queue: asyncio.Queue = asyncio.Queue()
        if managed is not None:
            managed.subscribers.add(queue)
        try:
            for payload in self.event_store.replay(run_id, cursor):
                sequence = int(payload.get("sequence") or 0)
                if sequence <= cursor:
                    continue
                cursor = sequence
                yield encode_sse(payload)
            if managed is None or managed.task is None or managed.task.done():
                return
            while True:
                payload = await queue.get()
                if payload is None:
                    return
                sequence = int(payload.get("sequence") or 0)
                if sequence <= cursor:
                    continue
                cursor = sequence
                yield encode_sse(payload)
        finally:
            if managed is not None:
                managed.subscribers.discard(queue)

    async def cancel(self, *, device_id: str, run_id: str, reason: str = "用户停止规划") -> Dict[str, Any]:
        run = self.repository.planning_run(device_id, run_id)
        if run is None:
            raise KeyError("PLANNING_RUN_NOT_FOUND")
        managed = self._runs.get(run_id)
        if managed is None or managed.task is None or managed.task.done():
            return {"run_id": run_id, "status": run["status"], "cancelled": False}
        managed.cancel_reason = reason
        managed.cancel_requested = True
        cancelled = build_cancelled_event(
            run_id=managed.run_id,
            request_id=managed.request_id,
            sequence=managed.last_sequence + 1,
            reason=reason,
        )
        await self._publish(managed, cancelled)
        self.repository.finish_planning_run(device_id, run_id, "cancelled")
        self.event_store.clear_run(device_id, run_id)
        if self.provider_gateway is not None:
            self.provider_gateway.clear_scope(run_id)
        managed.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(managed.task), timeout=0.25)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        return {"run_id": run_id, "status": "cancelled", "cancelled": True}

    async def close(self) -> None:
        tasks = [
            managed.task
            for managed in self._runs.values()
            if managed.task is not None and not managed.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
