from __future__ import annotations

import asyncio
import copy
import heapq
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import count
from typing import Any, Awaitable, Callable, Dict, Tuple


class BaiduRequestQueueTimeout(TimeoutError):
    """Raised when a Baidu request cannot start within the queue deadline."""


class BaiduRequestPriority(IntEnum):
    FORMAL = 0
    SCHEDULED_DINING = 10
    CANDIDATE = 20
    SUPPLEMENTAL = 30


_PRIORITY_ALIASES = {
    "formal": BaiduRequestPriority.FORMAL,
    "anchor": BaiduRequestPriority.FORMAL,
    "scheduled_dining": BaiduRequestPriority.SCHEDULED_DINING,
    "dining": BaiduRequestPriority.SCHEDULED_DINING,
    "candidate": BaiduRequestPriority.CANDIDATE,
    "supplemental": BaiduRequestPriority.SUPPLEMENTAL,
    "detail": BaiduRequestPriority.SUPPLEMENTAL,
}
_MISSING = object()


@dataclass
class _CacheEntry:
    created_at: float
    ttl_seconds: float
    value: Any


@dataclass
class _InFlightRequest:
    event: threading.Event = field(default_factory=threading.Event)
    value: Any = _MISSING
    error: BaseException | None = None


@dataclass
class _QueueTicket:
    priority: int
    sequence: int
    provider: str
    operation: str
    queued_at: float
    cancelled: bool = False
    waiting_event_emitted: bool = False


