from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from agents.utils.logger import logger

from config_loader import get_app_config

if TYPE_CHECKING:
    from agents.agent.agent_controller import AgentController
    from agents.tool.tool_manager import ToolManager
else:
    AgentController = Any
    ToolManager = Any


async def bootstrap_runtime() -> Tuple[Optional[ToolManager], Optional[AgentController], Optional[str]]:
    """Initialize runtime dependencies and resolve active controller."""
    from services.system_service import resolve_controller_from_app_config
    from services.tool_runtime_service import initialize_tool_manager

    app_config = get_app_config()

    print("🚀 Sage Multi-Agent Framework 启动中...")
    print(f"📊 模型: {app_config.model.model_name}")
    print(f"🔗 API: {app_config.model.base_url}")

    tool_manager = await initialize_tool_manager(app_config)
    controller, active_model_name, source = resolve_controller_from_app_config(app_config)

    if controller is None:
        print("⚠️  未配置API密钥，需要通过Web界面配置或在config.yaml中设置")
        print("💡 提示：编辑 backend/config.yaml 文件，设置您的API密钥")
        return tool_manager, None, None

    if source == "config":
        logger.info("智能体控制器初始化完成 (使用配置文件)")
    else:
        logger.info("智能体控制器初始化完成 (使用Sage配置)")

    print(f"✅ 系统已就绪，模型: {active_model_name}")
    return tool_manager, controller, active_model_name


async def cleanup_runtime(
    active_sessions: Dict[str, Dict[str, Any]],
    tool_manager: Optional[ToolManager],
) -> None:
    """Cleanup runtime sessions and related tool resources."""
    from services.tool_runtime_service import cleanup_managed_mcp_processes

    for session_id in list(active_sessions.keys()):
        if tool_manager:
            await tool_manager.cleanup_session(session_id)
    active_sessions.clear()
    if tool_manager:
        await cleanup_managed_mcp_processes(tool_manager)


async def initialize_runtime_with_boundary(logger: Any = logger) -> Tuple[Optional[ToolManager], Optional[AgentController]]:
    """Initialize runtime and keep existing startup error semantics."""
    try:
        tool_manager, controller, _ = await bootstrap_runtime()
        return tool_manager, controller
    except Exception as e:
        logger.error(f"系统初始化失败: {e}")
        print(f"❌ 系统初始化失败: {e}")
        print("💡 请检查配置文件或网络连接")
        return None, None


async def cleanup_runtime_with_boundary(
    active_sessions: Dict[str, Dict[str, Any]],
    tool_manager: Optional[ToolManager],
    logger: Any = logger,
) -> None:
    """Cleanup runtime and keep existing shutdown error semantics."""
    try:
        await cleanup_runtime(active_sessions=active_sessions, tool_manager=tool_manager)
        logger.info("系统资源清理完成")
    except Exception as e:
        logger.error(f"系统清理失败: {e}")
