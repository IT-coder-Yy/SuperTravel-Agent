import importlib
import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

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


if __name__ == "__main__":
    unittest.main()
