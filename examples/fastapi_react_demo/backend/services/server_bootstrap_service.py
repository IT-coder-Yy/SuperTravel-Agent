from typing import Any, Callable


def run_backend_server(
    config_loader: Callable[[], Any],
    uvicorn_runner: Callable[..., Any],
    printer: Callable[[str], None] = print,
    app_target: str = "main:app",
) -> None:
    """Run backend server with stable startup banner and uvicorn args."""
    app_config = config_loader()

    printer("🌟 启动 Sage Multi-Agent Framework 服务器")
    printer(f"🏠 地址: http://{app_config.server.host}:{app_config.server.port}")
    printer(f"📚 API文档: http://{app_config.server.host}:{app_config.server.port}/docs")
    printer(f"🔄 热重载: {'开启' if app_config.server.reload else '关闭'}")

    uvicorn_runner(
        app_target,
        host=app_config.server.host,
        port=app_config.server.port,
        reload=app_config.server.reload,
        log_level=app_config.server.log_level,
    )
