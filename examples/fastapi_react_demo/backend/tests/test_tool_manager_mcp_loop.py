import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp import StdioServerParameters

PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.tool.tool_base import McpToolSpec
from agents.tool.tool_manager import ToolManager


class ToolManagerMcpLoopTests(unittest.TestCase):
    def test_mcp_tool_runs_without_current_event_loop(self):
        manager = ToolManager(is_auto_discover=False)
        manager.tools["mock-mcp"] = McpToolSpec(
            name="mock-mcp",
            description="",
            func=lambda: None,
            parameters={},
            required=[],
            server_name="mock-server",
            server_params=StdioServerParameters(command="noop"),
        )

        def fake_run_mcp_tool(tool, timeout_seconds, **kwargs):
            return {"content": [{"text": "ok"}]}

        manager._execute_stdio_mcp_tool_sync = fake_run_mcp_tool

        result = manager.run_tool("mock-mcp", messages=[], session_id="s-1")

        self.assertEqual(json.loads(result), {"content": "ok"})

    def test_sync_stdio_call_has_a_hard_timeout(self):
        manager = ToolManager(is_auto_discover=False)
        tool = McpToolSpec(
            name="stuck-mcp",
            description="",
            func=lambda: None,
            parameters={},
            required=[],
            server_name="mock-server",
            server_params=StdioServerParameters(command="noop"),
        )

        async def stuck_call(tool, **kwargs):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(60)

        manager._execute_stdio_mcp_tool_on_runtime = stuck_call
        started = time.monotonic()
        with self.assertRaises(asyncio.TimeoutError):
            manager._execute_stdio_mcp_tool_sync(tool, timeout_seconds=0.05)

        self.assertLess(time.monotonic() - started, 0.5)


class ToolManagerMcpStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_stdio_startup_does_not_leave_an_orphan_connection_task(self):
        manager = ToolManager(is_auto_discover=False)
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def fake_server_task(server_name, server_params, ready, shutdown_event, connection):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        manager._persistent_stdio_server_task = fake_server_task
        registration = asyncio.create_task(manager._connect_persistent_stdio_server(
            "orphan-test",
            StdioServerParameters(command="noop"),
        ))
        await started.wait()

        registration.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await registration

        self.assertTrue(stopped.is_set())
        self.assertNotIn("orphan-test", manager._persistent_stdio_servers)

    async def test_mcp_result_redacts_key_embedded_in_provider_error_url(self):
        manager = ToolManager(is_auto_discover=False)
        tool = McpToolSpec(
            name="maps_text_search",
            description="",
            func=lambda: None,
            parameters={},
            required=[],
            server_name="amap-maps",
            server_params=StdioServerParameters(command="noop"),
        )
        secret = "amap-secret-value"
        manager._execute_stdio_mcp_tool = AsyncMock(return_value={
            "isError": True,
            "content": [{
                "type": "text",
                "text": f"request failed: https://restapi.amap.com/v5/place/text?key={secret}&keywords=西湖",
            }],
        })

        result = await manager._run_mcp_tool_async(tool, "redaction-test")

        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        self.assertIn("[REDACTED]", json.dumps(result, ensure_ascii=False))

    async def test_stdio_startup_has_a_hard_timeout(self):
        manager = ToolManager(is_auto_discover=False)

        async def stuck_registration(coroutine):
            coroutine.close()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(60)

        manager._run_on_mcp_runtime_loop = stuck_registration
        started = time.monotonic()
        with patch.dict(os.environ, {"MCP_SERVER_STARTUP_TIMEOUT_SECONDS": "1"}):
            connected = await manager._register_mcp_tools_stdio(
                "stuck-server",
                StdioServerParameters(command="noop"),
            )

        self.assertFalse(connected)
        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == "__main__":
    unittest.main()
