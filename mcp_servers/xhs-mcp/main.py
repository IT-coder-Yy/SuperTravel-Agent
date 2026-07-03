import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("XHS MCP")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _env_text(name: str) -> str:
    value = _safe_text(os.getenv(name))
    if re.fullmatch(r"\$\{[A-Z0-9_]+\}", value):
        return ""
    return value


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_note_id(url: str) -> str:
    text = _safe_text(url)
    if not text:
        return ""

    patterns = [
        r"/explore/([a-zA-Z0-9]+)",
        r"/discovery/item/([a-zA-Z0-9]+)",
        r"noteId=([a-zA-Z0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _normalize_like_count(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    if re.match(r"^\d+$", text):
        return text
    number_match = re.search(r"(\d+(?:\.\d+)?)", text)
    if number_match:
        return number_match.group(1)
    return text


def _normalize_official_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    note_card = item.get("note_card") if isinstance(item.get("note_card"), dict) else {}
    user_info = note_card.get("user") if isinstance(note_card.get("user"), dict) else {}
    interact_info = note_card.get("interact_info") if isinstance(note_card.get("interact_info"), dict) else {}

    note_id = _safe_text(item.get("id")) or _safe_text(note_card.get("note_id"))
    if not note_id:
        note_id = _safe_text(note_card.get("id"))

    title = _safe_text(note_card.get("display_title")) or _safe_text(note_card.get("title"))
    desc = _safe_text(note_card.get("desc"))
    author = _safe_text(user_info.get("nickname"))
    liked_count = _normalize_like_count(interact_info.get("liked_count"))

    if not note_id and not title and not desc:
        return None

    url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
    summary = desc or title

    return {
        "note_id": note_id,
        "title": title,
        "author": author,
        "liked_count": liked_count,
        "summary": summary,
        "url": url,
        "source": "xhs_web_api",
    }


def _search_xhs_official_api(
    query: str,
    page: int,
    limit: int,
    sort: str,
    cookie: str,
    timeout: int = 12,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not _safe_text(cookie):
        return [], "XHS_COOKIE is empty"

    endpoint = "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"
    payload: Dict[str, Any] = {
        "keyword": query,
        "page": max(1, page),
        "page_size": max(1, min(limit, 30)),
        "search_id": uuid.uuid4().hex[:20],
        "sort": sort or "general",
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": "https://www.xiaohongshu.com/",
        "Origin": "https://www.xiaohongshu.com",
        "Cookie": cookie,
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if _safe_int(data.get("code"), -1) != 0:
            message = _safe_text(data.get("msg")) or "official api error"
            return [], message

        raw_items = []
        data_block = data.get("data") if isinstance(data.get("data"), dict) else {}
        if isinstance(data_block.get("items"), list):
            raw_items = data_block.get("items")

        normalized: List[Dict[str, Any]] = []
        for raw_item in raw_items:
            item = _normalize_official_item(raw_item)
            if item:
                normalized.append(item)

        return normalized[:limit], None
    except Exception as error:
        return [], str(error)


def _search_with_serper(query: str, limit: int, timeout: int = 12) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    api_key = _env_text("SERPER_API_KEY")
    if not api_key:
        return [], "SERPER_API_KEY is empty"

    endpoint = "https://google.serper.dev/search"
    payload: Dict[str, Any] = {
        "q": f"site:xiaohongshu.com {query}",
        "num": max(1, min(limit, 20)),
        "hl": "zh-cn",
    }
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        organic = data.get("organic") if isinstance(data.get("organic"), list) else []
        if not organic:
            return [], "Serper returned no organic results"

        results: List[Dict[str, Any]] = []
        for item in organic:
            if not isinstance(item, dict):
                continue
            url = _safe_text(item.get("link"))
            if "xiaohongshu.com" not in url:
                continue
            title = _safe_text(item.get("title"))
            snippet = _safe_text(item.get("snippet"))
            results.append(
                {
                    "note_id": _extract_note_id(url),
                    "title": title,
                    "author": "",
                    "liked_count": "",
                    "summary": snippet,
                    "url": url,
                    "source": "serper_site_search",
                }
            )

        if not results:
            return [], f"Serper returned {len(organic)} organic results but none matched xiaohongshu.com"
        return results[:limit], None
    except Exception as error:
        return [], str(error)


def _search_from_xhs_page(query: str, limit: int, timeout: int = 12) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    url = f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.xiaohongshu.com/",
    }

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        text = response.text

        links = re.findall(
            r"https?://www\\.xiaohongshu\\.com/(?:explore|discovery/item)/[a-zA-Z0-9]+",
            text,
            re.IGNORECASE,
        )

        relative_note_ids = re.findall(
            r"/(?:explore|discovery/item)/([a-zA-Z0-9]+)",
            text,
            re.IGNORECASE,
        )
        for note_id in relative_note_ids:
            links.append(f"https://www.xiaohongshu.com/explore/{note_id}")

        dedup = set()
        results: List[Dict[str, Any]] = []
        for link in links:
            normalized_link = link.split("?", 1)[0]
            if normalized_link in dedup:
                continue
            dedup.add(normalized_link)
            note_id = _extract_note_id(normalized_link)
            results.append(
                {
                    "note_id": note_id,
                    "title": "",
                    "author": "",
                    "liked_count": "",
                    "summary": "",
                    "url": normalized_link,
                    "source": "xhs_html_fallback",
                }
            )
            if len(results) >= limit:
                break

        if results:
            return results, None
        return [], "no note links found in html"
    except Exception as error:
        return [], str(error)


def _dedupe_items(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    dedup = set()
    normalized: List[Dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        note_id = _safe_text(item.get("note_id"))
        title = _safe_text(item.get("title"))
        key = (url, note_id, title)
        if key in dedup:
            continue
        dedup.add(key)
        normalized.append(item)
        if len(normalized) >= limit:
            break

    return normalized


def _build_summary(query: str, items: List[Dict[str, Any]], focus: str = "") -> Dict[str, Any]:
    if not items:
        return {
            "query": query,
            "focus": _safe_text(focus),
            "summary": "没有检索到可用的小红书资源，请稍后重试或缩短关键词。",
            "highlights": [],
            "resource_count": 0,
        }

    highlights: List[str] = []
    top_items = items[: min(5, len(items))]
    for idx, item in enumerate(top_items, start=1):
        title = _safe_text(item.get("title")) or "(未提供标题)"
        summary = _safe_text(item.get("summary"))
        author = _safe_text(item.get("author"))
        liked_count = _safe_text(item.get("liked_count"))

        text_parts = [f"{idx}. {title}"]
        if author:
            text_parts.append(f"作者: {author}")
        if liked_count:
            text_parts.append(f"赞藏指标: {liked_count}")
        if summary:
            text_parts.append(f"摘要: {summary}")
        highlights.append(" | ".join(text_parts))

    focus_text = _safe_text(focus)
    if focus_text:
        summary_line = (
            f"已基于查询“{query}”完成小红书检索，并按关注点“{focus_text}”整理了 {len(items)} 条资源。"
        )
    else:
        summary_line = f"已基于查询“{query}”完成小红书检索，共整理 {len(items)} 条资源。"

    return {
        "query": query,
        "focus": focus_text,
        "summary": summary_line,
        "highlights": highlights,
        "resource_count": len(items),
    }


def _search_xhs_resources_internal(
    query: str,
    page: int = 1,
    limit: int = 10,
    sort: str = "general",
    prefer_official_api: bool = True,
) -> Dict[str, Any]:
    normalized_query = _safe_text(query)
    normalized_limit = max(1, min(_safe_int(limit, 10), 20))
    normalized_page = max(1, _safe_int(page, 1))
    normalized_sort = _safe_text(sort) or "general"

    if not normalized_query:
        return {
            "query": "",
            "source": "none",
            "items": [],
            "resource_count": 0,
            "warnings": ["query is empty"],
        }

    warnings: List[str] = []
    all_items: List[Dict[str, Any]] = []
    official_items: List[Dict[str, Any]] = []
    serper_items: List[Dict[str, Any]] = []
    selected_source = "none"

    cookie = _env_text("XHS_COOKIE")

    if prefer_official_api:
        official_items, official_error = _search_xhs_official_api(
            query=normalized_query,
            page=normalized_page,
            limit=normalized_limit,
            sort=normalized_sort,
            cookie=cookie,
        )
        if official_items:
            all_items.extend(official_items)
            selected_source = "xhs_web_api"
        elif official_error:
            warnings.append(f"official_api_failed: {official_error}")

    serper_items, serper_error = _search_with_serper(normalized_query, normalized_limit)
    if serper_items:
        all_items.extend(serper_items)
        selected_source = (
            f"{selected_source}+serper_site_search"
            if selected_source != "none"
            else "serper_site_search"
        )
    elif serper_error:
        warnings.append(f"serper_site_search_failed: {serper_error}")

    if not all_items:
        html_items, html_error = _search_from_xhs_page(normalized_query, normalized_limit)
        if html_items:
            all_items.extend(html_items)
            selected_source = "xhs_html_fallback"
        elif html_error:
            warnings.append(f"html_fallback_failed: {html_error}")

    if official_items and serper_items:
        interleaved_items: List[Dict[str, Any]] = []
        for index in range(max(len(official_items), len(serper_items))):
            if index < len(official_items):
                interleaved_items.append(official_items[index])
            if index < len(serper_items):
                interleaved_items.append(serper_items[index])
        dedup_items = _dedupe_items(interleaved_items, normalized_limit)
    else:
        dedup_items = _dedupe_items(all_items, normalized_limit)

    return {
        "query": normalized_query,
        "source": selected_source,
        "items": dedup_items,
        "resource_count": len(dedup_items),
        "warnings": warnings,
        "meta": {
            "page": normalized_page,
            "limit": normalized_limit,
            "sort": normalized_sort,
            "timestamp": int(time.time()),
        },
    }


@mcp.tool()
def xhs_search_resources(
    query: str,
    page: int = 1,
    limit: int = 10,
    sort: str = "general",
    prefer_official_api: bool = True,
) -> Dict[str, Any]:
    """
    Search Xiaohongshu resources by query.

    The tool tries multiple data sources with graceful fallback:
    1) official Xiaohongshu web API with XHS_COOKIE
    2) serper site search restricted to xiaohongshu.com
    3) Xiaohongshu web HTML link extraction

    Args:
        query: Search keywords from user request.
        page: Page index for official API path. Starts from 1.
        limit: Maximum number of resources to return. Range 1-20.
        sort: Sort strategy for official API path.
        prefer_official_api: Whether to try official API first.

    Returns:
        Structured result with `items`, `resource_count`, `source`, and `warnings`.
    """
    return _search_xhs_resources_internal(
        query=query,
        page=page,
        limit=limit,
        sort=sort,
        prefer_official_api=prefer_official_api,
    )


@mcp.tool()
def xhs_search_and_summarize(
    query: str,
    limit: int = 8,
    focus: str = "",
) -> Dict[str, Any]:
    """
    Search Xiaohongshu resources and produce concise summary for user response.

    Args:
        query: User query in natural language.
        limit: Maximum number of resources to include in summary context.
        focus: Optional focus such as budget, route, outfit, food, etc.

    Returns:
        Summary object containing summary text, highlights and resources.
    """
    search_result = _search_xhs_resources_internal(
        query=query,
        page=1,
        limit=limit,
        sort="general",
        prefer_official_api=True,
    )

    items = search_result.get("items") if isinstance(search_result.get("items"), list) else []
    summary_obj = _build_summary(query=query, items=items, focus=focus)

    return {
        "query": _safe_text(query),
        "focus": _safe_text(focus),
        "source": _safe_text(search_result.get("source")),
        "warnings": search_result.get("warnings", []),
        "summary": summary_obj.get("summary", ""),
        "highlights": summary_obj.get("highlights", []),
        "resource_count": summary_obj.get("resource_count", 0),
        "resources": items,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
