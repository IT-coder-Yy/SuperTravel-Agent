import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.chat_service import generate_chat_stream
from schemas.trip_models import TripPlan


class FakeController:
    def __init__(self, chunks):
        self.chunks = chunks
        self.called_with = None

    def run_stream(self, input_messages, tool_manager, session_id, deep_thinking, summary, deep_research):
        self.called_with = {
            "input_messages": input_messages,
            "tool_manager": tool_manager,
            "session_id": session_id,
            "deep_thinking": deep_thinking,
            "summary": summary,
            "deep_research": deep_research,
        }
        for chunk in self.chunks:
            yield chunk


class FakeSemanticAgent:
    def __init__(self, payload):
        self.payload = payload
        self.called_with = None

    def _call_llm_non_streaming(self, messages):
        self.called_with = messages
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
                )
            ]
        )


class FakeSemanticController(FakeController):
    def __init__(self, chunks, semantic_payload):
        super().__init__(chunks)
        self.task_analysis_agent = FakeSemanticAgent(semantic_payload)


class ErrorController:
    def run_stream(self, **kwargs):
        raise RuntimeError("stream boom")


def sanitize_text(value: str) -> str:
    return f"clean::{value}"


class ChatStreamContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 此层只校验请求路由/澄清契约；真实任务执行图由独立测试覆盖。
        self.production_calls = []
        async def fake_production(**kwargs):
            self.production_calls.append(kwargs)
            document = json.loads((BACKEND_ROOT / "tests/fixtures/trip_v3_domestic_3d.json").read_text(encoding="utf-8"))
            yield {"type": "trip_plan", "document": document, "task_graph_executed": True}
        patcher = patch("services.production_planning_service.stream_production_plan", side_effect=fake_production)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _collect_sse_payloads(self, generator):
        events = []
        async for line in generator:
            self.assertTrue(line.startswith("data: "))
            payload = json.loads(line[len("data: "):].strip())
            self.assertEqual(payload.get("sequence"), len(events) + 1)
            events.append(payload)
        sequences = [payload["sequence"] for payload in events]
        self.assertEqual(len(sequences), len(set(sequences)))
        return events

    def _get_single_clarification(self, payloads):
        clarifications = [payload for payload in payloads if payload.get("type") == "clarification_required"]
        self.assertEqual(len(clarifications), 1, "本轮应且仅应返回一个 clarification_required 事件")
        return clarifications[0]

    async def test_slow_travel_research_does_not_block_the_api_event_loop(self):
        request_messages = [
            SimpleNamespace(role="user", content="规划杭州3日游", message_id="u-responsive", type="normal"),
        ]
        controller = FakeController(chunks=[])

        research_started = threading.Event()
        release_research = threading.Event()
        research_finished = threading.Event()
        research_threads = []
        loop_thread = threading.get_ident()

        def slow_research(**_kwargs):
            research_threads.append(threading.get_ident())
            research_started.set()
            release_research.wait(timeout=5)
            research_finished.set()
            return None

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
                    with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                        with patch("services.chat_service.maybe_prepare_travel_experience_bundle", side_effect=slow_research):
                            collecting = asyncio.create_task(self._collect_sse_payloads(
                                generate_chat_stream(
                                    request_messages=request_messages,
                                    controller=controller,
                                    tool_manager=object(),
                                    selected_mcp_servers=None,
                                    use_deepthink=True,
                                    use_multi_agent=False,
                                    request_id="req-responsive",
                                    sanitize_text=sanitize_text,
                                )
                            ))
                            try:
                                self.assertTrue(await asyncio.to_thread(research_started.wait, 5))
                                # 研究仍被阻塞时，事件循环应能恢复执行并释放它；
                                # 不把磁盘、线程启动和测试机负载计入延迟阈值。
                                self.assertEqual(len(research_threads), 1)
                                self.assertNotEqual(research_threads[0], loop_thread)
                                self.assertFalse(research_finished.is_set())
                            finally:
                                release_research.set()
                                await collecting

    async def test_generate_chat_stream_emits_start_chunk_complete(self):
        request_messages = [
            SimpleNamespace(role="user", content="hello", message_id=None, type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-1",
                        "role": "assistant",
                        "content": "a",
                        "show_content": "A",
                        "type": "thinking",
                    }
                ],
                [
                    {
                        "message_id": "m-1",
                        "role": "assistant",
                        "content": "b",
                        "show_content": "B",
                        "type": "final_answer",
                    }
                ],
            ]
        )

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=False,
                request_id="req-normal",
                sanitize_text=sanitize_text,
            )
        )

        self.assertGreaterEqual(len(payloads), 4)
        self.assertEqual(payloads[0]["type"], "chat_start")
        self.assertEqual(payloads[1]["type"], "chat_chunk")
        self.assertEqual(payloads[1]["content"], "clean::正在整理行程方案")
        self.assertEqual(payloads[1]["show_content"], "clean::正在整理行程方案")
        self.assertEqual(payloads[1]["step_type"], "tool_progress")
        self.assertEqual(payloads[2]["step_type"], "final_answer")
        self.assertEqual(payloads[-1]["type"], "chat_complete")
        self.assertEqual(payloads[-1]["finish_reason"], "completed")
        self.assertTrue(all(payload["request_id"] == "req-normal" for payload in payloads))

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["deep_thinking"], True)
        self.assertEqual(controller.called_with["summary"], True)
        self.assertEqual(controller.called_with["deep_research"], False)
        self.assertEqual(len(controller.called_with["input_messages"]), 1)

    async def test_sequence_is_monotonic_for_repeated_public_event_types(self):
        request_messages = [
            SimpleNamespace(role="user", content="hello", message_id="u-sequence", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-sequence",
                        "role": "assistant",
                        "content": "step-1",
                        "show_content": "step-1",
                        "type": "thinking",
                    }
                ],
                [
                    {
                        "message_id": "m-sequence",
                        "role": "assistant",
                        "content": "step-2",
                        "show_content": "step-2",
                        "type": "thinking",
                    }
                ],
            ]
        )

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=False,
                use_multi_agent=False,
                request_id="req-sequence",
                sanitize_text=sanitize_text,
            )
        )

        repeated_chunks = [payload for payload in payloads if payload.get("type") == "chat_chunk"]
        self.assertEqual(len(repeated_chunks), 2)
        self.assertEqual(
            [payload["sequence"] for payload in payloads],
            list(range(1, len(payloads) + 1)),
        )
        self.assertTrue(all(payload["request_id"] == "req-sequence" for payload in payloads))

    async def test_internal_thought_and_tool_details_are_never_streamed(self):
        request_messages = [SimpleNamespace(role="user", content="规划杭州行程", message_id="u-private", type="normal")]
        controller = FakeController(chunks=[[{
            "message_id": "m-private",
            "role": "assistant",
            "content": '{"tool_name":"serper_site_search","arguments":{"path":"C:\\\\secret"}}',
            "show_content": "filesystem 12306 internal prompt",
            "type": "thinking",
        }]])

        payloads = await self._collect_sse_payloads(generate_chat_stream(
            request_messages=request_messages,
            controller=controller,
            tool_manager=object(),
            selected_mcp_servers=None,
            use_deepthink=True,
            use_multi_agent=False,
            request_id="req-private",
            sanitize_text=lambda value: value,
        ))

        public_text = " ".join(str(payload.get("content", "")) for payload in payloads)
        self.assertNotIn("serper_site_search", public_text)
        self.assertNotIn("filesystem", public_text)
        self.assertNotIn("12306", public_text)
        self.assertNotIn("secret", public_text)
        self.assertIn("正在生成最终行程方案", public_text)

    async def test_generate_chat_stream_emits_only_one_clarification_without_running_agent(self):
        request_messages = [
            SimpleNamespace(role="user", content="帮我规划杭州三日游", message_id="u-clarify", type="normal"),
        ]
        controller = FakeController(chunks=[])

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=True,
                profile={},
                sanitize_text=sanitize_text,
            )
        )

        self.assertEqual(payloads[0]["type"], "chat_start")
        self.assertTrue(
            any(
                payload.get("type") == "chat_chunk"
                and payload.get("step_type") == "tool_progress"
                and "正在分析" in payload.get("content", "")
                for payload in payloads
            )
        )
        self.assertTrue(any(payload.get("type") == "trip_intent" for payload in payloads))
        clarification = self._get_single_clarification(payloads)
        self.assertEqual(len(clarification["questions"]), 1)
        self.assertEqual(clarification["max_questions"], 4)
        self.assertEqual(clarification["answered_count"], 0)
        self.assertEqual(clarification["questions"][0]["field"], "origin")
        self.assertIsNone(controller.called_with)
        self.assertEqual(payloads[-1]["type"], "chat_complete")
        self.assertEqual(payloads[-1]["finish_reason"], "clarification_required")

    async def test_generate_chat_stream_clarifies_beijing_trip_one_question_per_round(self):
        request_messages = [
            SimpleNamespace(role="user", content="帮我规划一次北京3天2夜的特种兵之旅", message_id="u-beijing", type="normal"),
        ]
        answers = {}
        expected_rounds = [
            ("origin", "上海"),
            ("date_range", "2026年8月15日至17日"),
            ("people_type", "朋友"),
            ("budget_total", "不设严格预算"),
        ]
        asked_fields = []

        for expected_field, answer in expected_rounds:
            controller = FakeController(chunks=[])
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=True,
                    profile={},
                    planning_mode="standard_plan",
                    clarification_answers=dict(answers),
                    sanitize_text=sanitize_text,
                )
            )

            clarification = self._get_single_clarification(payloads)
            questions = clarification["questions"]
            self.assertEqual(len(questions), 1)
            self.assertEqual(questions[0]["field"], expected_field)
            self.assertNotIn(questions[0]["field"], answers)
            self.assertNotIn(questions[0]["field"], {"destination", "days", "pace"})
            self.assertIsNone(controller.called_with)
            self.assertEqual(payloads[-1]["type"], "chat_complete")
            self.assertEqual(payloads[-1]["finish_reason"], "clarification_required")
            asked_fields.append(expected_field)
            answers[expected_field] = answer

        self.assertEqual(asked_fields, ["origin", "date_range", "people_type", "budget_total"])
        self.assertEqual(len(set(asked_fields)), 4)

        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-beijing",
                        "role": "assistant",
                        "content": "北京三日游方案",
                        "show_content": "北京三日游方案",
                        "type": "final_answer",
                    }
                ]
            ]
        )
        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=False,
                            use_multi_agent=False,
                            profile={},
                            planning_mode="standard_plan",
                            clarification_answers=answers,
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertFalse(any(payload.get("type") == "clarification_required" for payload in payloads))
        self.assertIsNone(controller.called_with)
        self.assertTrue(self.production_calls)
        self.assertEqual(payloads[-1]["finish_reason"], "completed")

    async def test_generate_chat_stream_does_not_repeat_non_numeric_budget_answer(self):
        request_messages = [
            SimpleNamespace(role="user", content="帮我规划一次北京3天2夜的特种兵之旅", message_id="u-budget", type="normal"),
        ]
        controller = FakeController(chunks=[])

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=True,
                profile={},
                planning_mode="standard_plan",
                clarification_answers={"budget_total": "不设严格预算"},
                sanitize_text=sanitize_text,
            )
        )

        clarification = self._get_single_clarification(payloads)
        self.assertEqual(len(clarification["questions"]), 1)
        self.assertEqual(clarification["questions"][0]["field"], "origin")
        self.assertNotEqual(clarification["questions"][0]["field"], "budget_total")
        self.assertIsNone(controller.called_with)

    async def test_skip_sentinel_advances_without_entering_intent_or_model_context(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="2026年8月15日至17日从上海出发，帮我规划北京三日游，和朋友一起，节奏轻松",
                message_id="u-skip",
                type="normal",
            ),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-skip",
                        "role": "assistant",
                        "content": "北京三日游方案",
                        "show_content": "北京三日游方案",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=False,
                            use_multi_agent=False,
                            profile={"preferred_budget_level": "标准"},
                            planning_mode="standard_plan",
                            clarification_answers={"budget_total": "__skip__"},
                            request_id="req-skip",
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertFalse(any(payload.get("type") == "clarification_required" for payload in payloads))
        intent_event = next(payload for payload in payloads if payload.get("type") == "trip_intent")
        self.assertIsNone(intent_event["intent"]["budget_total"])
        self.assertIsNone(intent_event["intent"]["budget_per_person"])
        self.assertIsNone(controller.called_with)
        self.assertTrue(self.production_calls)
        model_context = json.dumps(self.production_calls[-1]["message_history"], ensure_ascii=False)
        self.assertNotIn("__skip__", model_context)
        self.assertNotIn("用户画像预算档位: 标准", model_context)
        final_chunks = [payload for payload in payloads if payload.get("type") == "trip_plan"]
        self.assertTrue(final_chunks)
        self.assertNotIn("__skip__", json.dumps(final_chunks, ensure_ascii=False))
        self.assertTrue(all(payload["request_id"] == "req-skip" for payload in payloads))
        self.assertEqual(payloads[-1]["finish_reason"], "completed")

    async def test_profile_default_is_exposed_and_only_applied_when_actual_value_is_submitted(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="2026年8月15日至17日从上海出发，帮我规划北京三日游",
                message_id="u-profile-default",
                type="normal",
            ),
        ]
        profile = {"default_people_type": "情侣"}

        first_payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=FakeController(chunks=[]),
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=False,
                profile=profile,
                planning_mode="standard_plan",
                clarification_answers={},
                sanitize_text=sanitize_text,
            )
        )
        first_question = self._get_single_clarification(first_payloads)["questions"][0]
        self.assertEqual(first_question["field"], "people_type")
        self.assertEqual(first_question["profile_default_value"], "情侣")
        first_intent = next(payload for payload in first_payloads if payload.get("type") == "trip_intent")
        self.assertIsNone(first_intent["intent"]["people_type"])

        second_payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=FakeController(chunks=[]),
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=False,
                profile=profile,
                planning_mode="standard_plan",
                clarification_answers={"people_type": "情侣"},
                sanitize_text=sanitize_text,
            )
        )
        second_intent = next(payload for payload in second_payloads if payload.get("type") == "trip_intent")
        self.assertEqual(second_intent["intent"]["people_type"], "情侣")
        second_question = self._get_single_clarification(second_payloads)["questions"][0]
        self.assertNotIn(second_question["field"], {"people_count", "people_type"})

    async def test_synonymous_answers_do_not_consume_multiple_clarification_slots(self):
        request_messages = [
            SimpleNamespace(role="user", content="帮我规划一次旅行", message_id="u-aliases", type="normal"),
        ]
        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=FakeController(chunks=[]),
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=False,
                profile={},
                planning_mode="standard_plan",
                clarification_answers={"people_count": "2人", "people_type": "情侣"},
                sanitize_text=sanitize_text,
            )
        )

        clarification = self._get_single_clarification(payloads)
        self.assertEqual(clarification["answered_count"], 1)
        self.assertNotIn(clarification["questions"][0]["field"], {"people_count", "people_type"})

    async def test_generate_chat_stream_continues_after_four_distinct_answers(self):
        request_messages = [
            SimpleNamespace(role="user", content="帮我规划一次旅行", message_id="u-plan", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-plan",
                        "role": "assistant",
                        "content": "杭州三日游方案",
                        "show_content": "杭州三日游方案",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=False,
                            use_multi_agent=False,
                            profile={},
                            planning_mode="deep_research",
                            clarification_answers={
                                "destination": "杭州",
                                "origin": "上海",
                                "days": "3天",
                                "date_range": "日期暂未确定",
                            },
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertFalse(any(payload.get("type") == "clarification_required" for payload in payloads))
        self.assertIsNone(controller.called_with)
        self.assertTrue(self.production_calls)
        self.assertTrue(
            any(message.get("type") == "system_trip_intent_context" for message in self.production_calls[-1]["message_history"])
        )
        self.assertTrue(any(payload.get("type") == "trip_plan" for payload in payloads))

    async def test_generate_chat_stream_complete_request_does_not_trigger_clarification(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="2026年8月15日至17日从上海出发，帮我规划北京3天2夜特种兵之旅，和朋友两个人，人均2000",
                message_id="u-complete",
                type="normal",
            ),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-complete",
                        "role": "assistant",
                        "content": "完整行程方案",
                        "show_content": "完整行程方案",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=False,
                            use_multi_agent=False,
                            profile={},
                            planning_mode="standard_plan",
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertFalse(any(payload.get("type") == "clarification_required" for payload in payloads))
        self.assertIsNone(controller.called_with)
        self.assertTrue(self.production_calls)
        self.assertTrue(any(payload.get("type") == "trip_plan" for payload in payloads))

    async def test_flexible_date_plan_skips_ticket_and_date_sensitive_research(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="日期还没定，从上海出发，帮我规划北京3天情侣游，人均2000",
                message_id="u-flexible-date",
                type="normal",
            ),
        ]
        controller = FakeController(chunks=[[
            {
                "message_id": "m-flexible-date",
                "role": "assistant",
                "content": "日期待定参考方案",
                "show_content": "日期待定参考方案",
                "type": "final_answer",
            }
        ]])

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None) as ticket_mock:
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None) as travel_mock:
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            profile={},
                            planning_mode="standard_plan",
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertFalse(any(payload.get("type") == "clarification_required" for payload in payloads))
        ticket_mock.assert_not_called()
        travel_mock.assert_not_called()
        self.assertEqual("日期暂未确定", self.production_calls[-1]["intent"].date_range)

    async def test_structured_form_bypasses_semantic_clarification_and_preserves_exact_intent(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="请帮我规划一趟从上海出发、前往杭州的3天旅行，并核验交通和地图数据。",
                message_id="u-structured-form",
                type="normal",
            ),
        ]
        controller = FakeSemanticController(
            chunks=[[
                {
                    "message_id": "m-structured-form",
                    "role": "assistant",
                    "content": "结构化表单行程方案",
                    "show_content": "结构化表单行程方案",
                    "type": "final_answer",
                }
            ]],
            semantic_payload={
                "next_question": {
                    "field": "budget_total",
                    "question": "预算是多少？",
                    "reason": "需要预算",
                    "options": ["3000", "6000"],
                }
            },
        )
        structured_trip_request = {
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-08-15",
            "end_date": "2026-08-17",
            "adults": 2,
            "children": 0,
            "seniors": 0,
            "budget": 6000,
            "party_type": "情侣",
            "preferences": ["人文历史", "当地美食"],
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            structured_trip_request=structured_trip_request,
                            profile={},
                            planning_mode="standard_plan",
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertFalse(any(payload.get("type") == "clarification_required" for payload in payloads))
        intent = next(payload["intent"] for payload in payloads if payload.get("type") == "trip_intent")
        self.assertEqual(intent["origin"], "上海")
        self.assertEqual(intent["destination"], "杭州")
        self.assertEqual(intent["date_range"], "2026-08-15 至 2026-08-17")
        self.assertEqual(intent["people_count"], 2)
        self.assertEqual(intent["adult_count"], 2)
        self.assertEqual(intent["child_count"], 0)
        self.assertEqual(intent["senior_count"], 0)
        self.assertEqual(intent["people_type"], "情侣")
        self.assertEqual(intent["budget_total"], 6000)
        self.assertEqual(intent["interests"], ["人文历史", "当地美食"])
        self.assertIsNone(controller.task_analysis_agent.called_with)
        self.assertIsNone(controller.called_with)
        self.assertTrue(self.production_calls)

    async def test_product_trip_stream_returns_structured_document_without_long_agent_loop(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="2026年8月15日至17日从上海出发，帮我规划杭州3天2夜行程，和朋友两个人，人均2000",
                message_id="u-structured-final",
                type="normal",
            ),
        ]
        controller = FakeController(chunks=[])
        travel_bundle = {
            "query": request_messages[0].content,
            "trip_intent": {
                "destination": "杭州",
                "origin": "上海",
                "days": 3,
                "people_count": 2,
                "people_type": "朋友",
                "budget_per_person": 2000,
                "date_range": "2026-08-15 至 2026-08-17",
            },
            "kinds": ["行程规划"],
            "context_message": "旅行参考信息已整理",
            "xhs_table": "",
            "web_rows": [],
            "map_locations": [
                {
                    "id": "west-lake",
                    "name": "西湖风景名胜区",
                    "lat": 30.242,
                    "lng": 120.141,
                    "description": "杭州市西湖区",
                    "category": "景点",
                    "order": 1,
                    "day": 1,
                    "data_type": "reference_data",
                }
            ],
            "append_markdown": "",
            "fallback_answer": "",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=travel_bundle):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=True,
                            use_multi_agent=True,
                            profile={},
                            planning_mode="standard_plan",
                            clarification_answers={"pace": "适中平衡"},
                            sanitize_text=lambda text: text,
                        )
                    )

        self.assertIsNone(controller.called_with)
        self.assertTrue(any(payload.get("type") == "trip_plan" for payload in payloads), payloads)
        plan = next(payload for payload in payloads if payload.get("type") == "trip_plan")
        self.assertEqual("3.0", plan["document"]["schema_version"])
        self.assertTrue(plan["task_graph_executed"])
        self.assertEqual(payloads[-1]["type"], "chat_complete")
        self.assertEqual(payloads[-1]["finish_reason"], "completed")

    async def test_generate_chat_stream_uses_semantic_analysis_before_next_question(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="帮我规划北京三日游，我们仨姐妹一起",
                message_id="u-semantic",
                type="normal",
            ),
        ]
        controller = FakeSemanticController(
            chunks=[],
            semantic_payload={
                "intent_patch": {"people_type": "朋友"},
                "evidence": {"people_type": "我们仨姐妹"},
                "next_question": {
                    "field": "date_range",
                    "question": "这趟北京之旅准备什么时候出发？",
                    "reason": "日期会影响天气和预约紧张程度。",
                    "options": ["本周末", "下周", "节假日", "日期未定"],
                    "allow_custom": True,
                },
            },
        )

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                profile={},
                planning_mode="standard_plan",
                sanitize_text=sanitize_text,
            )
        )

        intent_event = next(payload for payload in payloads if payload.get("type") == "trip_intent")
        clarification = self._get_single_clarification(payloads)
        self.assertEqual(intent_event["intent"]["people_type"], "朋友")
        self.assertEqual(clarification["questions"][0]["field"], "origin")
        self.assertIsNotNone(controller.task_analysis_agent.called_with)
        self.assertIsNone(controller.called_with)

    async def test_generate_chat_stream_injects_selected_knowledge_context(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="2026年8月15日至17日从上海出发，帮我规划杭州三日游，和朋友一起，轻松一点",
                message_id="u-knowledge",
                type="normal",
            ),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-knowledge",
                        "role": "assistant",
                        "content": "已结合知识库。",
                        "show_content": "已结合知识库。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=False,
                            use_multi_agent=False,
                            selected_knowledge_context=[
                                {
                                    "city": "杭州",
                                    "title": "西湖城区路线",
                                    "source": "travel_guide",
                                    "snippet": "西湖、河坊街适合放在同一天。",
                                }
                            ],
                            clarification_answers={"budget_total": "不设严格预算"},
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertIsNone(controller.called_with)
        self.assertTrue(self.production_calls)
        knowledge_messages = [
            message for message in self.production_calls[-1]["message_history"]
            if message.get("type") == "system_selected_knowledge_context"
        ]
        self.assertEqual(len(knowledge_messages), 1)
        self.assertIn("西湖城区路线", knowledge_messages[0]["content"])
        self.assertTrue(any(payload.get("type") == "trip_plan" for payload in payloads))

    async def test_generate_chat_stream_emits_error_event_on_exception(self):
        request_messages = [
            SimpleNamespace(role="user", content="fail", message_id="u-1", type="normal"),
        ]

        payloads = await self._collect_sse_payloads(
            generate_chat_stream(
                request_messages=request_messages,
                controller=ErrorController(),
                tool_manager=object(),
                selected_mcp_servers=None,
                use_deepthink=True,
                use_multi_agent=True,
                request_id="req-error",
                sanitize_text=sanitize_text,
            )
        )

        self.assertEqual(payloads[0]["type"], "chat_start")
        self.assertEqual(payloads[1]["type"], "error")
        self.assertEqual(
            set(payloads[1]),
            {"type", "request_id", "sequence", "code", "phase", "retryable", "user_message", "actions"},
        )
        self.assertEqual(payloads[1]["request_id"], "req-error")
        self.assertEqual(payloads[1]["code"], "CHAT_STREAM_FAILED")
        self.assertEqual(payloads[1]["phase"], "drafting")
        self.assertTrue(payloads[1]["retryable"])
        self.assertEqual(payloads[1]["actions"], ["retry"])
        serialized_error = json.dumps(payloads[1], ensure_ascii=False)
        self.assertNotIn("stream boom", serialized_error)
        self.assertNotIn("base_url", serialized_error)
        self.assertNotIn("diagnostics", serialized_error)

    async def test_generate_chat_stream_appends_realtime_ticket_table_when_bundle_exists(self):
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州高铁票", message_id="u-1", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-final",
                        "role": "assistant",
                        "content": "推荐直达车次如下。",
                        "show_content": "推荐直达车次如下。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        bundle = {
            "context_message": "【实时票务查询结果】\n查询路线: 安阳 -> 杭州",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=False,
                    sanitize_text=sanitize_text,
                )
            )

        self.assertIsNone(controller.called_with)

        appendix_chunks = [
            event for event in payloads
            if event.get("type") == "chat_chunk" and "火车和高铁票信息表" in event.get("content", "")
        ]
        self.assertTrue(appendix_chunks)
        self.assertEqual(appendix_chunks[-1]["message_id"], payloads[0]["message_id"])
        self.assertEqual(payloads[-1]["type"], "chat_complete")
        self.assertEqual(payloads[-1]["finish_reason"], "completed")

    async def test_generate_chat_stream_keeps_model_final_and_appends_table_when_enforced(self):
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州最便宜交通", message_id="u-2", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-real",
                        "role": "assistant",
                        "content": "推荐虚构车次 G999，票价99元。",
                        "show_content": "推荐虚构车次 G999，票价99元。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        bundle = {
            "context_message": "【实时票务查询结果】\n查询路线: 安阳 -> 杭州",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 中转 | G1955 + G743 |",
            "enforce_realtime_only": True,
            "realtime_only_answer": "仅基于实时查询结果输出。\n\n火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 中转 | G1955 + G743 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=False,
                    sanitize_text=sanitize_text,
                )
            )

        chunk_contents = [
            payload.get("content", "")
            for payload in payloads
            if payload.get("type") == "chat_chunk"
        ]

        self.assertFalse(any("G999" in content for content in chunk_contents))
        self.assertTrue(any("仅基于实时查询结果输出" in content for content in chunk_contents))
        self.assertTrue(any("火车和高铁票信息表" in content for content in chunk_contents))

    async def test_generate_chat_stream_hides_ticket_intermediate_phase_noise(self):
        request_messages = [
            SimpleNamespace(role="user", content="明天从安阳到杭州怎么去", message_id="u-3", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-ob",
                        "role": "assistant",
                        "content": "Observation: {\"needs_more_input\": false, \"is_completed\": false, \"analysis\": \"分析失败：格式错误或获取到了无效的返回。\"}",
                        "show_content": "Observation: {\"needs_more_input\": false, \"is_completed\": false, \"analysis\": \"分析失败：格式错误或获取到了无效的返回。\"}",
                        "type": "observation_result",
                    }
                ],
                [
                    {
                        "message_id": "m-final-fail",
                        "role": "assistant",
                        "content": "观察阶段执行出现异常，已停止后续链路。请稍后重试。",
                        "show_content": "观察阶段执行出现异常，已停止后续链路。请稍后重试。",
                        "type": "final_answer",
                    }
                ],
            ]
        )

        bundle = {
            "context_message": "【实时票务查询结果】\n查询路线: 安阳 -> 杭州",
            "append_markdown": "火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
            "enforce_realtime_only": True,
            "realtime_only_answer": "仅基于实时查询结果输出。\n\n火车和高铁票信息表\n| 类型 | 班次 |\n| --- | --- |\n| 直达 | G1 |",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=bundle):
            payloads = await self._collect_sse_payloads(
                generate_chat_stream(
                    request_messages=request_messages,
                    controller=controller,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    use_deepthink=True,
                    use_multi_agent=False,
                    sanitize_text=sanitize_text,
                )
            )

        chunk_contents = [
            payload.get("content", "")
            for payload in payloads
            if payload.get("type") == "chat_chunk"
        ]

        self.assertFalse(any("Observation:" in content for content in chunk_contents))
        self.assertFalse(any("观察阶段执行出现异常" in content for content in chunk_contents))
        self.assertTrue(any("仅基于实时查询结果输出" in content for content in chunk_contents))
        self.assertTrue(any("火车和高铁票信息表" in content for content in chunk_contents))

    async def test_generate_chat_stream_injects_xhs_context_and_appends_xhs_table(self):
        request_messages = [
            SimpleNamespace(role="user", content="杭州三日游攻略，帮我做一份简明总结", message_id="u-xhs", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-xhs",
                        "role": "assistant",
                        "content": "我先给你行程建议。",
                        "show_content": "我先给你行程建议。",
                        "type": "final_answer",
                    }
                ]
            ]
        )

        xhs_bundle = {
            "context_message": "【小红书检索结果】\n用户问题: 杭州三日游攻略，帮我做一份简明总结",
            "append_markdown": "### 小红书检索资源表\n| 标题 | 作者 |\n| --- | --- |\n| 杭州CityWalk | 旅行博主A |",
            "fallback_answer": "已完成小红书检索并总结。",
        }

        with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
            with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=xhs_bundle):
                with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=None):
                    payloads = await self._collect_sse_payloads(
                        generate_chat_stream(
                            request_messages=request_messages,
                            controller=controller,
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            use_deepthink=True,
                            use_multi_agent=False,
                            sanitize_text=sanitize_text,
                        )
                    )

        self.assertIsNotNone(controller.called_with)
        self.assertEqual(controller.called_with["input_messages"][-1]["type"], "system_xhs_search_context")
        chunk_contents = [
            payload.get("content", "")
            for payload in payloads
            if payload.get("type") == "chat_chunk"
        ]
        self.assertTrue(any("小红书检索资源表" in content for content in chunk_contents))

    @patch("services.chat_service.build_day_route", new_callable=AsyncMock)
    async def test_generate_chat_stream_appends_travel_map_locations(self, build_day_route):
        # This SSE contract covers unavailable routes, independent of local .env
        # credentials. Provider integration is verified by separate route tests.
        def unavailable_day_route(**kwargs):
            return {
                "day": kwargs["day"],
                "plan_version": kwargs["plan_version"],
                "provider": "baidu_directionlite",
                "coordinate_system": "BD09LL",
                "legs": [],
                "bbox": None,
                "status": "unavailable",
            }

        build_day_route.side_effect = unavailable_day_route
        request_messages = [
            SimpleNamespace(role="user", content="推荐上海外滩附近性价比高的酒店", message_id="u-travel", type="normal"),
        ]
        controller = FakeController(
            chunks=[
                [
                    {
                        "message_id": "m-travel-intro",
                        "role": "assistant",
                        "content": "I will check weather first.",
                        "show_content": "I will check weather first.",
                        "type": "final_answer",
                    },
                    {
                        "message_id": "m-travel-weather",
                        "role": "assistant",
                        "content": '[map_weather] result: {"content": "{\\"location\\": {\\"city\\": \\"Beijing\\"}}"}',
                        "show_content": '[map_weather] result: {"content": "{\\"location\\": {\\"city\\": \\"Beijing\\"}}"}',
                        "type": "final_answer",
                    },
                    {
                        "message_id": "m-travel",
                        "role": "assistant",
                        "content": "# 上海外滩酒店推荐\n\n## 结论\n优先看外滩步行范围和陆家嘴景观酒店。",
                        "show_content": "# 上海外滩酒店推荐\n\n## 结论\n优先看外滩步行范围和陆家嘴景观酒店。",
                        "type": "final_answer",
                    }
                ]
            ]
        )
        travel_bundle = {
            "query": "推荐上海外滩附近性价比高的酒店",
            "trip_intent": {"destination": "上海", "days": 2},
            "kinds": ["酒店住宿"],
            "context_message": "【旅行体验增强要求】\n分组样式\nmap_locations",
            "xhs_table": "",
            "web_rows": [
                {
                    "title": "上海外滩酒店推荐",
                    "snippet": "外滩附近酒店可分层选择。",
                    "url": "https://example.com/hotel",
                    "source": "web",
                }
            ],
            "map_locations": [
                {
                    "id": "travel_place_1",
                    "name": "上海外滩华尔道夫酒店",
                    "lat": 31.2366,
                    "lng": 121.4908,
                    "description": "中山东一路2号",
                    "category": "酒店",
                    "order": 1,
                    "day": 1,
                },
                {
                    "id": "travel_place_2",
                    "name": "外滩观景步道",
                    "lat": 31.2401,
                    "lng": 121.4905,
                    "description": "酒店附近步行游览",
                    "category": "景点",
                    "order": 2,
                    "day": 1,
                },
                {
                    "id": "travel_place_3",
                    "name": "豫园",
                    "lat": 31.2272,
                    "lng": 121.4921,
                    "description": "第二日城市文化游览",
                    "category": "景点",
                    "order": 3,
                    "day": 2,
                },
                {
                    "id": "travel_meal_1",
                    "name": "外滩早餐店",
                    "lat": 31.2370,
                    "lng": 121.4900,
                    "description": "第一日早餐",
                    "category": "餐厅",
                    "order": 4,
                    "day": 1,
                },
                {
                    "id": "travel_meal_2",
                    "name": "外滩午餐厅",
                    "lat": 31.2380,
                    "lng": 121.4910,
                    "description": "第一日午餐",
                    "category": "餐厅",
                    "order": 5,
                    "day": 1,
                },
                {
                    "id": "travel_meal_3",
                    "name": "外滩晚餐厅",
                    "lat": 31.2390,
                    "lng": 121.4920,
                    "description": "第一日晚餐",
                    "category": "餐厅",
                    "order": 6,
                    "day": 1,
                },
                {
                    "id": "travel_meal_4",
                    "name": "豫园早餐店",
                    "lat": 31.2260,
                    "lng": 121.4910,
                    "description": "第二日早餐",
                    "category": "餐厅",
                    "order": 7,
                    "day": 2,
                },
                {
                    "id": "travel_meal_5",
                    "name": "豫园午餐厅",
                    "lat": 31.2270,
                    "lng": 121.4930,
                    "description": "第二日午餐",
                    "category": "餐厅",
                    "order": 8,
                    "day": 2,
                },
                {
                    "id": "travel_meal_6",
                    "name": "豫园晚餐厅",
                    "lat": 31.2280,
                    "lng": 121.4940,
                    "description": "第二日晚餐",
                    "category": "餐厅",
                    "order": 9,
                    "day": 2,
                }
            ],
            "append_markdown": "",
            "fallback_answer": "# 上海外滩酒店推荐\n\n## 结论\n优先看外滩步行范围。",
        }

        with TemporaryDirectory() as tmpdir:
            with patch("services.chat_service.get_output_root_path", return_value=Path(tmpdir)):
                with patch("services.chat_service.maybe_prepare_train_ticket_bundle", return_value=None):
                    with patch("services.chat_service.maybe_prepare_xhs_search_bundle", return_value=None):
                        with patch("services.chat_service.maybe_prepare_travel_experience_bundle", return_value=travel_bundle):
                            payloads = await self._collect_sse_payloads(
                                generate_chat_stream(
                                    request_messages=request_messages,
                                    controller=controller,
                                    tool_manager=object(),
                                    selected_mcp_servers=None,
                                    use_deepthink=True,
                                    use_multi_agent=False,
                                    sanitize_text=sanitize_text,
                                )
                            )

        self.assertIsNone(controller.called_with)
        event_types = [payload.get("type") for payload in payloads]
        self.assertIn("trip_sources", event_types)
        self.assertIn("trip_locations", event_types)
        self.assertIn("trip_budget", event_types)
        self.assertIn("trip_validation", event_types)
        self.assertIn("trip_plan_repair", event_types)
        self.assertIn("trip_plan_delta", event_types)
        self.assertIn("trip_day_upsert", event_types)
        self.assertIn("trip_plan", event_types)

        shell_index = event_types.index("trip_plan_delta")
        day_indexes = [index for index, event_type in enumerate(event_types) if event_type == "trip_day_upsert"]
        budget_index = event_types.index("trip_budget")
        sources_index = event_types.index("trip_sources")
        validation_index = event_types.index("trip_validation")
        plan_index = event_types.index("trip_plan")
        self.assertTrue(day_indexes)
        self.assertLess(shell_index, day_indexes[0])
        self.assertLess(day_indexes[-1], budget_index)
        self.assertLess(budget_index, sources_index)
        self.assertLess(sources_index, validation_index)
        self.assertLess(validation_index, plan_index)

        sources_event = next(payload for payload in payloads if payload.get("type") == "trip_sources")
        self.assertEqual(sources_event["sources"][0]["title"], "上海外滩酒店推荐")

        locations_event = next(payload for payload in payloads if payload.get("type") == "trip_locations")
        self.assertEqual(locations_event["locations"][0]["name"], "上海外滩华尔道夫酒店")

        budget_event = next(payload for payload in payloads if payload.get("type") == "trip_budget")
        self.assertEqual(budget_event["budget"]["currency"], "CNY")

        validation_event = next(payload for payload in payloads if payload.get("type") == "trip_validation")
        self.assertTrue(validation_event["validation"]["valid"])

        repair_event = next(payload for payload in payloads if payload.get("type") == "trip_plan_repair")
        self.assertIn("initial_validation", repair_event)
        self.assertIn("final_validation", repair_event)
        self.assertEqual(validation_event["validation"], repair_event["final_validation"])

        plan_delta_event = next(payload for payload in payloads if payload.get("type") == "trip_plan_delta")
        self.assertEqual(plan_delta_event["operation"], "initialize_plan")
        self.assertEqual(plan_delta_event["delta"]["activities"], [])
        self.assertEqual(plan_delta_event["plan"]["days"], [])

        day_event = next(payload for payload in payloads if payload.get("type") == "trip_day_upsert")
        day_events = [payload for payload in payloads if payload.get("type") == "trip_day_upsert"]
        self.assertEqual([payload["day"]["day"] for payload in day_events], [1, 2])
        self.assertTrue(all(payload["revision"] == 1 for payload in day_events))
        self.assertEqual(day_event["day"]["day"], 1)
        self.assertEqual(day_event["revision"], 1)
        self.assertEqual(day_event["day"]["revision"], 1)
        self.assertEqual(day_event["day"]["activities"][0]["title"], "上海外滩华尔道夫酒店")
        self.assertTrue(day_event["day"]["activities"][0]["activity_id"].startswith("travel_place_"))
        route = day_event["day"]["activities"][0]["route_to_next"]
        self.assertEqual(build_day_route.await_count, 2)
        self.assertEqual([call.kwargs["day"] for call in build_day_route.await_args_list], [1, 2])
        self.assertEqual(route["data_type"], "estimated_data")
        self.assertEqual(route["provider"], "行程规划估算")
        self.assertIsNone(route["distance_meters"])
        self.assertIsNone(route["duration_minutes"])
        self.assertIsNone(route["estimated_cost"])
        self.assertIsNone(route["calculated_at"])

        plan_event = next(payload for payload in payloads if payload.get("type") == "trip_plan")
        self.assertEqual(plan_event["plan"]["title"], "推荐上海外滩附近性价比高的酒店")
        self.assertEqual(plan_event["plan"]["plan_id"], plan_delta_event["plan"]["plan_id"])
        self.assertEqual(plan_event["version"], plan_event["plan"]["version"])
        self.assertEqual(plan_event["plan"]["day_count"], 2)
        self.assertEqual(len(plan_event["plan"]["days"]), 2)
        self.assertEqual(len(plan_event["plan"]["activities"]), 7)
        self.assertEqual(plan_event["plan"]["validation"]["valid"], True)
        self.assertEqual(plan_event["plan"]["validation"], repair_event["final_validation"])
        structured_payloads = payloads[shell_index:plan_index + 1]
        self.assertTrue(all(payload["request_id"] == structured_payloads[0]["request_id"] for payload in structured_payloads))
        travel_chunks = [payload for payload in payloads if payload.get("type") == "chat_chunk"]
        chunk_contents = [payload.get("content", "") for payload in travel_chunks]
        self.assertFalse(any("map_weather" in content for content in chunk_contents))
        self.assertFalse(any("Beijing" in content for content in chunk_contents))
        self.assertTrue(any("## 1. 目的地介绍" in content for content in chunk_contents))
        self.assertTrue(any("## 8. 下载与分享" in content for content in chunk_contents))
        self.assertFalse(any('"map_locations"' in content for content in chunk_contents))
        self.assertTrue(any("上海外滩华尔道夫酒店" in content for content in chunk_contents))
        self.assertTrue(any(payload.get("replace") is True for payload in travel_chunks))

    def test_trip_plan_accepts_legacy_flat_and_new_daily_shapes(self):
        legacy = TripPlan.model_validate(
            {
                "title": "旧版结构",
                "intent": {"destination": "北京", "days": 1},
                "days": 1,
                "activities": [
                    {
                        "day": 1,
                        "title": "故宫",
                        "transport_to_next": "步行",
                    }
                ],
            }
        )
        self.assertEqual(legacy.days, 1)
        self.assertEqual(legacy.activities[0].route_to_next.mode, "步行")
        self.assertEqual(legacy.activities[0].route_to_next.data_type, "estimated_data")

        daily = TripPlan.model_validate(
            {
                "plan_id": "plan_daily",
                "version": 2,
                "title": "新版结构",
                "intent": {"destination": "杭州", "days": 1},
                "day_count": 1,
                "days": [
                    {
                        "day": 1,
                        "revision": 3,
                        "activities": [
                            {
                                "activity_id": "act_daily_1",
                                "day": 1,
                                "title": "西湖",
                                "duration_minutes": 120,
                                "reservation": {"required": False},
                                "evidence_refs": ["ref_1"],
                                "route_to_next": {
                                    "mode": "地铁",
                                    "duration_minutes": 20,
                                    "provider": "官方路线服务",
                                    "data_type": "confirmed_live_data",
                                },
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(daily.days, 1)
        self.assertEqual(daily.trip_days[0].revision, 3)
        self.assertEqual(daily.activities[0].activity_id, "act_daily_1")
        self.assertEqual(daily.activities[0].transport_to_next, "地铁")
        self.assertEqual(daily.activities[0].route_to_next.data_type, "confirmed_live_data")


if __name__ == "__main__":
    unittest.main()
