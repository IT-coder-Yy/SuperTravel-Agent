from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict

from agents.tool.baidu_request_dispatcher import (
    BaiduRequestDispatcher,
    BaiduRequestPriority,
)


class MapRequestLimitExceeded(RuntimeError):
    """Deprecated compatibility exception; planning requests are no longer hard-capped."""


class MapRequestGovernor:
    """Compatibility facade over the application-level Baidu dispatcher."""

    def __init__(
        self,
        *,
        max_requests_per_scope: int | None = None,
        max_concurrency: int | None = None,
        min_interval_seconds: float | None = None,
        cache_size: int = 1024,
        dispatcher: BaiduRequestDispatcher | None = None,
    ) -> None:
        del max_requests_per_scope, max_concurrency
        self.dispatcher = dispatcher or BaiduRequestDispatcher(
            min_interval_seconds=min_interval_seconds,
            cache_size=cache_size,
        )
        self.max_requests_per_scope = None
        self.max_concurrency = self.dispatcher.max_concurrency
        self.min_interval_seconds = self.dispatcher.min_interval_seconds

        self._lock = threading.RLock()
        self._session_scopes: Dict[str, str] = {}
        self._scope_keys: Dict[str, set[str]] = {}

    @classmethod
    def _canonical_arguments(cls, tool_name: str, kwargs: Dict[str, Any]) -> str:
        return BaiduRequestDispatcher.fingerprint(
            provider="baidu-map",
            operation=tool_name,
            arguments=kwargs,
        )

    @staticmethod
    def _cache_ttl(tool_name: str) -> float:
        normalized = tool_name.lower()
        if "weather" in normalized or "ip_location" in normalized:
            return 600.0
        if "direction" in normalized or "route" in normalized or "distance" in normalized:
            return 900.0
        return 21600.0

    def begin_scope(self, session_id: str, scope_id: str | None = None) -> str:
        session_key = str(session_id or "default")
        effective_scope = str(scope_id or f"{session_key}:{time.monotonic_ns()}")
        with self._lock:
            previous = self._session_scopes.get(session_key)
            self._session_scopes[session_key] = effective_scope
            self._scope_keys.setdefault(effective_scope, set())
            if previous and previous != effective_scope:
                self._scope_keys.pop(previous, None)
        return effective_scope

    def _scope_for(self, session_id: str) -> str:
        session_key = str(session_id or "default")
        with self._lock:
            scope = self._session_scopes.setdefault(session_key, session_key)
            self._scope_keys.setdefault(scope, set())
            return scope

    def execute(
        self,
        *,
        tool_name: str,
        session_id: str,
        kwargs: Dict[str, Any],
        callback: Callable[[], Any],
        priority: BaiduRequestPriority | str | int | None = None,
    ) -> Any:
        scope = self._scope_for(session_id)
        fingerprint = self._canonical_arguments(tool_name, kwargs)
        with self._lock:
            self._scope_keys[scope].add(fingerprint)
        return self.dispatcher.execute(
            callback,
            provider="baidu-map",
            operation=tool_name,
            arguments=kwargs,
            priority=priority,
            cache_ttl_seconds=self._cache_ttl(tool_name),
        )

    def scope_usage(self, session_id: str) -> int:
        scope = self._scope_for(session_id)
        with self._lock:
            return len(self._scope_keys.get(scope, set()))

    def metrics_snapshot(self) -> Dict[str, float | int]:
        return self.dispatcher.metrics_snapshot()
