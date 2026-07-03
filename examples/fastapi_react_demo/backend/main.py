"""
Sage FastAPI + React Demo Backend

现代化多智能体协作Web应用后端
采用FastAPI + WebSocket实现实时通信
支持从配置文件自动加载模型配置
"""

import sys
import asyncio
from pathlib import Path
from typing import List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
import uvicorn

# 添加项目路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.append(str(project_root))

from agents.utils.logger import logger

# 导入新的配置加载器
from config_loader import get_app_config
from services.mcp_service import build_mcp_servers_runtime_state_http_response
from services.chat_service import execute_chat_request_runtime_route, build_chat_stream_request_runtime_route
from services.file_service import build_download_response_safe
from services.session_service import list_active_sessions_runtime_route, cleanup_session_runtime_route
from services.system_service import build_system_status_runtime_model_http_response, configure_runtime_request_http_response
from services.tool_runtime_service import initialize_tool_manager
from services.http_response_service import build_options_route, build_heartbeat_sse_route
from services.spa_service import serve_root_with_boundary, serve_spa_with_boundary, mount_static_assets_with_boundary
from services.lifecycle_service import initialize_runtime_with_boundary, cleanup_runtime_with_boundary
from services.tool_catalog_service import build_tool_catalog_runtime_model_http_response
from services.skill_profile_service import build_skill_catalog_runtime_model_http_response
from services.powerpaint_status_service import build_powerpaint_status_model_http_response
from services.app_factory_service import create_fastapi_app
from services.server_bootstrap_service import run_backend_server
from services.runtime_state_service import create_runtime_state
from schemas.api_models import ChatMessage, ChatRequest, ConfigRequest, SkillInfo, ToolInfo, SystemStatus, PowerPaintStatus


# 运行时状态
runtime_state = create_runtime_state()
_tool_manager_recover_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化
    logger.info("FastAPI应用启动中...")
    await initialize_system()
    yield
    # 关闭时清理
    logger.info("FastAPI应用关闭中...")
    await cleanup_system()


# 创建FastAPI应用
app = create_fastapi_app(lifespan=lifespan)


async def initialize_system():
    """初始化系统组件"""
    runtime_state.tool_manager, runtime_state.controller = await initialize_runtime_with_boundary()


async def ensure_tool_manager_ready() -> None:
    """Recover tool manager lazily when runtime state becomes empty unexpectedly."""
    tool_count = 0
    if runtime_state.tool_manager is not None:
        try:
            tool_count = len(runtime_state.tool_manager.list_tools())
        except Exception as e:
            logger.error(f"读取工具列表失败，将尝试重建工具管理器: {e}")

    if tool_count > 0:
        return

    async with _tool_manager_recover_lock:
        retry_count = 0
        if runtime_state.tool_manager is not None:
            try:
                retry_count = len(runtime_state.tool_manager.list_tools())
            except Exception:
                retry_count = 0

        if retry_count > 0:
            return

        app_config = get_app_config()
        runtime_state.tool_manager = await initialize_tool_manager(app_config)

        rebuilt_count = 0
        if runtime_state.tool_manager is not None:
            try:
                rebuilt_count = len(runtime_state.tool_manager.list_tools())
            except Exception:
                rebuilt_count = 0

        logger.warning(f"检测到运行时工具为空，已自动重建工具管理器，当前工具数: {rebuilt_count}")


async def cleanup_system():
    """清理系统资源"""
    await cleanup_runtime_with_boundary(
        active_sessions=runtime_state.active_sessions,
        tool_manager=runtime_state.tool_manager,
    )


# API路由

@app.get("/", response_class=HTMLResponse)
async def read_root():
    """根路径，返回React应用"""
    return serve_root_with_boundary(static_path=static_path)


@app.get("/api/status", response_model=SystemStatus)
async def get_system_status(response: Response):
    """获取系统状态"""
    await ensure_tool_manager_ready()
    return build_system_status_runtime_model_http_response(
        response=response,
        runtime_state=runtime_state,
    )


@app.get("/api/powerpaint/status", response_model=PowerPaintStatus)
async def get_powerpaint_status(response: Response):
    """获取 PowerPaint 服务状态"""
    return build_powerpaint_status_model_http_response(response=response)


@app.post("/api/configure")
async def configure_system(config: ConfigRequest, response: Response):
    """配置系统"""
    return configure_runtime_request_http_response(
        response=response,
        config=config,
        runtime_state=runtime_state,
    )


@app.get("/api/tools", response_model=List[ToolInfo])
async def get_tools(response: Response):
    """获取可用工具列表"""
    await ensure_tool_manager_ready()
    return build_tool_catalog_runtime_model_http_response(
        response=response,
        runtime_state=runtime_state,
    )


@app.get("/api/skills", response_model=List[SkillInfo])
async def get_skills(response: Response):
    """鑾峰彇鍙€夌殑鏃呰鎶€鑳藉寘"""
    await ensure_tool_manager_ready()
    return build_skill_catalog_runtime_model_http_response(
        response=response,
        runtime_state=runtime_state,
    )


@app.get("/api/mcp-servers")
async def get_mcp_servers(response: Response):
    """获取MCP服务器状态"""
    await ensure_tool_manager_ready()
    return build_mcp_servers_runtime_state_http_response(
        response=response,
        runtime_state=runtime_state,
    )


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    """聊天API端点（非流式）"""
    await ensure_tool_manager_ready()
    return execute_chat_request_runtime_route(
        request=request,
        runtime_state=runtime_state,
    )


@app.post("/api/chat-stream")
async def chat_stream(request: ChatRequest):
    """处理聊天请求并返回流式响应"""
    await ensure_tool_manager_ready()
    return build_chat_stream_request_runtime_route(
        request=request,
        runtime_state=runtime_state,
    )


@app.get("/api/sse/{session_id}")
async def sse_endpoint(session_id: str):
    """SSE连接端点"""
    return build_heartbeat_sse_route(
        session_id=session_id,
    )


@app.get("/api/sessions")
async def get_active_sessions():
    """获取活跃会话列表"""
    return list_active_sessions_runtime_route(runtime_state=runtime_state)


@app.delete("/api/sessions/{session_id}")
async def cleanup_session(session_id: str):
    """清理指定会话"""
    return await cleanup_session_runtime_route(
        session_id=session_id,
        runtime_state=runtime_state,
    )


@app.get("/api/download/{session_id}/{filename}")
@app.head("/api/download/{session_id}/{filename}")
async def download_file(session_id: str, filename: str):
    """下载生成的文件"""
    return build_download_response_safe(session_id=session_id, filename=filename)


@app.options("/{rest_of_path:path}")
async def options_handler(rest_of_path: str, response: Response):
    """处理OPTIONS预检请求"""
    return build_options_route(response=response)


# 静态文件服务（用于React构建文件）
static_path = Path(__file__).parent / "static"
mount_static_assets_with_boundary(app=app, static_path=static_path)


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_spa(full_path: str):
    """React Router 路由回退，并优先返回实际静态文件。"""
    return serve_spa_with_boundary(static_path=static_path, full_path=full_path)


if __name__ == "__main__":
    run_backend_server(
        config_loader=get_app_config,
        uvicorn_runner=uvicorn.run,
        printer=print,
        app_target="main:app",
    ) 
