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

from services import http_response_service

from services.http_response_service import add_cors_headers, get_sse_headers, build_options_route
from services.http_response_service import build_heartbeat_sse_response, build_heartbeat_sse_route
from services.mcp_service import build_mcp_servers_response, build_mcp_servers_runtime_status
from services.tool_catalog_service import build_tool_catalog


class DummyResponse:
    def __init__(self):
        self.headers = {}


class FakeToolCatalogManager:
    def list_tools_simplified(self):
        return [
            {
                "name": "calculate",
                "description": "math calculator",
                "parameters": {"expression": {"type": "string"}},
            },
            {
                "name": "factorial",
                "description": "compute factorial",
            },
        ]


class FakeMcpToolManager:
    def list_tools(self):
        return [
            {"name": "map_search"},
            {"name": "map_route"},
            {"name": "train_query"},
            {"name": "12306_ticket"},
            {"name": "other_tool"},
        ]


def build_fake_app_config():
    return SimpleNamespace(
        mcp=SimpleNamespace(
            servers={
                "baidu-map": SimpleNamespace(
                    disabled=False,
                    description="map service",
                    sse_url=None,
                    command="python",
                    args=["server.py"],
                ),
                "12306-mcp": SimpleNamespace(
                    disabled=True,
                    description="train service",
                    sse_url="http://127.0.0.1:8123/sse",
                    command=None,
                    args=None,
                ),
            }
        )
    )


class ToolCatalogServiceTests(unittest.TestCase):
    def test_build_tool_catalog_empty_when_manager_missing(self):
        self.assertEqual(build_tool_catalog(None), [])

    def test_build_tool_catalog_maps_fields(self):
        payload = build_tool_catalog(FakeToolCatalogManager())

        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["name"], "calculate")
        self.assertEqual(payload[0]["description"], "math calculator")
        self.assertIn("parameters", payload[0])
        self.assertEqual(payload[1]["name"], "factorial")
        self.assertEqual(payload[1]["parameters"], {})


class McpServiceTests(unittest.TestCase):
    def test_build_mcp_servers_response_counts_tools(self):
        app_config = build_fake_app_config()
        payload = build_mcp_servers_response(
            app_config=app_config,
            tool_manager=FakeMcpToolManager(),
        )

        self.assertEqual(payload["total_servers"], 2)
        self.assertEqual(payload["active_servers"], 1)

        servers = {item["name"]: item for item in payload["servers"]}
        self.assertEqual(servers["baidu-map"]["tools_count"], 2)
        self.assertEqual(servers["12306-mcp"]["tools_count"], 2)
        self.assertEqual(servers["baidu-map"]["type"], "stdio")
        self.assertEqual(servers["12306-mcp"]["type"], "sse")

    def test_build_mcp_servers_runtime_status_uses_current_config(self):
        app_config = build_fake_app_config()
        with patch("services.mcp_service.get_app_config", return_value=app_config):
            payload = build_mcp_servers_runtime_status(tool_manager=FakeMcpToolManager())

        self.assertEqual(payload["total_servers"], 2)
        self.assertEqual(payload["active_servers"], 1)


class HttpResponseServiceTests(unittest.TestCase):
    def test_add_cors_headers_sets_expected_fields(self):
        response = DummyResponse()

        add_cors_headers(response)

        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, POST, PUT, DELETE, OPTIONS")
        self.assertEqual(response.headers["Access-Control-Allow-Headers"], "*")
        self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")

    def test_get_sse_headers_contains_contract_headers(self):
        headers = get_sse_headers()

        self.assertEqual(headers["Content-Type"], "text/event-stream")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["Connection"], "keep-alive")

    def test_build_options_route_returns_ok_payload_and_sets_cors(self):
        response = DummyResponse()

        payload = build_options_route(response=response)

        self.assertEqual(payload, {"message": "OK"})
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")

    def test_build_options_route_logs_and_reraises_on_unexpected_error(self):
        response = DummyResponse()
        logs = []
        logger = SimpleNamespace(error=lambda message: logs.append(message))

        with patch("services.http_response_service.build_options_ok_response", side_effect=RuntimeError("oops")):
            with self.assertRaises(RuntimeError):
                build_options_route(response=response, logger=logger)

        self.assertEqual(len(logs), 1)
        self.assertIn("OPTIONS预检处理失败", logs[0])

    def test_build_heartbeat_sse_response_uses_default_headers_when_missing(self):
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("fastapi is not installed in current environment")

        logger = SimpleNamespace(error=lambda _: None)
        sentinel = object()

        with patch("services.http_response_service.get_sse_headers", return_value={"x": "1"}) as headers_mock:
            with patch("fastapi.responses.StreamingResponse", return_value=sentinel) as response_mock:
                payload = build_heartbeat_sse_response(session_id="session-1", logger=logger)

        self.assertIs(payload, sentinel)
        headers_mock.assert_called_once_with()
        response_mock.assert_called_once()
        self.assertEqual(response_mock.call_args.kwargs["media_type"], "text/event-stream")
        self.assertEqual(response_mock.call_args.kwargs["headers"], {"x": "1"})

    def test_build_heartbeat_sse_route_delegates_to_response_builder(self):
        sentinel = object()

        with patch(
            "services.http_response_service.build_heartbeat_sse_response",
            return_value=sentinel,
        ) as mocked:
            payload = build_heartbeat_sse_route(session_id="session-1")

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(
            session_id="session-1",
            logger=http_response_service.logger,
        )

    def test_build_heartbeat_sse_route_logs_and_reraises_on_unexpected_error(self):
        logs = []
        logger = SimpleNamespace(error=lambda message: logs.append(message))

        with patch(
            "services.http_response_service.build_heartbeat_sse_response",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                build_heartbeat_sse_route(session_id="session-1", logger=logger)

        self.assertEqual(len(logs), 1)
        self.assertIn("SSE路由响应构建失败", logs[0])


if __name__ == "__main__":
    unittest.main()
