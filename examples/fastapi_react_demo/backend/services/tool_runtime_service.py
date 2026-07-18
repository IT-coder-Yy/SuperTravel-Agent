import asyncio
import os
import re
import subprocess
from typing import Any, Dict, List

import httpx

from agents.tool.tool_manager import ToolManager
from agents.tool.tool_base import McpToolSpec
from agents.utils.logger import logger


ENV_REF_REGEX = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _resolve_mcp_env_value(raw_value: Any) -> str:
    text = str(raw_value).strip()
    match = ENV_REF_REGEX.match(text)
    if not match:
        return text

    env_name = match.group(1)
    return os.getenv(env_name, "").strip()


def _build_resolved_env_map(raw_env: Any) -> Dict[str, str]:
    if not isinstance(raw_env, dict):
        return {}

    resolved: Dict[str, str] = {}
    for key, value in raw_env.items():
        if not key:
            continue
        resolved_value = _resolve_mcp_env_value(value)
        if resolved_value:
            resolved[str(key)] = resolved_value
    return resolved


def _build_mcp_registration_config(server_config: Any) -> Dict[str, Any]:
    mcp_config: Dict[str, Any] = {}

    if server_config.command:
        mcp_config["command"] = server_config.command
        if server_config.args:
            mcp_config["args"] = server_config.args
    elif server_config.sse_url:
        mcp_config["sse_url"] = server_config.sse_url

    if server_config.env:
        resolved_env = _build_resolved_env_map(server_config.env)
        if resolved_env:
            mcp_config["env"] = resolved_env

    return mcp_config


async def _is_sse_endpoint_ready(sse_url: str, timeout_seconds: float = 1.0) -> bool:
    try:
        timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", sse_url, headers={"Accept": "text/event-stream"}) as response:
                return 200 <= response.status_code < 300
    except Exception:
        return False


def _get_managed_mcp_processes(tool_manager: ToolManager) -> List[subprocess.Popen]:
    processes = getattr(tool_manager, "_managed_mcp_processes", None)
    if processes is None:
        processes = []
        setattr(tool_manager, "_managed_mcp_processes", processes)
    return processes


def _build_auto_start_env(auto_start_config: Any) -> Dict[str, str]:
    env = os.environ.copy()
    resolved_env = _build_resolved_env_map(getattr(auto_start_config, "env", None))
    env.update(resolved_env)
    return env


def _start_mcp_process(server_name: str, auto_start_config: Any) -> subprocess.Popen:
    command = getattr(auto_start_config, "command", None)
    if not command:
        raise ValueError(f"MCP server {server_name} auto_start.command is required")

    args = getattr(auto_start_config, "args", None) or []
    process_args = [str(command), *[str(arg) for arg in args]]
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    return subprocess.Popen(
        process_args,
        cwd=getattr(auto_start_config, "cwd", None),
        env=_build_auto_start_env(auto_start_config),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


async def _wait_for_sse_endpoint(sse_url: str, timeout_seconds: float, process: subprocess.Popen) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await _is_sse_endpoint_ready(sse_url):
            return True
        if process.poll() is not None:
            return False
        await asyncio.sleep(0.25)
    return await _is_sse_endpoint_ready(sse_url)


async def _ensure_auto_started_sse_server(
    tool_manager: ToolManager,
    server_name: str,
    server_config: Any,
) -> None:
    auto_start_config = getattr(server_config, "auto_start", None)
    sse_url = getattr(server_config, "sse_url", None)
    if not auto_start_config or not sse_url:
        return

    if await _is_sse_endpoint_ready(sse_url):
        logger.info(f"MCP SSE server already running: {server_name}")
        return

    process = _start_mcp_process(server_name, auto_start_config)
    _get_managed_mcp_processes(tool_manager).append(process)

    timeout_seconds = float(getattr(auto_start_config, "timeout_seconds", 10.0) or 10.0)
    if not await _wait_for_sse_endpoint(sse_url, timeout_seconds, process):
        raise RuntimeError(f"MCP SSE server {server_name} did not become ready at {sse_url}")

    logger.info(f"Auto-started MCP SSE server: {server_name}")


async def cleanup_managed_mcp_processes(tool_manager: Any) -> None:
    processes = list(getattr(tool_manager, "_managed_mcp_processes", []) or [])
    if not processes:
        return

    for process in processes:
        if process.poll() is None:
            process.terminate()

    deadline = asyncio.get_running_loop().time() + 5.0
    for process in processes:
        while process.poll() is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
        if process.poll() is None:
            process.kill()

    setattr(tool_manager, "_managed_mcp_processes", [])


async def initialize_tool_manager(
    app_config: Any,
    baidu_request_dispatcher: Any = None,
    provider_gateway: Any = None,
) -> ToolManager:
    """Initialize local tools and register MCP servers from config."""
    tool_manager = ToolManager(
        is_auto_discover=False,
        baidu_request_dispatcher=baidu_request_dispatcher,
        provider_gateway=provider_gateway,
    )
    tool_manager._auto_discover_tools()

    connected_mcp_servers = []
    failed_mcp_servers = []
    disabled_mcp_servers = []

    if app_config.mcp and app_config.mcp.servers:
        for server_name, server_config in app_config.mcp.servers.items():
            if server_config.disabled:
                disabled_mcp_servers.append(server_name)
                continue

            try:
                await _ensure_auto_started_sse_server(tool_manager, server_name, server_config)
                mcp_config = _build_mcp_registration_config(server_config)
                success = await tool_manager.register_mcp_server(server_name, mcp_config)
                if success:
                    connected_mcp_servers.append(server_name)
                else:
                    failed_mcp_servers.append(server_name)
            except Exception as e:
                failed_mcp_servers.append(server_name)
                logger.error(f"MCP服务器注册失败: {e}")

    local_tool_names = sorted(
        spec.name for spec in tool_manager.tools.values() if not isinstance(spec, McpToolSpec)
    )
    mcp_tool_names = sorted(
        spec.name for spec in tool_manager.tools.values() if isinstance(spec, McpToolSpec)
    )

    mcp_tool_count = len(mcp_tool_names)
    local_tool_count = len(local_tool_names)
    print(f"🔧 工具已启用：本地{local_tool_count}个，MCP {mcp_tool_count}个，总计{len(tool_manager.tools)}个")
    print(f"🧰 本地工具：{', '.join(local_tool_names) if local_tool_names else '无'}")
    print(f"🛰️ MCP工具：{', '.join(mcp_tool_names) if mcp_tool_names else '无'}")

    if connected_mcp_servers:
        print(f"🌐 MCP已连接：{', '.join(connected_mcp_servers)}")
    if disabled_mcp_servers:
        print(f"⏸️ MCP已禁用：{', '.join(disabled_mcp_servers)}")
    if failed_mcp_servers:
        print(f"⚠️ MCP连接失败：{', '.join(failed_mcp_servers)}")

    logger.info("工具管理器初始化完成")
    return tool_manager
