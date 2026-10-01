"""
Sage FastAPI + React Demo Backend

现代化多智能体协作Web应用后端
采用FastAPI + WebSocket实现实时通信
支持从配置文件自动加载模型配置
"""

import sys
import asyncio
import json
import uuid
import httpx
from pathlib import Path
from typing import List
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request, Response, Header
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

# 同时支持 ``python -m backend.main`` 与历史脚本导入路径。
project_root = Path(__file__).parent.parent.parent.parent
backend_root = Path(__file__).parent
for import_root in (project_root, backend_root):
    import_root_text = str(import_root)
    if import_root_text not in sys.path:
        sys.path.insert(0, import_root_text)

from agents.utils.logger import logger

# 导入新的配置加载器
from config_loader import get_app_config
from services.mcp_service import build_mcp_servers_runtime_state_http_response
from services.chat_service import (
    build_chat_stream_request_runtime_route,
    execute_chat_request_runtime_route,
    is_trip_planning_query,
)
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
from services.trip_edit_service import (
    TripEditError,
    apply_trip_edit,
    get_activity_delete_impact,
    get_day_route_optimization_preview,
)
from services.trip_draft_validation_service import validate_trip_draft
from services.trip_product_service import (
    TRIP_TEMPLATES,
    import_trip_document,
    share_repository,
)
from services.route_geometry_service import build_day_route
from services.location_service import LocationLookupUnavailable, reverse_geocode_current_location
from services.unsplash_image_proxy_service import fetch_unsplash_image
from services.travel_document_service import export_travel_plan_v3_plain_markdown, safe_delivery_filename
from services.pdf_export_service import export_travel_plan_v3_pdf, fetch_pdf_export_images
from services.export_cache_service import export_cache_key, travel_export_cache
from services.demo_case_replay_service import (
    default_demo_case_directory,
    demo_case_summary,
    iter_demo_case_sse,
    iter_valid_demo_case_packages,
    load_ready_demo_case_package,
)
from services.demo_case_package_service import DemoCasePackageError
from schemas.trip_v3_models import TravelPlanDocumentV3
from services.app_factory_service import create_fastapi_app
from services.server_bootstrap_service import run_backend_server
from services.runtime_state_service import create_runtime_state
from services.trip_repository import TripRepository, default_trip_database_path
from services.trip_repository import ActivePlanningRunError, TripRepositoryError
from services.anonymous_device_service import (
    DEVICE_COOKIE_NAME,
    resolve_anonymous_device,
    set_anonymous_device_cookie,
)
from services.planning_orchestrator import PlanningOrchestrator
from services.planning_run_service import PlanningRunManager, attach_persisted_planning_run
from services.provider_gateway import ProviderGateway
from routes.trip_persistence_routes import create_trip_persistence_router
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
    TripActivityDeleteImpactRequest,
    TripDayRouteOptimizationPreviewRequest,
    TripDraftValidationRequest,
    TripDocumentImportRequest,
    TripDocumentExportRequest,
    ShareCreateRequest,
    DayRouteRequest,
    ReverseGeocodeRequest,
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
app.include_router(
    create_trip_persistence_router(lambda: runtime_state.trip_repository)
)


async def initialize_system():
    """初始化系统组件"""
    runtime_state.trip_repository = TripRepository(default_trip_database_path())
    runtime_state.trip_repository.initialize()
    runtime_state.provider_gateway = ProviderGateway()
    runtime_state.tool_manager, runtime_state.controller = await initialize_runtime_with_boundary(
        baidu_request_dispatcher=runtime_state.baidu_request_dispatcher,
        provider_gateway=runtime_state.provider_gateway,
    )
    runtime_state.planning_orchestrator = PlanningOrchestrator(
        baidu_dispatcher=runtime_state.baidu_request_dispatcher,
    )
    runtime_state.planning_run_manager = PlanningRunManager(
        repository=runtime_state.trip_repository,
        orchestrator=runtime_state.planning_orchestrator,
        tool_manager=runtime_state.tool_manager,
        provider_gateway=runtime_state.provider_gateway,
    )


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
        runtime_state.tool_manager = await initialize_tool_manager(
            app_config,
            baidu_request_dispatcher=runtime_state.baidu_request_dispatcher,
            provider_gateway=runtime_state.provider_gateway,
        )
        if runtime_state.planning_run_manager is not None:
            runtime_state.planning_run_manager.update_runtime_dependencies(
                tool_manager=runtime_state.tool_manager,
            )

        rebuilt_count = 0
        if runtime_state.tool_manager is not None:
            try:
                rebuilt_count = len(runtime_state.tool_manager.list_tools())
            except Exception:
                rebuilt_count = 0

        logger.warning(f"检测到运行时工具为空，已自动重建工具管理器，当前工具数: {rebuilt_count}")


