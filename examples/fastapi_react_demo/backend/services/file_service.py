import mimetypes
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse

from agents.config import get_settings
from agents.utils.logger import logger


def get_output_root_path() -> Path:
    """Resolve output root from shared settings."""
    settings = get_settings()
    output_root = Path(settings.output_root)
    return output_root.expanduser().resolve()


def build_download_response(session_id: str, filename: str) -> FileResponse:
    """Build validated file download response under output root."""
    output_base = get_output_root_path()
    file_path = output_base / session_id / filename

    try:
        file_path.resolve().relative_to(output_base.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="访问被拒绝：文件路径无效")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    encoded_filename = urllib.parse.quote(filename, safe='')
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Access-Control-Allow-Origin": "*",
        },
    )


def build_download_response_safe(session_id: str, filename: str, logger: Any = logger) -> FileResponse:
    """Build download response and preserve existing route-level error semantics."""
    try:
        return build_download_response(session_id=session_id, filename=filename)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载文件失败: {str(e)}")
