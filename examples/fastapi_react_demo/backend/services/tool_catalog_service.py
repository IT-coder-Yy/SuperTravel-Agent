from typing import Any, Dict, List

from agents.utils.logger import logger


def build_tool_catalog(tool_manager: Any) -> List[Dict[str, Any]]:
    """Build simplified tool catalog payload for API response."""
    if not tool_manager:
        return []

    tools = tool_manager.list_tools_simplified()
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool.get("parameters", {}),
        }
        for tool in tools
    ]


def build_tool_catalog_route(tool_manager: Any, logger: Any = logger) -> List[Dict[str, Any]]:
    """Build tool catalog with route-level error boundary semantics."""
    from fastapi import HTTPException

    try:
        return build_tool_catalog(tool_manager=tool_manager)
    except Exception as e:
        logger.error(f"获取工具列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def build_tool_catalog_http_response(response: Any, tool_manager: Any, logger: Any = logger) -> List[Dict[str, Any]]:
    """Build tool catalog payload and attach CORS headers for API response."""
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    return build_tool_catalog_route(tool_manager=tool_manager, logger=logger)


def build_tool_catalog_model_http_response(response: Any, tool_manager: Any, logger: Any = logger) -> List[Any]:
    """Build tool catalog as ToolInfo model list for route handlers."""
    from schemas.api_models import ToolInfo

    payload = build_tool_catalog_http_response(response=response, tool_manager=tool_manager, logger=logger)
    return [ToolInfo(**tool) for tool in payload]


def build_tool_catalog_runtime_model_http_response(response: Any, runtime_state: Any) -> List[Any]:
    """Build tool catalog model list directly from runtime state container."""
    return build_tool_catalog_model_http_response(
        response=response,
        tool_manager=runtime_state.tool_manager,
    )