async def cleanup_system():
    """清理系统资源"""
    if runtime_state.planning_run_manager is not None:
        await runtime_state.planning_run_manager.close()
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


@app.post("/api/location/reverse-geocode")
async def reverse_geocode_endpoint(request: ReverseGeocodeRequest):
    """将浏览器授权的位置坐标解析为结构化表单可用的城市。"""
    try:
        return await reverse_geocode_current_location(
            latitude=request.latitude,
            longitude=request.longitude,
            dispatcher=runtime_state.baidu_request_dispatcher,
        )
    except LocationLookupUnavailable as exc:
        logger.warning(f"当前位置解析不可用: {exc}")
        raise HTTPException(
            status_code=503,
            detail="当前位置解析暂不可用，请手动输入出发地。",
        ) from None


@app.get("/api/images/unsplash")
async def unsplash_image_endpoint(url: str = Query(..., min_length=1, max_length=4096)):
    """Deliver a validated Unsplash CDN image without persisting or caching it."""
    try:
        content, media_type = await fetch_unsplash_image(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="图片地址不可用") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="图片暂时无法加载") from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
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
    "PLACE_SELECTION_INVALID": (422, "地点选择已过期或不属于当前目的地，请重新搜索选择"),
    "ACTIVITY_NOT_FOUND": (422, "未找到要编辑的行程活动"),
    "CANDIDATE_NOT_FOUND": (422, "未找到要排入行程的候选地点"),
    "CANDIDATE_POOL_FULL": (422, "候选池已满，暂不能继续移入候选"),
    "CANDIDATE_NO_FEASIBLE_SLOT": (422, "目标日期没有满足时间、营业窗口、距离和预算约束的空档"),
    "TIME_NOT_SET": (422, "请先为活动设置时间后再调整或固定"),
    "TIME_OUT_OF_RANGE": (422, "15 分钟调整不能超出当天的时间范围"),
    "TRANSPORT_OPTION_NOT_FOUND": (422, "未找到可选择的交通方案"),
    "TRANSPORT_OPTION_UNAVAILABLE": (422, "当前交通方案尚未核验，不能设为主方案"),
    "LODGING_OPTION_NOT_FOUND": (422, "未找到可选择的规划住宿点"),
    "DELETE_CONFIRMATION_REQUIRED": (422, "请先确认永久删除操作"),
    "ROUTE_OPTIMIZATION_CONFIRMATION_REQUIRED": (422, "请先确认当天路线优化"),
    "ROUTE_OPTIMIZATION_UNAVAILABLE": (422, "当天没有可应用的路线优化"),
    "ROUTE_OPTIMIZATION_BLOCKED": (422, "请先修正草稿中的时间或结构问题，再优化路线"),
}


