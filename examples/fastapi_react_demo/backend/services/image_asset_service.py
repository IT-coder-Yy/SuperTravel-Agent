from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from backend.schemas.trip_v3_models import ImageAssetV3


def _https_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
        quote(parsed.query, safe="!$&'()*+,;=:/?@-._~%"),
        quote(parsed.fragment, safe="!$&'()*+,;=:/?@-._~%"),
    ))


def _unsplash_url(value: Any) -> str:
    text = _https_url(value)
    host = urlparse(text).hostname or ""
    return text if host == "unsplash.com" or host.endswith(".unsplash.com") else ""


def _unsplash_profile_url(value: Any) -> str:
    text = _unsplash_url(value)
    return text if urlparse(text).path.lstrip("/").startswith("@") else ""


def _unsplash_photo_url(value: Any) -> str:
    text = _unsplash_url(value)
    return text if bool(urlparse(text).path.strip("/")) else ""


def build_unsplash_cover_asset(
    *,
    image_id: str,
    url: Any,
    alt: Any,
    photographer_name: Any,
    photographer_url: Any,
    unsplash_url: Any,
    download_location: Any,
    source_ref: Optional[str],
    checked_at: Optional[datetime],
) -> Optional[ImageAssetV3]:
    """Convert a verified Unsplash API result into a web-only image asset.

    Raw search and map image URLs do not enter this Unsplash-specific path;
    private-use external images are validated separately below.
    """
    image_url = _unsplash_url(url)
    photographer_link = _unsplash_profile_url(photographer_url)
    provider_link = _unsplash_photo_url(unsplash_url)
    download_link = _https_url(download_location)
    photographer = str(photographer_name or "").strip()
    if not all((image_id, image_url, photographer, photographer_link, provider_link, source_ref)):
        return None

    return ImageAssetV3(
        image_id=image_id,
        url=image_url,
        alt=str(alt or "").strip(),
        display_allowed=True,
        export_allowed=False,
        attribution_required=True,
        attribution_text=photographer,
        attribution_url=photographer_link,
        provider_name="Unsplash",
        provider_url=provider_link,
        download_location=download_link or None,
        source_ref=source_ref,
        checked_at=checked_at,
    )


def build_external_image_asset(
    *,
    image_id: str,
    url: Any,
    alt: Any,
    provider_name: Any,
    provider_url: Any,
    source_ref: Optional[str],
    checked_at: Optional[datetime],
) -> Optional[ImageAssetV3]:
    """Build a private-use image asset whose copyright status is not asserted.

    The caller must already have matched the image to an exact POI or a strict
    place-name search result. Source metadata is retained even though the user
    has opted to allow display and export without a licensing gate.
    """
    image_url = _https_url(url)
    provider_link = _https_url(provider_url)
    provider = str(provider_name or "公开图片来源").strip()
    if not all((image_id, image_url, source_ref)):
        return None
    suffix = Path(urlparse(image_url).path).suffix.casefold()
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix)
    return ImageAssetV3(
        image_id=image_id,
        url=image_url,
        alt=str(alt or "").strip(),
        mime_type=mime_type,
        display_allowed=True,
        export_allowed=True,
        attribution_required=False,
        provider_name=provider or None,
        provider_url=provider_link or None,
        source_ref=source_ref,
        checked_at=checked_at,
    )


def displayable_images(images: Iterable[ImageAssetV3], *, maximum: int = 3) -> List[ImageAssetV3]:
    """Keep only images with explicit online-display permission, in source order."""
    result: List[ImageAssetV3] = []
    seen_ids: set[str] = set()
    for image in images:
        if len(result) >= max(0, maximum) or image.image_id in seen_ids:
            continue
        if not image.display_allowed or not _https_url(image.url):
            continue
        if image.attribution_required and not (
            image.attribution_text and image.attribution_url and image.provider_name and image.provider_url
        ):
            continue
        seen_ids.add(image.image_id)
        result.append(image)
    return result
