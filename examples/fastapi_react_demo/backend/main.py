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

from fastapi import FastAPI, HTTPException, Query, Response, Header
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
from services.travel_knowledge_catalog_service import build_travel_knowledge_cities_response, build_travel_knowledge_search_response
from services.trip_edit_service import TripEditError, apply_trip_edit
from services.trip_product_service import (
    TRIP_TEMPLATES,
    export_trip_markdown,
    import_trip_document,
    share_repository,
)
from services.route_geometry_service import build_day_route
from services.unsplash_tracking_service import track_document_cover_download
from services.app_factory_service import create_fastapi_app
from services.server_bootstrap_service import run_backend_server
from services.runtime_state_service import create_runtime_state
from schemas.api_models import (
    ChatMessage,
    ChatRequest,
    ConfigRequest,
    PowerPaintStatus,
    SkillInfo,
    SystemStatus,
    ToolInfo,
    TripEditRequest,
    TripEditResponse,
    TripDocumentImportRequest,
    TripDocumentExportRequest,
    ShareCreateRequest,
    DayRouteRequest,
)


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


@app.get("/api/travel-knowledge/cities")
async def get_travel_knowledge_cities():
    """Return local travel RAG city cards for the knowledge base page."""
    return build_travel_knowledge_cities_response()


@app.get("/api/knowledge/cities")
async def get_knowledge_cities():
    """Return local travel RAG city cards using the product API path."""
    return build_travel_knowledge_cities_response()


@app.get("/api/knowledge/search")
async def search_knowledge(
    q: str = Query(..., min_length=1),
    top_k: int = Query(default=8, ge=1, le=20),
):
    """Search local travel RAG chunks for the knowledge base page."""
    return build_travel_knowledge_search_response(query=q, top_k=top_k)


_TRIP_EDIT_ERROR_RESPONSES = {
    "VERSION_CONFLICT": (409, "当前行程已更新，请刷新后重试"),
    "PLAN_ID_MISMATCH": (409, "操作对应的行程与当前行程不一致"),
    "INVALID_VERSION": (422, "行程版本信息无效"),
    "UNSUPPORTED_OPERATION": (422, "不支持的行程编辑操作"),
    "INVALID_INPUT": (422, "行程编辑请求无效"),
    "INVALID_PLAN": (422, "当前行程数据无效"),
    "INVALID_PAYLOAD": (422, "行程编辑参数无效"),
    "ACTIVITY_NOT_FOUND": (422, "未找到要编辑的行程活动"),
}


@app.post("/api/trip-edit", response_model=TripEditResponse)
async def trip_edit_endpoint(request: TripEditRequest):
    """Apply one versioned, deterministic edit to a structured trip plan."""
    try:
        return apply_trip_edit(plan=request.plan, operation=request.operation)
    except TripEditError as exc:
        status_code, message = _TRIP_EDIT_ERROR_RESPONSES.get(
            exc.code,
            (422, "行程编辑请求无效"),
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": message},
        ) from None
    except Exception:
        logger.exception("行程结构化编辑失败")
        raise HTTPException(
            status_code=500,
            detail={"code": "TRIP_EDIT_FAILED", "message": "行程编辑失败，请稍后重试"},
        ) from None


@app.get("/api/trip-templates")
async def get_trip_templates():
    return {"templates": TRIP_TEMPLATES, "count": len(TRIP_TEMPLATES)}


@app.post("/api/trips/import")
async def import_trip_document_endpoint(request: TripDocumentImportRequest):
    try:
        return import_trip_document(request.format, request.content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TRIP_DOCUMENT", "message": str(exc)}) from None


@app.post("/api/trips/export/markdown")
async def export_trip_markdown_endpoint(request: TripDocumentExportRequest):
    try:
        normalized = import_trip_document("json", request.document)
        await track_document_cover_download(normalized)
        return {
            "content": export_trip_markdown(normalized),
            "filename": normalized["delivery"]["markdown_filename"],
            "plan_id": normalized["plan_id"],
            "version": normalized["version"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TRIP_DOCUMENT", "message": str(exc)}) from None


@app.post("/api/routes/day")
async def build_day_route_endpoint(request: DayRouteRequest):
    return await build_day_route(
        day=request.day,
        plan_version=request.plan_version,
        activities=request.activities,
        scope=request.scope,
    )


@app.post("/api/shares")
async def create_share_endpoint(request: ShareCreateRequest):
    try:
        return share_repository.create(request.document, request.scopes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SHARE", "message": str(exc)}) from None


@app.get("/api/shares/{token}")
async def get_share_endpoint(token: str):
    try:
        return share_repository.read(token)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "SHARE_NOT_FOUND", "message": "分享不存在或已关闭"}) from None


@app.delete("/api/shares/{token}")
async def delete_share_endpoint(token: str, x_share_management_key: str = Header(default="")):
    try:
        share_repository.delete(token, x_share_management_key)
        return {"status": "closed"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "SHARE_NOT_FOUND", "message": "分享不存在或已关闭"}) from None
    except (ValueError, PermissionError):
        raise HTTPException(status_code=403, detail={"code": "SHARE_FORBIDDEN", "message": "无权关闭该分享"}) from None


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
