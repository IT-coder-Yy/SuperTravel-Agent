import os
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx


async def track_document_cover_download(document: Mapping[str, Any]) -> bool:
    overview = document.get("destination_overview")
    cover = overview.get("cover_image") if isinstance(overview, Mapping) else None
    location = str(cover.get("download_location") or "") if isinstance(cover, Mapping) else ""
    parsed = urlparse(location)
    key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
    if not key or parsed.scheme != "https" or parsed.netloc != "api.unsplash.com" or not parsed.path.startswith("/photos/"):
        return False
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(location, headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"})
        return response.is_success
    except httpx.HTTPError:
        return False
