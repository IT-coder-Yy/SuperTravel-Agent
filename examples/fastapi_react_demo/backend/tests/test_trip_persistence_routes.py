import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from backend.routes.trip_persistence_routes import create_trip_persistence_router
from backend.services.app_factory_service import create_fastapi_app
from backend.services.trip_repository import TripRepository


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@asynccontextmanager
async def empty_lifespan(_app):
    yield


class TripPersistenceRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sta-trip-api-")
        self.repository = TripRepository(Path(self.temp_dir.name) / "travel.sqlite3")
        self.repository.initialize()
        app = create_fastapi_app(lifespan=empty_lifespan)
        app.include_router(create_trip_persistence_router(lambda: self.repository))
        self.client = TestClient(app)
        self.fixture = json.loads(
            (FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def bootstrap(self):
        response = self.client.get("/api/device")
        self.assertEqual(200, response.status_code)
        return response

    def test_bootstrap_sets_http_only_same_site_cookie(self):
        response = self.bootstrap()
        cookie = response.headers["set-cookie"].lower()

        self.assertEqual("ready", response.json()["status"])
        self.assertIn("sta_device_token=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)

    def test_separate_browser_clients_have_isolated_local_history(self):
        first_device = self.bootstrap().json()["device_id"]
        saved = self.client.put(
            "/api/trips/trip-1",
            json={"title": "杭州", "messages": [], "change_reason": "user_message"},
        )
        other_client = TestClient(self.client.app)
        try:
            second_device = other_client.get("/api/device").json()["device_id"]
            response = other_client.get("/api/trips/trip-1")
        finally:
            other_client.close()

        self.assertEqual(200, saved.status_code)
        self.assertNotEqual(first_device, second_device)
        self.assertEqual(404, response.status_code)

    def test_active_planning_run_uses_frontend_contract_without_cache(self):
        device_id = self.bootstrap().json()["device_id"]
        self.client.put(
            "/api/trips/trip-1",
            json={"title": "杭州", "messages": [], "change_reason": "system"},
        )
        self.repository.begin_planning_run(device_id, "trip-1", "run-1", "request-1")

        response = self.client.get("/api/planning-runs/active")

        self.assertEqual(200, response.status_code)
        self.assertEqual("run-1", response.json()["active_run"]["run_id"])
        self.assertEqual("no-store", response.headers["cache-control"])

    def test_migrates_lists_and_loads_legacy_trip(self):
        self.bootstrap()
        migration = self.client.post(
            "/api/trips/migrate",
            json={"items": [{
                "id": "legacy-1",
                "title": "旧行程",
                "messages": [{"role": "user", "content": "杭州三日游"}],
                "createdAt": "2026-07-01T08:00:00Z",
                "contentUpdatedAt": "2026-07-01T09:00:00Z",
            }]},
        )
        listing = self.client.get("/api/trips?page=1&page_size=20")
        detail = self.client.get("/api/trips/legacy-1")

        self.assertEqual({"imported": 1, "skipped": 0}, migration.json())
        self.assertEqual(1, listing.json()["total"])
        self.assertEqual("杭州三日游", listing.json()["items"][0]["preview"])
        self.assertEqual("旧行程", detail.json()["title"])

    def test_upsert_preserves_omitted_structured_fields(self):
        self.bootstrap()
        self.client.put(
            "/api/trips/trip-1",
            json={
                "title": "杭州",
                "messages": [{"role": "user", "content": "杭州"}],
                "trip_plan": {"version": 2},
                "change_reason": "final_answer",
            },
        )
        self.client.put(
            "/api/trips/trip-1",
            json={
                "title": "杭州新标题",
                "messages": [{"role": "user", "content": "杭州"}],
                "change_reason": "rename",
            },
        )
        detail = self.client.get("/api/trips/trip-1").json()

        self.assertEqual(2, detail["tripPlan"]["version"])
        self.assertEqual("杭州新标题", detail["title"])

    def test_formal_endpoint_validates_v3_and_replays_operation(self):
        self.bootstrap()
        self.client.put(
            "/api/trips/trip-1",
            json={"title": "杭州", "messages": [], "change_reason": "system"},
        )
        document = copy.deepcopy(self.fixture)
        document["revision"] = 1
        applied = self.client.post(
            "/api/trips/trip-1/formal",
            json={"operation_id": "op-1", "document": document},
        )
        replay = self.client.post(
            "/api/trips/trip-1/formal",
            json={"operation_id": "op-1", "document": document},
        )
        invalid = copy.deepcopy(document)
        invalid["itinerary"]["days"] = []
        rejected = self.client.post(
            "/api/trips/trip-1/formal",
            json={"operation_id": "op-invalid", "document": invalid},
        )

        self.assertEqual(200, applied.status_code)
        self.assertFalse(applied.json()["idempotent_replay"])
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(422, rejected.status_code)
        self.assertEqual("INVALID_V3_DOCUMENT", rejected.json()["detail"]["code"])

    def test_draft_apply_endpoint_uses_persisted_draft_and_returns_new_formal_document(self):
        self.bootstrap()
        self.client.put(
            "/api/trips/trip-1",
            json={"title": "杭州", "messages": [], "change_reason": "system"},
        )
        initial = copy.deepcopy(self.fixture)
        initial["revision"] = 1
        self.client.post(
            "/api/trips/trip-1/formal",
            json={"operation_id": "op-initial", "document": initial},
        )
        draft = copy.deepcopy(initial)
        draft["revision"] = 4
        draft["title"] = "等待应用的连续编辑"
        self.client.put(
            "/api/trips/trip-1/draft",
            json={"document": draft, "operations": []},
        )

        applied = self.client.post(
            "/api/trips/trip-1/draft/apply",
            json={"operation_id": "op-apply", "expected_draft_revision": 4},
        )
        replay = self.client.post(
            "/api/trips/trip-1/draft/apply",
            json={"operation_id": "op-apply", "expected_draft_revision": 4},
        )
        detail = self.client.get("/api/trips/trip-1").json()

        self.assertEqual(200, applied.status_code)
        self.assertEqual(2, applied.json()["revision"])
        self.assertEqual(2, applied.json()["document"]["revision"])
        self.assertEqual("等待应用的连续编辑", applied.json()["document"]["title"])
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertIsNone(detail["draft"])
        self.assertEqual(1, detail["formalSnapshots"]["previous"]["revision"])

    def test_revision_event_endpoint_replays_full_document_and_can_resume_by_sequence(self):
        self.bootstrap()
        self.client.put(
            "/api/trips/trip-1",
            json={"title": "杭州", "messages": [], "change_reason": "system"},
        )
        initial = copy.deepcopy(self.fixture)
        initial["revision"] = 1
        self.client.post(
            "/api/trips/trip-1/formal",
            json={"operation_id": "op-initial", "document": initial},
        )
        draft = copy.deepcopy(initial)
        draft["revision"] = 2
        self.client.put("/api/trips/trip-1/draft", json={"document": draft, "operations": []})
        self.client.post(
            "/api/trips/trip-1/draft/apply",
            json={"operation_id": "op-apply", "expected_draft_revision": 2},
        )

        response = self.client.get("/api/trips/trip-1/revisions/op-apply/events")
        payloads = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        resumed = self.client.get("/api/trips/trip-1/revisions/op-apply/events?after_sequence=5")
        resumed_payloads = [
            json.loads(line.removeprefix("data: "))
            for line in resumed.text.splitlines()
            if line.startswith("data: ")
        ]

        self.assertEqual(200, response.status_code)
        self.assertEqual("text/event-stream; charset=utf-8", response.headers["content-type"])
        self.assertEqual(list(range(1, len(payloads) + 1)), [item["sequence"] for item in payloads])
        self.assertEqual("plan_revision_started", payloads[0]["type"])
        self.assertEqual("plan_revision_completed", payloads[-2]["type"])
        self.assertEqual("trip_plan", payloads[-1]["type"])
        self.assertEqual(2, payloads[-1]["document"]["revision"])
        self.assertTrue(all(item["sequence"] > 5 for item in resumed_payloads))
        self.assertEqual(payloads[0]["occurred_at"], resumed_payloads[0]["occurred_at"])

    def test_active_run_blocks_delete_all_with_chinese_error(self):
        device_id = self.bootstrap().json()["device_id"]
        self.client.put(
            "/api/trips/trip-1",
            json={"title": "杭州", "messages": [], "change_reason": "system"},
        )
        self.repository.begin_planning_run(device_id, "trip-1", "run-1", "request-1")

        blocked = self.client.delete("/api/trips")
        self.repository.finish_planning_run(device_id, "run-1", "cancelled")
        deleted = self.client.delete("/api/trips")

        self.assertEqual(409, blocked.status_code)
        self.assertEqual("ACTIVE_PLANNING_RUN", blocked.json()["detail"]["code"])
        self.assertIn("正在规划", blocked.json()["detail"]["message"])
        self.assertEqual(1, deleted.json()["deleted"])


if __name__ == "__main__":
    unittest.main()
