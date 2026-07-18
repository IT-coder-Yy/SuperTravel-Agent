import asyncio
import unittest

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.planning_orchestrator import (
    PlanningOrchestrator,
    PlanningTask,
    PlanningTaskGraph,
    build_default_task_graph,
    decode_sse,
    encode_sse,
)
from backend.tests.test_trip_v3_contract import build_legacy_v2_document


async def legacy_stream(events, delay=0):
    for sequence, event in enumerate(events, start=1):
        if delay:
            await asyncio.sleep(delay)
        yield encode_sse({**event, "request_id": "legacy", "sequence": sequence})


class PlanningOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_validates_v3_before_releasing_map_and_workspace_events(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )
        persisted_documents = []
        source_events = [
            {"type": "chat_start", "message_id": "message-1"},
            {"type": "trip_intent", "intent": {"destination": "杭州"}},
            {"type": "trip_locations", "locations": [{"id": "poi_legacy_1"}]},
            {"type": "trip_plan", "document": build_legacy_v2_document(), "plan": {}},
            {"type": "chat_complete", "message_id": "message-1", "finish_reason": "completed"},
        ]

        payloads = []
        async for chunk in orchestrator.orchestrate(
            legacy_stream(source_events),
            run_id="run-1",
            request_id="request-1",
            target_revision=2,
            existing_plan_id="plan-existing",
            requires_baidu_verification=False,
            on_validated_document=persisted_documents.append,
        ):
            payloads.append(decode_sse(chunk))

        event_types = [payload["type"] for payload in payloads]
        self.assertEqual(
            [
                payload["payload"]["stage"]
                for payload in payloads
                if payload["type"] == "agent_stage_started"
            ],
            [
                "requirements_analysis",
                "research",
                "route_planning",
                "realtime_verification",
                "validation_completed",
            ],
        )
        self.assertEqual(event_types.count("final_plan_section"), 8)
        completion_index = event_types.index("trip_plan_completed")
        self.assertGreater(event_types.index("trip_locations"), completion_index)
        self.assertGreater(event_types.index("trip_plan"), completion_index)
        self.assertEqual(len(persisted_documents), 1)
        document = TravelPlanDocumentV3.model_validate(persisted_documents[0])
        self.assertEqual(document.plan_id, "plan-existing")
        self.assertEqual(document.revision, 2)

    async def test_soft_and_hard_timeout_emit_public_events_without_formal_data(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=0.01,
            ordinary_hard_timeout_seconds=0.03,
        )

        async def stalled_stream():
            yield encode_sse({"type": "chat_start", "request_id": "legacy", "sequence": 1})
            await asyncio.sleep(1)

        payloads = []
        async for chunk in orchestrator.orchestrate(
            stalled_stream(),
            run_id="run-timeout",
            request_id="request-timeout",
            requires_baidu_verification=False,
        ):
            payloads.append(decode_sse(chunk))

        event_types = [payload["type"] for payload in payloads]
        self.assertIn("run_soft_timeout", event_types)
        self.assertIn("error", event_types)
        self.assertIn("chat_complete", event_types)
        self.assertNotIn("trip_plan_completed", event_types)

    async def test_snapshot_commit_failure_does_not_release_formal_sections(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )

        async def reject_snapshot(_document):
            raise RuntimeError("sqlite write failed")

        source_events = [
            {"type": "trip_intent", "intent": {"destination": "杭州"}},
            {"type": "trip_locations", "locations": [{"id": "poi_legacy_1"}]},
            {"type": "trip_plan", "document": build_legacy_v2_document(), "plan": {}},
        ]
        payloads = []
        async for chunk in orchestrator.orchestrate(
            legacy_stream(source_events),
            run_id="run-commit-failed",
            request_id="request-commit-failed",
            requires_baidu_verification=False,
            on_validated_document=reject_snapshot,
        ):
            payloads.append(decode_sse(chunk))

        self.assertTrue(
            any(
                event["type"] == "error"
                and (
                    event.get("code") == "FORMAL_SNAPSHOT_COMMIT_FAILED"
                    or event.get("payload", {}).get("code") == "FORMAL_SNAPSHOT_COMMIT_FAILED"
                )
                for event in payloads
            )
        )
        self.assertFalse(any(event["type"] == "final_plan_section" for event in payloads))
        self.assertFalse(any(event["type"] == "trip_plan_completed" for event in payloads))

    async def test_validation_log_identifies_field_without_document_content(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )
        invalid_document = build_legacy_v2_document()
        invalid_document["intent"]["date_range"] = "下周"
        invalid_document["title"] = "不应写入诊断日志的用户方案标题"

        with self.assertLogs("backend.services.planning_orchestrator", level="WARNING") as captured:
            async for _chunk in orchestrator.orchestrate(
                legacy_stream([{"type": "trip_plan", "document": invalid_document, "plan": {}}]),
                run_id="run-invalid-date",
                request_id="request-invalid-date",
                requires_baidu_verification=False,
            ):
                pass

        joined = "\n".join(captured.output)
        self.assertIn("intent.date_range", joined)
        self.assertIn("V2 行程缺少明确日期", joined)
        self.assertNotIn("不应写入诊断日志的用户方案标题", joined)

    async def test_task_graph_retries_each_professional_task_at_most_twice(self):
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("retry")
            return "ok"

        graph = PlanningTaskGraph(
            [
                PlanningTask(
                    task_id="quality",
                    agent_name="质量 Agent",
                    stage="validation_completed",
                    task="校验",
                    timeout_seconds=0.1,
                )
            ]
        )
        results = await graph.execute({"quality": flaky})

        self.assertEqual(results["quality"].value, "ok")
        self.assertEqual(results["quality"].attempts, 2)
        self.assertEqual(attempts, 2)

    def test_default_task_graph_contains_real_specialist_dependencies(self):
        graph = build_default_task_graph()

        self.assertEqual(len(graph.tasks), 9)
        self.assertEqual(
            set(graph.tasks["route"].dependencies),
            {"research", "transport", "lodging", "dining"},
        )
        self.assertEqual(graph.tasks["quality"].max_attempts, 2)


if __name__ == "__main__":
    unittest.main()
