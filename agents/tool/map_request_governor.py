from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple


class MapRequestLimitExceeded(RuntimeError):
    pass


@dataclass
class _CacheEntry:
    created_at: float
    value: Any
    ttl_seconds: float


class MapRequestGovernor:
    """Process-wide cache, deduplication and rate guard for Baidu map MCP calls."""

    def __init__(
        self,
        *,
        max_requests_per_scope: int | None = None,
        max_concurrency: int | None = None,
        min_interval_seconds: float | None = None,
        cache_size: int = 1024,
    ) -> None:
        configured_limit = max_requests_per_scope or int(os.getenv("BAIDU_MAP_MAX_REQUESTS_PER_SCOPE", "10"))
        configured_concurrency = max_concurrency or int(os.getenv("BAIDU_MAP_MAX_CONCURRENCY", "1"))
        configured_interval = (
            min_interval_seconds
            if min_interval_seconds is not None
            else float(os.getenv("BAIDU_MAP_MIN_INTERVAL_SECONDS", "0.45"))
        )
        self.max_requests_per_scope = min(10, max(1, configured_limit))
        self.max_concurrency = min(2, max(1, configured_concurrency))
        self.min_interval_seconds = max(0.0, configured_interval)
        self.cache_size = max(32, cache_size)

        self._lock = threading.RLock()
        self._rate_lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)
        self._last_request_started_at = 0.0
        self._cache: Dict[Tuple[str, str], _CacheEntry] = {}
        self._inflight: Dict[Tuple[str, str], threading.Event] = {}
        self._inflight_errors: Dict[Tuple[str, str], BaseException] = {}
        self._session_scopes: Dict[str, str] = {}
        self._scope_counts: Dict[str, int] = {}

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
        return re.sub(r"[\s,，;；]+", "", text)

    @classmethod
    def _canonical_arguments(cls, tool_name: str, kwargs: Dict[str, Any]) -> str:
        normalized_tool = cls._normalize_text(tool_name)
        identifier = next(
            (
                cls._normalize_text(kwargs.get(key))
                for key in ("uid", "place_id", "id")
                if cls._normalize_text(kwargs.get(key))
            ),
            "",
        )
        query = next(
            (
                cls._normalize_text(kwargs.get(key))
                for key in ("address", "query", "keywords", "keyword", "place_name", "text")
                if cls._normalize_text(kwargs.get(key))
            ),
            "",
        )
        region = next(
            (
                cls._normalize_text(kwargs.get(key))
                for key in ("city", "region")
                if cls._normalize_text(kwargs.get(key))
            ),
            "",
        )
        if identifier or query:
            return json.dumps(
                {"identifier": identifier, "query": query, "region": region},
                ensure_ascii=True,
                sort_keys=True,
            )

        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: normalize(value[key]) for key in sorted(value)}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, str):
                return cls._normalize_text(value)
            return value

        return json.dumps(normalize(kwargs), ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _cache_ttl(tool_name: str) -> float:
        normalized = tool_name.lower()
        if "weather" in normalized or "ip_location" in normalized:
            return 600.0
        if "direction" in normalized or "route" in normalized or "distance" in normalized:
            return 900.0
        return 21600.0

    @staticmethod
    def _looks_like_error(value: Any) -> bool:
        candidate = value
        if isinstance(value, str):
            try:
                candidate = json.loads(value)
            except (TypeError, ValueError):
                return False
        if not isinstance(candidate, dict):
            return False
        if candidate.get("error") is True or str(candidate.get("status", "")).lower() in {"error", "failed"}:
            return True
        content = candidate.get("content")
        if isinstance(content, str):
            try:
                return MapRequestGovernor._looks_like_error(json.loads(content))
            except (TypeError, ValueError):
                return False
        return False

    def begin_scope(self, session_id: str, scope_id: str | None = None) -> str:
        session_key = str(session_id or "default")
        effective_scope = str(scope_id or f"{session_key}:{time.monotonic_ns()}")
        with self._lock:
            previous = self._session_scopes.get(session_key)
            self._session_scopes[session_key] = effective_scope
            self._scope_counts.setdefault(effective_scope, 0)
            if previous and previous != effective_scope:
                self._scope_counts.pop(previous, None)
        return effective_scope

    def _scope_for(self, session_id: str) -> str:
        session_key = str(session_id or "default")
        with self._lock:
            return self._session_scopes.setdefault(session_key, session_key)

    def _cached_value(self, key: Tuple[str, str], now: float) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if now - entry.created_at > entry.ttl_seconds:
            self._cache.pop(key, None)
            return None
        return copy.deepcopy(entry.value)

    def _reserve_rate_slot(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            wait_seconds = max(0.0, self._last_request_started_at + self.min_interval_seconds - now)
            self._last_request_started_at = now + wait_seconds
        if wait_seconds:
            time.sleep(wait_seconds)

    def execute(
        self,
        *,
        tool_name: str,
        session_id: str,
        kwargs: Dict[str, Any],
        callback: Callable[[], Any],
    ) -> Any:
        key = (tool_name.lower(), self._canonical_arguments(tool_name, kwargs))
        scope = self._scope_for(session_id)
        owner = False

        with self._lock:
            cached = self._cached_value(key, time.monotonic())
            if cached is not None:
                return cached
            event = self._inflight.get(key)
            if event is None:
                used = self._scope_counts.get(scope, 0)
                if used >= self.max_requests_per_scope:
                    raise MapRequestLimitExceeded(
                        f"Map request limit reached ({self.max_requests_per_scope}) for this planning scope"
                    )
                self._scope_counts[scope] = used + 1
                event = threading.Event()
                self._inflight[key] = event
                self._inflight_errors.pop(key, None)
                owner = True

        if not owner:
            event.wait(timeout=90.0)
            with self._lock:
                cached = self._cached_value(key, time.monotonic())
                if cached is not None:
                    return cached
                error = self._inflight_errors.get(key)
            if error is not None:
                raise error
            raise TimeoutError("Timed out while waiting for a duplicated map request")

        try:
            self._semaphore.acquire()
            try:
                self._reserve_rate_slot()
                value = callback()
            finally:
                self._semaphore.release()
            with self._lock:
                ttl_seconds = 30.0 if self._looks_like_error(value) else self._cache_ttl(tool_name)
                self._cache[key] = _CacheEntry(time.monotonic(), copy.deepcopy(value), ttl_seconds)
                if len(self._cache) > self.cache_size:
                    oldest = min(self._cache, key=lambda item: self._cache[item].created_at)
                    self._cache.pop(oldest, None)
            return value
        except BaseException as error:
            with self._lock:
                self._inflight_errors[key] = error
            raise
        finally:
            with self._lock:
                completed = self._inflight.pop(key, None)
                if completed is not None:
                    completed.set()

    def scope_usage(self, session_id: str) -> int:
        scope = self._scope_for(session_id)
        with self._lock:
            return self._scope_counts.get(scope, 0)