@app.post("/api/trip-edit", response_model=TripEditResponse)
async def trip_edit_endpoint(request: TripEditRequest):
    """Apply one versioned, deterministic edit to a structured trip plan."""
    try:
        result = apply_trip_edit(plan=request.document or request.plan, operation=request.operation)
        document = result.get("document")
        if document and document.get("schema_version") == "3.0" and result.get("diff", {}).get("routes_revalidation_required"):
            from services.formal_consistency_service import rebuild_formal_routes, refresh_schedule_validation
            if request.operation.type == "select_transport_option":
                from services.transport_hub_service import enrich_selected_transport_hubs
                await enrich_selected_transport_hubs(
                    document, runtime_state.tool_manager,
                    directions=(request.operation.payload["direction"],),
                )
            await rebuild_formal_routes(
                document, dispatcher=runtime_state.baidu_request_dispatcher,
                affected_days=result["diff"].get("affected_days"),
            )
            if request.operation.type == "move_activity":
                from services.trip_edit_service import reschedule_moved_activity
                reschedule_moved_activity(document, request.operation.model_dump(mode="json"), result["diff"])
            elif request.operation.type == "insert_candidate":
                from services.candidate_route_schedule_service import reschedule_inserted_candidate
                reschedule_inserted_candidate(document, request.operation.model_dump(mode="json"), result["diff"])
            refresh_schedule_validation(document)
            result["document"] = TravelPlanDocumentV3.model_validate(document, context={"draft": True}).model_dump(mode="json")
            result["plan"] = result["document"]
            result["draft_validation"] = validate_trip_draft(document)
        return result
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


@app.get("/api/places/search")
async def search_places_endpoint(q: str = Query(min_length=2, max_length=100), destination: str = Query(min_length=1, max_length=80), kind: str = "attraction"):
    if kind not in {"attraction", "food", "hotel", "shopping", "transport", "other"}:
        raise HTTPException(status_code=422, detail={"code": "INVALID_PLACE_KIND", "message": "地点类别无效"})
    from services.editable_place_service import search_editable_places
    await ensure_tool_manager_ready()
    results = await asyncio.to_thread(search_editable_places, q, destination, kind, runtime_state.tool_manager)
    return {"items": results, "message": None if results else "没有找到带有效坐标的准确地点，请换一个名称搜索。"}


@app.post("/api/trip-drafts/validate")
async def validate_trip_draft_endpoint(request: TripDraftValidationRequest):
    """校验草稿；硬错误只阻止后续应用，不丢弃当前草稿。"""
    return validate_trip_draft(request.document)


@app.post("/api/trip-drafts/delete-impact")
async def trip_draft_delete_impact_endpoint(request: TripActivityDeleteImpactRequest):
    """返回永久删除活动前的服务端关联数据影响计数。"""
    try:
        return get_activity_delete_impact(request.document, request.activity_id)
    except TripEditError as exc:
        status_code, message = _TRIP_EDIT_ERROR_RESPONSES.get(
            exc.code,
            (422, "无法读取活动删除影响"),
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": message},
        ) from None


