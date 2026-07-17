import json
import re

from services.file_service import get_output_root_path


_INTERNAL_RULE_LINE_PATTERN = re.compile(
    r"(" 
    r"only\s+use\s+tools\s+in\s+the\s+slot"
    r"|start\s+planning\s+now"
    r"|rules\s+parsed"
    r"|last\s+updated\s+on"
    r"|if\s+there\s+is\s+an\s+empty\s+variable\s+such\s+as"
    r"|consider\s+using\s+the\s+tools\s+above"
    r"|please\s+return\s*\{\s*'error'"
    r"|<next_step_description>|</next_step_description>"
    r"|<required_tools>|</required_tools>"
    r"|<expected_output>|</expected_output>"
    r"|<success_criteria>|</success_criteria>"
    r"|如果涉及\s*file_write"
    r"|执行工具出错时"
    r"|禁止输出\s*<invoke>"
    r"|tool_call_id"
    r")",
    flags=re.IGNORECASE,
)

_RAW_TOOL_LINE_PATTERN = re.compile(
    r"^\s*(?:\[[a-z0-9_.-]+\]\s*)?(?:tool\s*)?(?:result|results|observation|工具结果)\s*[:：]",
    flags=re.IGNORECASE,
)
_DEBUG_LINE_PATTERN = re.compile(
    r"^\s*(?:traceback\s*\(most recent call last\)|file\s+\".+\",\s+line\s+\d+|"
    r"(?:debug|info|warning|error)\s*[:：]|stack trace\s*[:：])",
    flags=re.IGNORECASE,
)
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[a-zA-Z]:[\\/])[^\s<>\"'`，。；;]+"
)
_UNIX_PATH_PATTERN = re.compile(
    r"(?<![\w])/(?:home|Users|tmp|var|private|mnt|workspace|app|root)(?:/[^\s<>\"'`，。；;]+)+",
    flags=re.IGNORECASE,
)
_SECRET_PATTERN = re.compile(
    r"(?i)(?:bearer\s+|sk-)[a-z0-9._-]{12,}"
)
_INTERNAL_JSON_KEYS = {
    "tool_call_id",
    "tool_name",
    "arguments",
    "server_name",
    "session_id",
    "stdout",
    "stderr",
    "traceback",
    "stack_trace",
}

_INTERNAL_PRESENTATION_KEYS = _INTERNAL_JSON_KEYS | {
    "source_tool",
    "tool_result",
    "raw_result",
    "raw_response",
    "raw_payload",
    "mcp_server",
}

_SOURCE_LABELS = {
    "serper_site_search": "联网搜索",
    "serper_web_search": "联网搜索",
    "web_search": "联网搜索",
    "xhs_search_and_summarize": "小红书公开笔记",
    "xhs-mcp": "小红书公开笔记",
    "xiaohongshu": "小红书公开笔记",
    "baidu-map": "地图地点核验",
    "map_geocode": "地图地点核验",
    "trip_plan_rules": "行程可行性校验",
    "seed_fallback": "本地旅行知识",
    "filesystem": "本地文档服务",
    "12306": "铁路票务平台",
    "12306-mcp": "铁路票务平台",
    "query_12306_realtime_tickets": "铁路票务查询",
    "map_place_details": "地图地点详情",
    "search_image_from_web": "公开图片检索",
}

_INTERNAL_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name in sorted(_SOURCE_LABELS, key=len, reverse=True)) + r")\b",
    flags=re.IGNORECASE,
)


def _json_contains_internal_fields(value) -> bool:
    if isinstance(value, dict):
        if {str(key).lower() for key in value} & _INTERNAL_JSON_KEYS:
            return True
        return any(_json_contains_internal_fields(item) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_internal_fields(item) for item in value)
    return False


def _remove_internal_json_fence(match: re.Match) -> str:
    body = match.group("body").strip()
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return match.group(0)
    return "" if _json_contains_internal_fields(parsed) else match.group(0)


def _source_label(value: str) -> str:
    normalized = value.strip().lower()
    return _SOURCE_LABELS.get(normalized, value)


def _replace_internal_identifier(match: re.Match) -> str:
    return _source_label(match.group(0))


def sanitize_user_visible_text(text: str) -> str:
    """Clean tool traces and local path leakage from model-visible text."""
    if not isinstance(text, str) or not text:
        return ""

    cleaned = re.sub(
        r"```(?:json)?\s*(?P<body>[\s\S]*?)```",
        _remove_internal_json_fence,
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"<invoke>[\s\S]*?</invoke>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?(invoke|write_file|path|content)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfile_write\s*\([^)]*\)", "", cleaned, flags=re.IGNORECASE)

    filtered_lines = []
    output_root_str = str(get_output_root_path()).replace("\\", "/").lower()
    for line in cleaned.splitlines():
        low = line.lower().replace("\\", "/")
        stripped_low = low.strip()

        if _INTERNAL_RULE_LINE_PATTERN.search(stripped_low):
            continue
        if _RAW_TOOL_LINE_PATTERN.search(stripped_low):
            continue
        if _DEBUG_LINE_PATTERN.search(stripped_low):
            continue

        if output_root_str and output_root_str in low:
            continue
        safe_line = _WINDOWS_PATH_PATTERN.sub("[内部路径已隐藏]", line)
        safe_line = _UNIX_PATH_PATTERN.sub("[内部路径已隐藏]", safe_line)
        safe_line = _SECRET_PATTERN.sub("[敏感凭据已隐藏]", safe_line)
        filtered_lines.append(safe_line)

    cleaned = "\n".join(filtered_lines)
    cleaned = _INTERNAL_IDENTIFIER_PATTERN.sub(_replace_internal_identifier, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if cleaned.startswith(("{", "[")):
        try:
            parsed = json.loads(cleaned)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if parsed is not None and _json_contains_internal_fields(parsed):
            return ""
    return cleaned


def sanitize_user_visible_payload(value):
    """Remove internal fields and labels from structured data sent to the UI."""
    if isinstance(value, list):
        return [sanitize_user_visible_payload(item) for item in value]
    if not isinstance(value, dict):
        return sanitize_user_visible_text(value) if isinstance(value, str) else value

    sanitized = {}
    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if normalized_key in _INTERNAL_PRESENTATION_KEYS:
            continue
        if normalized_key in {"source", "source_name"} and isinstance(item, str):
            sanitized[key] = _source_label(sanitize_user_visible_text(item))
        else:
            sanitized[key] = sanitize_user_visible_payload(item)
    return sanitized
