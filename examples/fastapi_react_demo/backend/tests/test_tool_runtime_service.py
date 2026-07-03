import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services import tool_runtime_service


class ToolRuntimeServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_build_mcp_registration_config_keeps_sse_url_and_resolves_env(self):
        server_config = SimpleNamespace(
            command=None,
            args=None,
            sse_url="http://127.0.0.1:34011/sse",
            env={"SERPER_API_KEY": "${SERPER_API_KEY}"},
        )

        with patch.dict("os.environ", {"SERPER_API_KEY": "secret-value"}):
            payload = tool_runtime_service._build_mcp_registration_config(server_config)

        self.assertEqual(payload["sse_url"], "http://127.0.0.1:34011/sse")
        self.assertEqual(payload["env"], {"SERPER_API_KEY": "secret-value"})

    async def test_auto_start_skips_when_sse_endpoint_is_already_ready(self):
        tool_manager = SimpleNamespace()
        server_config = SimpleNamespace(
            sse_url="http://127.0.0.1:34011/sse",
            auto_start=SimpleNamespace(command="python", args=[], env={}, timeout_seconds=1),
        )

        with patch(
            "services.tool_runtime_service._is_sse_endpoint_ready",
            AsyncMock(return_value=True),
        ), patch("services.tool_runtime_service._start_mcp_process") as start_mock:
            await tool_runtime_service._ensure_auto_started_sse_server(
                tool_manager,
                "serper_web_search",
                server_config,
            )

        start_mock.assert_not_called()
        self.assertFalse(hasattr(tool_manager, "_managed_mcp_processes"))

    async def test_auto_start_starts_process_and_waits_for_sse(self):
        tool_manager = SimpleNamespace()
        process = Mock()
        server_config = SimpleNamespace(
            sse_url="http://127.0.0.1:34011/sse",
            auto_start=SimpleNamespace(command="python", args=["server.py"], env={}, timeout_seconds=1),
        )

        with patch(
            "services.tool_runtime_service._is_sse_endpoint_ready",
            AsyncMock(return_value=False),
        ), patch(
            "services.tool_runtime_service._start_mcp_process",
            return_value=process,
        ) as start_mock, patch(
            "services.tool_runtime_service._wait_for_sse_endpoint",
            AsyncMock(return_value=True),
        ) as wait_mock:
            await tool_runtime_service._ensure_auto_started_sse_server(
                tool_manager,
                "serper_web_search",
                server_config,
            )

        start_mock.assert_called_once()
        wait_mock.assert_awaited_once_with("http://127.0.0.1:34011/sse", 1.0, process)
        self.assertEqual(tool_manager._managed_mcp_processes, [process])

    async def test_cleanup_managed_mcp_processes_terminates_running_processes(self):
        process = Mock()
        process.poll.side_effect = [None, None, 0, 0]
        tool_manager = SimpleNamespace(_managed_mcp_processes=[process])

        await tool_runtime_service.cleanup_managed_mcp_processes(tool_manager)

        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        self.assertEqual(tool_manager._managed_mcp_processes, [])


if __name__ == "__main__":
    unittest.main()
