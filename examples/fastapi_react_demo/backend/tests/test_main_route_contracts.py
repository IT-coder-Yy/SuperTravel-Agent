import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


class DummyResponse:
    def __init__(self):
        self.headers = {}


class FakeToolManager:
    def list_tools(self):
        return [{"name": "tool-a"}, {"name": "tool-b"}]

    def list_tools_simplified(self):
        return [
            {
                "name": "calculate",
                "description": "math calculator",
                "parameters": {"expression": {"type": "string"}},
            },
            {
                "name": "factorial",
                "description": "factorial calculator",
                "parameters": {"n": {"type": "integer"}},
            },
        ]


class MainRouteContractTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        cls.main = importlib.import_module("main")

    def setUp(self):
        self._orig_tool_manager = self.main.runtime_state.tool_manager
        self._orig_controller = self.main.runtime_state.controller
        self._orig_trip_repository = self.main.runtime_state.trip_repository
        self._orig_provider_gateway = self.main.runtime_state.provider_gateway
        self._orig_planning_orchestrator = self.main.runtime_state.planning_orchestrator
        self._orig_planning_run_manager = self.main.runtime_state.planning_run_manager
        self._orig_sessions = dict(self.main.runtime_state.active_sessions)
        self.main.runtime_state.active_sessions.clear()

    def tearDown(self):
        self.main.runtime_state.tool_manager = self._orig_tool_manager
        self.main.runtime_state.controller = self._orig_controller
        self.main.runtime_state.trip_repository = self._orig_trip_repository
        self.main.runtime_state.provider_gateway = self._orig_provider_gateway
        self.main.runtime_state.planning_orchestrator = self._orig_planning_orchestrator
        self.main.runtime_state.planning_run_manager = self._orig_planning_run_manager
        self.main.runtime_state.active_sessions.clear()
        self.main.runtime_state.active_sessions.update(self._orig_sessions)

    async def test_get_system_status_contract_and_cors(self):
        self.main.runtime_state.tool_manager = FakeToolManager()
        self.main.runtime_state.active_sessions["session-1"] = {"x": 1}
        expected_payload = self.main.SystemStatus(
            status="running",
            agents_count=7,
            tools_count=2,
            active_sessions=1,
            version="0.8",
        )
        response = DummyResponse()

        with patch.object(
            self.main,
            "build_system_status_runtime_model_http_response",
            return_value=expected_payload,
        ) as mocked:
            status = await self.main.get_system_status(response)

        self.assertEqual(status.status, "running")
        self.assertEqual(status.tools_count, 2)
        self.assertEqual(status.active_sessions, 1)
        mocked.assert_called_once_with(
            response=response,
            runtime_state=self.main.runtime_state,
        )

    async def test_get_powerpaint_status_contract(self):
        expected_payload = self.main.PowerPaintStatus(
            url="http://localhost:7860",
            reachable=True,
            message="PowerPaint 服务可访问。",
        )
        response = DummyResponse()

        with patch.object(
            self.main,
            "build_powerpaint_status_model_http_response",
            return_value=expected_payload,
        ) as mocked:
            status = await self.main.get_powerpaint_status(response)

        self.assertEqual(status.url, "http://localhost:7860")
        self.assertEqual(status.reachable, True)
        self.assertEqual(status.message, "PowerPaint 服务可访问。")
        mocked.assert_called_once_with(response=response)

    async def test_read_root_delegates_to_spa_root_wrapper(self):
        sentinel = object()

        with patch.object(
            self.main,
            "serve_root_with_boundary",
            return_value=sentinel,
        ) as mocked:
            payload = await self.main.read_root()

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(static_path=self.main.static_path)

    async def test_get_tools_contract_and_cors(self):
        self.main.runtime_state.tool_manager = FakeToolManager()
        expected_tools = [
            self.main.ToolInfo(
                name="calculate",
                description="math calculator",
                parameters={"expression": {"type": "string"}},
            ),
            self.main.ToolInfo(
                name="factorial",
                description="factorial calculator",
                parameters={"n": {"type": "integer"}},
            ),
        ]
        response = DummyResponse()

        with patch.object(
            self.main,
            "build_tool_catalog_runtime_model_http_response",
            return_value=expected_tools,
        ) as mocked:
            tools = await self.main.get_tools(response)

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0].name, "calculate")
        self.assertEqual(tools[1].name, "factorial")
        mocked.assert_called_once_with(
            response=response,
            runtime_state=self.main.runtime_state,
        )

    async def test_get_skills_contract(self):
        self.main.runtime_state.tool_manager = FakeToolManager()
        expected_skills = [
            self.main.SkillInfo(
                id="travel_planner",
                name="综合旅行规划",
                description="规划旅行",
                workflow_steps=["识别需求"],
                allowed_mcp_servers=["baidu-map"],
                allowed_local_tools=["calculate"],
                answer_style="结论优先",
                fallback="说明限制",
                required_env=[],
                available=True,
                missing_mcp_servers=[],
                missing_local_tools=[],
                missing_env=[],
            )
        ]
        response = DummyResponse()

        with patch.object(
            self.main,
            "build_skill_catalog_runtime_model_http_response",
            return_value=expected_skills,
        ) as mocked:
            skills = await self.main.get_skills(response)

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].id, "travel_planner")
        mocked.assert_called_once_with(
            response=response,
            runtime_state=self.main.runtime_state,
        )

    async def test_get_mcp_servers_contract_and_cors(self):
        self.main.runtime_state.tool_manager = FakeToolManager()
        expected_payload = {
            "servers": [{"name": "mock-mcp", "disabled": False, "tools_count": 1}],
            "total_servers": 1,
            "active_servers": 1,
        }
        response = DummyResponse()

        with patch.object(
            self.main,
            "build_mcp_servers_runtime_state_http_response",
            return_value=expected_payload,
        ) as mocked:
            payload = await self.main.get_mcp_servers(response)

        mocked.assert_called_once_with(
            response=response,
            runtime_state=self.main.runtime_state,
        )
        self.assertEqual(payload, expected_payload)

    async def test_configure_system_contract_and_cors(self):
        self.main.runtime_state.controller = None
        expected_payload = {"status": "success", "message": "ok"}
        response = DummyResponse()
        config = self.main.ConfigRequest(
            api_key="k-1",
            model_name="m-1",
            base_url="http://localhost:9999/v1",
            max_tokens=512,
            temperature=0.3,
        )

        with patch.object(
            self.main,
            "configure_runtime_request_http_response",
            return_value=expected_payload,
        ) as mocked:
            payload = await self.main.configure_system(config, response)

        mocked.assert_called_once_with(
            response=response,
            config=config,
            runtime_state=self.main.runtime_state,
        )
        self.assertEqual(payload, expected_payload)

    async def test_options_handler_contract_and_cors(self):
        response = DummyResponse()
        expected_payload = {"message": "OK"}

        with patch.object(
            self.main,
            "build_options_route",
            return_value=expected_payload,
        ) as mocked:
            payload = await self.main.options_handler("api/chat", response)

        self.assertEqual(payload, expected_payload)
        mocked.assert_called_once_with(response=response)

    async def test_cleanup_session_delegates_to_route_wrapper(self):
        expected_payload = {"status": "success", "message": "ok"}

        with patch.object(
            self.main,
            "cleanup_session_runtime_route",
            return_value=expected_payload,
        ) as mocked:
            payload = await self.main.cleanup_session("session-1")

        mocked.assert_called_once_with(
            session_id="session-1",
            runtime_state=self.main.runtime_state,
        )
        self.assertEqual(payload, expected_payload)

    async def test_get_active_sessions_delegates_to_route_wrapper(self):
        expected_payload = {"active_sessions": ["session-1"], "count": 1}
        self.main.runtime_state.active_sessions["session-1"] = {"x": 1}

        with patch.object(
            self.main,
            "list_active_sessions_runtime_route",
            return_value=expected_payload,
        ) as mocked:
            payload = await self.main.get_active_sessions()

        self.assertEqual(payload, expected_payload)
        mocked.assert_called_once_with(
            runtime_state=self.main.runtime_state,
        )

    async def test_chat_endpoint_delegates_to_chat_service_wrapper(self):
        sentinel = {"status": "success", "result": {"ok": True}, "session_id": "s-1"}
        self.main.runtime_state.controller = object()
        self.main.runtime_state.tool_manager = FakeToolManager()
        request = self.main.ChatRequest(
            messages=[self.main.ChatMessage(role="user", content="hello")],
            use_deepthink=True,
            use_multi_agent=False,
            session_id="s-1",
        )

        with patch.object(
            self.main,
            "execute_chat_request_runtime_route",
            return_value=sentinel,
        ) as mocked:
            payload = await self.main.chat_endpoint(request)

        self.assertEqual(payload, sentinel)
        mocked.assert_called_once_with(
            request=request,
            runtime_state=self.main.runtime_state,
        )

    async def test_chat_stream_requires_controller(self):
        from fastapi import HTTPException

        self.main.runtime_state.controller = None
        request = self.main.ChatRequest(
            messages=[self.main.ChatMessage(role="user", content="hello")],
            use_deepthink=True,
            use_multi_agent=True,
        )

        with self.assertRaises(HTTPException) as ctx:
            await self.main.chat_stream(request)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "系统未配置，请先配置API密钥")

    async def test_chat_stream_delegates_to_response_builder(self):
        sentinel = object()
        self.main.runtime_state.controller = object()
        self.main.runtime_state.tool_manager = FakeToolManager()
        request = self.main.ChatRequest(
            messages=[self.main.ChatMessage(role="user", content="hello")],
            use_deepthink=False,
            use_multi_agent=True,
            selected_mcp_servers=["baidu-map"],
        )

        with patch.object(
            self.main,
            "build_chat_stream_request_runtime_route",
            return_value=sentinel,
        ) as mocked:
            payload = await self.main.chat_stream(request)

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(
            request=request,
            runtime_state=self.main.runtime_state,
        )

    async def test_sse_endpoint_delegates_to_response_builder(self):
        sentinel = object()

        with patch.object(
            self.main,
            "build_heartbeat_sse_route",
            return_value=sentinel,
        ) as mocked:
            payload = await self.main.sse_endpoint("session-xyz")

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(session_id="session-xyz")

    async def test_resume_planning_events_uses_last_event_id_cursor(self):
        class FakeRepository:
            @staticmethod
            def planning_run(device_id, run_id):
                return {"device_id": device_id, "run_id": run_id}

        class FakeManager:
            def __init__(self):
                self.calls = []

            async def stream(self, *, run_id, after_sequence=0):
                self.calls.append((run_id, after_sequence))
                yield 'data: {"type":"done"}\n\n'

        repository = FakeRepository()
        manager = FakeManager()
        self.main.runtime_state.trip_repository = repository
        self.main.runtime_state.planning_run_manager = manager
        request = self.main.Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/planning-runs/run-1/events",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
                "query_string": b"",
            }
        )
        identity = SimpleNamespace(device_id="device-1")

        with (
            patch.object(self.main, "resolve_anonymous_device", return_value=identity),
            patch.object(self.main, "set_anonymous_device_cookie"),
        ):
            response = await self.main.resume_planning_events(
                "run-1",
                request,
                last_sequence=4,
                last_event_id="run-1:7",
            )

        chunks = [chunk async for chunk in response.body_iterator]
        self.assertEqual(chunks, ['data: {"type":"done"}\n\n'])
        self.assertEqual(manager.calls, [("run-1", 7)])

    async def test_cancel_planning_run_delegates_with_owner(self):
        repository = object()
        manager = SimpleNamespace(cancel=AsyncMock(return_value={"cancelled": True, "run_id": "run-1"}))
        self.main.runtime_state.trip_repository = repository
        self.main.runtime_state.planning_run_manager = manager
        request = self.main.Request(
            {
                "type": "http",
                "method": "DELETE",
                "path": "/api/planning-runs/run-1",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
                "query_string": b"",
            }
        )
        identity = SimpleNamespace(device_id="device-1")

        with (
            patch.object(self.main, "resolve_anonymous_device", return_value=identity),
            patch.object(self.main, "set_anonymous_device_cookie"),
        ):
            response = await self.main.cancel_planning_run("run-1", request)

        manager.cancel.assert_awaited_once_with(device_id="device-1", run_id="run-1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"cancelled": true', response.body)

    async def test_download_file_delegates_to_file_service_wrapper(self):
        sentinel = object()

        with patch.object(
            self.main,
            "build_download_response_safe",
            return_value=sentinel,
        ) as mocked:
            payload = await self.main.download_file("session-1", "report.md")

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(
            session_id="session-1",
            filename="report.md",
        )

    async def test_initialize_system_delegates_to_lifecycle_wrapper(self):
        fake_tool_manager = object()
        fake_controller = object()

        with TemporaryDirectory() as directory, patch.object(
            self.main, "default_trip_database_path", return_value=Path(directory) / "trips.sqlite3",
        ), patch.object(
            self.main,
            "initialize_runtime_with_boundary",
            AsyncMock(return_value=(fake_tool_manager, fake_controller)),
        ) as mocked:
            await self.main.initialize_system()

        mocked.assert_awaited_once_with(
            baidu_request_dispatcher=self.main.runtime_state.baidu_request_dispatcher,
            provider_gateway=self.main.runtime_state.provider_gateway,
        )
        self.assertIs(self.main.runtime_state.tool_manager, fake_tool_manager)
        self.assertIs(self.main.runtime_state.controller, fake_controller)

    async def test_day_route_uses_application_baidu_dispatcher(self):
        request = self.main.DayRouteRequest(
            day=1,
            plan_version=1,
            scope="domestic",
            activities=[],
        )
        expected = {"status": "unavailable", "legs": []}

        with patch.object(
            self.main,
            "build_day_route",
            AsyncMock(return_value=expected),
        ) as mocked:
            result = await self.main.build_day_route_endpoint(request)

        self.assertEqual(result, expected)
        mocked.assert_awaited_once_with(
            day=1,
            plan_version=1,
            activities=[],
            scope="domestic",
            baidu_dispatcher=self.main.runtime_state.baidu_request_dispatcher,
            baidu_priority="candidate",
        )

    async def test_cleanup_system_delegates_to_lifecycle_wrapper(self):
        self.main.runtime_state.active_sessions["session-a"] = {"x": 1}

        with patch.object(
            self.main,
            "cleanup_runtime_with_boundary",
            AsyncMock(return_value=None),
        ) as mocked:
            await self.main.cleanup_system()

        mocked.assert_awaited_once_with(
            active_sessions=self.main.runtime_state.active_sessions,
            tool_manager=self.main.runtime_state.tool_manager,
        )

    async def test_serve_spa_delegates_to_spa_wrapper(self):
        sentinel = object()

        with patch.object(
            self.main,
            "serve_spa_with_boundary",
            return_value=sentinel,
        ) as mocked:
            payload = await self.main.serve_spa("trip/plan")

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(
            static_path=self.main.static_path,
            full_path="trip/plan",
        )


if __name__ == "__main__":
    unittest.main()
