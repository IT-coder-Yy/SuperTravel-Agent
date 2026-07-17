import importlib
import importlib.util
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


class KnowledgeSearchRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        if importlib.util.find_spec("httpx") is None:
            raise unittest.SkipTest("httpx is not installed in current environment")

        cls.httpx = importlib.import_module("httpx")
        cls.main = importlib.import_module("main")

    async def get(self, path: str):
        transport = self.httpx.ASGITransport(app=self.main.app)
        async with self.httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    async def test_search_hangzhou_returns_city_related_items(self):
        response = await self.get("/api/knowledge/search?q=杭州&top_k=8")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["query"], "杭州")
        self.assertEqual(payload["top_k"], 8)
        self.assertGreater(len(payload["items"]), 0)
        self.assertLessEqual(len(payload["items"]), 8)
        self.assertEqual(payload["items"][0]["city"], "杭州")

        required_fields = {
            "city",
            "title",
            "source",
            "snippet",
            "score",
            "matched_terms",
        }
        self.assertTrue(required_fields.issubset(payload["items"][0]))

    async def test_cities_returns_available_city_catalog(self):
        response = await self.get("/api/knowledge/cities")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["total_cities"], 305)
        self.assertGreaterEqual(payload["total_chunks"], 975)
        self.assertEqual(len(payload["cities"]), payload["total_cities"])
        self.assertTrue(any(item["city"] == "杭州" for item in payload["cities"]))
        self.assertTrue(any(item["city"] == "东京" and item["scope"] == "international" for item in payload["cities"]))

    async def test_search_requires_non_empty_query(self):
        missing_response = await self.get("/api/knowledge/search")
        empty_response = await self.get("/api/knowledge/search?q=")

        self.assertEqual(missing_response.status_code, 422)
        self.assertEqual(empty_response.status_code, 422)

    async def test_search_normalizes_whitespace_query_to_empty_results(self):
        response = await self.get("/api/knowledge/search?q=%20%20%20&top_k=3")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"query": "", "top_k": 3, "items": []},
        )

    async def test_search_rejects_top_k_outside_supported_range(self):
        too_small = await self.get("/api/knowledge/search?q=杭州&top_k=0")
        too_large = await self.get("/api/knowledge/search?q=杭州&top_k=21")

        self.assertEqual(too_small.status_code, 422)
        self.assertEqual(too_large.status_code, 422)


if __name__ == "__main__":
    unittest.main()
