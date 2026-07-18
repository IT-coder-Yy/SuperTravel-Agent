from __future__ import annotations

import asyncio
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, Literal, Optional

ProviderTimeoutClass = Literal[
    "quick_tool",
    "realtime_transaction",
    "web_research",
    "simple_agent",
    "professional_agent",
]
SourceStatus = Literal[
    "realtime_verified",
    "official_reference",
    "map_reference",
    "guide_reference",
    "user_confirmation_required",
]

PROVIDER_TIMEOUT_SECONDS: Dict[ProviderTimeoutClass, float] = {
    "quick_tool": 15.0,
    "realtime_transaction": 25.0,
    "web_research": 35.0,
    "simple_agent": 45.0,
    "professional_agent": 90.0,
}
SOURCE_PRIORITY: Dict[SourceStatus, int] = {
    "realtime_verified": 0,
    "official_reference": 1,
    "map_reference": 2,
    "guide_reference": 3,
    "user_confirmation_required": 4,
}


class ProviderAttemptLimitExceeded(RuntimeError):
    pass


class ProviderCallTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class SourceFact:
    field: str
    value: Any
    status: SourceStatus
    source_name: str
    updated_at: Optional[str] = None
    official_url: Optional[str] = None


@dataclass(frozen=True)
class ResolvedSourceFact:
    field: str
    value: Any
    status: SourceStatus
    source_name: str
    updated_at: Optional[str] = None
    official_url: Optional[str] = None
    conflicts: tuple[SourceFact, ...] = field(default_factory=tuple)


def resolve_source_fact(facts: Iterable[SourceFact]) -> ResolvedSourceFact:
    candidates = list(facts)
    if not candidates:
        raise ValueError("至少需要一个来源事实")
    fields = {candidate.field for candidate in candidates}
    if len(fields) != 1:
        raise ValueError("一次只能解决同一字段的来源冲突")

    candidates.sort(key=lambda item: SOURCE_PRIORITY[item.status])
    winner = candidates[0]
    winner_priority = SOURCE_PRIORITY[winner.status]
    top_values = {
        repr(candidate.value)
        for candidate in candidates
        if SOURCE_PRIORITY[candidate.status] == winner_priority
    }
    conflicts = tuple(candidate for candidate in candidates[1:] if candidate.value != winner.value)
    if len(top_values) > 1:
        return ResolvedSourceFact(
            field=winner.field,
            value=None,
            status="user_confirmation_required",
            source_name="来源冲突",
            official_url=winner.official_url,
            conflicts=tuple(candidates),
        )
    return ResolvedSourceFact(
        field=winner.field,
        value=winner.value,
        status=winner.status,
        source_name=winner.source_name,
        updated_at=winner.updated_at,
        official_url=winner.official_url,
        conflicts=conflicts,
    )


def realtime_claim_or_confirmation(
    fact: Optional[SourceFact],
    *,
    official_url: Optional[str] = None,
) -> ResolvedSourceFact:
    if fact is not None and fact.status == "realtime_verified":
        return resolve_source_fact([fact])
    field_name = fact.field if fact is not None else "realtime_claim"
    return ResolvedSourceFact(
        field=field_name,
        value=None,
        status="user_confirmation_required",
        source_name="官方实时入口",
        official_url=official_url or (fact.official_url if fact is not None else None),
        conflicts=tuple([fact] if fact is not None else []),
    )


