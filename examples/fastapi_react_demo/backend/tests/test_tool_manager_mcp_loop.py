import json
import sys
import unittest
from pathlib import Path

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

        async def fake_run_mcp_tool(tool, session_id=None, **kwargs):
            return {"content": [{"text": "ok"}]}

        manager._run_mcp_tool_async = fake_run_mcp_tool

        result = manager.run_tool("mock-mcp", messages=[], session_id="s-1")

        self.assertEqual(json.loads(result), {"content": "ok"})


if __name__ == "__main__":
    unittest.main()
