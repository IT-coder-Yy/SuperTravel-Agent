import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1]
for root in (BACKEND_ROOT, BACKEND_ROOT.parents[2]):
    if str(root) not in sys.path:
        sys.path.append(str(root))

from services.session_service import cleanup_session_runtime_route, list_active_sessions_runtime_route


def test_session_list():
    state = SimpleNamespace(active_sessions={"s1": {}, "s2": {}})
    assert list_active_sessions_runtime_route(state) == {"active_sessions": ["s1", "s2"], "count": 2}


def test_session_list_error():
    sessions = Mock()
    sessions.keys.side_effect = RuntimeError("list-fail")
    with pytest.raises(HTTPException) as caught:
        list_active_sessions_runtime_route(SimpleNamespace(active_sessions=sessions))
    assert caught.value.status_code == 500
    assert caught.value.detail == "list-fail"


@pytest.mark.parametrize("has_manager", [True, False])
def test_cleanup_is_idempotent_and_logs_only_existing_session(has_manager):
    manager = SimpleNamespace(cleanup_session=AsyncMock()) if has_manager else None
    state = SimpleNamespace(active_sessions={"s1": {}}, tool_manager=manager)
    logger = Mock()
    for _ in range(2):
        result = asyncio.run(cleanup_session_runtime_route("s1", state, logger=logger))
        assert result == {"status": "success", "message": "会话 s1 已清理"}
    assert state.active_sessions == {}
    logger.info.assert_called_once_with("会话 s1 已清理")
    if manager:
        manager.cleanup_session.assert_awaited_once_with("s1")


def test_cleanup_failure_preserves_session_for_retry():
    manager = SimpleNamespace(cleanup_session=AsyncMock(side_effect=RuntimeError("cleanup-fail")))
    state = SimpleNamespace(active_sessions={"s1": {"data": 1}}, tool_manager=manager)
    logger = Mock()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(cleanup_session_runtime_route("s1", state, logger=logger))
    assert caught.value.status_code == 500
    assert caught.value.detail == "cleanup-fail"
    assert state.active_sessions == {"s1": {"data": 1}}
    logger.info.assert_not_called()
    logger.error.assert_called_once()