class ProviderGateway:
    """Counts real Provider attempts and applies the shared retry/fallback contract."""

    def __init__(self, *, primary_attempt_limit: int = 3, fallback_attempt_limit: int = 1) -> None:
        self.primary_attempt_limit = max(1, primary_attempt_limit)
        self.fallback_attempt_limit = max(0, fallback_attempt_limit)
        self._lock = threading.RLock()
        self._primary_attempts: Dict[str, int] = {}
        self._fallback_attempts: Dict[str, int] = {}
        self._metrics: Dict[str, int] = {
            "primary_attempts": 0,
            "fallback_attempts": 0,
            "timeouts": 0,
            "failures": 0,
        }

    @staticmethod
    def _fingerprint(provider: str, operation: str, arguments: Optional[Dict[str, Any]]) -> str:
        def normalize_text(value: Any) -> str:
            text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
            return re.sub(r"\s+", " ", text)

        def normalize(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): normalize(value[key]) for key in sorted(value)}
            if isinstance(value, (list, tuple)):
                return [normalize(item) for item in value]
            if isinstance(value, str):
                return normalize_text(value)
            return value

        raw = dict(arguments or {})
        for secret_key in ("ak", "api_key", "token", "access_token"):
            raw.pop(secret_key, None)
        return json.dumps(
            {
                "provider": normalize_text(provider),
                "operation": normalize_text(operation),
                "arguments": normalize(raw),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _key(
        *,
        scope_id: str,
        provider: str,
        operation: str,
        arguments: Optional[Dict[str, Any]],
    ) -> str:
        fingerprint = ProviderGateway._fingerprint(provider, operation, arguments)
        return f"{scope_id or 'default'}:{fingerprint}"

    def _reserve(self, bucket: Dict[str, int], key: str, limit: int, metric: str) -> None:
        with self._lock:
            used = bucket.get(key, 0)
            if used >= limit:
                raise ProviderAttemptLimitExceeded("Provider 参数指纹调用次数已达上限")
            bucket[key] = used + 1
            self._metrics[metric] += 1

    def _record_failure(self, error: BaseException) -> None:
        with self._lock:
            self._metrics["failures"] += 1
            if isinstance(error, (ProviderCallTimeout, asyncio.TimeoutError, FuturesTimeoutError)):
                self._metrics["timeouts"] += 1

    @staticmethod
    def timeout_seconds(timeout_class: ProviderTimeoutClass) -> float:
        return PROVIDER_TIMEOUT_SECONDS[timeout_class]

    @staticmethod
    def _run_sync(callback: Callable[[], Any], timeout_seconds: float) -> Any:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-gateway")
        future = executor.submit(callback)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as error:
            future.cancel()
            raise ProviderCallTimeout("Provider 调用超时") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def execute(
        self,
        callback: Callable[[], Any],
        *,
        scope_id: str,
        provider: str,
        operation: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout_class: ProviderTimeoutClass = "quick_tool",
        fallback_provider: Optional[str] = None,
        fallback_callback: Optional[Callable[[], Any]] = None,
    ) -> Any:
        primary_key = self._key(
            scope_id=scope_id,
            provider=provider,
            operation=operation,
            arguments=arguments,
        )
        self._reserve(self._primary_attempts, primary_key, self.primary_attempt_limit, "primary_attempts")
        try:
            return self._run_sync(callback, self.timeout_seconds(timeout_class))
        except BaseException as primary_error:
            self._record_failure(primary_error)
            if fallback_callback is None or not fallback_provider or self.fallback_attempt_limit == 0:
                raise

        fallback_key = self._key(
            scope_id=scope_id,
            provider=fallback_provider,
            operation=operation,
            arguments=arguments,
        )
        self._reserve(self._fallback_attempts, fallback_key, self.fallback_attempt_limit, "fallback_attempts")
        try:
            return self._run_sync(fallback_callback, self.timeout_seconds(timeout_class))
        except BaseException as fallback_error:
            self._record_failure(fallback_error)
            raise

    async def execute_async(
        self,
        callback: Callable[[], Awaitable[Any]],
        *,
        scope_id: str,
        provider: str,
        operation: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout_class: ProviderTimeoutClass = "quick_tool",
        fallback_provider: Optional[str] = None,
        fallback_callback: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> Any:
        primary_key = self._key(
            scope_id=scope_id,
            provider=provider,
            operation=operation,
            arguments=arguments,
        )
        self._reserve(self._primary_attempts, primary_key, self.primary_attempt_limit, "primary_attempts")
        try:
            return await asyncio.wait_for(callback(), timeout=self.timeout_seconds(timeout_class))
        except asyncio.TimeoutError as error:
            wrapped = ProviderCallTimeout("Provider 调用超时")
            self._record_failure(wrapped)
            primary_error: BaseException = wrapped
        except BaseException as error:
            self._record_failure(error)
            primary_error = error

        if fallback_callback is None or not fallback_provider or self.fallback_attempt_limit == 0:
            raise primary_error
        fallback_key = self._key(
            scope_id=scope_id,
            provider=fallback_provider,
            operation=operation,
            arguments=arguments,
        )
        self._reserve(self._fallback_attempts, fallback_key, self.fallback_attempt_limit, "fallback_attempts")
        try:
            return await asyncio.wait_for(
                fallback_callback(),
                timeout=self.timeout_seconds(timeout_class),
            )
        except asyncio.TimeoutError as error:
            wrapped = ProviderCallTimeout("备用 Provider 调用超时")
            self._record_failure(wrapped)
            raise wrapped from error
        except BaseException as error:
            self._record_failure(error)
            raise

    def clear_scope(self, scope_id: str) -> None:
        prefix = f"{scope_id or 'default'}:"
        with self._lock:
            self._primary_attempts = {
                key: count for key, count in self._primary_attempts.items() if not key.startswith(prefix)
            }
            self._fallback_attempts = {
                key: count for key, count in self._fallback_attempts.items() if not key.startswith(prefix)
            }

    def metrics_snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._metrics)
