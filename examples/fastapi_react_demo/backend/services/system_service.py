import os
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from agents.config import get_settings
from agents.utils.logger import logger

from config_loader import get_app_config, save_app_config

if TYPE_CHECKING:
    from agents.agent.agent_controller import AgentController
else:
    AgentController = Any


def build_system_status(
    tool_manager: Any,
    active_sessions: Dict[str, Dict[str, Any]],
    agents_count: int = 7,
    version: str = "0.8",
) -> Dict[str, Any]:
    """Build status payload for system status endpoint."""
    tools_count = len(tool_manager.list_tools()) if tool_manager else 0
    runtime_model = build_runtime_model_status_payload()
    return {
        "status": "running",
        "agents_count": agents_count,
        "tools_count": tools_count,
        "active_sessions": len(active_sessions),
        "version": version,
        **runtime_model,
    }


def _mask_api_key(api_key: str) -> str:
    text = (api_key or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return f"{text[:2]}***"
    return f"{text[:6]}***{text[-4:]}"


def build_runtime_model_status_payload() -> Dict[str, str]:
    """Collect effective model/runtime diagnostics for API status visibility."""
    app_config = get_app_config()
    settings = get_settings()

    model_name = app_config.model.model_name or settings.model.model_name
    base_url = app_config.model.base_url or settings.model.base_url
    api_key = app_config.model.api_key or settings.model.api_key
    if not api_key:
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "")

    source = "config_file"
    if any(os.getenv(name) for name in ("SAGE_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY", "SAGE_MODEL_NAME", "SAGE_BASE_URL")):
        source = "environment"

    payload = {
        "model_name": model_name,
        "base_url": base_url,
        "api_key_masked": _mask_api_key(api_key),
        "config_source": source,
    }

    return {key: value for key, value in payload.items() if value}


def build_system_status_runtime_model_http_response(response: Any, runtime_state: Any) -> Any:
    """在单一 HTTP 边界组装状态，保留响应模型、CORS 和错误语义。"""
    from fastapi import HTTPException
    from schemas.api_models import SystemStatus
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    try:
        payload = build_system_status(runtime_state.tool_manager, runtime_state.active_sessions)
    except Exception as exc:
        logger.error(f"获取系统状态失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SystemStatus(**payload)


def sync_settings_from_model(
    api_key: str,
    model_name: str,
    base_url: str,
    max_tokens: int,
    temperature: float,
) -> None:
    """Sync model settings into shared runtime settings object."""
    settings = get_settings()
    settings.model.api_key = api_key
    settings.model.model_name = model_name
    settings.model.base_url = base_url
    settings.model.max_tokens = max_tokens
    settings.model.temperature = temperature


def build_controller(
    api_key: str,
    model_name: str,
    base_url: str,
    max_tokens: int,
    temperature: float,
) -> AgentController:
    """Create agent controller using current model runtime config."""
    from agents.agent.agent_controller import AgentController
    from services.model_runtime import create_model_and_config

    model, model_config = create_model_and_config(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return AgentController(model, model_config)


def configure_runtime(
    api_key: str,
    model_name: str,
    base_url: str,
    max_tokens: int,
    temperature: float,
) -> AgentController:
    """Persist model config and return a new controller instance."""
    sync_settings_from_model(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    app_config = get_app_config()
    app_config.model.api_key = api_key
    app_config.model.model_name = model_name
    app_config.model.base_url = base_url
    app_config.model.max_tokens = max_tokens
    app_config.model.temperature = temperature
    save_app_config(app_config)

    return build_controller(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def configure_runtime_request_http_response(response: Any, config: Any, runtime_state: Any) -> Dict[str, str]:
    """保存配置并在成功后替换运行时控制器。"""
    from fastapi import HTTPException
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    try:
        controller = configure_runtime(
            api_key=config.api_key,
            model_name=config.model_name,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
        logger.info(f"系统配置更新成功: {config.model_name}")
        print(f"🔄 配置已更新并保存: {config.model_name}")
    except Exception as exc:
        logger.error(f"系统配置失败: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime_state.controller = controller
    return {"status": "success", "message": "配置更新成功并已保存到文件"}


def resolve_controller_from_app_config(app_config: Any) -> Tuple[Optional[AgentController], Optional[str], Optional[str]]:
    """Resolve controller from app config first, then fallback to shared settings."""
    if app_config.model.api_key:
        sync_settings_from_model(
            api_key=app_config.model.api_key,
            model_name=app_config.model.model_name,
            base_url=app_config.model.base_url,
            max_tokens=app_config.model.max_tokens,
            temperature=app_config.model.temperature,
        )
        return (
            build_controller(
                api_key=app_config.model.api_key,
                model_name=app_config.model.model_name,
                base_url=app_config.model.base_url,
                max_tokens=app_config.model.max_tokens,
                temperature=app_config.model.temperature,
            ),
            app_config.model.model_name,
            "config",
        )

    settings = get_settings()
    if settings.model.api_key:
        return (
            build_controller(
                api_key=settings.model.api_key,
                model_name=settings.model.model_name,
                base_url=settings.model.base_url,
                max_tokens=settings.model.max_tokens,
                temperature=settings.model.temperature,
            ),
            settings.model.model_name,
            "settings",
        )

    return None, None, None
