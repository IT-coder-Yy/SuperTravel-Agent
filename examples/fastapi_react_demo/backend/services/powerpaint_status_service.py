import os
from typing import Any, Callable, Dict, Optional
from urllib import request

from agents.utils.logger import logger


DEFAULT_POWERPAINT_URL = "http://localhost:7860"
POWERPAINT_URL_ENV = "POWERPAINT_URL"
POWERPAINT_TIMEOUT_SECONDS = 1.5


def resolve_powerpaint_url(environ: Any = os.environ) -> str:
    """Resolve PowerPaint service URL from environment with a stable default."""
    return (environ.get(POWERPAINT_URL_ENV) or DEFAULT_POWERPAINT_URL).strip() or DEFAULT_POWERPAINT_URL


def is_powerpaint_reachable(url: str, timeout_seconds: float = POWERPAINT_TIMEOUT_SECONDS) -> bool:
    """Probe PowerPaint with a short HTTP request."""
    try:
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", getattr(response, "code", 200))
            return int(status_code) < 500
    except Exception:
        return False


def build_powerpaint_status(
    url: Optional[str] = None,
    checker: Callable[[str], bool] = is_powerpaint_reachable,
) -> Dict[str, Any]:
    """Build PowerPaint status payload."""
    resolved_url = url or resolve_powerpaint_url()
    reachable = checker(resolved_url)
    message = (
        "PowerPaint 服务可访问。"
        if reachable
        else "PowerPaint 服务暂不可访问，请确认服务已启动并检查地址配置。"
    )
    return {
        "url": resolved_url,
        "reachable": reachable,
        "message": message,
    }


def build_powerpaint_status_route(
    logger: Any = logger,
    checker: Callable[[str], bool] = is_powerpaint_reachable,
) -> Dict[str, Any]:
    """Build PowerPaint status payload with route-level error boundary semantics."""
    from fastapi import HTTPException

    try:
        return build_powerpaint_status(checker=checker)
    except Exception as e:
        logger.error(f"获取 PowerPaint 状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def build_powerpaint_status_http_response(
    response: Any,
    logger: Any = logger,
    checker: Callable[[str], bool] = is_powerpaint_reachable,
) -> Dict[str, Any]:
    """Build PowerPaint status payload and attach CORS headers."""
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    return build_powerpaint_status_route(logger=logger, checker=checker)


def build_powerpaint_status_model_http_response(response: Any) -> Any:
    """Build PowerPaint status payload as response model."""
    from schemas.api_models import PowerPaintStatus

    return PowerPaintStatus(**build_powerpaint_status_http_response(response=response))
