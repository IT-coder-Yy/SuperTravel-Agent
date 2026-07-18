import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from backend.services.planning_event_store import PlanningEventStore
from backend.services.planning_orchestrator import PlanningOrchestrator, decode_sse, encode_sse
from backend.services.planning_run_service import PlanningRunManager
from backend.services.trip_repository import TripRepository
from backend.tests.test_trip_v3_contract import build_legacy_v2_document


def load_v3_fixture() -> dict:
    path = Path(__file__).parent / "fixtures" / "trip_v3_domestic_3d.json"
    return json.loads(path.read_text(encoding="utf-8"))


class PlanningEventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="supertravel-event-store-")
        self.repository = TripRepository(Path(self.temp_dir.name) / "events.sqlite3")
        self.repository.initialize()
        self.device_id = self.repository.get_or_create_device("event-store-token")
        self.repository.ensure_trip(self.device_id, "trip-1", "杭州行程")
        self.repository.begin_planning_run(self.device_id, "trip-1", "run-1", "request-1")
        self.store = PlanningEventStore(self.repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_event_store_persists_five_minute_log_and_deduplicates(self):
        event = {
            "event_id": "evt-1",
            "run_id": "run-1",
            "request_id": "request-1",
            "sequence": 1,
            "occurred_at": "2026-07-18T14:00:00+00:00",
            "type": "run_started",
            "payload": {"status": "planning", "message": "开始规划"},
        }
        self.assertTrue(self.store.append("run-1", event))
        self.assertFalse(self.store.append("run-1", event))
        self.assertEqual(self.store.replay("run-1"), [event])
        self.assertEqual(self.store.replay("run-1", after_sequence=1), [])

    def test_clear_run_requires_owner_and_only_deletes_runtime_events(self):
        self.store.append(
            "run-1",
            {"event_id": "evt-1", "sequence": 1, "type": "chat_start"},
        )
        self.assertEqual(self.store.clear_run(self.device_id, "run-1"), 1)
        self.assertEqual(self.store.replay("run-1"), [])
        with self.assertRaises(Exception):
            self.store.clear_run("another-device", "run-1")


class PlanningRunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="supertravel-run-manager-")
        self.repository = TripRepository(Path(self.temp_dir.name) / "runs.sqlite3")
        self.repository.initialize()
        self.device_id = self.repository.get_or_create_device("run-manager-token")
        self.repository.ensure_trip(self.device_id, "trip-1", "杭州行程")
        self.orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )
        self.manager = PlanningRunManager(
            repository=self.repository,
            orchestrator=self.orchestrator,
        )

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp_dir.cleanup()

    @staticmethod
    async def _completed_source(delay=0.005):
        events = [
            {"type": "chat_start", "message_id": "message-1"},
            {"type": "trip_intent", "intent": {"destination": "杭州"}},
            {"type": "trip_locations", "locations": [{"id": "poi_legacy_1"}]},
            {"type": "trip_plan", "document": build_legacy_v2_document(), "plan": {}},
            {"type": "chat_complete", "message_id": "message-1", "finish_reason": "completed"},
        ]
        for sequence, event in enumerate(events, start=1):
            await asyncio.sleep(delay)
            yield encode_sse({**event, "request_id": "legacy", "sequence": sequence})

    async def test_background_run_survives_subscriber_disconnect_and_replays_from_sequence(self):
        self.repository.begin_planning_run(self.device_id, "trip-1", "run-1", "request-1")
        managed = self.manager.start(
            self._completed_source(),
            run_id="run-1",
            request_id="request-1",
            device_id=self.device_id,
            trip_id="trip-1",
            target_revision=1,
            existing_plan_id=None,
            requires_baidu_verification=False,
        )
        first_connection = self.manager.stream(run_id="run-1")
        first_payload = decode_sse(await anext(first_connection))
        await first_connection.aclose()
        self.assertEqual(first_payload["sequence"], 1)

        await managed.task
        replayed = []
        async for chunk in self.manager.stream(run_id="run-1", after_sequence=1):
            replayed.append(decode_sse(chunk))

        self.assertTrue(any(event["type"] == "trip_plan_completed" for event in replayed))
        trip = self.repository.get_trip(self.device_id, "trip-1")
        formal = trip["formalSnapshots"]["current"]["document"]
        self.assertEqual(formal["schema_version"], "3.0")
        self.assertEqual(trip["currentRevision"], 1)
        self.assertEqual(
            self.repository.planning_run(self.device_id, "run-1")["status"],
            "degraded",
        )

    async def test_cancel_clears_only_run_runtime_data_and_preserves_formal_and_draft(self):
        formal = load_v3_fixture()
        self.repository.apply_formal_snapshot(
            self.device_id,
            "trip-1",
            "apply-initial",
            formal,
        )
        self.repository.save_draft(
            self.device_id,
            "trip-1",
            formal,
            [{"type": "note", "payload": {"text": "保留草稿"}}],
        )
        self.repository.begin_planning_run(self.device_id, "trip-1", "run-cancel", "request-cancel")

        async def stalled_source():
            yield encode_sse({"type": "chat_start", "request_id": "legacy", "sequence": 1})
            await asyncio.sleep(5)

        self.manager.start(
            stalled_source(),
            run_id="run-cancel",
            request_id="request-cancel",
            device_id=self.device_id,
            trip_id="trip-1",
            target_revision=2,
            existing_plan_id=formal["plan_id"],
            requires_baidu_verification=False,
        )
        await asyncio.sleep(0.02)
        result = await self.manager.cancel(device_id=self.device_id, run_id="run-cancel")

        self.assertTrue(result["cancelled"])
        trip = self.repository.get_trip(self.device_id, "trip-1")
        self.assertIn("current", trip["formalSnapshots"])
        self.assertIsNotNone(trip["draft"])
        self.assertEqual(self.repository.recent_run_events("run-cancel"), [])
        self.assertEqual(
            self.repository.planning_run(self.device_id, "run-cancel")["status"],
            "cancelled",
        )


if __name__ == "__main__":
    unittest.main()
