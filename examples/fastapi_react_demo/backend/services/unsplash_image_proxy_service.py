"""Narrow, no-store delivery for display-approved Unsplash images."""

from urllib.parse import urlparse

import httpx


UNSPLASH_IMAGE_HOST = "images.unsplash.com"
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def is_allowed_unsplash_image_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme == "https" and parsed.hostname == UNSPLASH_IMAGE_HOST


async def fetch_unsplash_image(url: str) -> tuple[bytes, str]:
    """Fetch only an Unsplash CDN image without retaining a server-side copy."""
    if not is_allowed_unsplash_image_url(url):
        raise ValueError("UNSPLASH_IMAGE_URL_NOT_ALLOWED")

    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    response.raise_for_status()

    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not media_type.startswith("image/"):
        raise ValueError("UNSPLASH_IMAGE_CONTENT_TYPE_INVALID")
    if len(response.content) > MAX_IMAGE_BYTES:
        raise ValueError("UNSPLASH_IMAGE_TOO_LARGE")
    return response.content, media_type