class BaiduRequestDispatcher:
    """Application-level priority queue, cache and singleflight for Baidu calls."""

    def __init__(
        self,
        *,
        min_interval_seconds: float | None = None,
        cooldown_interval_seconds: float | None = None,
        queue_timeout_seconds: float | None = None,
        cache_size: int = 2048,
    ) -> None:
        configured_interval = float(os.getenv("BAIDU_MAP_MIN_INTERVAL_SECONDS", "2"))
        configured_cooldown = float(os.getenv("BAIDU_MAP_COOLDOWN_INTERVAL_SECONDS", "5"))
        configured_queue_timeout = float(os.getenv("BAIDU_MAP_QUEUE_TIMEOUT_SECONDS", "300"))

        self.min_interval_seconds = (
            max(0.0, min_interval_seconds)
            if min_interval_seconds is not None
            else max(2.0, configured_interval)
        )
        self.cooldown_interval_seconds = (
            max(0.0, cooldown_interval_seconds)
            if cooldown_interval_seconds is not None
            else max(5.0, configured_cooldown)
        )
        self.queue_timeout_seconds = (
            max(0.01, queue_timeout_seconds)
            if queue_timeout_seconds is not None
            else max(300.0, configured_queue_timeout)
        )
        self.max_concurrency = 1
        self.cache_size = max(32, cache_size)

        self._state_lock = threading.RLock()
        self._cache: Dict[str, _CacheEntry] = {}
        self._inflight: Dict[str, _InFlightRequest] = {}
        self._event_listeners: list[Callable[[Dict[str, Any]], None]] = []
        self._metrics: Dict[str, float | int] = {
            "total_requests": 0,
            "provider_executions": 0,
            "cache_hits": 0,
            "singleflight_waits": 0,
            "queue_waits": 0,
            "queue_timeouts": 0,
            "pressure_signals": 0,
            "failed_executions": 0,
            "active_executions": 0,
            "peak_concurrency": 0,
            "total_queue_wait_ms": 0.0,
            "max_queue_wait_ms": 0.0,
            "total_execution_ms": 0.0,
            "max_execution_ms": 0.0,
        }

        self._queue_condition = threading.Condition(threading.RLock())
        self._queue: list[Tuple[int, int, _QueueTicket]] = []
        self._sequence = count()
        self._active = False

        self._rate_lock = threading.Lock()
        self._next_request_not_before = 0.0

    def add_event_listener(
        self,
        listener: Callable[[Dict[str, Any]], None],
    ) -> Callable[[], None]:
        with self._state_lock:
            self._event_listeners.append(listener)

        def unsubscribe() -> None:
            with self._state_lock:
                if listener in self._event_listeners:
                    self._event_listeners.remove(listener)

        return unsubscribe

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        with self._state_lock:
            listeners = list(self._event_listeners)
        event = {"type": event_type, "payload": dict(payload)}
        for listener in listeners:
            try:
                listener(self._copy(event))
            except Exception:
                continue

    def _increment_metric(self, name: str, amount: float | int = 1) -> None:
        with self._state_lock:
            self._metrics[name] = self._metrics.get(name, 0) + amount

    def metrics_snapshot(self) -> Dict[str, float | int]:
        with self._queue_condition:
            waiting_count = sum(not ticket.cancelled for _, _, ticket in self._queue)
        with self._state_lock:
            snapshot = dict(self._metrics)
        snapshot["waiting_count"] = waiting_count
        return snapshot

    @staticmethod
    def _copy(value: Any) -> Any:
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
        return re.sub(r"\s+", " ", text)

    @classmethod
    def _normalize_query(cls, value: Any) -> str:
        return re.sub(r"[\s,;、]+", "", cls._normalize_text(value))

    @classmethod
    def _canonical_arguments(cls, arguments: Dict[str, Any] | None) -> Any:
        raw = dict(arguments or {})
        for secret_key in ("ak", "api_key", "token", "access_token"):
            raw.pop(secret_key, None)

        identifier_aliases = ("uid", "place_id", "id")
        query_aliases = ("address", "query", "keywords", "keyword", "place_name", "text")
        region_aliases = ("city", "region")
        identifier = next((cls._normalize_query(raw.get(key)) for key in identifier_aliases if raw.get(key)), "")
        query = next((cls._normalize_query(raw.get(key)) for key in query_aliases if raw.get(key)), "")
        region = next((cls._normalize_query(raw.get(key)) for key in region_aliases if raw.get(key)), "")

        alias_keys = {*identifier_aliases, *query_aliases, *region_aliases}

        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): normalize(value[key]) for key in sorted(value)}
            if isinstance(value, (list, tuple)):
                return [normalize(item) for item in value]
            if isinstance(value, str):
                return cls._normalize_text(value)
            return value

        options = {key: normalize(value) for key, value in sorted(raw.items()) if key not in alias_keys}
        if identifier or query or region:
            return {
                "identifier": identifier,
                "query": query,
                "region": region,
                "options": options,
            }
        return normalize(raw)

    @classmethod
    def fingerprint(
        cls,
        *,
        provider: str,
        operation: str,
        arguments: Dict[str, Any] | None,
    ) -> str:
        return json.dumps(
            {
                "provider": cls._normalize_text(provider),
                "operation": cls._normalize_text(operation),
                "arguments": cls._canonical_arguments(arguments),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def resolve_priority(priority: BaiduRequestPriority | str | int | None) -> int:
        if isinstance(priority, BaiduRequestPriority):
            return int(priority)
        if isinstance(priority, str):
            normalized = priority.strip().lower()
            if normalized in _PRIORITY_ALIASES:
                return int(_PRIORITY_ALIASES[normalized])
            try:
                return int(normalized)
            except ValueError:
                return int(BaiduRequestPriority.CANDIDATE)
        if isinstance(priority, int):
            return priority
        return int(BaiduRequestPriority.CANDIDATE)

    @classmethod
    def _looks_like_error(cls, value: Any) -> bool:
        candidate = value
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except (TypeError, ValueError):
                return False
        if not isinstance(candidate, dict):
            return False
        if candidate.get("error") is True or candidate.get("error_type"):
            return True
        status = candidate.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
            return True
        if isinstance(status, (int, float)) and status != 0:
            return True
        status_code = candidate.get("status_code") or candidate.get("http_status")
        if isinstance(status_code, (int, float)) and status_code >= 400:
            return True
        content = candidate.get("content")
        return cls._looks_like_error(content) if isinstance(content, (str, dict)) else False

    @staticmethod
    def _looks_like_pressure(value: Any) -> bool:
        candidate = value
        if isinstance(value, BaseException):
            response = getattr(value, "response", None)
            if getattr(response, "status_code", None) == 429:
                return True
            candidate = str(value)

        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except (TypeError, ValueError):
                text = candidate.lower()
                return any(
                    marker in text
                    for marker in (
                        "429",
                        "too many requests",
                        "rate limit",
                        "rate_limit",
                        "qps",
                        "quota",
                        "concurrent",
                        "\u9650\u6d41",
                        "\u914d\u989d",
                        "\u5e76\u53d1",
                    )
                )

        if isinstance(candidate, dict):
            if candidate.get("status_code") == 429 or candidate.get("http_status") == 429:
                return True
            return any(BaiduRequestDispatcher._looks_like_pressure(item) for item in candidate.values())
        if isinstance(candidate, (list, tuple)):
            return any(BaiduRequestDispatcher._looks_like_pressure(item) for item in candidate)
        return False

    def _cached_value(self, key: str, now: float) -> Any:
        entry = self._cache.get(key)
        if entry is None:
            return _MISSING
        if now - entry.created_at > entry.ttl_seconds:
            self._cache.pop(key, None)
            return _MISSING
        return self._copy(entry.value)

    def _claim(self, key: str | None) -> Tuple[str, Any]:
        if key is None:
            return "owner", None
        with self._state_lock:
            cached = self._cached_value(key, time.monotonic())
            if cached is not _MISSING:
                return "cached", cached
            inflight = self._inflight.get(key)
            if inflight is not None:
                return "waiter", inflight
            inflight = _InFlightRequest()
            self._inflight[key] = inflight
            return "owner", inflight

    def _complete(
        self,
        key: str | None,
        inflight: _InFlightRequest | None,
        *,
        value: Any = _MISSING,
        error: BaseException | None = None,
        cache_ttl_seconds: float = 0,
    ) -> None:
        if key is None or inflight is None:
            return
        with self._state_lock:
            inflight.value = self._copy(value) if value is not _MISSING else _MISSING
            inflight.error = error
            if (
                error is None
                and value is not _MISSING
                and cache_ttl_seconds > 0
                and not self._looks_like_error(value)
            ):
                self._cache[key] = _CacheEntry(
                    created_at=time.monotonic(),
                    ttl_seconds=cache_ttl_seconds,
                    value=self._copy(value),
                )
                if len(self._cache) > self.cache_size:
                    oldest = min(self._cache, key=lambda item: self._cache[item].created_at)
                    self._cache.pop(oldest, None)
            if self._inflight.get(key) is inflight:
                self._inflight.pop(key, None)
            inflight.event.set()

    def _wait_for_inflight(self, inflight: _InFlightRequest, deadline: float) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not inflight.event.wait(timeout=remaining):
            self._increment_metric("queue_timeouts")
            raise BaiduRequestQueueTimeout("Baidu map singleflight wait exceeded")
        if inflight.error is not None:
            raise inflight.error
        if inflight.value is _MISSING:
            raise RuntimeError("Baidu map singleflight completed without a result")
        return self._copy(inflight.value)

    async def _wait_for_inflight_async(self, inflight: _InFlightRequest, deadline: float) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._increment_metric("queue_timeouts")
            raise BaiduRequestQueueTimeout("Baidu map singleflight wait exceeded")
        completed = await asyncio.to_thread(inflight.event.wait, remaining)
        if not completed:
            self._increment_metric("queue_timeouts")
            raise BaiduRequestQueueTimeout("Baidu map singleflight wait exceeded")
        if inflight.error is not None:
            raise inflight.error
        if inflight.value is _MISSING:
            raise RuntimeError("Baidu map singleflight completed without a result")
        return self._copy(inflight.value)

    def _enqueue(
        self,
        priority: BaiduRequestPriority | str | int | None,
        *,
        provider: str,
        operation: str,
    ) -> _QueueTicket:
        ticket = _QueueTicket(
            priority=self.resolve_priority(priority),
            sequence=next(self._sequence),
            provider=provider or "baidu-map",
            operation=operation or "unspecified",
            queued_at=time.monotonic(),
        )
        with self._queue_condition:
            heapq.heappush(self._queue, (ticket.priority, ticket.sequence, ticket))
            should_wait = self._active or self._queue[0][2] is not ticket
            queue_position = 1 + sum(
                1
                for priority_value, sequence, queued_ticket in self._queue
                if not queued_ticket.cancelled
                and (priority_value, sequence) < (ticket.priority, ticket.sequence)
            )
            self._queue_condition.notify_all()
        if should_wait:
            self._emit_queue_waiting(ticket, queue_position)
        return ticket

    def _emit_queue_waiting(self, ticket: _QueueTicket, queue_position: int) -> None:
        if ticket.waiting_event_emitted:
            return
        ticket.waiting_event_emitted = True
        self._emit(
            "provider_queue_waiting",
            {
                "stage": "realtime_verification",
                "provider": ticket.provider,
                "operation": ticket.operation,
                "priority": self._priority_label(ticket.priority),
                "queue_position": max(1, queue_position),
                "message": "地点核验排队中",
            },
        )

    @staticmethod
    def _priority_label(priority: int) -> str:
        labels = {
            int(BaiduRequestPriority.FORMAL): "formal",
            int(BaiduRequestPriority.SCHEDULED_DINING): "scheduled_dining",
            int(BaiduRequestPriority.CANDIDATE): "candidate",
            int(BaiduRequestPriority.SUPPLEMENTAL): "supplemental",
        }
        return labels.get(priority, "candidate")

    def _clean_queue(self) -> None:
        while self._queue and self._queue[0][2].cancelled:
            heapq.heappop(self._queue)

    def _wait_for_ticket(self, ticket: _QueueTicket, deadline: float, wait_slice: float | None = None) -> bool:
        with self._queue_condition:
            while True:
                self._clean_queue()
                if not self._active and self._queue and self._queue[0][2] is ticket:
                    heapq.heappop(self._queue)
                    self._active = True
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    ticket.cancelled = True
                    self._clean_queue()
                    self._queue_condition.notify_all()
                    self._increment_metric("queue_timeouts")
                    raise BaiduRequestQueueTimeout("Baidu map request queue wait exceeded")
                timeout = remaining if wait_slice is None else min(wait_slice, remaining)
                self._queue_condition.wait(timeout=timeout)
                if wait_slice is not None:
                    return False

    def _cancel_ticket(self, ticket: _QueueTicket) -> None:
        with self._queue_condition:
            ticket.cancelled = True
            self._clean_queue()
            self._queue_condition.notify_all()

    def _release_ticket(self) -> None:
        with self._queue_condition:
            self._active = False
            self._queue_condition.notify_all()

    async def _wait_for_ticket_async(self, ticket: _QueueTicket, deadline: float) -> None:
        while True:
            wait_task = asyncio.create_task(
                asyncio.to_thread(self._wait_for_ticket, ticket, deadline, 0.1)
            )
            try:
                acquired = await asyncio.shield(wait_task)
            except asyncio.CancelledError:
                acquired = await wait_task
                if acquired:
                    self._release_ticket()
                else:
                    self._cancel_ticket(ticket)
                raise
            if acquired:
                return

    def _mark_pressure(self) -> None:
        self._increment_metric("pressure_signals")
        with self._rate_lock:
            self._next_request_not_before = max(
                self._next_request_not_before,
                time.monotonic() + self.cooldown_interval_seconds,
            )

    def _record_queue_wait(self, ticket: _QueueTicket) -> None:
        if not ticket.waiting_event_emitted:
            return
        wait_ms = max(0.0, (time.monotonic() - ticket.queued_at) * 1000)
        with self._state_lock:
            self._metrics["queue_waits"] += 1
            self._metrics["total_queue_wait_ms"] += wait_ms
            self._metrics["max_queue_wait_ms"] = max(
                float(self._metrics["max_queue_wait_ms"]),
                wait_ms,
            )

    def _record_execution_started(self) -> None:
        with self._state_lock:
            self._metrics["provider_executions"] += 1
            self._metrics["active_executions"] += 1
            self._metrics["peak_concurrency"] = max(
                int(self._metrics["peak_concurrency"]),
                int(self._metrics["active_executions"]),
            )

    def _record_execution_completed(self, execution_started: float) -> None:
        execution_ms = max(0.0, (time.monotonic() - execution_started) * 1000)
        with self._state_lock:
            self._metrics["active_executions"] = max(
                0,
                int(self._metrics["active_executions"]) - 1,
            )
            self._metrics["total_execution_ms"] += execution_ms
            self._metrics["max_execution_ms"] = max(
                float(self._metrics["max_execution_ms"]),
                execution_ms,
            )

    def _rate_wait_seconds(self, deadline: float) -> float:
        with self._rate_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._next_request_not_before - now)
            if now + wait_seconds > deadline:
                self._increment_metric("queue_timeouts")
                raise BaiduRequestQueueTimeout("Baidu map request queue wait exceeded")
            self._next_request_not_before = now + wait_seconds + self.min_interval_seconds
            return wait_seconds

    def _execute_owner(
        self,
        callback: Callable[[], Any],
        *,
        deadline: float,
        priority: BaiduRequestPriority | str | int | None,
        provider: str,
        operation: str,
    ) -> Any:
        ticket = self._enqueue(priority, provider=provider, operation=operation)
        self._wait_for_ticket(ticket, deadline)
        try:
            wait_seconds = self._rate_wait_seconds(deadline)
            if wait_seconds:
                self._emit_queue_waiting(ticket, 1)
                time.sleep(wait_seconds)
            self._record_queue_wait(ticket)
            execution_started = time.monotonic()
            self._record_execution_started()
            try:
                result = callback()
            except BaseException as error:
                self._increment_metric("failed_executions")
                if self._looks_like_pressure(error):
                    self._mark_pressure()
                raise
            finally:
                self._record_execution_completed(execution_started)
            if self._looks_like_pressure(result):
                self._mark_pressure()
            return result
        finally:
            self._release_ticket()

    async def _execute_owner_async(
        self,
        callback: Callable[[], Awaitable[Any]],
        *,
        deadline: float,
        priority: BaiduRequestPriority | str | int | None,
        provider: str,
        operation: str,
    ) -> Any:
        ticket = self._enqueue(priority, provider=provider, operation=operation)
        await self._wait_for_ticket_async(ticket, deadline)
        try:
            wait_seconds = self._rate_wait_seconds(deadline)
            if wait_seconds:
                self._emit_queue_waiting(ticket, 1)
                await asyncio.sleep(wait_seconds)
            self._record_queue_wait(ticket)
            execution_started = time.monotonic()
            self._record_execution_started()
            try:
                result = await callback()
            except BaseException as error:
                self._increment_metric("failed_executions")
                if self._looks_like_pressure(error):
                    self._mark_pressure()
                raise
            finally:
                self._record_execution_completed(execution_started)
            if self._looks_like_pressure(result):
                self._mark_pressure()
            return result
        finally:
            self._release_ticket()

    def execute(
        self,
        callback: Callable[[], Any],
        *,
        provider: str = "",
        operation: str = "",
        arguments: Dict[str, Any] | None = None,
        priority: BaiduRequestPriority | str | int | None = None,
        cache_ttl_seconds: float = 0,
    ) -> Any:
        self._increment_metric("total_requests")
        deadline = time.monotonic() + self.queue_timeout_seconds
        key = self.fingerprint(provider=provider, operation=operation, arguments=arguments) if provider and operation else None
        role, state = self._claim(key)
        if role == "cached":
            self._increment_metric("cache_hits")
            return state
        if role == "waiter":
            self._increment_metric("singleflight_waits")
            return self._wait_for_inflight(state, deadline)

        inflight = state if isinstance(state, _InFlightRequest) else None
        try:
            value = self._execute_owner(
                callback,
                deadline=deadline,
                priority=priority,
                provider=provider,
                operation=operation,
            )
        except BaseException as error:
            self._complete(key, inflight, error=error)
            raise
        self._complete(
            key,
            inflight,
            value=value,
            cache_ttl_seconds=max(0.0, cache_ttl_seconds),
        )
        return value

    async def execute_async(
        self,
        callback: Callable[[], Awaitable[Any]],
        *,
        provider: str = "",
        operation: str = "",
        arguments: Dict[str, Any] | None = None,
        priority: BaiduRequestPriority | str | int | None = None,
        cache_ttl_seconds: float = 0,
    ) -> Any:
        self._increment_metric("total_requests")
        deadline = time.monotonic() + self.queue_timeout_seconds
        key = self.fingerprint(provider=provider, operation=operation, arguments=arguments) if provider and operation else None
        role, state = self._claim(key)
        if role == "cached":
            self._increment_metric("cache_hits")
            return state
        if role == "waiter":
            self._increment_metric("singleflight_waits")
            return await self._wait_for_inflight_async(state, deadline)

        inflight = state if isinstance(state, _InFlightRequest) else None
        try:
            value = await self._execute_owner_async(
                callback,
                deadline=deadline,
                priority=priority,
                provider=provider,
                operation=operation,
            )
        except BaseException as error:
            self._complete(key, inflight, error=error)
            raise
        self._complete(
            key,
            inflight,
            value=value,
            cache_ttl_seconds=max(0.0, cache_ttl_seconds),
        )
        return value
