import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import services.session_service as session_service

from services.session_service import (
    cleanup_session_state,
    cleanup_session_with_logging,
    cleanup_session_route,
    cleanup_session_runtime_route,
    list_active_sessions_route,
    list_active_sessions_runtime_route,
)


class FakeToolManager:
    def __init__(self):
        self.cleaned = []

    async def cleanup_session(self, session_id):
        self.cleaned.append(session_id)


class FakeLogger:
    def __init__(self):
        self.logs = []
        self.errors = []

    def info(self, message):
        self.logs.append(message)

    def error(self, message):
        self.errors.append(message)


class SessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_active_sessions_route_success(self):
        logger = FakeLogger()
        sessions = {"s1": {}, "s2": {}}

        payload = list_active_sessions_route(active_sessions=sessions, logger=logger)

        self.assertEqual(payload["count"], 2)
        self.assertCountEqual(payload["active_sessions"], ["s1", "s2"])
        self.assertEqual(logger.errors, [])

    async def test_list_active_sessions_route_wraps_exception_as_http_500(self):
        logger = FakeLogger()

        try:
            import fastapi
        except ImportError:
            self.skipTest("fastapi is not installed in current environment")

        class BrokenSessions(dict):
            def keys(self):
                raise RuntimeError("list-fail")

        with self.assertRaises(fastapi.HTTPException) as ctx:
            list_active_sessions_route(active_sessions=BrokenSessions(), logger=logger)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(str(ctx.exception.detail), "list-fail")
        self.assertEqual(len(logger.errors), 1)

    async def test_list_active_sessions_runtime_route_delegates_to_route_wrapper(self):
        runtime_state = SimpleNamespace(active_sessions={"s1": {}}, tool_manager=None)
        expected = {"active_sessions": ["s1"], "count": 1}

        with patch(
            "services.session_service.list_active_sessions_route",
            return_value=expected,
        ) as mocked:
            payload = list_active_sessions_runtime_route(runtime_state=runtime_state)

        self.assertEqual(payload, expected)
        mocked.assert_called_once_with(active_sessions=runtime_state.active_sessions, logger=session_service.logger)

    async def test_cleanup_session_state_removes_existing_session(self):
        sessions = {"s1": {"x": 1}}
        tool_manager = FakeToolManager()

        payload = await cleanup_session_state(
            session_id="s1",
            active_sessions=sessions,
            tool_manager=tool_manager,
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(sessions, {})
        self.assertEqual(tool_manager.cleaned, ["s1"])

    async def test_cleanup_session_with_logging_logs_only_when_session_exists(self):
        sessions = {"s2": {"x": 2}}
        tool_manager = FakeToolManager()
        logger = FakeLogger()

        await cleanup_session_with_logging(
            session_id="s2",
            active_sessions=sessions,
            tool_manager=tool_manager,
            logger=logger,
        )

        self.assertEqual(logger.logs, ["会话 s2 已清理"])

        await cleanup_session_with_logging(
            session_id="missing",
            active_sessions=sessions,
            tool_manager=tool_manager,
            logger=logger,
        )

        self.assertEqual(logger.logs, ["会话 s2 已清理"])

    async def test_cleanup_session_route_wraps_exception_as_http_500(self):
        logger = FakeLogger()

        try:
            import fastapi
        except ImportError:
            self.skipTest("fastapi is not installed in current environment")

        with patch(
            "services.session_service.cleanup_session_with_logging",
            side_effect=RuntimeError("cleanup-fail"),
        ):
            with self.assertRaises(fastapi.HTTPException) as ctx:
                await cleanup_session_route(
                    session_id="s3",
                    active_sessions={},
                    tool_manager=None,
                    logger=logger,
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(str(ctx.exception.detail), "cleanup-fail")
        self.assertEqual(len(logger.errors), 1)

    async def test_cleanup_session_runtime_route_delegates_to_route_wrapper(self):
        runtime_state = SimpleNamespace(active_sessions={"s1": {}}, tool_manager=FakeToolManager())
        expected = {"status": "success", "message": "ok"}

        with patch(
            "services.session_service.cleanup_session_route",
            AsyncMock(return_value=expected),
        ) as mocked:
            payload = await cleanup_session_runtime_route(
                session_id="s1",
                runtime_state=runtime_state,
            )

        self.assertEqual(payload, expected)
        mocked.assert_awaited_once_with(
            session_id="s1",
            active_sessions=runtime_state.active_sessions,
            tool_manager=runtime_state.tool_manager,
            logger=session_service.logger,
        )


if __name__ == "__main__":
    unittest.main()
