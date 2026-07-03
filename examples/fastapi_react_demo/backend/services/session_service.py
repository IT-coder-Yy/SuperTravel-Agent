from typing import Any, Dict

from agents.utils.logger import logger


def list_active_sessions(active_sessions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build active session payload for API response."""
    return {
        "active_sessions": list(active_sessions.keys()),
        "count": len(active_sessions),
    }


def list_active_sessions_route(
    active_sessions: Dict[str, Dict[str, Any]],
    logger: Any = logger,
) -> Dict[str, Any]:
    """List active sessions while preserving route-level error semantics."""
    try:
        return list_active_sessions(active_sessions)
    except Exception as e:
        from fastapi import HTTPException

        logger.error(f"获取活跃会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def cleanup_session_state(
    session_id: str,
    active_sessions: Dict[str, Dict[str, Any]],
    tool_manager: Any,
) -> Dict[str, str]:
    """Clean runtime session resources and remove session from registry."""
    if session_id in active_sessions:
        if tool_manager:
            await tool_manager.cleanup_session(session_id)
        del active_sessions[session_id]

    return {
        "status": "success",
        "message": f"会话 {session_id} 已清理",
    }


async def cleanup_session_with_logging(
    session_id: str,
    active_sessions: Dict[str, Dict[str, Any]],
    tool_manager: Any,
    logger: Any = logger,
) -> Dict[str, str]:
    """Cleanup session resources and keep existing logging behavior."""
    existed = session_id in active_sessions
    result = await cleanup_session_state(
        session_id=session_id,
        active_sessions=active_sessions,
        tool_manager=tool_manager,
    )
    if existed:
        logger.info(f"会话 {session_id} 已清理")
    return result


async def cleanup_session_route(
    session_id: str,
    active_sessions: Dict[str, Dict[str, Any]],
    tool_manager: Any,
    logger: Any = logger,
) -> Dict[str, str]:
    """Cleanup session for route usage while preserving existing error semantics."""
    from fastapi import HTTPException

    try:
        return await cleanup_session_with_logging(
            session_id=session_id,
            active_sessions=active_sessions,
            tool_manager=tool_manager,
            logger=logger,
        )
    except Exception as e:
        logger.error(f"清理会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def list_active_sessions_runtime_route(
    runtime_state: Any,
    logger: Any = logger,
) -> Dict[str, Any]:
    """List active sessions directly from runtime state container."""
    return list_active_sessions_route(
        active_sessions=runtime_state.active_sessions,
        logger=logger,
    )


async def cleanup_session_runtime_route(
    session_id: str,
    runtime_state: Any,
    logger: Any = logger,
) -> Dict[str, str]:
    """Cleanup session directly from runtime state container."""
    return await cleanup_session_route(
        session_id=session_id,
        active_sessions=runtime_state.active_sessions,
        tool_manager=runtime_state.tool_manager,
        logger=logger,
    )
