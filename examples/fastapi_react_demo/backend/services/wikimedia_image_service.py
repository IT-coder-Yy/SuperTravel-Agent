from __future__ import annotations

import html
import hashlib
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

import requests


_WIKIPEDIA_API = "https://zh.wikipedia.org/w/api.php"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_PUBLIC_DOMAIN_LICENSES = {"cc0", "public domain", "pdm"}
_ATTRIBUTED_LICENSE_PREFIXES = ("cc by", "cc-by")
_PLACE_TITLE_SUFFIXES = (
    "国家级风景名胜区",
    "风景名胜区",
    "旅游度假区",
    "旅游景区",
    "风景区",
    "景区",
)


def _plain_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _metadata_value(metadata: Dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    return _plain_text(value)


def _first_page(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    pages = payload.get("query", {}).get("pages", [])
    if isinstance(pages, dict):
        pages = list(pages.values())
    return next((page for page in pages if isinstance(page, dict)), {})


def _pages(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    pages = payload.get("query", {}).get("pages", [])
    if isinstance(pages, dict):
        pages = list(pages.values())
    return [page for page in pages if isinstance(page, dict)] if isinstance(pages, list) else []


def _normalize_title(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "")).casefold()


def _place_title_candidates(place_name: str) -> List[str]:
    """Build conservative Wikipedia title aliases without broadening to generic terms."""
    original = str(place_name or "").strip()
    if not original:
        return []
    candidates = [original]
    without_brackets = re.sub(r"[（(][^）)]*[）)]", "", original).strip()
    if without_brackets and without_brackets not in candidates:
        candidates.append(without_brackets)
    for suffix in _PLACE_TITLE_SUFFIXES:
        if without_brackets.endswith(suffix):
            stripped = without_brackets[: -len(suffix)].strip()
            if len(_normalize_title(stripped)) >= 2 and stripped not in candidates:
                candidates.append(stripped)
            break
    return candidates


def fetch_wikipedia_place_coordinates(
    places: Iterable[tuple[str, str]],
    *,
    timeout_seconds: float = 12.0,
) -> Dict[str, Dict[str, Any]]:
    """Resolve exact place articles to stable WGS84 coordinates in one request."""

    if os.getenv("WIKIMEDIA_COORDINATES_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return {}
    unique_places = list(dict.fromkeys(
        (str(name).strip(), str(city).strip())
        for name, city in places
        if str(name).strip()
    ))[:40]
    if not unique_places:
        return {}
    candidates_by_name = {
        name: _place_title_candidates(name)
        for name, _city in unique_places
    }
    lookup_titles = list(dict.fromkeys(
        title
        for name, _city in unique_places
        for title in candidates_by_name[name]
    ))[:50]
    headers = {"User-Agent": "SuperTravelAgent/1.0 (travel itinerary coordinate lookup)"}
    try:
        response = requests.get(
            _WIKIPEDIA_API,
            params={
                "action": "query",
                "titles": "|".join(lookup_titles),
                "prop": "coordinates|info|pageprops",
                "inprop": "url",
                "ppprop": "wikibase_item",
                "redirects": 1,
                "converttitles": 1,
                "format": "json",
                "formatversion": 2,
            },
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        pages = _pages(payload)
    except (requests.RequestException, ValueError, TypeError):
        return {}

    title_aliases: Dict[str, set[str]] = {}
    query = payload.get("query", {}) if isinstance(payload, dict) else {}
    for relation_name in ("converted", "redirects"):
        relations = query.get(relation_name, []) if isinstance(query, dict) else []
        for relation in relations if isinstance(relations, list) else []:
            if not isinstance(relation, dict):
                continue
            source = _normalize_title(relation.get("from"))
            target = _normalize_title(relation.get("to"))
            if source and target:
                title_aliases.setdefault(source, set()).add(target)
                title_aliases.setdefault(target, set()).add(source)

    wikidata_pages = {
        str(page.get("pageprops", {}).get("wikibase_item") or "").strip(): page
        for page in pages
        if not page.get("coordinates")
        and isinstance(page.get("pageprops"), dict)
        and str(page["pageprops"].get("wikibase_item") or "").strip()
    }
    if wikidata_pages:
        try:
            entity_response = requests.get(
                _WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(wikidata_pages),
                    "props": "claims",
                    "format": "json",
                },
                headers=headers,
                timeout=timeout_seconds,
            )
            entity_response.raise_for_status()
            entities = entity_response.json().get("entities", {})
            for entity_id, page in wikidata_pages.items():
                entity = entities.get(entity_id, {}) if isinstance(entities, dict) else {}
                claims = entity.get("claims", {}) if isinstance(entity, dict) else {}
                coordinate_claims = claims.get("P625", []) if isinstance(claims, dict) else []
                coordinate_value = (
                    coordinate_claims[0].get("mainsnak", {}).get("datavalue", {}).get("value", {})
                    if isinstance(coordinate_claims, list) and coordinate_claims
                    else {}
                )
                if isinstance(coordinate_value, dict):
                    page["coordinates"] = [{
                        "lat": coordinate_value.get("latitude"),
                        "lon": coordinate_value.get("longitude"),
                    }]
                    page["wikidata_coordinate_id"] = entity_id
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            pass

    def equivalent_title(article_title: Any, candidates: set[str]) -> bool:
        pending = [_normalize_title(article_title)]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if not current or current in visited:
                continue
            if current in candidates:
                return True
            visited.add(current)
            pending.extend(title_aliases.get(current, set()) - visited)
        return False

    resolved: Dict[str, Dict[str, Any]] = {}
    for name, city in unique_places:
        normalized_candidates = {
            _normalize_title(candidate)
            for candidate in candidates_by_name[name]
            if _normalize_title(candidate)
        }
        article = next(
            (
                page
                for page in pages
                if equivalent_title(page.get("title"), normalized_candidates)
                and isinstance(page.get("coordinates"), list)
                and page["coordinates"]
            ),
            None,
        )
        if article is None:
            continue
        coordinate = article["coordinates"][0]
        try:
            latitude = float(coordinate["lat"])
            longitude = float(coordinate["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            continue
        wikidata_id = str(article.get("wikidata_coordinate_id") or "").strip()
        page_id = wikidata_id or str(article.get("pageid") or hashlib.sha1(name.encode("utf-8")).hexdigest()[:16])
        resolved[name] = {
            "name": name,
            "city": city,
            "poi_id": f"{'wikidata' if wikidata_id else 'wikipedia'}_{page_id}",
            "provider_place_id": page_id,
            "lat": latitude,
            "lng": longitude,
            "coordinate_system": "WGS84",
            "coordinates_trusted": True,
            "coordinate_source": "wikidata_p625" if wikidata_id else "wikipedia_article",
            "source": "Wikidata" if wikidata_id else "Wikipedia",
            "source_provider": "wikidata" if wikidata_id else "wikipedia",
            "source_url": (
                f"https://www.wikidata.org/wiki/{wikidata_id}"
                if wikidata_id
                else str(article.get("fullurl") or "")
            ),
            "source_reference_id": f"source_{'wikidata' if wikidata_id else 'wikipedia'}_{page_id}",
            "data_type": "reference_data",
        }
    return resolved


def _build_asset(
    place: str,
    filename: str,
    image_page: Dict[str, Any],
    article_url: str = "",
) -> Dict[str, Any]:
    image_infos = image_page.get("imageinfo")
    image_info = image_infos[0] if isinstance(image_infos, list) and image_infos and isinstance(image_infos[0], dict) else {}
    metadata = image_info.get("extmetadata") if isinstance(image_info.get("extmetadata"), dict) else {}
    mime_type = str(image_info.get("mime") or "").strip().lower()
    image_url = str(image_info.get("url") or "").strip()
    description_url = str(image_info.get("descriptionurl") or article_url or "").strip()
    license_name = _metadata_value(metadata, "LicenseShortName").casefold()
    public_domain = license_name in _PUBLIC_DOMAIN_LICENSES
    attributed = license_name.startswith(_ATTRIBUTED_LICENSE_PREFIXES)
    if (
        mime_type not in _SUPPORTED_MIME_TYPES
        or not image_url.startswith("https://upload.wikimedia.org/")
        or not description_url.startswith("https://commons.wikimedia.org/")
        or not (public_domain or attributed)
    ):
        return {}

    artist = _metadata_value(metadata, "Artist") or "Wikimedia Commons contributor"
    stable_digest = hashlib.sha1(f"{place}|{filename}".encode("utf-8")).hexdigest()[:16]
    image_digest = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:16]
    source_ref = f"source_image_wikimedia_{stable_digest}"
    return {
        "image_id": f"img_wikimedia_{image_digest}",
        "url": image_url,
        "alt": f"{place}参考图片",
        "mime_type": mime_type,
        "width": image_info.get("width"),
        "height": image_info.get("height"),
        "display_allowed": True,
        "export_allowed": public_domain,
        "attribution_required": not public_domain,
        "attribution_text": None if public_domain else artist,
        "attribution_url": None if public_domain else description_url,
        "provider_name": "Wikimedia Commons",
        "provider_url": description_url,
        "source_ref": source_ref,
        "source": {
            "reference_id": source_ref,
            "type": "image",
            "title": f"{place}活动图片",
            "source": "Wikimedia Commons",
            "url": description_url,
            "snippet": f"{license_name.upper() or '公共领域'}；作者：{artist}",
            "data_type": "reference_data",
            "related_fields": ["images"],
            "related_places": [place],
        },
    }


@lru_cache(maxsize=512)
def fetch_wikimedia_activity_image(place_name: str, city: str, timeout_seconds: float = 6.0) -> Dict[str, Any]:
    """Return one licence-aware Wikimedia image for an exact place article."""
    if os.getenv("WIKIMEDIA_IMAGES_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return {}
    place = str(place_name or "").strip()
    if not place:
        return {}
    headers = {"User-Agent": "SuperTravelAgent/1.0 (travel itinerary image lookup)"}
    search_response = requests.get(
        _WIKIPEDIA_API,
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": f'\"{place}\" {str(city or "").strip()}',
            "gsrnamespace": 0,
            "gsrlimit": 3,
            "prop": "pageimages|info",
            "piprop": "name",
            "inprop": "url",
            "format": "json",
            "formatversion": 2,
        },
        headers=headers,
        timeout=timeout_seconds,
    )
    search_response.raise_for_status()
    article = _first_page(search_response.json())
    filename = str(article.get("pageimage") or "").strip()
    article_title = str(article.get("title") or "").strip()
    normalized_place = re.sub(r"\W+", "", place).casefold()
    normalized_title = re.sub(r"\W+", "", article_title).casefold()
    if not filename or not normalized_title or normalized_place not in normalized_title:
        return {}

    image_response = requests.get(
        _COMMONS_API,
        params={
            "action": "query",
            "titles": f"File:{filename}",
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "format": "json",
            "formatversion": 2,
        },
        headers=headers,
        timeout=timeout_seconds,
    )
    image_response.raise_for_status()
    image_page = _first_page(image_response.json())
    return _build_asset(place, filename, image_page, str(article.get("fullurl") or ""))


def fetch_wikimedia_activity_images(
    places: Iterable[tuple[str, str]],
    *,
    max_workers: int = 4,
    timeout_seconds: float = 15.0,
) -> Dict[str, Dict[str, Any]]:
    del max_workers  # Kept for call compatibility; exact-title lookup is batched.
    if os.getenv("WIKIMEDIA_IMAGES_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return {}
    unique_names = list(dict.fromkeys(str(name).strip() for name, _city in places if str(name).strip()))[:50]
    if not unique_names:
        return {}
    headers = {"User-Agent": "SuperTravelAgent/1.0 (travel itinerary image lookup)"}
    try:
        title_candidates_by_name = {
            name: _place_title_candidates(name)
            for name in unique_names
        }
        lookup_titles = list(dict.fromkeys(
            title
            for name in unique_names
            for title in title_candidates_by_name[name]
        ))[:50]
        article_response = requests.get(
            _WIKIPEDIA_API,
            params={
                "action": "query",
                "titles": "|".join(lookup_titles),
                "prop": "pageimages|info",
                "piprop": "name",
                "inprop": "url",
                "redirects": 1,
                "format": "json",
                "formatversion": 2,
            },
            headers=headers,
            timeout=timeout_seconds,
        )
        article_response.raise_for_status()
        article_pages = _pages(article_response.json())
        article_by_name: Dict[str, Dict[str, Any]] = {}
        filenames: List[str] = []
        for name in unique_names:
            normalized_candidates = [
                _normalize_title(candidate)
                for candidate in title_candidates_by_name[name]
                if _normalize_title(candidate)
            ]
            article = next(
                (
                    page
                    for page in article_pages
                    if any(
                        normalized_title == candidate
                        or candidate in normalized_title
                        for candidate in normalized_candidates
                        for normalized_title in [_normalize_title(page.get("title"))]
                    )
                ),
                None,
            )
            filename = str((article or {}).get("pageimage") or "").strip()
            if article and filename:
                article_by_name[name] = article
                filenames.append(filename)
        if not filenames:
            return {}
        image_response = requests.get(
            _COMMONS_API,
            params={
                "action": "query",
                "titles": "|".join(f"File:{filename}" for filename in filenames),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "format": "json",
                "formatversion": 2,
            },
            headers=headers,
            timeout=timeout_seconds,
        )
        image_response.raise_for_status()
        image_pages = _pages(image_response.json())
    except (requests.RequestException, ValueError, TypeError):
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    for name, article in article_by_name.items():
        filename = str(article.get("pageimage") or "").strip()
        normalized_filename = _normalize_title(filename)
        image_page: Optional[Dict[str, Any]] = next(
            (
                page for page in image_pages
                if _normalize_title(str(page.get("title") or "").removeprefix("File:")) == normalized_filename
            ),
            None,
        )
        if image_page:
            asset = _build_asset(name, filename, image_page, str(article.get("fullurl") or ""))
            if asset:
                results[name] = asset
    return results
