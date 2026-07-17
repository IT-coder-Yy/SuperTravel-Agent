import os
import time
from typing import Any, Dict
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP


API_ROOT = "https://api.unsplash.com"
CACHE_TTL_SECONDS = 1800
mcp = FastMCP("Unsplash Travel Images")
_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}


def _headers() -> Dict[str, str]:
    key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
    if not key:
        raise RuntimeError("UNSPLASH_ACCESS_KEY_NOT_CONFIGURED")
    return {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}


def _referral_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    app_name = os.getenv("UNSPLASH_APP_NAME", "supertravelagent").strip() or "supertravelagent"
    return f"{url}{separator}utm_source={app_name}&utm_medium=referral"


def _public_photo(photo: Dict[str, Any], remaining: str | None = None) -> Dict[str, Any]:
    user = photo.get("user") or {}
    links = photo.get("links") or {}
    urls = photo.get("urls") or {}
    return {
        "photo_id": str(photo.get("id") or ""),
        "url": urls.get("regular") or urls.get("full") or "",
        "width": photo.get("width"),
        "height": photo.get("height"),
        "alt": photo.get("alt_description") or photo.get("description") or "",
        "photographer_name": user.get("name") or "Unsplash photographer",
        "photographer_url": _referral_url(user.get("links", {}).get("html") or "https://unsplash.com"),
        "unsplash_url": _referral_url(links.get("html") or "https://unsplash.com"),
        "download_location": links.get("download_location") or "",
        "rate_limit_remaining": remaining,
    }


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.request(method, f"{API_ROOT}{path}", headers=_headers(), **kwargs)
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After") or "60"
        raise RuntimeError(f"UNSPLASH_RATE_LIMITED_RETRY_AFTER_{retry_after}")
    response.raise_for_status()
    return response


@mcp.tool(description="搜索适合作为目的地展示的 Unsplash 图片。")
async def unsplash_search_photos(query: str, per_page: int = 8) -> Dict[str, Any]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return {"photos": [], "status": "needs_query"}
    count = max(1, min(int(per_page), 12))
    cache_key = f"{normalized_query.casefold()}:{count}"
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    response = await _request("GET", "/search/photos", params={
        "query": normalized_query, "per_page": count, "orientation": "landscape", "content_filter": "high",
    })
    remaining = response.headers.get("X-Ratelimit-Remaining")
    payload = response.json()
    result = {"status": "ready", "photos": [_public_photo(item, remaining) for item in payload.get("results", [])]}
    _cache[cache_key] = (time.monotonic(), result)
    return result


@mcp.tool(description="获取指定 Unsplash 图片的完整信息。")
async def unsplash_get_photo(photo_id: str) -> Dict[str, Any]:
    response = await _request("GET", f"/photos/{str(photo_id or '').strip()}")
    return {"status": "ready", "photo": _public_photo(response.json(), response.headers.get("X-Ratelimit-Remaining"))}


@mcp.tool(description="上报图片被选作封面或下载内容的使用事件。")
async def unsplash_track_download(download_location: str) -> Dict[str, Any]:
    parsed = urlparse(str(download_location or ""))
    if parsed.scheme != "https" or parsed.netloc != "api.unsplash.com" or not parsed.path.startswith("/photos/"):
        raise ValueError("UNSPLASH_DOWNLOAD_LOCATION_NOT_ALLOWED")
    response = await _request("GET", parsed.path + (f"?{parsed.query}" if parsed.query else ""))
    payload = response.json()
    return {"status": "tracked", "url": payload.get("url") or ""}


if __name__ == "__main__":
    mcp.run(transport="stdio")
