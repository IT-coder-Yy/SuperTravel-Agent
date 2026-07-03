from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from agents.utils.logger import logger


def build_root_fallback_page() -> HTMLResponse:
    """Return fallback HTML when frontend static build is missing."""
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sage Multi-Agent Framework</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
    </head>
    <body>
        <div id="root"></div>
        <script>
            document.getElementById('root').innerHTML = `
                <div style="text-align: center; padding: 50px; font-family: Arial, sans-serif;">
                    <h1>🧠 Sage Multi-Agent Framework</h1>
                    <p>FastAPI Backend is running successfully!</p>
                    <p>Please build the React frontend first.</p>
                    <div style="margin-top: 30px;">
                        <h3>API Endpoints:</h3>
                        <ul style="list-style: none;">
                            <li>📡 WebSocket: <code>ws://localhost:8001/ws</code></li>
                            <li>🔧 API Docs: <a href="/docs">http://localhost:8001/docs</a></li>
                            <li>⚙️ System Status: <a href="/api/status">http://localhost:8001/api/status</a></li>
                        </ul>
                    </div>
                </div>
            `;
        </script>
    </body>
    </html>
    """)


def serve_root_or_fallback(static_path: Path):
    """Serve built root index if available, otherwise fallback html page."""
    index_path = static_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return build_root_fallback_page()


def serve_spa_or_static(static_path: Path, full_path: str):
    """Serve static assets first, then fallback to SPA index route."""
    if full_path.startswith("api") or full_path in {"docs", "redoc", "openapi.json"}:
        raise HTTPException(status_code=404, detail="Not Found")

    requested_file = (static_path / full_path).resolve()
    try:
        requested_file.relative_to(static_path.resolve())
        if requested_file.exists() and requested_file.is_file():
            return FileResponse(requested_file)
    except Exception:
        pass

    index_path = static_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Frontend build not found")


def serve_root_with_boundary(static_path: Path, logger: Any = logger):
    """Serve root page with unified error boundary semantics."""
    try:
        return serve_root_or_fallback(static_path=static_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"根路径页面服务失败: {e}")
        raise HTTPException(status_code=500, detail=f"根路径页面服务失败: {str(e)}")


def serve_spa_with_boundary(static_path: Path, full_path: str, logger: Any = logger):
    """Serve SPA/static content with unified error boundary semantics."""
    try:
        return serve_spa_or_static(static_path=static_path, full_path=full_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SPA静态回退失败: {e}")
        raise HTTPException(status_code=500, detail=f"SPA静态回退失败: {str(e)}")


def mount_static_assets(app: Any, static_path: Path) -> None:
    """Mount static and assets directories for built frontend files."""
    if not static_path.exists():
        return

    from fastapi.staticfiles import StaticFiles

    assets_path = static_path / "assets"
    if assets_path.exists():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    app.mount("/static", StaticFiles(directory=static_path), name="static")


def mount_static_assets_with_boundary(app: Any, static_path: Path, logger: Any = logger) -> None:
    """Mount static assets with unified startup error logging semantics."""
    try:
        mount_static_assets(app=app, static_path=static_path)
    except Exception as e:
        logger.error(f"静态资源挂载失败: {e}")
        raise
