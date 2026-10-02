import asyncio
import json
from typing import Any, AsyncGenerator, Optional

from agents.utils.logger import logger


def add_cors_headers(response: Any) -> Any:
    """Attach permissive CORS headers for compatibility with existing frontend behavior."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def build_options_ok_response(response: Any) -> dict:
    """Build standardized OPTIONS preflight response payload."""
    add_cors_headers(response)
    return {"message": "OK"}


def build_options_route(response: Any, logger: Any = logger) -> dict:
    """Build OPTIONS response with unified logging boundary."""
    try:
        return build_options_ok_response(response)
    except Exception as e:
        logger.error(f"OPTIONS预检处理失败: {e}")
        raise


def get_sse_headers() -> dict:
    """Shared headers for SSE responses."""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Cache-Control",
    }


async def generate_heartbeat_stream(session_id: str, logger: Any = logger) -> AsyncGenerator[str, None]:
    """Emit initial SSE connected event and heartbeat events."""
    try:
        yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id})}\n\n"
        while True:
            await asyncio.sleep(30)
            yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
    except Exception as e:
        logger.error(f"SSE连接错误: {str(e)}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


def build_heartbeat_sse_response(session_id: str, logger: Any = logger, sse_headers: Optional[dict] = None) -> Any:
    """Build SSE response for heartbeat endpoint."""
    from fastapi.responses import StreamingResponse

    effective_sse_headers = sse_headers if sse_headers is not None else get_sse_headers()

    return StreamingResponse(
        generate_heartbeat_stream(session_id=session_id, logger=logger),
        media_type="text/event-stream",
        headers=effective_sse_headers,
    )


def build_heartbeat_sse_route(session_id: str, logger: Any = logger) -> Any:
    """Build SSE route response with unified logging boundary."""
    try:
        return build_heartbeat_sse_response(
            session_id=session_id,
            logger=logger,
        )
    except Exception as e:
        logger.error(f"SSE路由响应构建失败: {e}")
        raise
