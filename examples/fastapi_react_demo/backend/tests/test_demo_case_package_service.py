import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
for path in (BACKEND_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from backend.services.demo_case_package_service import (
    DEMO_CHAT_PLACEHOLDER,
    DEMO_CASE_PACKAGE_VERSION,
    DemoCasePackageError,
    DemoCasePackageService,
    RecordedRun,
    iter_ready_demo_packages,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_document():
    return json.loads((FIXTURE_DIR / "trip_v3_domestic_3d.json").read_text(encoding="utf-8"))


def build_events(document):
    events = [
        {
            "event_version": 1,
            "event_id": "event-1",
            "run_id": "real-run",
            "request_id": "real-request",
            "sequence": 1,
            "occurred_at": "2026-07-29T08:00:00+00:00",
            "type": "run_started",
            "payload": {"status": "planning", "message": "开始规划"},
        }
    ]
    for index, stage in enumerate(
        [
            "requirements_analysis",
            "research",
            "route_planning",
            "realtime_verification",
            "validation_completed",
        ],
        start=2,
    ):
        events.append(
            {
                "event_version": 1,
                "event_id": f"event-{index}",
                "run_id": "real-run",
                "request_id": "real-request",
                "sequence": index,
                "occurred_at": "2026-07-29T08:00:00+00:00",
                "type": "agent_stage_started",
                "payload": {"stage": stage, "agent_name": "验收 Agent", "task": "真实运行", "status": "running"},
            }
        )
    events.extend(
        [
            {
                "event_version": 1,
                "event_id": "event-7",
                "run_id": "real-run",
                "request_id": "real-request",
                "sequence": 7,
                "occurred_at": "2026-07-29T08:00:00+00:00",
                "type": "final_plan_section",
                "payload": {
                    "plan_id": document["plan_id"],
                    "revision": document["revision"],
                    "section": "map_guidance",
                    "content": document["map_guidance"],
                },
            },
            {
                "event_version": 1,
                "event_id": "event-8",
                "run_id": "real-run",
                "request_id": "real-request",
                "sequence": 8,
                "occurred_at": "2026-07-29T08:00:00+00:00",
                "type": "trip_plan_completed",
                "payload": {
                    "plan_id": document["plan_id"],
                    "revision": document["revision"],
                    "status": "completed_degraded",
                    "checksum": "real-checksum",
                },
            },
            {
                "event_id": "event-9",
                "run_id": "real-run",
                "request_id": "real-request",
                "sequence": 9,
                "occurred_at": "2026-07-29T08:00:00+00:00",
                "type": "trip_plan",
                "document": copy.deepcopy(document),
                "plan": {},
            },
        ]
    )
    return events


class DemoCasePackageServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = DemoCasePackageService()
        self.document = load_document()
        self.run = RecordedRun(run_id="real-run", status="degraded", events=build_events(self.document))

    def build_package(self):
        return self.service.build_package(
            case_id="hangzhou-humanities-3d",
            title="杭州三日人文慢游",
            summary="湖山、人文与本地餐饮结合的轻松路线。",
            traveler_label="双人",
            recorded_runs=[self.run],
            final_document=self.document,
            accepted_on="2026-07-29",
        )

    def test_builds_versioned_ready_package_from_completed_real_sse_shape(self):
        package = self.build_package()

        self.assertEqual(DEMO_CASE_PACKAGE_VERSION, package["package_version"])
        self.assertEqual("real_sse", package["recording"]["source"])
        self.assertTrue(package["recording"]["accepted"])
        self.assertEqual(1, package["recording"]["run_count"])
        self.assertEqual("ready", package["case"]["recording_status"])
        self.assertEqual("上海", package["case"]["origin"])
        self.assertEqual(package["document"]["map_guidance"], package["map_data"])
        self.assertEqual("demo-hangzhou-humanities-3d", package["document"]["plan_id"])
        self.assertTrue(all(event["run_id"] == "demo:hangzhou-humanities-3d:run" for event in package["events"]))

    def test_removes_temporary_prices_external_links_images_and_personal_contact(self):
        document = copy.deepcopy(self.document)
        document["outbound_transport"]["options"][0]["price"] = {
            "amount": 123.0,
            "currency": "CNY",
            "source_status": "realtime_verified",
        }
        document["outbound_transport"]["options"][0]["booking_url"] = "https://booking.example.test"
        document["itinerary"]["days"][0]["activities"][0]["place"]["phone"] = "13800138000"
        document["itinerary"]["days"][0]["activities"][0]["images"] = [
            {
                "image_id": "image-1",
                "url": "https://images.example.test/1.jpg",
                "display_allowed": True,
                "export_allowed": False,
                "attribution_required": True,
            }
        ]
        document["itinerary"]["days"][0]["activities"][0]["cover_image_id"] = "image-1"
        self.document = document
        self.run = RecordedRun(run_id="real-run", status="degraded", events=build_events(document))

        package = self.build_package()
        activity = package["document"]["itinerary"]["days"][0]["activities"][0]
        option = package["document"]["outbound_transport"]["options"][0]

        self.assertIsNone(option["price"])
        self.assertIsNone(option["booking_url"])
        self.assertIsNone(activity["place"]["phone"])
        self.assertEqual([], activity["images"])
        self.assertIsNone(activity["cover_image_id"])
        self.assertEqual([], package["document"]["budget"]["categories"])
        self.assertIsNone(package["document"]["budget"]["estimated_total"])

    def test_redacts_personal_text_but_rejects_secrets(self):
        events = build_events(self.document)
        events[0]["payload"]["message"] = "请联系 alice@example.com 或 13800138000"
        self.run = RecordedRun(run_id="real-run", status="degraded", events=events)
        package = self.build_package()
        self.assertIn("[邮箱已隐藏]", package["events"][0]["payload"]["message"])
        self.assertIn("[手机号已隐藏]", package["events"][0]["payload"]["message"])

        events[0]["payload"]["message"] = "api_key=sk_abcdefghijklmnopqrstuvwxyz"
        self.run = RecordedRun(run_id="real-run", status="degraded", events=events)
        with self.assertRaises(DemoCasePackageError):
            self.build_package()

    def test_replaces_freeform_chat_chunks_with_a_safe_replay_placeholder(self):
        events = build_events(self.document)
        events[0].update(
            {
                "type": "chat_chunk",
                "content": "车票 ¥999，联系 alice@example.com。",
                "show_content": "https://temporary.example.test",
                "message_id": "raw-message-id",
            }
        )
        self.run = RecordedRun(run_id="real-run", status="degraded", events=events)

        package = self.build_package()

        self.assertEqual(DEMO_CHAT_PLACEHOLDER, package["events"][0]["content"])
        self.assertEqual(DEMO_CHAT_PLACEHOLDER, package["events"][0]["show_content"])
        self.assertEqual("demo:hangzhou-humanities-3d:message:0001", package["events"][0]["message_id"])

    def test_rejects_unfinished_or_incomplete_runs(self):
        with self.assertRaisesRegex(DemoCasePackageError, "未成功结束"):
            self.service.build_package(
                case_id="hangzhou-humanities-3d",
                title="杭州三日人文慢游",
                summary="摘要",
                traveler_label="双人",
                recorded_runs=[RecordedRun(run_id="running", status="running", events=build_events(self.document))],
                final_document=self.document,
                accepted_on="2026-07-29",
            )

        events = [event for event in build_events(self.document) if event["type"] != "trip_plan_completed"]
        with self.assertRaisesRegex(DemoCasePackageError, "正式方案完成事件"):
            self.service.build_package(
                case_id="hangzhou-humanities-3d",
                title="杭州三日人文慢游",
                summary="摘要",
                traveler_label="双人",
                recorded_runs=[RecordedRun(run_id="broken", status="completed", events=events)],
                final_document=self.document,
                accepted_on="2026-07-29",
            )

    def test_write_and_discover_only_accepted_packages(self):
        package = self.build_package()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            target = self.service.write_package(package, output_dir)
            target.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
            (output_dir / "broken.json").write_text('{"recording":{"accepted":false}}', encoding="utf-8")
            found = list(iter_ready_demo_packages(output_dir))
            self.assertEqual(["hangzhou-humanities-3d"], [item["case"]["id"] for item in found])

    def test_reads_the_repository_event_wrapper_without_losing_sse_payload(self):
        test_case = self

        class FakeRepository:
            def planning_run(self, device_id, run_id):
                test_case.assertEqual("device-1", device_id)
                test_case.assertEqual("run-1", run_id)
                return {"trip_id": "trip-1", "status": "degraded"}

            def recent_run_events(self, run_id):
                return [
                    {"sequence": event["sequence"], "event": event}
                    for event in build_events(load_document())
                ]

            def get_trip(self, device_id, trip_id):
                test_case.assertEqual("device-1", device_id)
                test_case.assertEqual("trip-1", trip_id)
                return {"formalSnapshots": {"current": {"document": load_document()}}}

        repository = FakeRepository()
        package = self.service.build_from_repository(
            repository=repository,
            device_id="device-1",
            run_ids=["run-1"],
            case_id="hangzhou-humanities-3d",
            title="杭州三日人文慢游",
            summary="摘要",
            traveler_label="双人",
            accepted_on="2026-07-29",
        )
        self.assertEqual("trip_plan_completed", package["events"][-2]["type"])


if __name__ == "__main__":
    unittest.main()
