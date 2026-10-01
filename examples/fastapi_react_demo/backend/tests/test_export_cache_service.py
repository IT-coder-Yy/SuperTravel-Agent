import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.export_cache_service import EXPORT_CACHE_TTL_SECONDS, TravelExportCache, export_cache_key


FIXTURE_DIR = Path(__file__).parent / "fixtures"


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def load_document() -> TravelPlanDocumentV3:
    payload = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
    return TravelPlanDocumentV3.model_validate(payload)


class TravelExportCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_one_exact_export_for_ten_minutes_then_regenerates(self):
        clock = MutableClock()
        cache = TravelExportCache(clock=clock)
        builder = AsyncMock(return_value=b"markdown")
        key = export_cache_key(load_document(), "markdown")

        first, first_hit = await cache.get_or_create(key, builder)
        second, second_hit = await cache.get_or_create(key, builder)
        clock.value += EXPORT_CACHE_TTL_SECONDS + 1
        third, third_hit = await cache.get_or_create(key, builder)

        self.assertEqual((b"markdown", False), (first, first_hit))
        self.assertEqual((b"markdown", True), (second, second_hit))
        self.assertEqual((b"markdown", False), (third, third_hit))
        self.assertEqual(2, builder.await_count)

    async def test_singleflight_joins_concurrent_requests_for_the_same_artifact(self):
        cache = TravelExportCache()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def build() -> bytes:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return b"pdf"

        key = export_cache_key(load_document(), "pdf")
        first = asyncio.create_task(cache.get_or_create(key, build))
        await started.wait()
        second = asyncio.create_task(cache.get_or_create(key, build))
        release.set()
        results = await asyncio.gather(first, second)

        self.assertEqual(1, calls)
        self.assertEqual({b"pdf"}, {content for content, _ in results})
        self.assertEqual({False, True}, {hit for _, hit in results})

    def test_key_changes_when_formal_revision_or_content_changes(self):
        document = load_document()
        revised = document.model_copy(update={"revision": document.revision + 1})
        changed_title = document.model_copy(update={"title": "另一份正式方案"})

        self.assertNotEqual(export_cache_key(document, "markdown"), export_cache_key(revised, "markdown"))
        self.assertNotEqual(export_cache_key(document, "markdown"), export_cache_key(changed_title, "markdown"))
        self.assertNotEqual(export_cache_key(document, "markdown"), export_cache_key(document, "pdf"))


if __name__ == "__main__":
    unittest.main()
