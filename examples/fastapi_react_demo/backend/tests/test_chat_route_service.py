import importlib
import importlib.util
import sys
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


class FakeLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


class FakeController:
    def run(self, messages, tool_manager, session_id, deep_thinking, summary, deep_research):
        return {
            "messages": messages,
            "session_id": session_id,
            "deep_thinking": deep_thinking,
            "summary": summary,
            "deep_research": deep_research,
        }


class ChatRouteServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        cls.chat_service = importlib.import_module("services.chat_service")
        cls.HTTPException = importlib.import_module("fastapi").HTTPException

    def test_execute_chat_route_returns_success_payload(self):
        logger = FakeLogger()
        controller = FakeController()
        messages = [SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")]

        payload = self.chat_service.execute_chat_route(
            request_messages=messages,
            controller=controller,
            tool_manager=object(),
            session_id="s1",
            use_deepthink=True,
            use_multi_agent=False,
            selected_skill_ids=[],
            logger=logger,
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["session_id"], "s1")
        self.assertEqual(payload["result"]["deep_research"], False)
        self.assertEqual(logger.errors, [])

    def test_execute_chat_route_adds_travel_rag_context_for_travel_query(self):
        logger = FakeLogger()
        controller = FakeController()
        messages = [
            SimpleNamespace(
                role="user",
                content="帮我规划杭州两日游",
                message_id="u1",
                type="normal",
            )
        ]

        with patch(
            "services.travel_rag_service.build_travel_rag_context",
            return_value="旅行知识库上下文",
        ) as rag_mock:
            payload = self.chat_service.execute_chat_route(
                request_messages=messages,
                controller=controller,
                tool_manager=object(),
                session_id="s1",
                use_deepthink=True,
                use_multi_agent=False,
                selected_skill_ids=[],
                logger=logger,
            )

        rag_mock.assert_called_once_with("帮我规划杭州两日游")
        injected_messages = [
            message
            for message in payload["result"]["messages"]
            if message.get("type") == "system_travel_rag_context"
        ]
        self.assertEqual(len(injected_messages), 1)
        self.assertEqual(injected_messages[0]["role"], "system")
        self.assertIn("旅行知识库上下文", injected_messages[0]["content"])

    def test_execute_chat_route_wraps_missing_controller_as_500(self):
        logger = FakeLogger()
        messages = [SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")]

        with self.assertRaises(self.HTTPException) as ctx:
            self.chat_service.execute_chat_route(
                request_messages=messages,
                controller=None,
                tool_manager=object(),
                session_id="s1",
                use_deepthink=True,
                use_multi_agent=True,
                selected_skill_ids=[],
                logger=logger,
            )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("400", str(ctx.exception.detail))
        self.assertEqual(len(logger.errors), 1)

    def test_execute_chat_request_route_delegates_to_field_wrapper(self):
        logger = FakeLogger()
        request = SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")],
            session_id="s1",
            use_deepthink=False,
            use_multi_agent=True,
            selected_skill_ids=["rail_transport"],
        )
        controller = object()
        tool_manager = object()
        expected = {"status": "success", "result": {"ok": True}, "session_id": "s1"}

        with patch.object(self.chat_service, "execute_chat_route", return_value=expected) as mocked:
            payload = self.chat_service.execute_chat_request_route(
                request=request,
                controller=controller,
                tool_manager=tool_manager,
                logger=logger,
            )

        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(
            request_messages=request.messages,
            controller=controller,
            tool_manager=tool_manager,
            session_id="s1",
            use_deepthink=False,
            use_multi_agent=True,
            selected_skill_ids=["rail_transport"],
            logger=logger,
        )

    def test_execute_chat_request_runtime_route_delegates_to_request_wrapper(self):
        request = SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")],
            session_id="s1",
            use_deepthink=True,
            use_multi_agent=False,
            selected_skill_ids=[],
        )
        runtime_state = SimpleNamespace(controller=object(), tool_manager=object())
        expected = {"status": "success", "result": {"ok": True}, "session_id": "s1"}

        with patch.object(self.chat_service, "execute_chat_request_route", return_value=expected) as mocked:
            payload = self.chat_service.execute_chat_request_runtime_route(
                request=request,
                runtime_state=runtime_state,
            )

        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(
            request=request,
            controller=runtime_state.controller,
            tool_manager=runtime_state.tool_manager,
            logger=self.chat_service.logger,
        )

    def test_build_chat_stream_route_delegates_to_response_builder(self):
        logger = FakeLogger()
        messages = [SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")]
        sentinel = object()
        controller = object()
        tool_manager = object()

        with patch.object(
            self.chat_service,
            "build_chat_stream_response",
            return_value=sentinel,
        ) as mocked:
            payload = self.chat_service.build_chat_stream_route(
                request_messages=messages,
                controller=controller,
                tool_manager=tool_manager,
                selected_mcp_servers=["mcp-a"],
                selected_skill_ids=[],
                use_deepthink=True,
                use_multi_agent=False,
                logger=logger,
            )

        self.assertIs(payload, sentinel)
        self.assertEqual(logger.errors, [])
        mocked.assert_called_once_with(
            request_messages=messages,
            controller=controller,
            tool_manager=tool_manager,
            selected_mcp_servers=["mcp-a"],
            selected_skill_ids=[],
            use_deepthink=True,
            use_multi_agent=False,
        )

    def test_build_chat_stream_route_uses_default_sse_headers_when_missing(self):
        logger = FakeLogger()
        messages = [SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")]
        sentinel = object()
        controller = object()
        tool_manager = object()

        with patch.object(
            self.chat_service,
            "build_chat_stream_response",
            return_value=sentinel,
        ) as mocked:
            payload = self.chat_service.build_chat_stream_route(
                request_messages=messages,
                controller=controller,
                tool_manager=tool_manager,
                selected_mcp_servers=None,
                selected_skill_ids=[],
                use_deepthink=True,
                use_multi_agent=True,
                logger=logger,
            )

        self.assertIs(payload, sentinel)
        self.assertEqual(logger.errors, [])
        mocked.assert_called_once_with(
            request_messages=messages,
            controller=controller,
            tool_manager=tool_manager,
            selected_mcp_servers=None,
            selected_skill_ids=[],
            use_deepthink=True,
            use_multi_agent=True,
        )

    def test_build_chat_stream_response_uses_default_sse_headers_when_missing(self):
        sentinel = object()

        with patch.object(self.chat_service, "get_sse_headers", return_value={"h": "v"}) as headers_mock:
            with patch("fastapi.responses.StreamingResponse", return_value=sentinel) as response_mock:
                payload = self.chat_service.build_chat_stream_response(
                    request_messages=[],
                    controller=object(),
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    selected_skill_ids=[],
                    use_deepthink=False,
                    use_multi_agent=False,
                    sanitize_text=lambda text: text,
                )

        self.assertIs(payload, sentinel)
        headers_mock.assert_called_once_with()
        response_mock.assert_called_once()
        self.assertEqual(response_mock.call_args.kwargs["headers"], {"h": "v"})

    def test_build_chat_stream_response_uses_default_sanitizer_when_missing(self):
        sentinel = object()
        fake_stream = object()
        default_sanitizer = lambda text: f"default::{text}"

        with patch.object(self.chat_service, "resolve_sanitize_text", return_value=default_sanitizer) as sanitizer_mock:
            with patch.object(self.chat_service, "generate_chat_stream", return_value=fake_stream) as stream_mock:
                with patch.object(self.chat_service, "get_sse_headers", return_value={"h": "v"}):
                    with patch("fastapi.responses.StreamingResponse", return_value=sentinel):
                        payload = self.chat_service.build_chat_stream_response(
                            request_messages=[],
                            controller=object(),
                            tool_manager=object(),
                            selected_mcp_servers=None,
                            selected_skill_ids=[],
                            use_deepthink=False,
                            use_multi_agent=False,
                        )

        self.assertIs(payload, sentinel)
        sanitizer_mock.assert_called_once_with(None)
        stream_mock.assert_called_once_with(
            request_messages=[],
            controller=unittest.mock.ANY,
            tool_manager=unittest.mock.ANY,
            selected_mcp_servers=None,
            selected_skill_ids=[],
            use_deepthink=False,
            use_multi_agent=False,
            sanitize_text=default_sanitizer,
        )

    def test_generate_chat_stream_filters_tool_manager_when_selected_servers_empty(self):
        request_messages = [SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")]
        original_tool_manager = object()
        filtered_tool_manager = object()
        observed = {}

        def run_stream(**kwargs):
            observed["tool_manager"] = kwargs["tool_manager"]
            return [[{
                "content": "hi",
                "show_content": "hi",
                "message_id": "m1",
                "role": "assistant",
                "type": "normal",
            }]]

        controller = SimpleNamespace(run_stream=run_stream)

        async def collect_events():
            events = []
            async for chunk in self.chat_service.generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=original_tool_manager,
                selected_mcp_servers=[],
                selected_skill_ids=[],
                use_deepthink=False,
                use_multi_agent=False,
                sanitize_text=lambda text: text,
            ):
                events.append(chunk)
                if '"type": "chat_complete"' in chunk:
                    break
            return events

        with patch.object(
            self.chat_service,
            "create_filtered_tool_manager",
            AsyncMock(return_value=filtered_tool_manager),
        ) as mocked_filter:
            events = asyncio.run(collect_events())

        mocked_filter.assert_awaited_once_with(original_tool_manager, [])
        self.assertIs(observed["tool_manager"], filtered_tool_manager)
        self.assertTrue(any('"type": "chat_start"' in event for event in events))
        self.assertTrue(any('"type": "chat_complete"' in event for event in events))

    def test_generate_chat_stream_adds_travel_rag_context_for_travel_query(self):
        request_messages = [
            SimpleNamespace(
                role="user",
                content="帮我规划杭州两日游",
                message_id="u1",
                type="normal",
            )
        ]
        observed = {}

        def run_stream(**kwargs):
            observed["input_messages"] = kwargs["input_messages"]
            return [[{
                "content": "hi",
                "show_content": "hi",
                "message_id": "m1",
                "role": "assistant",
                "type": "normal",
            }]]

        controller = SimpleNamespace(run_stream=run_stream)

        async def collect_events():
            events = []
            async for chunk in self.chat_service.generate_chat_stream(
                request_messages=request_messages,
                controller=controller,
                tool_manager=object(),
                selected_mcp_servers=None,
                selected_skill_ids=[],
                use_deepthink=False,
                use_multi_agent=False,
                sanitize_text=lambda text: text,
            ):
                events.append(chunk)
                if '"type": "chat_complete"' in chunk:
                    break
            return events

        with patch(
            "services.travel_rag_service.build_travel_rag_context",
            return_value="旅行知识库上下文",
        ) as rag_mock:
            events = asyncio.run(collect_events())

        rag_mock.assert_called_once_with("帮我规划杭州两日游")
        injected_messages = [
            message
            for message in observed["input_messages"]
            if message.get("type") == "system_travel_rag_context"
        ]
        self.assertEqual(len(injected_messages), 1)
        self.assertIn("旅行知识库上下文", injected_messages[0]["content"])
        self.assertTrue(any('"type": "chat_start"' in event for event in events))
        self.assertTrue(any('"type": "chat_complete"' in event for event in events))

    def test_build_chat_stream_request_route_delegates_to_field_wrapper(self):
        logger = FakeLogger()
        request = SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")],
            selected_mcp_servers=["mcp-a"],
            selected_skill_ids=["rail_transport"],
            use_deepthink=True,
            use_multi_agent=False,
        )
        controller = object()
        tool_manager = object()
        sentinel = object()

        with patch.object(self.chat_service, "build_chat_stream_route", return_value=sentinel) as mocked:
            payload = self.chat_service.build_chat_stream_request_route(
                request=request,
                controller=controller,
                tool_manager=tool_manager,
                logger=logger,
            )

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(
            request_messages=request.messages,
            controller=controller,
            tool_manager=tool_manager,
            selected_mcp_servers=["mcp-a"],
            selected_skill_ids=["rail_transport"],
            use_deepthink=True,
            use_multi_agent=False,
            logger=logger,
        )

    def test_build_chat_stream_request_runtime_route_delegates_to_request_wrapper(self):
        request = SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="hello", message_id="u1", type="normal")],
            selected_mcp_servers=["mcp-a"],
            selected_skill_ids=["rail_transport"],
            use_deepthink=True,
            use_multi_agent=True,
        )
        runtime_state = SimpleNamespace(controller=object(), tool_manager=object())
        sentinel = object()

        with patch.object(self.chat_service, "build_chat_stream_request_route", return_value=sentinel) as mocked:
            payload = self.chat_service.build_chat_stream_request_runtime_route(
                request=request,
                runtime_state=runtime_state,
            )

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(
            request=request,
            controller=runtime_state.controller,
            tool_manager=runtime_state.tool_manager,
            logger=self.chat_service.logger,
        )

    def test_build_chat_stream_route_passthrough_http_exception(self):
        logger = FakeLogger()
        expected_exc = self.HTTPException(status_code=500, detail="系统未配置，请先配置API密钥")

        with patch.object(
            self.chat_service,
            "build_chat_stream_response",
            side_effect=expected_exc,
        ):
            with self.assertRaises(self.HTTPException) as ctx:
                self.chat_service.build_chat_stream_route(
                    request_messages=[],
                    controller=None,
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    selected_skill_ids=[],
                    use_deepthink=True,
                    use_multi_agent=True,
                    logger=logger,
                )

        self.assertIs(ctx.exception, expected_exc)
        self.assertEqual(logger.errors, [])

    def test_build_chat_stream_route_wraps_unexpected_exception(self):
        logger = FakeLogger()

        with patch.object(
            self.chat_service,
            "build_chat_stream_response",
            side_effect=RuntimeError("stream build boom"),
        ):
            with self.assertRaises(self.HTTPException) as ctx:
                self.chat_service.build_chat_stream_route(
                    request_messages=[],
                    controller=object(),
                    tool_manager=object(),
                    selected_mcp_servers=None,
                    selected_skill_ids=[],
                    use_deepthink=True,
                    use_multi_agent=True,
                    logger=logger,
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "stream build boom")
        self.assertEqual(len(logger.errors), 1)
        self.assertIn("stream build boom", logger.errors[0])


if __name__ == "__main__":
    unittest.main()
