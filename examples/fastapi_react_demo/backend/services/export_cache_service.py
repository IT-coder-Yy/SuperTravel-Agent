"""Short-lived, revision-safe cache for generated travel exports."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import time
from typing import Awaitable, Callable, Tuple

from backend.schemas.trip_v3_models import TravelPlanDocumentV3


EXPORT_CACHE_TTL_SECONDS = 10 * 60
EXPORT_CACHE_MAX_ENTRIES = 12
EXPORT_CACHE_MAX_BYTES = 48 * 1024 * 1024


@dataclass(frozen=True)
class CachedExport:
    content: bytes
    expires_at: float


def export_cache_key(document: TravelPlanDocumentV3, export_format: str) -> str:
    """Bind cache reuse to one exact formal revision and its validated content."""

    canonical_document = json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    content_hash = sha256(canonical_document.encode("utf-8")).hexdigest()
    return f"{document.plan_id}:{document.revision}:{export_format}:{content_hash}"


class TravelExportCache:
    """Process-local TTL cache with per-artifact singleflight generation."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: OrderedDict[str, CachedExport] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        self._entries.clear()
        self._locks.clear()

    def _purge(self, now: float) -> None:
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._entries.pop(key, None)
        while (
            len(self._entries) > EXPORT_CACHE_MAX_ENTRIES
            or sum(len(entry.content) for entry in self._entries.values()) > EXPORT_CACHE_MAX_BYTES
        ):
            self._entries.popitem(last=False)

    async def get_or_create(
        self,
        key: str,
        builder: Callable[[], Awaitable[bytes]],
    ) -> Tuple[bytes, bool]:
        now = self._clock()
        self._purge(now)
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached.content, True

        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                now = self._clock()
                self._purge(now)
                cached = self._entries.get(key)
                if cached is not None:
                    self._entries.move_to_end(key)
                    return cached.content, True
                content = await builder()
                stored_at = self._clock()
                self._entries[key] = CachedExport(
                    content=content,
                    expires_at=stored_at + EXPORT_CACHE_TTL_SECONDS,
                )
                self._entries.move_to_end(key)
                self._purge(stored_at)
                return content, False
        finally:
            if not lock.locked():
                self._locks.pop(key, None)


travel_export_cache = TravelExportCache()
