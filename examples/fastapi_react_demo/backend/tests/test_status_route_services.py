import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class DummyResponse:
    def __init__(self):
        self.headers = {}


class StatusRouteServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")

        cls.system_service = importlib.import_module("services.system_service")
        cls.tool_catalog_service = importlib.import_module("services.tool_catalog_service")
        cls.mcp_service = importlib.import_module("services.mcp_service")
        cls.powerpaint_status_service = importlib.import_module("services.powerpaint_status_service")
        cls.HTTPException = importlib.import_module("fastapi").HTTPException

    def test_build_system_status_route_success(self):
        logger = FakeLogger()
        expected = {
            "status": "running",
            "agents_count": 7,
            "tools_count": 1,
            "active_sessions": 2,
            "version": "0.8",
        }

        with patch("services.system_service.build_system_status", return_value=expected):
            payload = self.system_service.build_system_status_route(
                tool_manager=object(),
                active_sessions={"a": {}, "b": {}},
                logger=logger,
            )

        self.assertEqual(payload, expected)
        self.assertEqual(logger.errors, [])

    def test_build_system_status_route_wraps_exception(self):
        logger = FakeLogger()

        with patch("services.system_service.build_system_status", side_effect=RuntimeError("status-fail")):
            with self.assertRaises(self.HTTPException) as ctx:
                self.system_service.build_system_status_route(
                    tool_manager=object(),
                    active_sessions={},
                    logger=logger,
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("status-fail", str(ctx.exception.detail))
        self.assertEqual(len(logger.errors), 1)

    def test_build_system_status_http_response_adds_cors_and_delegates(self):
        response = DummyResponse()
        expected = {
            "status": "running",
            "agents_count": 7,
            "tools_count": 1,
            "active_sessions": 0,
            "version": "0.8",
        }

        with patch("services.system_service.build_system_status_route", return_value=expected) as mocked:
            payload = self.system_service.build_system_status_http_response(
                response=response,
                tool_manager=object(),
                active_sessions={},
            )

        self.assertEqual(payload, expected)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        mocked.assert_called_once_with(
            tool_manager=unittest.mock.ANY,
            active_sessions={},
            logger=self.system_service.logger,
            agents_count=7,
            version="0.8",
        )

    def test_build_system_status_model_http_response_returns_system_status_model(self):
        response = DummyResponse()
        tool_manager = object()
        active_sessions = {"s": {}}
        expected = {
            "status": "running",
            "agents_count": 7,
            "tools_count": 2,
            "active_sessions": 1,
            "version": "0.8",
        }

        with patch("services.system_service.build_system_status_http_response", return_value=expected) as mocked:
            model = self.system_service.build_system_status_model_http_response(
                response=response,
                tool_manager=tool_manager,
                active_sessions=active_sessions,
            )

        self.assertEqual(model.status, "running")
        self.assertEqual(model.tools_count, 2)
        self.assertEqual(model.active_sessions, 1)
        mocked.assert_called_once_with(
            response=response,
            tool_manager=tool_manager,
            active_sessions=active_sessions,
            logger=self.system_service.logger,
            agents_count=7,
            version="0.8",
        )

    def test_build_system_status_runtime_model_http_response_delegates(self):
        response = DummyResponse()
        runtime_state = SimpleNamespace(tool_manager=object(), active_sessions={"s": {}})
        expected_model = SimpleNamespace(status="running")

        with patch(
            "services.system_service.build_system_status_model_http_response",
            return_value=expected_model,
        ) as mocked:
            payload = self.system_service.build_system_status_runtime_model_http_response(
                response=response,
                runtime_state=runtime_state,
            )

        self.assertIs(payload, expected_model)
        mocked.assert_called_once_with(
            response=response,
            tool_manager=runtime_state.tool_manager,
            active_sessions=runtime_state.active_sessions,
        )

    def test_configure_runtime_route_wraps_exception(self):
        logger = FakeLogger()

        with patch("services.system_service.configure_runtime_with_response", side_effect=RuntimeError("cfg-fail")):
            with self.assertRaises(self.HTTPException) as ctx:
                self.system_service.configure_runtime_route(
                    api_key="k",
                    model_name="m",
                    base_url="u",
                    max_tokens=256,
                    temperature=0.1,
                    logger=logger,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("cfg-fail", str(ctx.exception.detail))
        self.assertEqual(len(logger.errors), 1)

    def test_configure_runtime_http_response_adds_cors_and_delegates(self):
        response = DummyResponse()
        fake_controller = object()
        expected_payload = {"status": "success", "message": "ok"}

        with patch(
            "services.system_service.configure_runtime_route",
            return_value=(fake_controller, expected_payload),
        ) as mocked:
            controller, payload = self.system_service.configure_runtime_http_response(
                response=response,
                api_key="k",
                model_name="m",
                base_url="u",
                max_tokens=256,
                temperature=0.1,
            )

        self.assertIs(controller, fake_controller)
        self.assertEqual(payload, expected_payload)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        mocked.assert_called_once_with(
            api_key="k",
            model_name="m",
            base_url="u",
            max_tokens=256,
            temperature=0.1,
            logger=self.system_service.logger,
        )

    def test_configure_runtime_request_http_response_updates_runtime_state(self):
        response = DummyResponse()
        fake_controller = object()
        expected_payload = {"status": "success", "message": "ok"}
        runtime_state = SimpleNamespace(controller=None)
        config = SimpleNamespace(
            api_key="k",
            model_name="m",
            base_url="u",
            max_tokens=256,
            temperature=0.1,
        )

        with patch(
            "services.system_service.configure_runtime_http_response",
            return_value=(fake_controller, expected_payload),
        ) as mocked:
            payload = self.system_service.configure_runtime_request_http_response(
                response=response,
                config=config,
                runtime_state=runtime_state,
            )

        self.assertEqual(payload, expected_payload)
        self.assertIs(runtime_state.controller, fake_controller)
        mocked.assert_called_once_with(
            response=response,
            api_key="k",
            model_name="m",
            base_url="u",
            max_tokens=256,
            temperature=0.1,
        )

    def test_build_tool_catalog_route_wraps_exception(self):
        logger = FakeLogger()

        with patch("services.tool_catalog_service.build_tool_catalog", side_effect=RuntimeError("tools-fail")):
            with self.assertRaises(self.HTTPException) as ctx:
                self.tool_catalog_service.build_tool_catalog_route(tool_manager=object(), logger=logger)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("tools-fail", str(ctx.exception.detail))
        self.assertEqual(len(logger.errors), 1)

    def test_build_tool_catalog_http_response_adds_cors_and_delegates(self):
        response = DummyResponse()
        expected = [{"name": "calculate", "description": "d", "parameters": {}}]

        with patch("services.tool_catalog_service.build_tool_catalog_route", return_value=expected) as mocked:
            payload = self.tool_catalog_service.build_tool_catalog_http_response(
                response=response,
                tool_manager=object(),
            )

        self.assertEqual(payload, expected)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        mocked.assert_called_once_with(tool_manager=unittest.mock.ANY, logger=self.tool_catalog_service.logger)

    def test_build_tool_catalog_model_http_response_returns_tool_models(self):
        response = DummyResponse()
        tool_manager = object()
        expected = [{"name": "calculate", "description": "d", "parameters": {}}]

        with patch("services.tool_catalog_service.build_tool_catalog_http_response", return_value=expected) as mocked:
            models = self.tool_catalog_service.build_tool_catalog_model_http_response(
                response=response,
                tool_manager=tool_manager,
            )

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].name, "calculate")
        self.assertEqual(models[0].description, "d")
        mocked.assert_called_once_with(
            response=response,
            tool_manager=tool_manager,
            logger=self.tool_catalog_service.logger,
        )

    def test_build_tool_catalog_runtime_model_http_response_delegates(self):
        response = DummyResponse()
        runtime_state = SimpleNamespace(tool_manager=object())
        expected = [SimpleNamespace(name="calculate")]

        with patch(
            "services.tool_catalog_service.build_tool_catalog_model_http_response",
            return_value=expected,
        ) as mocked:
            payload = self.tool_catalog_service.build_tool_catalog_runtime_model_http_response(
                response=response,
                runtime_state=runtime_state,
            )

        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(
            response=response,
            tool_manager=runtime_state.tool_manager,
        )

    def test_build_mcp_runtime_status_safe_wraps_exception(self):
        logger = FakeLogger()

        with patch("services.mcp_service.build_mcp_servers_runtime_status", side_effect=RuntimeError("mcp-fail")):
            with self.assertRaises(self.HTTPException) as ctx:
                self.mcp_service.build_mcp_servers_runtime_status_safe(tool_manager=object(), logger=logger)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("获取MCP服务器状态失败: mcp-fail", str(ctx.exception.detail))
        self.assertEqual(len(logger.errors), 1)

    def test_build_mcp_runtime_status_http_response_adds_cors_and_delegates(self):
        response = DummyResponse()
        expected = {"servers": [], "total_servers": 0, "active_servers": 0}

        with patch("services.mcp_service.build_mcp_servers_runtime_status_safe", return_value=expected) as mocked:
            payload = self.mcp_service.build_mcp_servers_runtime_status_http_response(
                response=response,
                tool_manager=object(),
            )

        self.assertEqual(payload, expected)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        mocked.assert_called_once_with(tool_manager=unittest.mock.ANY, logger=self.mcp_service.logger)

    def test_build_mcp_runtime_state_http_response_delegates(self):
        response = DummyResponse()
        runtime_state = SimpleNamespace(tool_manager=object())
        expected = {"servers": [], "total_servers": 0, "active_servers": 0}

        with patch(
            "services.mcp_service.build_mcp_servers_runtime_status_http_response",
            return_value=expected,
        ) as mocked:
            payload = self.mcp_service.build_mcp_servers_runtime_state_http_response(
                response=response,
                runtime_state=runtime_state,
            )

        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(
            response=response,
            tool_manager=runtime_state.tool_manager,
        )

    def test_build_powerpaint_status_uses_default_url_and_success_message(self):
        payload = self.powerpaint_status_service.build_powerpaint_status(
            checker=lambda url: True,
        )

        self.assertEqual(payload["url"], "http://localhost:7860")
        self.assertEqual(payload["reachable"], True)
        self.assertEqual(payload["message"], "PowerPaint 服务可访问。")

    def test_build_powerpaint_status_uses_environment_url_and_unavailable_message(self):
        with patch.dict("os.environ", {"POWERPAINT_URL": "http://127.0.0.1:9000"}, clear=False):
            payload = self.powerpaint_status_service.build_powerpaint_status(
                checker=lambda url: False,
            )

        self.assertEqual(payload["url"], "http://127.0.0.1:9000")
        self.assertEqual(payload["reachable"], False)
        self.assertEqual(payload["message"], "PowerPaint 服务暂不可访问，请确认服务已启动并检查地址配置。")

    def test_build_powerpaint_status_http_response_adds_cors_and_delegates(self):
        response = DummyResponse()
        expected = {
            "url": "http://localhost:7860",
            "reachable": True,
            "message": "PowerPaint 服务可访问。",
        }

        with patch(
            "services.powerpaint_status_service.build_powerpaint_status_route",
            return_value=expected,
        ) as mocked:
            payload = self.powerpaint_status_service.build_powerpaint_status_http_response(
                response=response,
            )

        self.assertEqual(payload, expected)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        mocked.assert_called_once_with(
            logger=self.powerpaint_status_service.logger,
            checker=self.powerpaint_status_service.is_powerpaint_reachable,
        )

    def test_build_powerpaint_status_model_http_response_returns_model(self):
        response = DummyResponse()
        expected = {
            "url": "http://localhost:7860",
            "reachable": True,
            "message": "PowerPaint 服务可访问。",
        }

        with patch(
            "services.powerpaint_status_service.build_powerpaint_status_http_response",
            return_value=expected,
        ) as mocked:
            model = self.powerpaint_status_service.build_powerpaint_status_model_http_response(
                response=response,
            )

        self.assertEqual(model.url, "http://localhost:7860")
        self.assertEqual(model.reachable, True)
        self.assertEqual(model.message, "PowerPaint 服务可访问。")
        mocked.assert_called_once_with(response=response)


if __name__ == "__main__":
    unittest.main()
