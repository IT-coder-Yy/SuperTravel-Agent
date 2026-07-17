from typing import Any, Callable, Optional

from agents.utils.console import configure_utf8_console, safe_print


def run_backend_server(
    config_loader: Callable[[], Any],
    uvicorn_runner: Callable[..., Any],
    printer: Optional[Callable[[str], None]] = None,
    app_target: str = "main:app",
) -> None:
    """Run backend server with stable startup banner and uvicorn args."""
    configure_utf8_console()
    printer = printer or safe_print
    app_config = config_loader()
    display_host = "127.0.0.1" if app_config.server.host in {"0.0.0.0", "::"} else app_config.server.host

    printer("🌟 启动 Sage Multi-Agent Framework 服务器")
    printer(f"🏠 本机地址: http://{display_host}:{app_config.server.port}")
    printer(f"📚 API文档: http://{display_host}:{app_config.server.port}/docs")
    printer(f"🔄 热重载: {'开启' if app_config.server.reload else '关闭'}")

    uvicorn_runner(
        app_target,
        host=app_config.server.host,
        port=app_config.server.port,
        reload=app_config.server.reload,
        log_level=app_config.server.log_level,
    )
