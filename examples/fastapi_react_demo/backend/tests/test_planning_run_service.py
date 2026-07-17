import tempfile
import unittest
from pathlib import Path

from fastapi.responses import StreamingResponse

from backend.services.planning_run_service import attach_persisted_planning_run
from backend.services.trip_repository import TripRepository


class PlanningRunServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sta-planning-run-")
        self.repository = TripRepository(Path(self.temp_dir.name) / "travel.sqlite3")
        self.repository.initialize()
        self.device_id = self.repository.get_or_create_device("hash-1")
        self.repository.ensure_trip(self.device_id, "trip-1")

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_stream_events_are_retained_and_run_lock_is_released(self):
        self.repository.begin_planning_run(
            self.device_id, "trip-1", "run-1", "request-1"
        )

        async def source():
            yield 'data: {"type":"chat_start","request_id":"request-1","sequence":1}\n\n'
            yield 'data: {"type":"chat_complete","finish_reason":"completed","sequence":2}\n\n'

        response = attach_persisted_planning_run(
            StreamingResponse(source()),
            repository=self.repository,
            device_id=self.device_id,
            trip_id="trip-1",
            run_id="run-1",
        )
        chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(2, len(chunks))
        self.assertIsNone(self.repository.active_planning_run(self.device_id))
        events = self.repository.recent_run_events("run-1")
        self.assertEqual([1, 2], [event["sequence"] for event in events])
        self.assertEqual("chat_complete", events[-1]["event"]["type"])

    async def test_closing_stream_marks_run_cancelled_and_releases_lock(self):
        self.repository.begin_planning_run(
            self.device_id, "trip-1", "run-1", "request-1"
        )

        async def source():
            yield 'data: {"type":"chat_start","sequence":1}\n\n'
            yield 'data: {"type":"chat_chunk","sequence":2}\n\n'

        response = attach_persisted_planning_run(
            StreamingResponse(source()),
            repository=self.repository,
            device_id=self.device_id,
            trip_id="trip-1",
            run_id="run-1",
        )
        iterator = response.body_iterator
        await anext(iterator)
        await iterator.aclose()

        self.assertIsNone(self.repository.active_planning_run(self.device_id))
        next_run = self.repository.begin_planning_run(
            self.device_id, "trip-1", "run-2", "request-2"
        )
        self.assertEqual("running", next_run["status"])
        self.repository.finish_planning_run(self.device_id, "run-2", "cancelled")


if __name__ == "__main__":
    unittest.main()
