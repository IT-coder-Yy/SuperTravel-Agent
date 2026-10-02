import os
import re
from typing import Any, Dict, List

from agents.tool.tool_base import McpToolSpec
from agents.utils.logger import logger

from config_loader import get_app_config


ENV_REF_REGEX = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _build_env_status(raw_env: Any) -> Dict[str, bool]:
    if not isinstance(raw_env, dict):
        return {}

    status: Dict[str, bool] = {}
    for key, raw_value in raw_env.items():
        env_key = str(key or "").strip()
        if not env_key:
            continue
        text = str(raw_value or "").strip()
        match = ENV_REF_REGEX.match(text)
        if match:
            status[env_key] = bool(os.getenv(match.group(1), "").strip())
        else:
            status[env_key] = bool(text)
    return status


def build_mcp_servers_response(app_config: Any, tool_manager: Any) -> Dict[str, Any]:
    """Build MCP server status payload used by API routes."""
    mcp_servers: List[Dict[str, Any]] = []
    mcp_tool_counts: Dict[str, int] = {}

    # Count MCP tools by their real server_name to avoid hardcoded name heuristics.
    if tool_manager and hasattr(tool_manager, "tools"):
        for tool in tool_manager.tools.values():
            if isinstance(tool, McpToolSpec):
                mcp_tool_counts[tool.server_name] = mcp_tool_counts.get(tool.server_name, 0) + 1
    elif tool_manager and hasattr(tool_manager, "list_tools"):
        mcp_tool_counts = _count_mcp_tools_from_list(tool_manager)

    if app_config.mcp and app_config.mcp.servers:
        for server_name, server_config in app_config.mcp.servers.items():
            server_info: Dict[str, Any] = {
                "name": server_name,
                "disabled": server_config.disabled,
                "description": server_config.description,
                "type": "sse" if server_config.sse_url else "stdio",
                "config": {},
                "tools_count": 0,
                "status": "disabled" if server_config.disabled else "unknown",
            }

            if server_config.command:
                server_info["config"]["command"] = server_config.command
                server_info["config"]["args"] = server_config.args
            elif server_config.sse_url:
                server_info["config"]["sse_url"] = server_config.sse_url
            env_status = _build_env_status(getattr(server_config, "env", None))
            if env_status:
                server_info["config"]["env_status"] = env_status

            if tool_manager:
                server_info["tools_count"] = mcp_tool_counts.get(server_name, 0)

                if server_config.disabled:
                    server_info["status"] = "disabled"
                else:
                    server_info["status"] = "connected" if server_info["tools_count"] > 0 else "disconnected"
            elif not server_config.disabled:
                server_info["status"] = "uninitialized"

            mcp_servers.append(server_info)

    return {
        "servers": mcp_servers,
        "total_servers": len(mcp_servers),
        "active_servers": len([s for s in mcp_servers if not s["disabled"]]),
    }


def _count_mcp_tools_from_list(tool_manager: Any) -> Dict[str, int]:
    try:
        tools = tool_manager.list_tools()
    except Exception:
        return {}

    counts: Dict[str, int] = {}
    for tool in tools or []:
        name = ""
        if isinstance(tool, dict):
            name = str(tool.get("name") or "").lower()
        else:
            name = str(getattr(tool, "name", "") or "").lower()

        server_name = _infer_mcp_server_name(name)
        if server_name:
            counts[server_name] = counts.get(server_name, 0) + 1
    return counts


def _infer_mcp_server_name(tool_name: str) -> str:
    if not tool_name:
        return ""
    if "12306" in tool_name or "train" in tool_name or "ticket" in tool_name:
        return "12306-mcp"
    if "baidu" in tool_name or "map" in tool_name or "route" in tool_name:
        return "baidu-map"
    if "xhs" in tool_name or "xiaohongshu" in tool_name:
        return "xhs-mcp"
    if "unsplash" in tool_name:
        return "unsplash-mcp"
    if "serper" in tool_name or "web_search" in tool_name:
        return "serper_web_search"
    if "fetch" in tool_name:
        return "fetch"
    return ""


def build_mcp_servers_runtime_status(tool_manager: Any) -> Dict[str, Any]:
    """Build MCP runtime status payload using current app configuration."""
    app_config = get_app_config()
    return build_mcp_servers_response(app_config=app_config, tool_manager=tool_manager)


def build_mcp_servers_runtime_state_http_response(response: Any, runtime_state: Any) -> Dict[str, Any]:
    """从当前运行时读取 MCP 状态并保留 HTTP 错误格式。"""
    from fastapi import HTTPException
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    try:
        return build_mcp_servers_runtime_status(runtime_state.tool_manager)
    except Exception as exc:
        logger.error(f"获取MCP服务器状态失败: {exc}")
        raise HTTPException(status_code=500, detail=f"获取MCP服务器状态失败: {exc}") from exc