@app.post("/api/trip-drafts/route-optimization-preview")
async def trip_draft_route_optimization_preview_endpoint(
    request: TripDayRouteOptimizationPreviewRequest,
):
    """返回仅调整当天非固定活动的路线优化预览。"""
    try:
        return get_day_route_optimization_preview(request.document, request.day)
    except TripEditError as exc:
        status_code, message = _TRIP_EDIT_ERROR_RESPONSES.get(
            exc.code,
            (422, "无法生成当天路线优化预览"),
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": message},
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


def _current_export_document(payload: TripDocumentExportRequest, request: Request) -> TravelPlanDocumentV3:
    repository = runtime_state.trip_repository
    if repository is None:
        raise HTTPException(status_code=503, detail={"code": "TRIP_STORAGE_UNAVAILABLE", "message": "旅程存储暂不可用"})
    identity = resolve_anonymous_device(repository, request.cookies.get(DEVICE_COOKIE_NAME))
    try:
        trip = repository.get_trip(identity.device_id, payload.trip_id)
    except TripRepositoryError as error:
        raise HTTPException(status_code=404, detail={"code": error.code, "message": "未找到当前设备的旅程"}) from None
    snapshot = trip.get("formalSnapshots", {}).get("current")
    if not snapshot:
        raise HTTPException(status_code=409, detail={"code": "FORMAL_SNAPSHOT_NOT_FOUND", "message": "请先生成并保存正式方案"})
    if snapshot["revision"] != payload.expected_revision:
        raise HTTPException(status_code=409, detail={"code": "REVISION_CONFLICT", "message": "正式方案已更新，请刷新后下载当前版本"})
    return TravelPlanDocumentV3.model_validate(snapshot["document"])


@app.post("/api/trips/export/markdown")
async def export_trip_markdown_endpoint(request: TripDocumentExportRequest, http_request: Request):
    try:
        document = _current_export_document(request, http_request)
        async def build_markdown() -> bytes:
            return export_travel_plan_v3_plain_markdown(document).encode("utf-8")

        content, cache_hit = await travel_export_cache.get_or_create(
            export_cache_key(document, "markdown"),
            build_markdown,
        )
        return {
            "content": content.decode("utf-8"),
            "filename": safe_delivery_filename(
                document.delivery.markdown_filename,
                fallback_title=document.title,
                revision=document.revision,
                suffix=".md",
            ),
            "plan_id": document.plan_id,
            "version": document.revision,
            "cache_hit": cache_hit,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TRIP_DOCUMENT", "message": str(exc)}) from None


@app.post("/api/trips/export/pdf")
async def export_trip_pdf_endpoint(request: TripDocumentExportRequest, http_request: Request):
    try:
        document = _current_export_document(request, http_request)
        async def build_pdf() -> bytes:
            image_data = await fetch_pdf_export_images(document)
            return export_travel_plan_v3_pdf(document, image_data)

        content, cache_hit = await travel_export_cache.get_or_create(
            export_cache_key(document, "pdf"),
            build_pdf,
        )
        filename = quote(
            safe_delivery_filename(
                document.delivery.pdf_filename,
                fallback_title=document.title,
                revision=document.revision,
                suffix=".pdf",
            ),
            safe="",
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                "X-Export-Cache": "HIT" if cache_hit else "MISS",
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TRIP_DOCUMENT", "message": str(exc)}) from None


@app.post("/api/routes/day")
async def build_day_route_endpoint(request: DayRouteRequest):
    return await build_day_route(
        day=request.day,
        plan_version=request.plan_version,
        activities=request.activities,
        scope=request.scope,
        baidu_dispatcher=runtime_state.baidu_request_dispatcher,
        baidu_priority="candidate",
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
    if runtime_state.controller is None:
        return execute_chat_request_runtime_route(request=request, runtime_state=runtime_state)
    await ensure_tool_manager_ready()
    return execute_chat_request_runtime_route(
        request=request,
        runtime_state=runtime_state,
    )


@app.post("/api/chat-stream")
async def chat_stream(request: ChatRequest, http_request: Request = None):
    """处理聊天请求并返回流式响应"""
    if runtime_state.controller is None:
        return build_chat_stream_request_runtime_route(request=request, runtime_state=runtime_state)
    await ensure_tool_manager_ready()
    repository = runtime_state.trip_repository
    trip_id = (request.trip_id or request.session_id or "").strip()
    if repository is None or not trip_id or http_request is None:
        return build_chat_stream_request_runtime_route(
            request=request,
            runtime_state=runtime_state,
        )

    identity = resolve_anonymous_device(
        repository,
        http_request.cookies.get(DEVICE_COOKIE_NAME),
    )
    latest_user_message = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "新旅程",
    )
    repository.ensure_trip(identity.device_id, trip_id, latest_user_message[:20])
    run_id = request.request_id or str(uuid.uuid4())
    request.request_id = run_id
    try:
        repository.begin_planning_run(
            identity.device_id,
            trip_id,
            run_id,
            run_id,
        )
        stream_response = build_chat_stream_request_runtime_route(
            request=request,
            runtime_state=runtime_state,
        )
    except ActivePlanningRunError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": str(error)},
        ) from None
    except TripRepositoryError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": str(error)},
        ) from None
    except Exception:
        repository.finish_planning_run(identity.device_id, run_id, "failed")
        raise

    set_anonymous_device_cookie(
        stream_response,
        identity,
        secure=http_request.url.scheme == "https",
    )
    planning_run_manager = runtime_state.planning_run_manager
    if planning_run_manager is not None and is_trip_planning_query(latest_user_message):
        trip = repository.get_trip(identity.device_id, trip_id)
        current_snapshot = (trip.get("formalSnapshots") or {}).get("current") or {}
        current_document = current_snapshot.get("document") or {}
        planning_run_manager.start(
            stream_response.body_iterator,
            run_id=run_id,
            request_id=run_id,
            device_id=identity.device_id,
            trip_id=trip_id,
            target_revision=int(trip.get("currentRevision") or 0) + 1,
            existing_plan_id=str(current_document.get("plan_id") or "") or None,
            requires_baidu_verification=True,
        )
        managed_response = StreamingResponse(
            planning_run_manager.stream(run_id=run_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        set_anonymous_device_cookie(
            managed_response,
            identity,
            secure=http_request.url.scheme == "https",
        )
        return managed_response
    return attach_persisted_planning_run(
        stream_response,
        repository=repository,
        device_id=identity.device_id,
        trip_id=trip_id,
        run_id=run_id,
    )


def _last_sequence_from_header(last_event_id: str, fallback: int) -> int:
    text = str(last_event_id or "").strip()
    if not text:
        return max(0, fallback)
    for separator in (":", "_"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    try:
        return max(max(0, fallback), int(text))
    except ValueError:
        return max(0, fallback)


@app.get("/api/demo-cases")
async def list_demo_cases():
    """只读取已验收的静态案例包；不触发模型、Provider 或旅程存储。"""
    packages = iter_valid_demo_case_packages(default_demo_case_directory())
    return {"cases": [demo_case_summary(package) for package in packages]}


@app.get("/api/demo-cases/{case_id}/events")
async def replay_demo_case_events(
    case_id: str,
    after_sequence: int = Query(default=0, ge=0),
    speed: float = Query(default=1.0),
    final_only: bool = Query(default=False),
):
    """按录制顺序回放版本化 SSE，不读取用户历史或调用实时服务。"""
    try:
        package = load_ready_demo_case_package(default_demo_case_directory(), case_id)
        stream = iter_demo_case_sse(package, after_sequence=after_sequence, speed=speed, final_only=final_only)
    except DemoCasePackageError as exc:
        raise HTTPException(status_code=404, detail={"code": "DEMO_CASE_NOT_FOUND", "message": str(exc)}) from exc
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/planning-runs/{run_id}/events")
async def resume_planning_events(
    run_id: str,
    http_request: Request,
    last_sequence: int = Query(default=0, ge=0),
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
):
    repository = runtime_state.trip_repository
    manager = runtime_state.planning_run_manager
    if repository is None or manager is None:
        raise HTTPException(status_code=503, detail={"code": "PLANNING_RUNTIME_UNAVAILABLE", "message": "规划服务尚未就绪"})
    identity = resolve_anonymous_device(repository, http_request.cookies.get(DEVICE_COOKIE_NAME))
    if repository.planning_run(identity.device_id, run_id) is None:
        raise HTTPException(status_code=404, detail={"code": "PLANNING_RUN_NOT_FOUND", "message": "规划任务不存在"})
    cursor = _last_sequence_from_header(last_event_id, last_sequence)
    response = StreamingResponse(
        manager.stream(run_id=run_id, after_sequence=cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    set_anonymous_device_cookie(response, identity, secure=http_request.url.scheme == "https")
    return response


@app.delete("/api/planning-runs/{run_id}")
async def cancel_planning_run(run_id: str, http_request: Request):
    repository = runtime_state.trip_repository
    manager = runtime_state.planning_run_manager
    if repository is None or manager is None:
        raise HTTPException(status_code=503, detail={"code": "PLANNING_RUNTIME_UNAVAILABLE", "message": "规划服务尚未就绪"})
    identity = resolve_anonymous_device(repository, http_request.cookies.get(DEVICE_COOKIE_NAME))
    try:
        result = await manager.cancel(device_id=identity.device_id, run_id=run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"code": "PLANNING_RUN_NOT_FOUND", "message": "规划任务不存在"}) from None
    response = Response(content=json.dumps(result, ensure_ascii=False), media_type="application/json")
    set_anonymous_device_cookie(response, identity, secure=http_request.url.scheme == "https")
    return response


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
        app_target="backend.main:app",
    )
