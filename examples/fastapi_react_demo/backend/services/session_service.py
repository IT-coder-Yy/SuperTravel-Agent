from typing import Any, Dict

from fastapi import HTTPException
from agents.utils.logger import logger


def list_active_sessions_runtime_route(runtime_state: Any, logger: Any = logger) -> Dict[str, Any]:
    """返回当前会话列表，并保留接口的错误语义。"""
    try:
        sessions = runtime_state.active_sessions
        return {"active_sessions": list(sessions.keys()), "count": len(sessions)}
    except Exception as exc:
        logger.error(f"获取活跃会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def cleanup_session_runtime_route(
    session_id: str, runtime_state: Any, logger: Any = logger,
) -> Dict[str, str]:
    """清理成功后移除会话；不存在的会话仍按幂等成功处理。"""
    try:
        if session_id in runtime_state.active_sessions:
            if runtime_state.tool_manager:
                await runtime_state.tool_manager.cleanup_session(session_id)
            del runtime_state.active_sessions[session_id]
            logger.info(f"会话 {session_id} 已清理")
        return {"status": "success", "message": f"会话 {session_id} 已清理"}
    except Exception as exc:
        logger.error(f"清理会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
