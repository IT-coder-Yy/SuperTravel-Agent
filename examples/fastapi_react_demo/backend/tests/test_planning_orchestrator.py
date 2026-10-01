import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas.trip_v3_models import TravelPlanDocumentV3
from backend.services.planning_orchestrator import (
    PlanningOrchestrator,
    PlanningTask,
    PlanningTaskGraph,
    build_default_task_graph,
    decode_sse,
    encode_sse,
)
from backend.services.planning_errors import PlanningPipelineError
from backend.tests.test_trip_v3_contract import build_legacy_v2_document


async def legacy_stream(events, delay=0):
    for sequence, event in enumerate(events, start=1):
        if delay:
            await asyncio.sleep(delay)
        yield encode_sse({**event, "request_id": "legacy", "sequence": sequence})


class PlanningOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_graph_v3_output_is_validated_and_persisted_without_v2_readaptation(self):
        from backend.services.travel_document_service import adapt_v2_to_v3
        document = adapt_v2_to_v3(build_legacy_v2_document()).model_dump(mode="json")
        persisted = []
        events = [
            {"type": "trip_intent", "intent": {"destination": "杭州"}, "task_graph": True},
            {"type": "trip_plan", "document": document, "task_graph_executed": True},
            {"type": "chat_complete", "finish_reason": "completed"},
        ]
        orchestrator = PlanningOrchestrator()
        with patch('backend.services.planning_orchestrator.adapt_v2_to_v3', side_effect=AssertionError('V3 不应再次按 V2 适配')):
            output = [decode_sse(chunk) async for chunk in orchestrator.orchestrate(
                legacy_stream(events), run_id='native-v3', request_id='native-v3-request',
                target_revision=3, existing_plan_id='existing-native-v3',
                requires_baidu_verification=False, on_validated_document=persisted.append)]
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]['schema_version'], '3.0')
        self.assertEqual(persisted[0]['revision'], 3)
        self.assertEqual(persisted[0]['plan_id'], 'existing-native-v3')
        self.assertEqual(persisted[0]['budget'], document['budget'])
        self.assertEqual(sum(event['type'] == 'trip_plan_completed' for event in output), 1)
        self.assertFalse(any(event['type'] == 'error' for event in output))

    async def test_structured_build_failure_persists_field_details_and_stops_completion(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )

        async def failed_stream():
            raise PlanningPipelineError(
                code="FORMAL_DOCUMENT_BUILD_FAILED",
                user_message="方案结构校验失败，未保存不完整内容。请重试本次规划。",
                diagnostics=[{
                    "location": "map_guidance.location_ids",
                    "type": "value_error",
                    "message": "地图地点必须与当前日程一致",
                }],
            )
            yield  # pragma: no cover

        payloads = []
        async for chunk in orchestrator.orchestrate(
            failed_stream(),
            run_id="run-build-failed",
            request_id="request-build-failed",
            requires_baidu_verification=False,
        ):
            payloads.append(decode_sse(chunk))

        error = next(event for event in payloads if event["type"] == "error" and event.get("payload"))
        self.assertEqual("FORMAL_DOCUMENT_BUILD_FAILED", error["payload"]["code"])
        self.assertEqual("map_guidance.location_ids", error["payload"]["details"][0]["location"])
        self.assertTrue(any(event["type"] == "chat_complete" and event["finish_reason"] == "failed" for event in payloads))
        self.assertFalse(any(event["type"] == "trip_plan_completed" for event in payloads))

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
            {
                "type": "chat_chunk",
                "message_id": "message-1",
                "role": "assistant",
                "content": "旧版 Schema：2.0",
                "show_content": "旧版 Schema：2.0",
                "step_type": "final_answer",
            },
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
        self.assertNotIn("trip_locations", event_types)
        self.assertGreater(event_types.index("trip_plan"), completion_index)
        formal_trip_plan = next(payload for payload in payloads if payload["type"] == "trip_plan")
        self.assertEqual(formal_trip_plan["document"]["schema_version"], "3.0")
        final_answer = next(
            payload
            for payload in payloads
            if payload["type"] == "chat_chunk" and payload.get("step_type") == "final_answer"
        )
        self.assertIn("Schema：3.0", final_answer["content"])
        self.assertNotIn("Schema：2.0", final_answer["content"])
        self.assertEqual(len(persisted_documents), 1)
        document = TravelPlanDocumentV3.model_validate(persisted_documents[0])
        self.assertEqual(document.plan_id, "plan-existing")
        self.assertEqual(document.revision, 2)

    async def test_persists_flexible_date_plan_as_day_numbers_without_fake_dates(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )
        legacy_document = build_legacy_v2_document()
        legacy_document["intent"]["date_range"] = "日期暂未确定"
        legacy_document["intent"]["days"] = 3
        persisted_documents = []

        async for _chunk in orchestrator.orchestrate(
            legacy_stream([{"type": "trip_plan", "document": legacy_document, "plan": {}}]),
            run_id="run-flexible-date",
            request_id="request-flexible-date",
            requires_baidu_verification=False,
            on_validated_document=persisted_documents.append,
        ):
            pass

        document = TravelPlanDocumentV3.model_validate(persisted_documents[0])
        self.assertEqual(document.intent.date_mode, "flexible")
        self.assertEqual([day.date for day in document.itinerary.days], [None, None, None])
        self.assertEqual(document.outbound_transport.options, [])
        self.assertEqual(document.return_transport.options, [])

    async def test_soft_and_hard_timeout_emit_public_events_without_formal_data(self):
        orchestrator = PlanningOrchestrator(
            ordinary_soft_timeout_seconds=1,
            ordinary_hard_timeout_seconds=2,
        )
        now = 0.0

        async def stalled_stream():
            yield encode_sse({"type": "chat_start", "request_id": "legacy", "sequence": 1})
            await asyncio.Future()

        payloads = []
        # 只控制编排器的时钟，不改事件循环时钟；按已收到的事件推进，
        # 避免测试机一次调度延迟同时越过 10ms/30ms 两个截止点。
        with patch(
            "backend.services.planning_orchestrator.time",
            SimpleNamespace(monotonic=lambda: now),
        ):
            async for chunk in orchestrator.orchestrate(
                stalled_stream(),
                run_id="run-timeout",
                request_id="request-timeout",
                requires_baidu_verification=False,
            ):
                payload = decode_sse(chunk)
                payloads.append(payload)
                if payload["type"] == "chat_start":
                    now = 1.1
                elif payload["type"] == "run_soft_timeout":
                    now = 2.1

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
