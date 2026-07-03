import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.lifecycle_service import cleanup_runtime, initialize_runtime_with_boundary, cleanup_runtime_with_boundary


class FakeToolManager:
    def __init__(self):
        self.cleaned = []

    async def cleanup_session(self, session_id):
        self.cleaned.append(session_id)


class FakeLogger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def info(self, message):
        self.infos.append(message)

    def error(self, message):
        self.errors.append(message)


class LifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_runtime_clears_sessions_and_calls_tool_cleanup(self):
        active_sessions = {
            "session-1": {"foo": "bar"},
            "session-2": {"foo": "baz"},
        }
        tool_manager = FakeToolManager()

        await cleanup_runtime(active_sessions=active_sessions, tool_manager=tool_manager)

        self.assertEqual(active_sessions, {})
        self.assertCountEqual(tool_manager.cleaned, ["session-1", "session-2"])

    async def test_cleanup_runtime_handles_missing_tool_manager(self):
        active_sessions = {"session-3": {"x": 1}}

        await cleanup_runtime(active_sessions=active_sessions, tool_manager=None)

        self.assertEqual(active_sessions, {})

    async def test_initialize_runtime_with_boundary_returns_runtime(self):
        fake_logger = FakeLogger()
        fake_tool_manager = object()
        fake_controller = object()

        with patch(
            "services.lifecycle_service.bootstrap_runtime",
            AsyncMock(return_value=(fake_tool_manager, fake_controller, "model-x")),
        ):
            tool_manager, controller = await initialize_runtime_with_boundary(fake_logger)

        self.assertIs(tool_manager, fake_tool_manager)
        self.assertIs(controller, fake_controller)
        self.assertEqual(fake_logger.errors, [])

    async def test_initialize_runtime_with_boundary_handles_error(self):
        fake_logger = FakeLogger()

        with patch(
            "services.lifecycle_service.bootstrap_runtime",
            AsyncMock(side_effect=RuntimeError("init boom")),
        ):
            with patch("builtins.print") as mocked_print:
                tool_manager, controller = await initialize_runtime_with_boundary(fake_logger)

        self.assertIsNone(tool_manager)
        self.assertIsNone(controller)
        self.assertEqual(len(fake_logger.errors), 1)
        self.assertTrue(mocked_print.called)

    async def test_cleanup_runtime_with_boundary_success_logs_info(self):
        fake_logger = FakeLogger()
        active_sessions = {"session-1": {"x": 1}}
        tool_manager = FakeToolManager()

        await cleanup_runtime_with_boundary(active_sessions, tool_manager, fake_logger)

        self.assertEqual(active_sessions, {})
        self.assertEqual(fake_logger.infos, ["系统资源清理完成"])
        self.assertEqual(fake_logger.errors, [])

    async def test_cleanup_runtime_with_boundary_handles_error(self):
        fake_logger = FakeLogger()

        with patch(
            "services.lifecycle_service.cleanup_runtime",
            AsyncMock(side_effect=RuntimeError("cleanup boom")),
        ):
            await cleanup_runtime_with_boundary({}, None, fake_logger)

        self.assertEqual(fake_logger.infos, [])
        self.assertEqual(len(fake_logger.errors), 1)


if __name__ == "__main__":
    unittest.main()
