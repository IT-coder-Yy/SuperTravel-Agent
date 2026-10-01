import importlib
import importlib.util
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


class TripEditRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        if importlib.util.find_spec("httpx") is None:
            raise unittest.SkipTest("httpx is not installed in current environment")

        cls.httpx = importlib.import_module("httpx")
        cls.main = importlib.import_module("main")

    def setUp(self):
        self.plan = {
            "plan_id": "plan-beijing",
            "version": 3,
            "activities": [
                {
                    "activity_id": "act-1",
                    "day": 1,
                    "title": "故宫博物院",
                    "estimated_cost": 60,
                }
            ],
            "warnings": [],
        }

    async def post(self, payload):
        transport = self.httpx.ASGITransport(app=self.main.app)
        async with self.httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post("/api/trip-edit", json=payload)

    async def post_delete_impact(self, payload):
        transport = self.httpx.ASGITransport(app=self.main.app)
        async with self.httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post("/api/trip-drafts/delete-impact", json=payload)

    async def post_route_optimization_preview(self, payload):
        transport = self.httpx.ASGITransport(app=self.main.app)
        async with self.httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post("/api/trip-drafts/route-optimization-preview", json=payload)

    def operation(self, **overrides):
        value = {
            "operation_id": "op-1",
            "plan_id": "plan-beijing",
            "base_version": 3,
            "type": "add_activity",
            "payload": {
                "activity": {
                    "activity_id": "act-2",
                    "day": 2,
                    "title": "颐和园",
                    "estimated_cost": 30,
                }
            },
        }
        value.update(overrides)
        return value

    async def test_trip_edit_returns_updated_plan_and_diff(self):
        original_plan = deepcopy(self.plan)

        response = await self.post(
            {"plan": self.plan, "operation": self.operation()}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["operation_id"], "op-1")
        self.assertEqual(payload["plan"]["version"], 4)
        self.assertEqual(payload["previous_plan"], original_plan)
        self.assertEqual(payload["diff"]["affected_days"], [2])
        self.assertEqual(payload["diff"]["added"][0]["activity_id"], "act-2")

    async def test_trip_edit_returns_409_for_version_conflict(self):
        response = await self.post(
            {
                "plan": self.plan,
                "operation": self.operation(base_version=2),
            }
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "VERSION_CONFLICT",
                    "message": "当前行程已更新，请刷新后重试",
                }
            },
        )

    async def test_trip_edit_returns_safe_422_for_unsupported_operation(self):
        response = await self.post(
            {
                "plan": self.plan,
                "operation": self.operation(type="delete_plan", payload={}),
            }
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "UNSUPPORTED_OPERATION",
                    "message": "不支持的行程编辑操作",
                }
            },
        )
        serialized = response.text.lower()
        self.assertNotIn("traceback", serialized)
        self.assertNotIn("unsupported operation:", serialized)
        self.assertNotIn("delete_plan", serialized)

    async def test_trip_edit_accepts_v3_document_and_returns_synced_document_snapshot(self):
        document = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
        activity_id = document["itinerary"]["days"][0]["activities"][0]["activity_id"]

        response = await self.post(
            {
                "plan": self.plan,
                "document": document,
                "operation": {
                    "operation_id": "op-v3-candidate",
                    "plan_id": document["plan_id"],
                    "base_version": document["revision"],
                    "type": "move_activity_to_candidate",
                    "payload": {"activity_id": activity_id},
                },
            }
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(2, payload["document"]["revision"])
        self.assertEqual("3.0", payload["plan"]["schema_version"])
        self.assertEqual(1, payload["previous_document"]["revision"])
        self.assertIn(
            activity_id,
            [candidate["activity_id"] for candidate in payload["document"]["candidate_pool"]],
        )
        self.assertTrue(payload["draft_validation"]["can_apply"])
        self.assertEqual("degraded", payload["draft_validation"]["status"])

    async def test_trip_edit_accepts_document_only_v3_time_shift(self):
        document = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
        activity = document["itinerary"]["days"][0]["activities"][0]
        original_start = activity["start_at"]

        response = await self.post({
            "document": document,
            "operation": {
                "operation_id": "op-v3-document-only-shift",
                "plan_id": document["plan_id"],
                "base_version": document["revision"],
                "type": "shift_activity_time",
                "payload": {"activity_id": activity["activity_id"], "delta_minutes": 15},
            },
        })

        self.assertEqual(200, response.status_code)
        payload = response.json()
        shifted = payload["document"]["itinerary"]["days"][0]["activities"][0]
        self.assertNotEqual(original_start, shifted["start_at"])
        self.assertEqual(2, payload["document"]["revision"])
        self.assertEqual("3.0", payload["plan"]["schema_version"])

    async def test_trip_draft_validation_reports_hard_errors_without_deleting_the_draft(self):
        document = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
        document["itinerary"]["days"].pop()
        transport = self.httpx.ASGITransport(app=self.main.app)
        async with self.httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post("/api/trip-drafts/validate", json={"document": document})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertFalse(payload["can_apply"])
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("INVALID_TRIP_DOCUMENT", payload["hard_errors"][0]["code"])

    async def test_trip_draft_delete_impact_returns_server_counts(self):
        document = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
        activity = document["itinerary"]["days"][0]["activities"][0]
        activity["images"] = [{
            "image_id": "route-impact-image",
            "url": "https://example.test/route-impact.jpg",
            "alt": "删除影响测试图片",
        }]

        response = await self.post_delete_impact({
            "document": document,
            "activity_id": activity["activity_id"],
        })

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(activity["activity_id"], payload["activity_id"])
        self.assertEqual(1, payload["image_references"])
        self.assertEqual(1, payload["map_markers"])

    async def test_route_optimization_preview_safely_reports_when_no_change_is_available(self):
        document = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))

        response = await self.post_route_optimization_preview({"document": document, "day": 1})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertFalse(payload["can_optimize"])
        self.assertEqual(1, payload["day"])
        self.assertIn("无需优化", payload["reason"])

    async def test_trip_edit_accepts_the_previous_draft_document_for_continuous_edits(self):
        document = json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
        activity_id = document["itinerary"]["days"][0]["activities"][0]["activity_id"]
        first = await self.post(
            {
                "plan": self.plan,
                "document": document,
                "operation": {
                    "operation_id": "op-v3-note-1",
                    "plan_id": document["plan_id"],
                    "base_version": document["revision"],
                    "type": "update_activity_note",
                    "payload": {"activity_id": activity_id, "content": "第一步草稿备注"},
                },
            }
        )

        self.assertEqual(first.status_code, 200)
        first_document = first.json()["document"]
        second = await self.post(
            {
                "plan": self.plan,
                "document": first_document,
                "operation": {
                    "operation_id": "op-v3-note-2",
                    "plan_id": first_document["plan_id"],
                    "base_version": first_document["revision"],
                    "type": "update_activity_note",
                    "payload": {"activity_id": activity_id, "content": "第二步草稿备注"},
                },
            }
        )

        self.assertEqual(second.status_code, 200)
        payload = second.json()
        self.assertEqual(3, payload["document"]["revision"])
        self.assertIn(payload["document"]["status"], {"formal", "degraded"})
        self.assertIn("第二步草稿备注", [note["content"] for note in payload["document"]["notes"]])


if __name__ == "__main__":
    unittest.main()
