import asyncio
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

from backend.services.demo_case_package_service import DemoCasePackageError
from backend.services.demo_case_replay_service import (
    demo_case_summary,
    iter_demo_case_sse,
    iter_valid_demo_case_packages,
    load_ready_demo_case_package,
)


DEMO_DIRECTORY = BACKEND_ROOT.parent / "data" / "trip_demos"


async def collect_sse(package, *, final_only=False):
    return [item async for item in iter_demo_case_sse(package, speed=2.0, final_only=final_only)]


class DemoCaseReplayServiceTests(unittest.TestCase):
    def test_reads_only_ready_real_sse_packages_and_exposes_safe_card_metadata(self):
        packages = list(iter_valid_demo_case_packages(DEMO_DIRECTORY))

        self.assertEqual(
            {"beijing-family-3d", "chengdu-food-3d", "hangzhou-humanities-3d"},
            {package["case"]["id"] for package in packages},
        )
        summary = demo_case_summary(packages[0])
        self.assertEqual("ready", summary["recording_status"])
        self.assertGreater(summary["event_count"], 0)
        self.assertNotIn("budget", summary["template"])
        self.assertTrue(summary["template"]["destination"])

    def test_stream_replays_recorded_event_order_and_can_skip_to_final_result(self):
        package = load_ready_demo_case_package(DEMO_DIRECTORY, "hangzhou-humanities-3d")
        events = asyncio.run(collect_sse(package))
        payloads = [json.loads(item.split("data: ", 1)[1]) for item in events]

        self.assertEqual(list(range(1, len(payloads) + 1)), [item["sequence"] for item in payloads])
        self.assertEqual("run_started", payloads[0]["type"])
        self.assertEqual("chat_complete", payloads[-1]["type"])

        final_events = asyncio.run(collect_sse(package, final_only=True))
        final_types = [json.loads(item.split("data: ", 1)[1])["type"] for item in final_events]
        self.assertEqual(["trip_plan_completed", "trip_plan", "chat_complete"], final_types)

    def test_rejects_unaccepted_or_invalid_packages_without_any_runtime_dependency(self):
        source = load_ready_demo_case_package(DEMO_DIRECTORY, "hangzhou-humanities-3d")
        rejected = copy.deepcopy(source)
        rejected["recording"]["accepted"] = False
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hangzhou-humanities-3d.json"
            path.write_text(json.dumps(rejected, ensure_ascii=False), encoding="utf-8")

            self.assertEqual([], list(iter_valid_demo_case_packages(temp_dir)))
            with self.assertRaises(DemoCasePackageError):
                load_ready_demo_case_package(temp_dir, "hangzhou-humanities-3d")

