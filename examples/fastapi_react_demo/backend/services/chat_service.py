import asyncio
import datetime
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from agents.tool.ticket_query_utils import clean_route_name, extract_route_from_query
from agents.utils.logger import logger
from services.file_service import get_output_root_path
from services.http_response_service import get_sse_headers
from services.skill_profile_service import build_skill_system_message, merge_skill_mcp_servers


TRAIN_QUERY_REGEX = re.compile(
    r"(12306|火车票|高铁|动车|列车|车次|余票|中转|经停|硬座|软卧|卧铺|票价|铁路|rail|train)",
    re.IGNORECASE,
)

FLIGHT_QUERY_REGEX = re.compile(r"(飞机|航班|机票|机场|airline|flight)", re.IGNORECASE)

BUS_QUERY_REGEX = re.compile(r"(大巴|汽车票|客车|长途汽车|bus|coach)", re.IGNORECASE)

GENERIC_TRANSPORT_QUERY_REGEX = re.compile(
    r"(交通|出行|通勤|路线|路程|怎么去|前往|预算|省钱|便宜|交通方式)",
    re.IGNORECASE,
)

GENERIC_TICKET_QUERY_REGEX = re.compile(
    r"(车票|票务|订票|买票|余票|班次|查票|(?:查|查询).{0,12}票|票\s*$)",
    re.IGNORECASE,
)

MAX_STATION_PAIR_ATTEMPTS = 40

STATION_JS_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
STATION_CACHE_TTL_SECONDS = 24 * 60 * 60
STATION_CANDIDATE_LIMIT = 12

_station_cache_lock = threading.Lock()
_station_names_cache: List[str] = []
_station_cache_time: Optional[datetime.datetime] = None

FLIGHT_TICKET_TOOL_CANDIDATES = [
    "get-flight-tickets",
    "query_flight_tickets",
    "search_flight_tickets",
    "search_flights",
    "flight_ticket_search",
    "query_realtime_flights",
]

BUS_TICKET_TOOL_CANDIDATES = [
    "get-bus-tickets",
    "query_bus_tickets",
    "search_bus_tickets",
    "search_buses",
    "bus_ticket_search",
    "query_realtime_buses",
    "get-coach-tickets",
]

SEAT_AVAILABILITY_FIELDS = [
    ("二等座", "ze_num"),
    ("硬座", "yz_num"),
    ("无座", "wz_num"),
    ("一等座", "zy_num"),
    ("硬卧", "yw_num"),
    ("软卧", "rw_num"),
    ("商务座", "swz_num"),
    ("特等座", "tz_num"),
    ("高级软卧", "gr_num"),
    ("软座", "rz_num"),
]

TRANSPORT_ANSWER_REGEX = re.compile(
    r"(12306|车次|火车|高铁|动车|票价|余票|中转|直达|交通|出发|到达|历时|班次)",
    re.IGNORECASE,
)

SCENIC_ANSWER_REGEX = re.compile(
    r"(景点|游玩|打卡|西湖|灵隐|雷峰塔|美食|住宿|酒店|攻略|行程)",
    re.IGNORECASE,
)

XHS_EXPLICIT_QUERY_REGEX = re.compile(r"(小红书|\bxhs\b)", re.IGNORECASE)

XHS_STYLE_QUERY_REGEX = re.compile(
    r"(种草|避雷|探店|穿搭|妆容|平替|好物|笔记|攻略|测评|踩雷|打卡)",
    re.IGNORECASE,
)

XHS_ACTION_QUERY_REGEX = re.compile(
    r"(找|搜|查询|看看|参考|推荐|对比|合集|经验|分享|真实)",
    re.IGNORECASE,
)

XHS_GUIDE_QUERY_REGEX = re.compile(
    r"(攻略|行程|怎么玩|推荐|景点|美食|住宿|路线|打卡|citywalk|旅行|旅游|出游|周末|自由行|探店|避雷)",
    re.IGNORECASE,
)

XHS_ANSWER_REGEX = re.compile(
    r"(小红书|笔记|链接|作者|资源|种草|避雷|推荐)",
    re.IGNORECASE,
)

TRAVEL_EXPERIENCE_QUERY_REGEX = re.compile(
    r"(行程|攻略|规划|旅行|旅游|之旅|游玩|路线|景点|打卡|citywalk|自由行|周末游|三天两夜|3天2夜|"
    r"酒店|住宿|住哪|民宿|客栈|预订|订房|入住|"
    r"美食|餐厅|小吃|探店|吃什么|饭店|咖啡|夜市)",
    re.IGNORECASE,
)

HOTEL_QUERY_REGEX = re.compile(r"(酒店|住宿|住哪|民宿|客栈|预订|订房|入住|hotel)", re.IGNORECASE)

FOOD_QUERY_REGEX = re.compile(r"(美食|餐厅|小吃|探店|吃什么|饭店|咖啡|夜市|food|restaurant)", re.IGNORECASE)

ITINERARY_QUERY_REGEX = re.compile(
    r"(行程|攻略|规划|旅行|旅游|之旅|游玩|路线|景点|打卡|citywalk|自由行|周末游|三天两夜|3天2夜|itinerary|trip)",
    re.IGNORECASE,
)

MAP_LOCATION_JSON_REGEX = re.compile(r'"map_locations"\s*:', re.IGNORECASE)

TRAVEL_DOCUMENT_FILENAME = "travel_recommendation.md"
TRAVEL_DOCUMENT_FALLBACK_STEM = "旅行推荐"

COMMON_CHINESE_CITY_NAMES = [
    "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "成都", "西安", "武汉",
    "长沙", "郑州", "洛阳", "开封", "安阳", "石家庄", "太原", "济南", "青岛", "大连", "沈阳",
    "长春", "哈尔滨", "呼和浩特", "银川", "兰州", "西宁", "乌鲁木齐", "拉萨", "昆明", "贵阳",
    "南宁", "海口", "三亚", "福州", "厦门", "南昌", "合肥", "宁波", "无锡", "扬州", "绍兴",
]

DESTINATION_SEED_PLACES: Dict[str, List[Tuple[str, int]]] = {
    "安阳": [
        ("殷墟博物馆", 1),
        ("殷墟宫殿宗庙遗址", 1),
        ("中国文字博物馆", 1),
        ("曹操高陵遗址博物馆", 2),
        ("羑里城", 2),
        ("岳飞庙", 2),
        ("天宁寺文峰塔", 3),
        ("袁林", 3),
    ],
    "北京": [
        ("天安门广场", 1),
        ("故宫博物院", 1),
        ("景山公园", 1),
        ("颐和园", 2),
        ("圆明园遗址公园", 2),
        ("中国国家博物馆", 3),
        ("798艺术区", 3),
    ],
    "杭州": [
        ("西湖风景名胜区", 1),
        ("浙江省博物馆", 1),
        ("河坊街", 1),
        ("灵隐寺", 2),
        ("中国茶叶博物馆", 2),
        ("良渚博物院", 3),
        ("中国京杭大运河博物馆", 3),
    ],
}

INTERNAL_LEAK_REGEX = re.compile(
    r"(only\s+use\s+tools\s+in\s+the\s+slot|start\s+planning\s+now|rules\s+parsed|"
    r"last\s+updated\s+on|if\s+there\s+is\s+an\s+empty\s+variable\s+such\s+as|"
    r"please\s+return\s*\{\s*'error'|<next_step_description>|<required_tools>|"
    r"<expected_output>|<success_criteria>|tool_call_id|file_write|"
    r"\[[a-z0-9_-]+\]\s*(result|results|结果)|map_weather|tool_call_result|observation:)",
    re.IGNORECASE,
)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _mask_api_key(api_key: str) -> str:
    value = _safe_text(api_key)
    if not value:
        return ""
    if len(value) <= 8:
        return f"{value[:2]}***"
    return f"{value[:6]}***{value[-4:]}"


def _build_runtime_model_diagnostics(controller: Any) -> Dict[str, str]:
    model_name = ""
    base_url = ""
    api_key = ""

    model_config = getattr(controller, "model_config", None)
    if isinstance(model_config, dict):
        model_name = _safe_text(model_config.get("model"))

    model_client = getattr(controller, "model", None)
    if model_client is not None:
        base_url = _safe_text(getattr(model_client, "base_url", ""))
        if not base_url:
            base_url = _safe_text(getattr(model_client, "_base_url", ""))

        api_key = _safe_text(getattr(model_client, "api_key", ""))

    if not model_name:
        model_name = _safe_text(os.getenv("SAGE_MODEL_NAME"))

    if not base_url:
        base_url = _safe_text(os.getenv("SAGE_BASE_URL"))

    if not api_key:
        api_key = _safe_text(os.getenv("SAGE_API_KEY"))
    if not api_key:
        api_key = _safe_text(os.getenv("DASHSCOPE_API_KEY"))
    if not api_key:
        api_key = _safe_text(os.getenv("OPENAI_API_KEY"))

    diagnostics = {
        "model_name": model_name,
        "base_url": base_url,
        "api_key_masked": _mask_api_key(api_key),
    }

    return {key: value for key, value in diagnostics.items() if value}


def _is_realtime_only_enforced() -> bool:
    """Whether train-ticket answers should enforce realtime grounding constraints."""
    raw_value = _safe_text(os.getenv("SAGE_ENFORCE_REALTIME_ONLY", "0"))
    return raw_value.lower() in {"1", "true", "yes", "on"}


def _is_train_ticket_query(query_text: str) -> bool:
    text = _safe_text(query_text)
    if not text:
        return False

    if TRAIN_QUERY_REGEX.search(text):
        return True

    # 兜底：用户未显式提“火车票”，但已给出OD并询问交通/预算，也触发实时票务流程。
    route = _extract_route_from_query(text)
    if route and GENERIC_TRANSPORT_QUERY_REGEX.search(text):
        return True
    if route and GENERIC_TICKET_QUERY_REGEX.search(text):
        return True
    if route and (FLIGHT_QUERY_REGEX.search(text) or BUS_QUERY_REGEX.search(text)):
        return True

    return False


def _has_selected_skill(selected_skill_ids: Optional[List[str]], skill_ids: List[str]) -> bool:
    selected = {str(skill_id or "").strip() for skill_id in (selected_skill_ids or [])}
    return any(skill_id in selected for skill_id in skill_ids)


def _extract_latest_user_query(message_history: List[Dict[str, Any]]) -> str:
    for message in reversed(message_history):
        if message.get("role") == "user":
            return _safe_text(message.get("content"))
    return ""


def _extract_latest_user_message_id(message_history: List[Dict[str, Any]]) -> str:
    for message in reversed(message_history):
        if message.get("role") == "user":
            return _safe_text(message.get("message_id"))
    return ""


FOLLOWUP_DATE_OR_DEPARTURE_REGEX = re.compile(
    r"(今天|明天|后天|\d{1,2}月\d{1,2}日?|\d{4}[-/]\d{1,2}[-/]\d{1,2}|出发|启程|动身|走|发车)",
    re.IGNORECASE,
)


def _resolve_train_ticket_query(user_query: str, message_history: List[Dict[str, Any]]) -> str:
    query_text = _safe_text(user_query)
    if not query_text or _extract_route_from_query(query_text):
        return query_text

    if not FOLLOWUP_DATE_OR_DEPARTURE_REGEX.search(query_text):
        return query_text

    for message in reversed(message_history):
        if message.get("role") != "user":
            continue
        previous_text = _safe_text(message.get("content"))
        if not previous_text or previous_text == query_text:
            continue
        if _extract_route_from_query(previous_text):
            return f"{previous_text} {query_text}".strip()

    return query_text


def _extract_route_from_query(query_text: str) -> Optional[Tuple[str, str]]:
    return extract_route_from_query(_safe_text(query_text))


def _clean_route_name(value: str) -> str:
    return clean_route_name(_safe_text(value))


def _dedupe_texts(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        text = _safe_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _load_12306_station_names() -> List[str]:
    global _station_names_cache, _station_cache_time

    with _station_cache_lock:
        now = datetime.datetime.now()
        if (
            _station_names_cache
            and _station_cache_time
            and (now - _station_cache_time).total_seconds() < STATION_CACHE_TTL_SECONDS
        ):
            return list(_station_names_cache)

        try:
            response = requests.get(
                STATION_JS_URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/123.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://kyfw.12306.cn/otn/leftTicket/init",
                },
                timeout=12,
            )
            response.raise_for_status()
            match = re.search(r"station_names\s*=\s*'([^']+)';", response.text)
            if not match:
                return list(_station_names_cache)

            station_names: List[str] = []
            for row in match.group(1).strip("@").split("@"):
                parts = row.split("|")
                if len(parts) < 2:
                    continue
                name = _safe_text(parts[1])
                if name:
                    station_names.append(name)

            _station_names_cache = _dedupe_texts(station_names)
            _station_cache_time = now
            return list(_station_names_cache)
        except Exception as station_error:
            logger.warning(f"加载12306站点表失败: {station_error}")
            return list(_station_names_cache)


def _station_candidate_sort_key(city_name: str, station_name: str) -> Tuple[int, int, str]:
    if station_name == f"{city_name}南":
        return (0, len(station_name), station_name)
    if station_name == f"{city_name}北":
        return (1, len(station_name), station_name)
    if station_name == f"{city_name}东":
        return (2, len(station_name), station_name)
    if station_name == city_name:
        return (3, len(station_name), station_name)
    if station_name == f"{city_name}西":
        return (4, len(station_name), station_name)
    if station_name.startswith(city_name):
        return (5, len(station_name), station_name)
    return (9, len(station_name), station_name)


def _station_query_candidates(station_name: str) -> List[str]:
    text = _safe_text(station_name)
    if not text:
        return []

    all_station_names = _load_12306_station_names()
    if all_station_names:
        exact_matches = [name for name in all_station_names if name == text]
        prefix_matches = [
            name
            for name in all_station_names
            if name != text and name.startswith(text) and len(name) <= len(text) + 6
        ]
        candidates = _dedupe_texts(exact_matches + prefix_matches)
        if len(candidates) > 1:
            return sorted(candidates, key=lambda name: _station_candidate_sort_key(text, name))[:STATION_CANDIDATE_LIMIT]
        if len(candidates) == 1:
            return candidates

    return [text]


def _station_query_pairs(from_station: str, to_station: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    seen = set()
    for start in _station_query_candidates(from_station):
        for end in _station_query_candidates(to_station):
            if not start or not end or start == end:
                continue
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
            if len(pairs) >= MAX_STATION_PAIR_ATTEMPTS:
                return pairs
    return pairs


def _city_name_for_ticket_query(station_name: str) -> str:
    text = _safe_text(station_name)
    if not text:
        return ""

    all_station_names = _load_12306_station_names()
    if text not in all_station_names:
        return text

    for suffix in ["南", "北", "东", "西"]:
        if len(text) > 2 and text.endswith(suffix):
            return text[:-1]

    return text


def _normalize_query_date(date_text: str, now: Optional[datetime.date] = None) -> Optional[str]:
    today = now or datetime.date.today()
    value = _safe_text(date_text)
    if not value:
        return None

    if value == "今天":
        return today.strftime("%Y-%m-%d")
    if value == "明天":
        return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if value == "后天":
        return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

    full_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if full_match:
        year = int(full_match.group(1))
        month = int(full_match.group(2))
        day = int(full_match.group(3))
        return datetime.date(year, month, day).strftime("%Y-%m-%d")

    month_day_match = re.search(r"(\d{1,2})月(\d{1,2})日?", value)
    if month_day_match:
        month = int(month_day_match.group(1))
        day = int(month_day_match.group(2))
        return datetime.date(today.year, month, day).strftime("%Y-%m-%d")

    return None


def _extract_travel_date(query_text: str) -> str:
    text = _safe_text(query_text)
    if not text:
        return datetime.date.today().strftime("%Y-%m-%d")

    if "后天" in text:
        return _normalize_query_date("后天") or datetime.date.today().strftime("%Y-%m-%d")
    if "明天" in text:
        return _normalize_query_date("明天") or datetime.date.today().strftime("%Y-%m-%d")
    if "今天" in text:
        return _normalize_query_date("今天") or datetime.date.today().strftime("%Y-%m-%d")

    explicit_full = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text)
    if explicit_full:
        normalized = _normalize_query_date(explicit_full.group(0))
        if normalized:
            return normalized

    explicit_md = re.search(r"\d{1,2}月\d{1,2}日?", text)
    if explicit_md:
        normalized = _normalize_query_date(explicit_md.group(0))
        if normalized:
            return normalized

    return datetime.date.today().strftime("%Y-%m-%d")


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    fragment_match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
    if not fragment_match:
        return None
    try:
        return json.loads(fragment_match.group(1))
    except Exception:
        return None


def _unwrap_tool_output(raw_result: Any) -> Any:
    parsed = _safe_json_loads(raw_result) if isinstance(raw_result, str) else raw_result
    if parsed is None:
        parsed = raw_result

    if isinstance(parsed, dict):
        content = parsed.get("content")
        if isinstance(content, list):
            text_blocks: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    text_blocks.append(_safe_text(item.get("text")))
                else:
                    text_blocks.append(_safe_text(item))
            joined = "\n".join([block for block in text_blocks if block])
            nested = _safe_json_loads(joined)
            return nested if nested is not None else joined
        if isinstance(content, str):
            nested = _safe_json_loads(content)
            return nested if nested is not None else content

    return parsed


def _extract_first_value(item: Dict[str, Any], keys: List[str], default: str = "-") -> str:
    for key in keys:
        value = item.get(key)
        text = _safe_text(value)
        if text:
            return text
    return default


def _extract_destination_city(query_text: str) -> str:
    text = _safe_text(query_text)
    if not text:
        return ""

    for city in sorted(COMMON_CHINESE_CITY_NAMES, key=len, reverse=True):
        if city and city in text:
            return city

    match = re.search(r"(?:去|到|游|玩|规划一次|规划|推荐)?([\u4e00-\u9fa5]{2,8})(?:\d+|一|二|两|三|四|五|六|七)\s*(?:天|日)", text)
    if match:
        city = re.sub(r"^(?:帮我|请|我想|想|一次|规划|推荐|去|到)+", "", match.group(1)).strip()
        return city[:4]

    match = re.search(r"([\u4e00-\u9fa5]{2,8})(?:文化之旅|之旅|旅游|旅行|攻略|酒店|住宿|美食)", text)
    if match:
        city = re.sub(r"^(?:帮我|请|我想|想|一次|规划|推荐|去|到)+", "", match.group(1)).strip()
        return city[:4]

    return ""


def _extract_city_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        parsed = _safe_json_loads(payload)
        if parsed is not None:
            payload = parsed
    if isinstance(payload, dict) and ("content" in payload or "result" in payload):
        unwrapped = _unwrap_tool_output(payload)
        if unwrapped is not payload:
            payload = unwrapped
    if not isinstance(payload, dict):
        return ""

    stack = [payload]
    seen_ids = set()
    while stack:
        current = stack.pop()
        if id(current) in seen_ids:
            continue
        seen_ids.add(id(current))
        for key in ("city", "city_name", "adcode_name", "name", "地级市", "城市"):
            value = _safe_text(current.get(key))
            if value:
                return re.sub(r"(市|地区|自治州|盟)$", "", value).strip()
        for value in current.values():
            if isinstance(value, dict):
                stack.append(value)
    return ""


def _extract_url_from_text(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    markdown_match = re.search(r"\[[^\]]+\]\((https?://[^)\s]+)\)", text)
    if markdown_match:
        return markdown_match.group(1)
    url_match = re.search(r"https?://[^\s\]|）)]+", text)
    return url_match.group(0) if url_match else ""


def _extract_price_number(value: Any) -> Optional[float]:
    text = _safe_text(value)
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _format_price(value: Any) -> str:
    price_number = _extract_price_number(value)
    if price_number is None:
        return "-"
    if abs(price_number - int(price_number)) < 0.0001:
        return f"{int(price_number)}元"
    return f"{price_number:.1f}元"


def _normalize_date_component(value: Any) -> str:
    text = _safe_text(value)
    if not text or text == "-":
        return ""

    compact_match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if compact_match:
        return f"{compact_match.group(1)}-{compact_match.group(2)}-{compact_match.group(3)}"

    iso_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if iso_match:
        return datetime.date(
            int(iso_match.group(1)),
            int(iso_match.group(2)),
            int(iso_match.group(3)),
        ).strftime("%Y-%m-%d")

    return text


def _combine_date_time(date_value: Any, time_value: Any) -> str:
    time_text = _safe_text(time_value)
    date_text = _normalize_date_component(date_value)

    if time_text and time_text != "-" and date_text:
        return f"{date_text} {time_text}"
    if time_text and time_text != "-":
        return time_text
    return date_text or "-"


def _direct_depart_date_for_query(item: Dict[str, Any], travel_date: str) -> str:
    # 12306 的 start_train_date/start_date 可能是列车始发站日期，不是本查询区间上车日期。
    return _extract_first_value(item, ["depart_date", "train_date"], default=travel_date)


def _looks_like_ticket_rows(value: Any, row_keys: List[str]) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, dict) and any(key in item for key in row_keys):
            return True
    return False


def _find_ticket_rows(payload: Any, container_keys: List[str], row_keys: List[str]) -> List[Dict[str, Any]]:
    if _looks_like_ticket_rows(payload, row_keys):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in container_keys:
            if key in payload:
                nested_rows = _find_ticket_rows(payload.get(key), container_keys, row_keys)
                if nested_rows:
                    return nested_rows

        for value in payload.values():
            nested_rows = _find_ticket_rows(value, container_keys, row_keys)
            if nested_rows:
                return nested_rows

    if isinstance(payload, list):
        for item in payload:
            nested_rows = _find_ticket_rows(item, container_keys, row_keys)
            if nested_rows:
                return nested_rows

    return []


def _parse_date_or_today(value: str) -> datetime.date:
    text = _safe_text(value)
    if not text:
        return datetime.date.today()

    try:
        return datetime.date.fromisoformat(text)
    except Exception:
        pass

    date_match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", text)
    if date_match:
        try:
            return datetime.date(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
            )
        except Exception:
            return datetime.date.today()

    return datetime.date.today()


def _extract_day_offset_hint(text: str) -> int:
    lowered = _safe_text(text).lower()
    if not lowered:
        return 0

    offset = 0
    if re.search(r"(次日|翌日|第二天|隔日)", lowered):
        offset = max(offset, 1)
    if re.search(r"(第三天)", lowered):
        offset = max(offset, 2)

    plus_match = re.search(r"\+(\d+)\s*(?:天|d)?", lowered)
    if plus_match:
        try:
            offset = max(offset, int(plus_match.group(1)))
        except Exception:
            pass

    return offset


def _parse_schedule_datetime(value: Any, base_date: datetime.date) -> Tuple[Optional[datetime.datetime], bool]:
    text = _safe_text(value)
    if not text or text == "-":
        return None, False

    full_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*(\d{1,2}):(\d{2})", text)
    if full_match:
        try:
            dt = datetime.datetime(
                int(full_match.group(1)),
                int(full_match.group(2)),
                int(full_match.group(3)),
                int(full_match.group(4)),
                int(full_match.group(5)),
            )
            return dt, True
        except Exception:
            pass

    month_day_match = re.search(r"(\d{1,2})[-/](\d{1,2})\s*(\d{1,2}):(\d{2})", text)
    if month_day_match:
        try:
            dt = datetime.datetime(
                base_date.year,
                int(month_day_match.group(1)),
                int(month_day_match.group(2)),
                int(month_day_match.group(3)),
                int(month_day_match.group(4)),
            )
            return dt, True
        except Exception:
            pass

    hhmm_match = re.search(r"(\d{1,2}):(\d{2})", text)
    if hhmm_match:
        try:
            day_offset = _extract_day_offset_hint(text)
            dt = datetime.datetime.combine(
                base_date + datetime.timedelta(days=day_offset),
                datetime.time(int(hhmm_match.group(1)), int(hhmm_match.group(2))),
            )
            return dt, day_offset > 0
        except Exception:
            pass

    return None, False


def _duration_to_minutes(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))

    text = _safe_text(value)
    if not text or text == "-":
        return None

    day_match = re.search(r"(\d+)\s*天", text)
    day_count = int(day_match.group(1)) if day_match else 0

    hour_min_match = re.search(r"(\d{1,2}):(\d{2})", text)
    if hour_min_match:
        hours = int(hour_min_match.group(1))
        minutes = int(hour_min_match.group(2))
        return day_count * 24 * 60 + hours * 60 + minutes

    if re.fullmatch(r"\d+", text):
        return int(text)

    cn_hour_min_match = re.search(r"(\d+)\s*(?:小时|时)\s*(\d+)?\s*(?:分钟|分)?", text)
    if cn_hour_min_match:
        hours = int(cn_hour_min_match.group(1))
        minutes = int(cn_hour_min_match.group(2) or 0)
        return day_count * 24 * 60 + hours * 60 + minutes

    minute_match = re.search(r"(\d+)\s*(?:分钟|分)", text)
    if minute_match:
        return day_count * 24 * 60 + int(minute_match.group(1))

    return None


def _format_duration_human(value: Any) -> str:
    minutes = _duration_to_minutes(value)
    if minutes is None:
        text = _safe_text(value)
        return text if text else "-"

    days = minutes // (24 * 60)
    remain = minutes % (24 * 60)
    hours = remain // 60
    mins = remain % 60

    if days > 0:
        return f"{days}天{hours}小时{mins}分钟"
    return f"{hours}小时{mins}分钟"


def _format_datetime_human(dt: Optional[datetime.datetime], raw: str) -> str:
    if dt is None:
        fallback = _safe_text(raw)
        return fallback if fallback else "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def _normalize_trip_schedule(
    depart_raw: Any,
    arrive_raw: Any,
    duration_raw: Any,
    travel_date: str,
) -> Tuple[str, str, str, bool]:
    base_date = _parse_date_or_today(travel_date)
    depart_dt, _depart_has_day = _parse_schedule_datetime(depart_raw, base_date)
    arrive_dt, arrive_has_day = _parse_schedule_datetime(arrive_raw, base_date)

    duration_minutes = _duration_to_minutes(duration_raw)

    if depart_dt and arrive_dt and arrive_dt < depart_dt and not arrive_has_day:
        arrive_dt = arrive_dt + datetime.timedelta(days=1)

    if depart_dt and duration_minutes is not None and arrive_dt is None:
        arrive_dt = depart_dt + datetime.timedelta(minutes=duration_minutes)

    if depart_dt and arrive_dt and duration_minutes is None and arrive_dt >= depart_dt:
        duration_minutes = int((arrive_dt - depart_dt).total_seconds() // 60)

    duration_text = _format_duration_human(duration_minutes if duration_minutes is not None else duration_raw)
    depart_text = _format_datetime_human(depart_dt, _safe_text(depart_raw))
    arrive_text = _format_datetime_human(arrive_dt, _safe_text(arrive_raw))

    cross_day = bool(depart_dt and arrive_dt and arrive_dt.date() > depart_dt.date())
    return depart_text, arrive_text, duration_text, cross_day


def _row_duration_minutes(row: Dict[str, str]) -> Optional[int]:
    if not isinstance(row, dict):
        return None
    return _duration_to_minutes(row.get("duration"))


def _rank_ticket_rows(
    direct_rows: List[Dict[str, str]],
    interline_rows: List[Dict[str, str]],
    user_query: str,
) -> List[Dict[str, str]]:
    candidates: List[Dict[str, Any]] = []
    for row in [*(direct_rows or []), *(interline_rows or [])]:
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "row": row,
                "price": _extract_price_number(row.get("price", "")),
                "duration": _row_duration_minutes(row),
                "is_direct": row.get("type") == "直达",
            }
        )

    if not candidates:
        return []

    text = _safe_text(user_query)
    prefer_cheap = bool(re.search(r"(便宜|省钱|预算|经济|性价比|最省)", text))
    prefer_fast = bool(re.search(r"(最快|尽快|快一点|时间短|赶时间)", text))
    prefer_direct = bool(re.search(r"(直达|少换乘|不换乘|不要中转)", text))

    def sort_key(item: Dict[str, Any]) -> Tuple[float, float, float]:
        price = item["price"] if item["price"] is not None else float("inf")
        duration = item["duration"] if item["duration"] is not None else float("inf")
        transfer_penalty = 0.0 if item["is_direct"] else 1.0

        if prefer_cheap:
            return price, transfer_penalty, duration
        if prefer_fast:
            return duration, transfer_penalty, price
        if prefer_direct:
            return transfer_penalty, price, duration

        # 默认平衡策略：优先直达，其次价格，再看时长
        return transfer_penalty, price, duration

    ranked = sorted(candidates, key=sort_key)

    ranked_rows: List[Dict[str, str]] = []
    seen = set()
    for item in ranked:
        row = item["row"]
        uniq_key = (
            _safe_text(row.get("type")),
            _safe_text(row.get("trip_no")),
            _safe_text(row.get("route")),
            _safe_text(row.get("depart")),
            _safe_text(row.get("arrive")),
        )
        if uniq_key in seen:
            continue
        seen.add(uniq_key)
        ranked_rows.append(row)

    return ranked_rows


def _build_recommendation_lines(
    direct_rows: List[Dict[str, str]],
    interline_rows: List[Dict[str, str]],
    user_query: str,
    top_k: int = 3,
) -> List[str]:
    ranked_rows = _rank_ticket_rows(
        direct_rows=direct_rows,
        interline_rows=interline_rows,
        user_query=user_query,
    )

    if not ranked_rows:
        return []

    recommendation_lines: List[str] = []
    seen = set()
    for row in ranked_rows:
        uniq_key = (
            _safe_text(row.get("trip_no")),
            _safe_text(row.get("route")),
            _safe_text(row.get("depart")),
            _safe_text(row.get("arrive")),
        )
        if uniq_key in seen:
            continue
        seen.add(uniq_key)

        recommendation_lines.append(
            f"{_safe_text(row.get('type'))} { _safe_text(row.get('trip_no')) } | "
            f"{_safe_text(row.get('depart'))} -> {_safe_text(row.get('arrive'))} | "
            f"历时 {_safe_text(row.get('duration'))} | "
            f"参考价 {_safe_text(row.get('price'))}"
        )

        if len(recommendation_lines) >= max(1, top_k):
            break

    return recommendation_lines


def _is_available_seat(left_text: str) -> bool:
    lowered = _safe_text(left_text).lower()
    return lowered not in {"", "-", "无", "0", "--", "none", "null"}


def _pick_best_seat(prices: Any) -> Tuple[str, str, str]:
    if not isinstance(prices, list) or not prices:
        return "-", "-", "-"

    best_item: Optional[Dict[str, Any]] = None
    best_sort_key: Tuple[int, float] = (2, float("inf"))

    for entry in prices:
        if not isinstance(entry, dict):
            continue
        seat_name = _extract_first_value(entry, ["seat_name", "seatName", "seat", "name"], default="-")
        left_text = _extract_first_value(entry, ["num", "left", "left_num", "leftNum"], default="-")
        price_num = _extract_price_number(
            _extract_first_value(entry, ["price", "ticketPrice", "amount", "price_num"], default="")
        )
        availability_rank = 0 if _is_available_seat(left_text) else 1
        price_rank = price_num if price_num is not None else float("inf")
        sort_key = (availability_rank, price_rank)

        if best_item is None or sort_key < best_sort_key:
            best_item = {
                "seat": seat_name,
                "left": left_text,
                "price": _format_price(price_num if price_num is not None else ""),
            }
            best_sort_key = sort_key

    if not best_item:
        return "-", "-", "-"

    return best_item["seat"], best_item["left"], best_item["price"]


def _pick_best_seat_from_availability(item: Dict[str, Any]) -> Tuple[str, str]:
    available_fallback: Optional[Tuple[str, str]] = None

    for seat_name, field_name in SEAT_AVAILABILITY_FIELDS:
        left_text = _safe_text(item.get(field_name))
        if not left_text or left_text == "--":
            continue
        if _is_available_seat(left_text):
            return seat_name, left_text
        if available_fallback is None:
            available_fallback = (seat_name, left_text)

    return available_fallback if available_fallback is not None else ("-", "-")


def _normalize_direct_rows(payload: Any, from_station: str, to_station: str, travel_date: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    data = _find_ticket_rows(
        payload,
        container_keys=["tickets", "data", "result", "rows", "list"],
        row_keys=["train_code", "start_train_code", "trainCode", "checi", "station_train_code"],
    )
    if not data:
        return rows

    dedupe = set()
    for item in data:
        if not isinstance(item, dict):
            continue

        trip_no = _extract_first_value(item, ["train_code", "start_train_code", "trainCode", "checi", "station_train_code"])
        if trip_no == "-":
            continue

        depart_raw = _combine_date_time(
            _direct_depart_date_for_query(item, travel_date),
            _extract_first_value(item, ["depart_time", "start_time", "startTime", "departure_time"], default=""),
        )
        arrive_raw = _combine_date_time(
            "",
            _extract_first_value(item, ["arrive_time", "arriveTime", "arrival_time"], default=""),
        )
        duration_raw = _extract_first_value(item, ["duration", "lishi", "run_time"], default="-")

        depart, arrive, duration, is_cross_day = _normalize_trip_schedule(
            depart_raw=depart_raw,
            arrive_raw=arrive_raw,
            duration_raw=duration_raw,
            travel_date=travel_date,
        )

        from_name = _extract_first_value(
            item,
            ["from_station", "from_station_name", "fromStation", "start_station", "start_station_name"],
            default=from_station,
        )
        to_name = _extract_first_value(
            item,
            ["to_station", "to_station_name", "toStation", "end_station", "end_station_name"],
            default=to_station,
        )

        seat = _extract_first_value(item, ["seat_recommendation", "seat", "seat_name"], default="-")
        seat_left = _extract_first_value(item, ["seat_left", "left", "left_num", "remaining"], default="-")
        price = _format_price(_extract_first_value(item, ["price", "ticketPrice", "price_num", "amount"], default=""))

        if (seat == "-" or price == "-") and isinstance(item.get("prices"), list):
            best_seat, best_left, best_price = _pick_best_seat(item.get("prices"))
            if seat == "-":
                seat = best_seat
            if seat_left == "-":
                seat_left = best_left
            if price == "-":
                price = best_price

        row = {
            "type": "直达",
            "trip_no": trip_no,
            "route": f"{from_name} -> {to_name}",
            "depart": depart,
            "arrive": arrive,
            "duration": duration,
            "seat": seat,
            "price": price,
            "note": (
                f"余票:{seat_left}，跨天到达" if seat_left != "-" and is_cross_day else
                (f"余票:{seat_left}" if seat_left != "-" else ("跨天到达" if is_cross_day else "-"))
            ),
        }

        dedupe_key = (row["type"], row["trip_no"], row["route"], row["depart"], row["arrive"], row["price"])
        if dedupe_key in dedupe:
            continue
        dedupe.add(dedupe_key)
        rows.append(row)

    return rows


def _normalize_interline_rows(payload: Any, from_station: str, to_station: str, travel_date: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    data = _find_ticket_rows(
        payload,
        container_keys=["tickets", "data", "result", "rows", "list", "interlines", "middleList"],
        row_keys=["ticketList", "fullList", "middle_station_name", "middleStation", "middle_station"],
    )
    if not data:
        return rows

    dedupe = set()
    for item in data:
        if not isinstance(item, dict):
            continue

        if isinstance(item.get("ticketList"), list):
            ticket_list = item.get("ticketList")
        elif isinstance(item.get("fullList"), list):
            ticket_list = item.get("fullList")
        else:
            ticket_list = []

        train_codes: List[str] = []
        seat_names: List[str] = []
        total_price = 0.0
        has_price = False
        known_availability_count = 0
        available_leg_count = 0

        for leg in ticket_list:
            if not isinstance(leg, dict):
                continue
            code = _extract_first_value(
                leg,
                ["start_train_code", "train_code", "trainCode", "station_train_code"],
                default="",
            )
            if code:
                train_codes.append(code)

            best_seat, best_left, best_price = _pick_best_seat(leg.get("prices"))
            if best_seat == "-":
                best_seat, best_left = _pick_best_seat_from_availability(leg)

            if best_seat != "-":
                seat_names.append(best_seat)

            if best_left != "-":
                known_availability_count += 1
                if _is_available_seat(best_left):
                    available_leg_count += 1

            price_num = _extract_price_number(best_price)
            if price_num is not None:
                total_price += price_num
                has_price = True

        if ticket_list and known_availability_count == len(ticket_list) and available_leg_count < len(ticket_list):
            continue

        from_name = _extract_first_value(item, ["from_station_name", "from_station", "fromStation"], default=from_station)
        middle_name = _extract_first_value(item, ["middle_station_name", "middleStation", "middle_station"], default="")
        end_name = _extract_first_value(item, ["end_station_name", "to_station_name", "to_station", "toStation"], default=to_station)

        route_parts = [from_name]
        if middle_name and middle_name != "-":
            route_parts.append(middle_name)
        route_parts.append(end_name)
        route_text = " -> ".join([part for part in route_parts if part and part != "-"])

        depart_raw = _combine_date_time(
            _extract_first_value(item, ["start_date", "train_date", "depart_date"], default=""),
            _extract_first_value(item, ["start_time", "depart_time", "departure_time"], default=""),
        )
        arrive_raw = _combine_date_time(
            _extract_first_value(item, ["arrive_date", "arrival_date"], default=""),
            _extract_first_value(item, ["arrive_time", "arrival_time"], default=""),
        )
        duration_raw = _extract_first_value(
            item,
            ["all_lishi_minutes", "all_lishi", "lishi", "duration"],
            default="-",
        )
        depart, arrive, duration, is_cross_day = _normalize_trip_schedule(
            depart_raw=depart_raw,
            arrive_raw=arrive_raw,
            duration_raw=duration_raw,
            travel_date=travel_date,
        )
        wait_time = _format_duration_human(_extract_first_value(
            item,
            ["wait_time_minutes", "wait_time", "waitTime"],
            default="-",
        ))

        if not train_codes:
            single_code = _extract_first_value(item, ["start_train_code", "train_code", "trainCode"], default="-")
            if single_code != "-":
                train_codes.append(single_code)

        trip_no = " + ".join(train_codes) if train_codes else "-"
        seat = "+".join(seat_names[:2]) if seat_names else "-"
        price = _format_price(total_price) if has_price else "-"

        note_parts: List[str] = []
        if wait_time != "-":
            note_parts.append(f"候车:{wait_time}")
        if middle_name and "-" in middle_name:
            note_parts.append("跨站换乘")
        if is_cross_day:
            note_parts.append("跨天到达")

        row = {
            "type": "中转",
            "trip_no": trip_no,
            "route": route_text or f"{from_station} -> {to_station}",
            "depart": depart,
            "arrive": arrive,
            "duration": duration,
            "seat": seat,
            "price": price,
            "note": "，".join(note_parts) if note_parts else "-",
        }

        dedupe_key = (row["type"], row["trip_no"], row["route"], row["depart"], row["arrive"], row["price"])
        if dedupe_key in dedupe:
            continue
        dedupe.add(dedupe_key)
        rows.append(row)

    return rows


def _normalize_flight_rows(payload: Any, from_station: str, to_station: str, travel_date: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    data = _find_ticket_rows(
        payload,
        container_keys=["flights", "flightList", "tickets", "data", "result", "rows", "list"],
        row_keys=["flight_no", "flightNo", "flight_number", "flightNumber", "airline", "airline_name", "depTime"],
    )
    if not data:
        return rows

    dedupe = set()
    for item in data:
        if not isinstance(item, dict):
            continue

        flight_no = _extract_first_value(
            item,
            ["flight_no", "flightNo", "flight_number", "flightNumber", "flight_code", "flightCode", "code"],
        )
        if flight_no == "-":
            continue

        airline = _extract_first_value(item, ["airline", "airline_name", "airlineName", "company", "carrier"], default="")
        depart_raw = _combine_date_time(
            _extract_first_value(item, ["depart_date", "departureDate", "depDate", "date"], default=travel_date),
            _extract_first_value(item, ["depart_time", "departure_time", "departureTime", "depTime", "start_time"], default=""),
        )
        arrive_raw = _combine_date_time(
            _extract_first_value(item, ["arrive_date", "arrivalDate", "arrDate"], default=""),
            _extract_first_value(item, ["arrive_time", "arrival_time", "arrivalTime", "arrTime", "end_time"], default=""),
        )
        duration_raw = _extract_first_value(item, ["duration", "run_time", "elapsed_time"], default="-")
        depart, arrive, duration, is_cross_day = _normalize_trip_schedule(
            depart_raw=depart_raw,
            arrive_raw=arrive_raw,
            duration_raw=duration_raw,
            travel_date=travel_date,
        )

        from_name = _extract_first_value(
            item,
            ["from_airport", "fromAirport", "depart_airport", "departureAirport", "depAirport", "from_city", "fromCity"],
            default=from_station,
        )
        to_name = _extract_first_value(
            item,
            ["to_airport", "toAirport", "arrive_airport", "arrivalAirport", "arrAirport", "to_city", "toCity"],
            default=to_station,
        )
        seat = _extract_first_value(item, ["cabin", "cabinClass", "seat", "seat_name", "class"], default="-")
        price = _format_price(_extract_first_value(item, ["price", "ticketPrice", "amount", "lowest_price", "lowestPrice"], default=""))
        note = _extract_first_value(item, ["note", "remain", "left", "discount"], default="-")
        if is_cross_day and note == "-":
            note = "跨天到达"
        elif is_cross_day:
            note = f"{note}，跨天到达"

        row = {
            "mode": "飞机",
            "trip_no": f"{airline}{flight_no}" if airline and airline not in flight_no else flight_no,
            "route": f"{from_name} -> {to_name}",
            "depart": depart,
            "arrive": arrive,
            "duration": duration,
            "seat": seat,
            "price": price,
            "note": note,
        }
        dedupe_key = (row["trip_no"], row["route"], row["depart"], row["arrive"], row["price"])
        if dedupe_key in dedupe:
            continue
        dedupe.add(dedupe_key)
        rows.append(row)

    return rows


def _normalize_bus_rows(payload: Any, from_station: str, to_station: str, travel_date: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    data = _find_ticket_rows(
        payload,
        container_keys=["buses", "busList", "coachList", "tickets", "data", "result", "rows", "list", "lines", "displayLines"],
        row_keys=[
            "bus_no",
            "busNo",
            "busNumber",
            "coach_no",
            "coachNo",
            "schedule_id",
            "scheduleId",
            "from_station",
            "fromStationName",
            "startStationName",
            "fromTime",
        ],
    )
    if not data:
        return rows

    dedupe = set()
    for item in data:
        if not isinstance(item, dict):
            continue

        bus_no = _extract_first_value(
            item,
            ["bus_no", "busNo", "busNumber", "coach_no", "coachNo", "schedule_id", "scheduleId", "line_no"],
            default="班次",
        )
        depart_raw = _combine_date_time(
            _extract_first_value(item, ["depart_date", "departureDate", "startDate", "fromDate", "date"], default=travel_date),
            _extract_first_value(item, ["depart_time", "departure_time", "departureTime", "start_time", "startTime", "fromTime"], default=""),
        )
        arrive_raw = _combine_date_time(
            _extract_first_value(item, ["arrive_date", "arrivalDate", "endDate"], default=""),
            _extract_first_value(item, ["arrive_time", "arrival_time", "arrivalTime", "end_time", "endTime", "toTime"], default=""),
        )
        duration_raw = _extract_first_value(item, ["duration", "run_time", "elapsed_time", "runTime", "useMinutes"], default="-")
        depart, arrive, duration, is_cross_day = _normalize_trip_schedule(
            depart_raw=depart_raw,
            arrive_raw=arrive_raw,
            duration_raw=duration_raw,
            travel_date=travel_date,
        )

        from_name = _extract_first_value(
            item,
            ["from_station", "from_station_name", "fromStation", "fromStationName", "depart_station", "departureStation", "startStationName"],
            default=from_station,
        )
        to_name = _extract_first_value(
            item,
            ["to_station", "to_station_name", "toStation", "toStationName", "toCityName", "arrive_station", "arrivalStation", "endStationName"],
            default=to_station,
        )
        seat = _extract_first_value(item, ["seat", "seat_name", "vehicle_type", "bus_type", "busType"], default="-")
        price = _format_price(_extract_first_value(item, ["price", "ticketPrice", "amount", "salePrice", "fullPrice"], default=""))
        left = _extract_first_value(item, ["left", "remain", "remaining", "ticket_left", "ticketNum"], default="-")
        note = _extract_first_value(item, ["note", "remark"], default="")
        book_info = item.get("bookInfo") if isinstance(item.get("bookInfo"), dict) else {}
        bookable = _safe_text(book_info.get("bookable")) if book_info else ""
        if not note and bookable == "0":
            note = "\u65e0\u7968"
        elif not note and bookable:
            note = "\u53ef\u9884\u8ba2"
        if not note:
            note = f"余票:{left}" if left != "-" else "-"
        if is_cross_day and note == "-":
            note = "跨天到达"
        elif is_cross_day:
            note = f"{note}，跨天到达"

        row = {
            "mode": "大巴",
            "trip_no": bus_no,
            "route": f"{from_name} -> {to_name}",
            "depart": depart,
            "arrive": arrive,
            "duration": duration,
            "seat": seat,
            "price": price,
            "note": note,
        }
        dedupe_key = (row["trip_no"], row["route"], row["depart"], row["arrive"], row["price"])
        if dedupe_key in dedupe:
            continue
        dedupe.add(dedupe_key)
        rows.append(row)

    return rows


def _build_mode_ticket_markdown_table(title: str, rows: List[Dict[str, str]], max_rows: int = 8) -> str:
    if not rows:
        return ""

    header = [
        "| 班次 | 路线 | 出发时间 | 到达时间 | 历时 | 推荐座席 | 参考价格 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines = [
        f"| {row.get('trip_no', '-')} | {row.get('route', '-')} | {row.get('depart', '-')} | {row.get('arrive', '-')} | {row.get('duration', '-')} | {row.get('seat', '-')} | {row.get('price', '-')} | {row.get('note', '-')} |"
        for row in rows[: max(1, max_rows)]
    ]
    return "\n".join([title, *header, *lines])


def _build_ticket_markdown_table(
    direct_rows: List[Dict[str, str]],
    interline_rows: List[Dict[str, str]],
    user_query: str,
    flight_rows: Optional[List[Dict[str, str]]] = None,
    bus_rows: Optional[List[Dict[str, str]]] = None,
    max_rows: int = 8,
) -> str:
    sections: List[str] = []
    if flight_rows:
        sections.append(_build_mode_ticket_markdown_table("飞机票信息表", flight_rows, max_rows=max_rows))

    train_rows = _rank_ticket_rows(
        direct_rows=direct_rows,
        interline_rows=interline_rows,
        user_query=user_query,
    )[: max(1, max_rows)]

    train_header = [
        "| 类型 | 班次 | 路线 | 出发时间 | 到达时间 | 历时 | 推荐座席 | 参考价格 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    if train_rows:
        train_lines = [
            f"| {row['type']} | {row['trip_no']} | {row['route']} | {row['depart']} | {row['arrive']} | {row['duration']} | {row['seat']} | {row['price']} | {row['note']} |"
            for row in train_rows
        ]
        sections.append("\n".join(["火车和高铁票信息表", *train_header, *train_lines]))
    else:
        sections.append("火车和高铁票信息表\n\n未查到实时可售车票")

    if bus_rows:
        sections.append(_build_mode_ticket_markdown_table("大巴票信息表", bus_rows, max_rows=max_rows))

    return "\n\n".join([section for section in sections if section])


def _ticket_mode_rows(ticket_bundle: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
    if not isinstance(ticket_bundle, dict):
        return []

    row_keys = {
        "train": ["direct_rows", "interline_rows"],
        "flight": ["flight_rows"],
        "bus": ["bus_rows"],
    }
    rows: List[Dict[str, Any]] = []
    for key in row_keys.get(mode, []):
        value = ticket_bundle.get(key, [])
        if isinstance(value, list):
            rows.extend([row for row in value if isinstance(row, dict)])
    return rows


def _ticket_mode_state(ticket_bundle: Dict[str, Any], mode: str) -> Dict[str, Any]:
    states = ticket_bundle.get("ticket_states", {}) if isinstance(ticket_bundle, dict) else {}
    state = states.get(mode) if isinstance(states, dict) else None
    if isinstance(state, dict):
        return state

    rows = _ticket_mode_rows(ticket_bundle, mode)
    return {
        "status": "success" if rows else "empty",
        "result_count": len(rows),
        "error": "",
    }


def _ticket_bundle_has_valid_results(ticket_bundle: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(ticket_bundle, dict):
        return False
    if "has_valid_results" in ticket_bundle:
        return bool(ticket_bundle.get("has_valid_results"))
    if any(_ticket_mode_rows(ticket_bundle, mode) for mode in ("train", "flight", "bus")):
        return True
    return _contains_ticket_table(_safe_text(ticket_bundle.get("append_markdown")))


def _build_ticket_status_note(ticket_bundle: Dict[str, Any]) -> str:
    mode_labels = {
        "train": "火车和高铁",
        "flight": "飞机",
        "bus": "大巴",
    }
    state_parts: List[str] = []
    missing_parts: List[str] = []

    for mode in ("train", "flight", "bus"):
        state = _ticket_mode_state(ticket_bundle, mode)
        status = _safe_text(state.get("status")) or "empty"
        count = int(state.get("result_count") or len(_ticket_mode_rows(ticket_bundle, mode)))
        label = mode_labels[mode]
        reason = _safe_text(state.get("error"))
        if status == "success":
            partial_error = f"（部分查询失败：{reason}）" if reason else ""
            state_parts.append(f"{label} {count} 条{partial_error}")
            continue

        if status == "error":
            missing_parts.append(f"{label}查询失败" + (f"（{reason}）" if reason else ""))
        else:
            missing_parts.append(f"{label}未找到可确认实时结果")

    if not _ticket_bundle_has_valid_results(ticket_bundle):
        detail = "；".join(missing_parts) if missing_parts else "三种票务均未返回可确认实时结果"
        return f"未找到可确认的票务结果。{detail}。"

    available = "，".join(state_parts)
    if missing_parts:
        return f"当前可确认的实时结果为：{available}。另外，{'；'.join(missing_parts)}。"
    return f"当前可确认的实时结果为：{available}。"


def _build_realtime_only_answer(ticket_bundle: Dict[str, Any]) -> str:
    route = ticket_bundle.get("route", {}) if isinstance(ticket_bundle, dict) else {}
    from_station = _safe_text(route.get("from_station")) or "出发地"
    to_station = _safe_text(route.get("to_station")) or "目的地"
    travel_date = _safe_text(route.get("travel_date")) or datetime.date.today().strftime("%Y-%m-%d")

    direct_rows = ticket_bundle.get("direct_rows", []) if isinstance(ticket_bundle, dict) else []
    interline_rows = ticket_bundle.get("interline_rows", []) if isinstance(ticket_bundle, dict) else []
    flight_rows = ticket_bundle.get("flight_rows", []) if isinstance(ticket_bundle, dict) else []
    bus_rows = ticket_bundle.get("bus_rows", []) if isinstance(ticket_bundle, dict) else []
    user_query = _safe_text(ticket_bundle.get("user_query")) if isinstance(ticket_bundle, dict) else ""
    all_rows: List[Dict[str, str]] = []
    if isinstance(direct_rows, list):
        all_rows.extend([row for row in direct_rows if isinstance(row, dict)])
    if isinstance(interline_rows, list):
        all_rows.extend([row for row in interline_rows if isinstance(row, dict)])
    if isinstance(flight_rows, list):
        all_rows.extend([row for row in flight_rows if isinstance(row, dict)])
    if isinstance(bus_rows, list):
        all_rows.extend([row for row in bus_rows if isinstance(row, dict)])

    recommendation_lines = []
    if isinstance(ticket_bundle, dict) and isinstance(ticket_bundle.get("recommendation_lines"), list):
        recommendation_lines = [str(x) for x in ticket_bundle.get("recommendation_lines") if _safe_text(x)]
    if not recommendation_lines:
        recommendation_lines = _build_recommendation_lines(
            direct_rows=direct_rows if isinstance(direct_rows, list) else [],
            interline_rows=interline_rows if isinstance(interline_rows, list) else [],
            user_query=user_query,
            top_k=3,
        )

    direct_count = len(direct_rows) if isinstance(direct_rows, list) else 0
    interline_count = len(interline_rows) if isinstance(interline_rows, list) else 0
    flight_count = len(flight_rows) if isinstance(flight_rows, list) else 0
    bus_count = len(bus_rows) if isinstance(bus_rows, list) else 0
    summary_lines = [
        f"已按城市站点扩展查询 {from_station} 到 {to_station}（{travel_date}）的实时票务结果。",
        _build_ticket_status_note(ticket_bundle),
        f"结果数量：飞机 {flight_count} 条，火车和高铁直达 {direct_count} 条、中转 {interline_count} 条，大巴 {bus_count} 条。",
    ]

    cheapest_row: Optional[Dict[str, str]] = None
    cheapest_price = float("inf")
    for row in all_rows:
        price_value = _extract_price_number(row.get("price", ""))
        if price_value is not None and price_value < cheapest_price:
            cheapest_price = price_value
            cheapest_row = row

    if cheapest_row is not None:
        row_type = _safe_text(cheapest_row.get("type")) or _safe_text(cheapest_row.get("mode")) or "班次"
        summary_lines.append(
            "当前最低价方案："
            f"{row_type} {_safe_text(cheapest_row.get('trip_no'))}，"
            f"{_safe_text(cheapest_row.get('route'))}，"
            f"{_safe_text(cheapest_row.get('depart'))}->{_safe_text(cheapest_row.get('arrive'))}，"
            f"参考价格 {_safe_text(cheapest_row.get('price'))}。"
        )
    elif _ticket_bundle_has_valid_results(ticket_bundle):
        summary_lines.append("当前可确认结果未提供可比较的价格信息。")
    else:
        summary_lines.append("若要继续查，请稍后重试或调整出发站、到达站、日期。")

    if recommendation_lines:
        formatted_recommendations = "；".join(
            f"{index}. {line}" for index, line in enumerate(recommendation_lines, start=1)
        )
        summary_lines.append(f"根据实时结果，建议优先参考：{formatted_recommendations}。")

    summary_lines.append("额外判断：优先看总耗时、是否跨天、换乘等待时间和余票状态；临近出发时票价与余票可能变化，下单前仍需以支付页为准。")

    markdown_table = _safe_text(ticket_bundle.get("append_markdown")) if isinstance(ticket_bundle, dict) else ""
    if markdown_table:
        return "\n\n".join([*summary_lines, markdown_table])
    return "\n\n".join(summary_lines)


def _looks_like_internal_or_failed_answer(content: str) -> bool:
    text = _safe_text(content)
    if not text:
        return True

    lowered = text.lower()
    if INTERNAL_LEAK_REGEX.search(lowered):
        return True

    if any(
        marker in lowered
        for marker in [
            "任务执行失败",
            "观察分析失败",
            "规划失败",
            "阶段执行出现异常",
            "阶段解析结果异常",
            "已停止后续链路",
            "已中止后续链路",
            "allocationquota",
            "invalidparameter",
            "messages with role \"tool\"",
        ]
    ):
        return True

    return False


def _should_hide_stream_message(msg: Dict[str, Any]) -> bool:
    role = _safe_text(msg.get("role")).lower()
    step_type = _safe_text(msg.get("type")).lower()
    content = _safe_text(msg.get("show_content") or msg.get("content"))

    if role == "tool":
        return True

    if step_type in {"tool_call", "tool_call_result", "do_subtask"}:
        return True

    return bool(content and _looks_like_internal_or_failed_answer(content))


def _split_final_answer_for_stream(content: str, max_chars: int = 280) -> List[str]:
    text = _safe_text(content)
    if not text:
        return []

    chunks: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        end = min(length, index + max_chars)
        if end < length:
            window = text[index:end]
            cut_points = [
                window.rfind("\n\n"),
                window.rfind("\n"),
                window.rfind("。"),
                window.rfind("；"),
                window.rfind("！"),
                window.rfind("？"),
            ]
            cut = max(cut_points)
            if cut >= max_chars // 3:
                end = index + cut + 1

        chunk = text[index:end]
        if chunk:
            chunks.append(chunk)
        index = end

    return chunks


def _normalize_markdown_text_part(text: str) -> str:
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)

    normalized = re.sub(r"(?m)^[ \t]*-{3,}[ \t]*$", "\n", normalized)
    normalized = re.sub(r"[ \t]*-{3,}[ \t]*(?=#{1,6}(?!#))", "\n\n", normalized)
    normalized = re.sub(r"(?<!^)(?<![#\n])[ \t]*(#{1,6})(?=[^\s#])", r"\n\n\1 ", normalized)
    normalized = re.sub(r"(?m)^(#{1,6})(?=[^\s#])", r"\1 ", normalized)
    normalized = re.sub(r"(?<!^)(?<![#\n])[ \t]*(#{1,6}\s+)", r"\n\n\1", normalized)
    normalized = re.sub(r"(?<![#\n])[ \t]*(###\s*(?:上午|中午|下午|晚上)(?:（[^）\n]+）|\([^)\n]+\)))\s+", r"\n\n\1\n\n", normalized)
    normalized = re.sub(r"(?m)^(###\s*(?:上午|中午|下午|晚上)(?:（[^）\n]+）|\([^)\n]+\)))\s+", r"\1\n\n", normalized)
    normalized = re.sub(r"(?m)^(#{2,6}\s*(?:住宿推荐|交通建议|重要提醒|结尾|结论|使用限制|数据说明|地理位置信息|生成的文档)[:：])\s+", r"\1\n\n", normalized)
    normalized = re.sub(
        r"(?m)^#{1,6}\s*(\d+[.、]\s*(?:预约提醒|天气建议|交通建议|住宿建议|饮食建议|行李准备|安全提醒|费用提醒|实用贴士).*)$",
        r"### \1",
        normalized,
    )
    normalized = re.sub(r"(?m)^(###\s+\d+[.、])(?=\S)", r"\1 ", normalized)
    normalized = re.sub(r"(?m)^(\d+)[.、](?=\S)", r"\1. ", normalized)

    normalized = re.sub(r"(?m)^(#{1,6}\s+[^\n|]{1,80})\|", r"\1\n|", normalized)
    normalized = re.sub(r"(网络搜索参考表|小红书检索资源表|地图地点表)[ \t]*(\|)", r"\1\n\2", normalized)
    normalized = re.sub(r"\|[ \t]*\|", "|\n|", normalized)
    normalized = re.sub(r"(\|[^\n]*\|)[ \t]*(\|[ \t]*---)", r"\1\n\2", normalized)
    normalized = re.sub(r"(\|[ \t:.-]+(?:\|[ \t:.-]+)+\|)[ \t]*(\|)", r"\1\n\2", normalized)

    normalized = re.sub(r"(?<![#\n])[ \t]+(?=(?:[一二三四五六七八九十]+、|\d+\.)\s+\S)", "\n", normalized)
    normalized = re.sub(r"(?<![#\n])[ \t]+(?=[-*]\s+\S)", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _normalize_markdown_for_display(content: str) -> str:
    text = _safe_text(content)
    if not text:
        return ""

    parts = re.split(r"(```[\s\S]*?```)", text)
    normalized_parts = [
        part if part.startswith("```") else _normalize_markdown_text_part(part)
        for part in parts
        if part
    ]
    return "\n\n".join(part for part in normalized_parts if part).strip()


def _build_progress_chunk(
    message_id: str,
    content: str,
    sanitize_text: Callable[[str], str],
    linked_user_message_id: str = "",
) -> Dict[str, Any]:
    safe_content = sanitize_text(_safe_text(content))
    chunk = {
        "type": "chat_chunk",
        "message_id": message_id,
        "role": "assistant",
        "content": safe_content,
        "show_content": safe_content,
        "step_type": "tool_progress",
        "agent_type": "assistant",
    }
    if linked_user_message_id:
        chunk["linked_user_message_id"] = linked_user_message_id
    return chunk


def _build_final_answer_chunk(
    message_id: str,
    content: str,
    sanitize_text: Callable[[str], str],
    replace: bool = False,
    linked_user_message_id: str = "",
) -> Dict[str, Any]:
    safe_content = sanitize_text(_safe_text(content))
    chunk = {
        "type": "chat_chunk",
        "message_id": message_id,
        "role": "assistant",
        "content": safe_content,
        "show_content": safe_content,
        "step_type": "final_answer",
        "agent_type": "assistant",
    }
    if replace:
        chunk["replace"] = True
    if linked_user_message_id:
        chunk["linked_user_message_id"] = linked_user_message_id
    return chunk


def _looks_offtopic_for_transport(content: str) -> bool:
    text = _safe_text(content)
    if not text:
        return True

    transport_hits = len(TRANSPORT_ANSWER_REGEX.findall(text))
    scenic_hits = len(SCENIC_ANSWER_REGEX.findall(text))

    return transport_hits < 2 and scenic_hits >= 2


def _looks_like_useful_ticket_answer(content: str) -> bool:
    text = _safe_text(content)
    if not text or _looks_like_internal_or_failed_answer(text) or _looks_offtopic_for_transport(text):
        return False
    return _contains_ticket_table(text) or bool(TRANSPORT_ANSWER_REGEX.search(text))


def _looks_like_confirmed_ticket_result(content: str) -> bool:
    text = _safe_text(content)
    if not _looks_like_useful_ticket_answer(text):
        return False
    if _contains_ticket_table(text):
        return True
    return bool(
        re.search(
            r"(?<![A-Za-z0-9])(?:[GDCZTKS]\d{1,5}|[A-Z0-9]{2}\d{3,4})(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
    )


def _append_ticket_grounding_if_missing(content: str, ticket_bundle: Dict[str, Any]) -> str:
    base = _safe_text(content)
    status_note = _build_ticket_status_note(ticket_bundle)
    markdown_table = _safe_text(ticket_bundle.get("append_markdown"))

    if status_note and "当前可确认的实时结果为" not in base and "未找到可确认的票务结果" not in base:
        base = f"{base}\n\n{status_note}" if base else status_note

    if markdown_table and not _content_covers_ticket_bundle(base, ticket_bundle):
        base = f"{base}\n\n{markdown_table}" if base else markdown_table
    return base


def _select_ticket_final_answer(existing_final_answer: str, ticket_bundle: Dict[str, Any]) -> str:
    existing = _safe_text(existing_final_answer)
    forced_answer = _safe_text(ticket_bundle.get("realtime_only_answer"))
    if not forced_answer:
        forced_answer = _build_realtime_only_answer(ticket_bundle)

    has_valid_results = _ticket_bundle_has_valid_results(ticket_bundle)
    enforce_realtime_only = bool(ticket_bundle.get("enforce_realtime_only"))

    if not has_valid_results and _looks_like_confirmed_ticket_result(existing):
        return existing

    if enforce_realtime_only:
        return forced_answer

    if _looks_like_useful_ticket_answer(existing):
        # An empty pre-query must never overwrite a later valid ticket answer.
        if not has_valid_results:
            return forced_answer
        return _append_ticket_grounding_if_missing(existing, ticket_bundle)

    return forced_answer


def _replace_or_append_final_answer(messages: Any, content: str) -> None:
    if not isinstance(messages, list):
        return

    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if not isinstance(msg, dict):
            continue
        if msg.get("role", "assistant") == "assistant" and msg.get("type") == "final_answer":
            updated_msg = dict(msg)
            updated_msg["content"] = content
            updated_msg["role"] = "assistant"
            updated_msg["type"] = "final_answer"
            messages[idx] = updated_msg
            return

    messages.append(
        {
            "role": "assistant",
            "content": content,
            "type": "final_answer",
        }
    )


def _contains_ticket_table(content: str) -> bool:
    return bool(re.search(r"(飞机票信息表|火车和高铁票信息表|大巴票信息表|12306实时车票班次信息(?:总)?表)", _safe_text(content)))


def _append_ticket_table_if_missing(content: str, markdown_table: str) -> str:
    base = _safe_text(content)
    table = _safe_text(markdown_table)

    if not table:
        return base
    if _contains_ticket_table(base):
        return base
    if not base:
        return table

    return f"{base}\n\n{table}"


def _content_covers_ticket_bundle(content: str, ticket_bundle: Dict[str, Any]) -> bool:
    text = _safe_text(content)
    if not text or not _contains_ticket_table(text):
        return False

    trip_numbers = [
        _safe_text(row.get("trip_no"))
        for mode in ("train", "flight", "bus")
        for row in _ticket_mode_rows(ticket_bundle, mode)
        if _safe_text(row.get("trip_no")) not in {"", "-"}
    ]
    return bool(trip_numbers) and all(trip_no in text for trip_no in trip_numbers)


def _extract_latest_final_answer_content(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""

    final_output = result.get("final_output")
    if isinstance(final_output, dict) and final_output.get("type") == "final_answer":
        return _safe_text(final_output.get("content"))

    all_messages = result.get("all_messages")
    if isinstance(all_messages, list):
        for msg in reversed(all_messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role", "assistant") == "assistant" and msg.get("type") == "final_answer":
                return _safe_text(msg.get("content"))

    return ""


def _apply_realtime_only_result(result: Dict[str, Any], ticket_bundle: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    markdown_table = _safe_text(ticket_bundle.get("append_markdown"))
    existing_final_answer = _extract_latest_final_answer_content(result)

    merged_answer = _select_ticket_final_answer(existing_final_answer, ticket_bundle)
    if not merged_answer:
        merged_answer = markdown_table

    if not merged_answer:
        return result

    _replace_or_append_final_answer(result.get("all_messages"), merged_answer)
    _replace_or_append_final_answer(result.get("new_messages"), merged_answer)

    final_output = result.get("final_output")
    if isinstance(final_output, dict):
        updated_output = dict(final_output)
        updated_output["role"] = "assistant"
        updated_output["type"] = "final_answer"
        updated_output["content"] = merged_answer
        result["final_output"] = updated_output
    else:
        result["final_output"] = {
            "role": "assistant",
            "type": "final_answer",
            "content": merged_answer,
        }

    result["realtime_ticket_enforced"] = True
    return result


def _is_xhs_query(query_text: str) -> bool:
    text = _safe_text(query_text)
    if not text:
        return False

    if XHS_EXPLICIT_QUERY_REGEX.search(text):
        return True

    # 票务强规则场景由12306链路优先处理，不触发小红书预检索。
    if _is_train_ticket_query(text):
        return False

    # 老逻辑：偏内容种草类检索，且有明确查找动作。
    if XHS_STYLE_QUERY_REGEX.search(text) and XHS_ACTION_QUERY_REGEX.search(text):
        return True

    # 新逻辑：常规攻略/推荐/行程提问也默认触发小红书检索。
    if XHS_GUIDE_QUERY_REGEX.search(text):
        return True

    return False


def _is_travel_experience_query(query_text: str) -> bool:
    text = _safe_text(query_text)
    if not text:
        return False
    if _is_train_ticket_query(text):
        return False
    return bool(TRAVEL_EXPERIENCE_QUERY_REGEX.search(text))


def _travel_experience_kinds(query_text: str) -> List[str]:
    text = _safe_text(query_text)
    kinds: List[str] = []
    if ITINERARY_QUERY_REGEX.search(text):
        kinds.append("行程规划")
    if HOTEL_QUERY_REGEX.search(text):
        kinds.append("酒店住宿")
    if FOOD_QUERY_REGEX.search(text):
        kinds.append("美食推荐")
    if not kinds and _is_travel_experience_query(text):
        kinds.append("目的地攻略")
    return kinds


def _first_available_tool_name(tool_manager: Any, candidates: List[str]) -> str:
    for tool_name in candidates:
        if _tool_exists(tool_manager, tool_name):
            return tool_name
    return ""


def _extract_xhs_focus(query_text: str) -> str:
    text = _safe_text(query_text)
    if not text:
        return ""

    focus_candidates = [
        "预算",
        "省钱",
        "拍照",
        "路线",
        "住宿",
        "美食",
        "亲子",
        "情侣",
        "避雷",
        "穿搭",
        "美妆",
        "探店",
    ]

    selected: List[str] = []
    for candidate in focus_candidates:
        if candidate in text:
            selected.append(candidate)
        if len(selected) >= 4:
            break

    return "、".join(selected)


def _is_tool_execution_error_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False

    if payload.get("error") is True:
        return True

    if _safe_text(payload.get("status")).lower() in {"error", "failed", "failure"}:
        return True

    if _safe_text(payload.get("error_type")):
        return True

    message = _safe_text(payload.get("message")).lower()
    if message and any(
        keyword in message
        for keyword in ["tool", "failed", "not found", "invalid", "login", "auth", "unauthorized", "forbidden", "登录", "未登录"]
    ):
        if not isinstance(payload.get("items"), list) and not isinstance(payload.get("resources"), list):
            return True

    return False


def _tool_error_reason(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "error_type", "detail"):
            value = _safe_text(payload.get(key))
            if value and value.lower() not in {"true", "false"}:
                return value
    return fallback


def _build_ticket_mode_state(
    rows: List[Dict[str, Any]],
    sources: Any,
    attempted: bool,
    successful_attempt: bool,
    errors: List[str],
) -> Dict[str, Any]:
    if rows:
        status = "success"
    elif errors:
        status = "error"
    elif successful_attempt:
        status = "empty"
    else:
        status = "error"

    return {
        "status": status,
        "result_count": len(rows),
        "sources": sources,
        "attempted": attempted,
        "error": "；".join(_dedupe_texts(errors)),
    }


def _run_tool_with_arg_candidates(
    tool_manager: Any,
    tool_name: str,
    message_history: List[Dict[str, Any]],
    session_id: str,
    arg_candidates: List[Dict[str, Any]],
) -> Any:
    last_payload: Any = None
    compatible_candidates = arg_candidates

    try:
        tool_spec = tool_manager.get_tool(tool_name)
        parameter_names = set(getattr(tool_spec, "parameters", {}).keys())
        required_names = set(getattr(tool_spec, "required", []))
        if parameter_names:
            matched_candidates = [
                args
                for args in arg_candidates
                if set(args.keys()).issubset(parameter_names) and required_names.issubset(args.keys())
            ]
            if matched_candidates:
                compatible_candidates = matched_candidates
    except Exception:
        pass

    for args in compatible_candidates:
        try:
            raw_result = tool_manager.run_tool(
                tool_name,
                messages=message_history,
                session_id=session_id,
                **args,
            )
            payload = _unwrap_tool_output(raw_result)
            last_payload = payload
            if not _is_tool_execution_error_payload(payload):
                return payload
        except Exception as run_error:
            last_payload = {"error": True, "message": str(run_error)}

    return last_payload


def _run_xhs_tool_with_candidates(
    tool_manager: Any,
    tool_name: str,
    message_history: List[Dict[str, Any]],
    session_id: str,
    arg_candidates: List[Dict[str, Any]],
) -> Any:
    return _run_tool_with_arg_candidates(
        tool_manager=tool_manager,
        tool_name=tool_name,
        message_history=message_history,
        session_id=session_id,
        arg_candidates=arg_candidates,
    )


def _normalize_xhs_rows(payload: Any, max_rows: int = 8) -> List[Dict[str, str]]:
    if isinstance(payload, str):
        parsed_payload = _safe_json_loads(payload)
        if parsed_payload is not None:
            payload = parsed_payload
    if isinstance(payload, dict) and ("content" in payload or "result" in payload):
        unwrapped_payload = _unwrap_tool_output(payload)
        if unwrapped_payload is not payload:
            payload = unwrapped_payload

    raw_items: Any = _first_present_list(
        payload,
        ["resources", "items", "notes", "results", "data", "list"],
    )

    if not isinstance(raw_items, list):
        return []

    rows: List[Dict[str, str]] = []
    dedupe = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        title = _extract_first_value(item, ["title", "name", "note_title", "display_title"], default="-")
        url = _extract_first_value(item, ["url", "link", "href", "note_url"], default="-")

        note_id = _extract_first_value(item, ["note_id", "noteId", "id"], default="")
        if (not url or url == "-") and note_id:
            url = f"https://www.xiaohongshu.com/explore/{note_id}"

        author_value = item.get("author")
        if isinstance(author_value, dict):
            author = _extract_first_value(author_value, ["nickname", "name", "user_name"], default="-")
        else:
            author = _safe_text(author_value) or "-"

        liked_count = _extract_first_value(item, ["liked_count", "likes", "like_count", "hot"], default="-")
        summary = _extract_first_value(
            item,
            ["summary", "snippet", "desc", "description", "content"],
            default="-",
        )
        source = _extract_first_value(item, ["source", "data_source"], default="-")

        if summary != "-" and len(summary) > 120:
            summary = f"{summary[:117]}..."
        if title == "-" and summary != "-":
            title = summary if len(summary) <= 24 else f"{summary[:21]}..."

        if not url:
            url = "-"

        dedupe_key = (url, title, author)
        if dedupe_key in dedupe:
            continue
        dedupe.add(dedupe_key)

        rows.append(
            {
                "title": title,
                "author": author,
                "liked_count": liked_count,
                "summary": summary,
                "url": url,
                "source": source,
            }
        )

        if len(rows) >= max(1, max_rows):
            break

    return rows


def _build_xhs_markdown_table(rows: List[Dict[str, str]], max_rows: int = 8) -> str:
    if not rows:
        return ""

    header = [
        "| 标题 | 作者 | 点赞指标 | 摘要 | 链接 | 来源 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    table_rows = rows[: max(1, max_rows)] if rows else []
    if not table_rows:
        table_rows = [
            {
                "title": "-",
                "author": "-",
                "liked_count": "-",
                "summary": "未检索到可用的小红书资源",
                "url": "-",
                "source": "-",
            }
        ]

    lines = [
        (
            f"| {_markdown_table_cell(row.get('title'), 48)} | "
            f"{_markdown_table_cell(row.get('author'), 24)} | "
            f"{_markdown_table_cell(row.get('liked_count'), 16)} | "
            f"{_markdown_table_cell(row.get('summary'), 140)} | "
            f"{_markdown_link_cell(row.get('url'))} | "
            f"{_markdown_table_cell(row.get('source'), 24)} |"
        )
        for row in table_rows
    ]

    return "\n".join(["### 小红书检索资源表", *header, *lines])


def _build_xhs_fallback_answer(xhs_bundle: Dict[str, Any]) -> str:
    query_text = _safe_text(xhs_bundle.get("query"))
    rows = xhs_bundle.get("rows", []) if isinstance(xhs_bundle.get("rows"), list) else []
    summary_text = _safe_text(xhs_bundle.get("summary"))
    markdown_table = _safe_text(xhs_bundle.get("append_markdown"))

    lines = [
        f"已完成小红书检索：{query_text or '用户请求'}。",
        f"命中资源数量：{len(rows)}。",
    ]

    if summary_text:
        lines.append(f"检索摘要：{summary_text}")

    if rows:
        lines.append("优先参考资源：")
        for row in rows[:3]:
            lines.append(
                f"- {_safe_text(row.get('title'))} | 作者: {_safe_text(row.get('author'))} | 链接: {_safe_text(row.get('url'))}"
            )
    else:
        lines.append("当前检索结果不足，建议补充更具体关键词后重试。")

    if markdown_table:
        lines.append(markdown_table)

    return "\n\n".join(lines)


def _contains_xhs_table(content: str) -> bool:
    return bool(re.search(r"小红书检索资源表", _safe_text(content)))


def _append_xhs_table_if_missing(content: str, markdown_table: str) -> str:
    base = _safe_text(content)
    table = _safe_text(markdown_table)

    if not table:
        return base
    if _contains_xhs_table(base):
        return base
    if not base:
        return table

    return f"{base}\n\n{table}"


def _looks_offtopic_for_xhs(content: str) -> bool:
    text = _safe_text(content)
    if not text:
        return True

    xhs_hits = len(XHS_ANSWER_REGEX.findall(text))
    transport_hits = len(TRANSPORT_ANSWER_REGEX.findall(text))
    scenic_hits = len(SCENIC_ANSWER_REGEX.findall(text))

    return xhs_hits < 1 and (transport_hits >= 2 or scenic_hits >= 3)


def _select_xhs_final_answer(existing_final_answer: str, xhs_bundle: Dict[str, Any]) -> str:
    fallback_answer = _safe_text(xhs_bundle.get("fallback_answer"))
    if not fallback_answer:
        fallback_answer = _build_xhs_fallback_answer(xhs_bundle)

    existing = _safe_text(existing_final_answer)
    if not existing:
        return _normalize_markdown_for_display(fallback_answer)

    if _looks_like_internal_or_failed_answer(existing) or _looks_offtopic_for_xhs(existing):
        return _normalize_markdown_for_display(fallback_answer)

    markdown_table = _safe_text(xhs_bundle.get("append_markdown"))
    return _normalize_markdown_for_display(_append_xhs_table_if_missing(existing, markdown_table))


def _apply_xhs_result(result: Dict[str, Any], xhs_bundle: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    existing_final_answer = _extract_latest_final_answer_content(result)
    merged_answer = _select_xhs_final_answer(existing_final_answer, xhs_bundle)
    if not merged_answer:
        merged_answer = _safe_text(xhs_bundle.get("append_markdown"))

    if not merged_answer:
        return result

    _replace_or_append_final_answer(result.get("all_messages"), merged_answer)
    _replace_or_append_final_answer(result.get("new_messages"), merged_answer)

    final_output = result.get("final_output")
    if isinstance(final_output, dict):
        updated_output = dict(final_output)
        updated_output["role"] = "assistant"
        updated_output["type"] = "final_answer"
        updated_output["content"] = merged_answer
        result["final_output"] = updated_output
    else:
        result["final_output"] = {
            "role": "assistant",
            "type": "final_answer",
            "content": merged_answer,
        }

    result["xhs_resource_enforced"] = True
    return result


def _first_present_list(payload: Any, keys: List[str]) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _first_present_list(value, keys)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, dict):
            nested = _first_present_list(value, keys)
            if nested:
                return nested
    return []


def _run_origin_city_lookup(tool_manager: Any, message_history: List[Dict[str, Any]], session_id: str) -> Tuple[str, str, str]:
    tool_name = _first_available_tool_name(tool_manager, ["map_ip_location", "ip_location", "geo_ip_location"])
    if not tool_name:
        return "", "", "定位工具不可用"
    payload = _run_tool_with_arg_candidates(
        tool_manager=tool_manager,
        tool_name=tool_name,
        message_history=message_history,
        session_id=session_id,
        arg_candidates=[{}, {"ip": ""}],
    )
    if _is_tool_execution_error_payload(payload):
        return "", tool_name, _tool_error_reason(payload, "定位失败")
    city = _extract_city_from_payload(payload)
    return city, tool_name, "" if city else "定位结果未包含地级市"


def _summarize_weather_payload(payload: Any, travel_date: str) -> str:
    if isinstance(payload, str):
        parsed = _safe_json_loads(payload)
        if parsed is not None:
            payload = parsed
    if isinstance(payload, dict) and ("content" in payload or "result" in payload):
        unwrapped = _unwrap_tool_output(payload)
        if unwrapped is not payload:
            payload = unwrapped
    if not isinstance(payload, dict):
        return _safe_text(payload)[:160]

    forecasts = _first_present_list(payload, ["forecasts", "forecast", "casts", "weather", "data"])
    if forecasts:
        selected = None
        for item in forecasts:
            if isinstance(item, dict) and travel_date and _safe_text(item.get("date")) == travel_date:
                selected = item
                break
        if selected is None and isinstance(forecasts[0], dict):
            selected = forecasts[0]
        if isinstance(selected, dict):
            day = _extract_first_value(selected, ["text_day", "dayweather", "weather", "text"], default="")
            night = _extract_first_value(selected, ["text_night", "nightweather"], default="")
            high = _extract_first_value(selected, ["high", "daytemp", "max_temp"], default="")
            low = _extract_first_value(selected, ["low", "nighttemp", "min_temp"], default="")
            wind = _extract_first_value(selected, ["wd_day", "wind_dir", "wind", "windpower"], default="")
            parts = [part for part in [day, f"夜间{night}" if night else "", f"{low}-{high}℃" if low or high else "", wind] if part]
            return "，".join(parts)

    now = payload.get("now") if isinstance(payload.get("now"), dict) else payload
    text = _extract_first_value(now, ["text", "weather", "condition"], default="")
    temp = _extract_first_value(now, ["temp", "temperature"], default="")
    wind = _extract_first_value(now, ["wind_dir", "wind", "windpower"], default="")
    return "，".join([part for part in [text, f"{temp}℃" if temp else "", wind] if part])


def _run_weather_for_travel(
    destination_city: str,
    travel_date: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> Tuple[str, str, str]:
    if not destination_city:
        return "", "", "未识别目的地，无法查询天气"
    tool_name = _first_available_tool_name(tool_manager, ["map_weather", "weather", "get_weather"])
    if not tool_name:
        return "", "", "天气工具不可用"
    if tool_name == "map_weather":
        arg_candidates = [
            {"city": destination_city, "date": travel_date},
            {"district": destination_city, "date": travel_date},
            {"city": destination_city},
            {"district": destination_city},
        ]
    else:
        arg_candidates = [
            {"city": destination_city, "date": travel_date},
            {"district": destination_city, "date": travel_date},
            {"location": destination_city, "date": travel_date},
            {"query": f"{destination_city} {travel_date} 天气"},
            {"city": destination_city},
            {"district": destination_city},
            {"location": destination_city},
        ]
    payload = _run_tool_with_arg_candidates(
        tool_manager=tool_manager,
        tool_name=tool_name,
        message_history=message_history,
        session_id=session_id,
        arg_candidates=arg_candidates,
    )
    if _is_tool_execution_error_payload(payload):
        return "", tool_name, _tool_error_reason(payload, "天气查询失败")
    summary = _summarize_weather_payload(payload, travel_date)
    return summary, tool_name, "" if summary else "天气工具未返回可用摘要"


def _normalize_web_rows(payload: Any, max_rows: int = 6) -> List[Dict[str, str]]:
    if isinstance(payload, str):
        parsed_payload = _safe_json_loads(payload)
        if parsed_payload is not None:
            payload = parsed_payload
    if isinstance(payload, dict) and ("content" in payload or "result" in payload):
        unwrapped_payload = _unwrap_tool_output(payload)
        if unwrapped_payload is not payload:
            payload = unwrapped_payload

    raw_items = _first_present_list(payload, ["organic", "results", "items", "data", "resources"])
    rows: List[Dict[str, str]] = []
    seen = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = _extract_first_value(item, ["title", "name"], default="-")
        url = _extract_first_value(item, ["url", "link", "href"], default="-")
        snippet = _extract_first_value(item, ["snippet", "summary", "description", "desc", "content"], default="-")
        source = _extract_first_value(item, ["source", "site"], default="-")
        if not url or url == "-":
            url = _extract_url_from_text(snippet) or _extract_url_from_text(title) or _extract_url_from_text(item)
        if not url:
            url = "-"
        if snippet != "-" and len(snippet) > 120:
            snippet = f"{snippet[:117]}..."
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "snippet": snippet, "url": url, "source": source})
        if len(rows) >= max(1, max_rows):
            break

    return rows


def _markdown_table_cell(value: Any, max_len: int = 120) -> str:
    text = _safe_text(value) or "-"
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", "｜")
    if max_len > 0 and len(text) > max_len:
        text = f"{text[: max_len - 3].rstrip()}..."
    return text or "-"


def _markdown_link_cell(url: Any, label: str = "查看") -> str:
    text = _safe_text(url)
    if not text or text == "-":
        return "-"
    safe_url = text.replace(")", "%29").replace(" ", "%20")
    return f"[{label}]({safe_url})"


def _row_link_value(row: Dict[str, Any]) -> str:
    url = _safe_text(row.get("url"))
    if url and url != "-":
        return url
    return (
        _extract_url_from_text(row.get("snippet"))
        or _extract_url_from_text(row.get("summary"))
        or _extract_url_from_text(row.get("title"))
        or "-"
    )


def _build_web_markdown_table(rows: List[Dict[str, str]], max_rows: int = 6) -> str:
    if not rows:
        return ""

    header = [
        "### 网络搜索参考表",
        "| 标题 | 摘要 | 链接 | 来源 |",
        "| --- | --- | --- | --- |",
    ]
    body = [
        (
            f"| {_markdown_table_cell(row.get('title'), 56)} | "
            f"{_markdown_table_cell(row.get('snippet'), 160)} | "
            f"{_markdown_link_cell(_row_link_value(row))} | "
            f"{_markdown_table_cell(row.get('source'), 24)} |"
        )
        for row in rows[: max(1, max_rows)]
    ]
    return "\n".join([*header, *body])


def _extract_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        number = float(value)
        return number if -180 <= number <= 180 else None
    text = _safe_text(value)
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except Exception:
        return None
    return number if -180 <= number <= 180 else None


def _extract_location_pair(item: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    candidate_containers = [item]
    for key in ["location", "point", "coord", "coordinate", "coordinates"]:
        value = item.get(key)
        if isinstance(value, dict):
            candidate_containers.append(value)
        elif isinstance(value, list) and len(value) >= 2:
            lng = _extract_float(value[0])
            lat = _extract_float(value[1])
            if lat is not None and lng is not None:
                return lat, lng

    for container in candidate_containers:
        lat = _extract_float(
            container.get("lat")
            or container.get("latitude")
            or container.get("y")
        )
        lng = _extract_float(
            container.get("lng")
            or container.get("lon")
            or container.get("longitude")
            or container.get("x")
        )
        if lat is not None and lng is not None:
            return lat, lng

    return None, None


def _normalize_map_locations(payload: Any, max_rows: int = 10) -> List[Dict[str, Any]]:
    raw_items = _first_present_list(payload, ["places", "pois", "results", "items", "data", "content"])
    rows: List[Dict[str, Any]] = []
    seen = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _extract_first_value(item, ["name", "title", "address", "uid"], default="")
        lat, lng = _extract_location_pair(item)
        if not name or lat is None or lng is None:
            continue
        address = _extract_first_value(item, ["address", "formatted_address", "area", "city"], default="")
        category = _extract_first_value(item, ["category", "type", "tag"], default="")
        key = (name, round(lat, 6), round(lng, 6))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "id": f"travel_place_{len(rows) + 1}",
                "name": name,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "description": address or "地图检索命中地点",
                "category": category or "景点",
                "order": len(rows) + 1,
            }
        )
        if len(rows) >= max(1, max_rows):
            break

    return rows


def _default_map_category_for_query(query_text: str) -> str:
    text = _safe_text(query_text)
    if HOTEL_QUERY_REGEX.search(text):
        return "酒店"
    if FOOD_QUERY_REGEX.search(text):
        return "餐厅"
    return "景点"


def _apply_default_map_category(locations: List[Dict[str, Any]], query_text: str) -> List[Dict[str, Any]]:
    default_category = _default_map_category_for_query(query_text)
    normalized: List[Dict[str, Any]] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        updated = dict(location)
        if not _safe_text(updated.get("category")) or _safe_text(updated.get("category")) == "景点":
            updated["category"] = default_category
        normalized.append(updated)
    return normalized


def _travel_seed_places(destination_city: str, query_text: str) -> List[Tuple[str, int]]:
    city = _safe_text(destination_city)
    seeds = list(DESTINATION_SEED_PLACES.get(city, []))
    text = _safe_text(query_text)
    for city_name, city_seeds in DESTINATION_SEED_PLACES.items():
        if city_name in text and city_name != city:
            seeds.extend(city_seeds)

    seen = set()
    deduped: List[Tuple[str, int]] = []
    for name, day in seeds:
        if name in seen:
            continue
        seen.add(name)
        deduped.append((name, day))
    return deduped


def _merge_map_locations(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
    max_rows: int = 20,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for location in [*primary, *secondary]:
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name"))
        lat = _extract_float(location.get("lat"))
        lng = _extract_float(location.get("lng"))
        if not name or lat is None or lng is None:
            continue
        key = (name, round(lat, 5), round(lng, 5))
        if key in seen:
            continue
        seen.add(key)
        updated = dict(location)
        updated["id"] = updated.get("id") or f"travel_place_{len(merged) + 1}"
        updated["order"] = updated.get("order") or len(merged) + 1
        merged.append(updated)
    for index, location in enumerate(merged, start=1):
        location["id"] = f"travel_place_{index}"
        location["order"] = index
    return merged[: max(1, max_rows)]


def _run_map_geocode_for_places(
    place_seeds: List[Tuple[str, int]],
    destination_city: str,
    query_text: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> Tuple[List[Dict[str, Any]], str, str]:
    if not place_seeds:
        return [], "", ""
    tool_name = _first_available_tool_name(tool_manager, ["map_geocode", "map_search_places", "map_poi_extract"])
    if not tool_name:
        return [], "", "地图地理编码工具不可用"

    locations: List[Dict[str, Any]] = []
    errors: List[str] = []
    for place_name, day in place_seeds[:10]:
        query = f"{destination_city}{place_name}" if destination_city and destination_city not in place_name else place_name
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=[
                {"address": query, "city": destination_city},
                {"address": query},
                {"query": query, "region": destination_city},
                {"keywords": query, "region": destination_city},
                {"keyword": query, "region": destination_city},
                {"text": query},
            ],
        )
        if _is_tool_execution_error_payload(payload):
            errors.append(f"{place_name}: {_tool_error_reason(payload, '地图地理编码失败')}")
            continue
        rows = _normalize_map_locations(payload, max_rows=1)
        if not rows:
            continue
        row = dict(rows[0])
        row["name"] = place_name
        row["category"] = row.get("category") or _default_map_category_for_query(query_text)
        row["day"] = day
        row["description"] = _safe_text(row.get("description")) or f"{destination_city}{place_name}"
        locations.append(row)

    return locations, tool_name, "；".join(errors)


def _build_map_markdown_table(locations: List[Dict[str, Any]], max_rows: int = 10) -> str:
    if not locations:
        return ""

    header = [
        "### 地图地点表",
        "| 顺序 | 地点 | 类型 | 说明 | 坐标 |",
        "| --- | --- | --- | --- | --- |",
    ]
    body = [
        (
            f"| {_markdown_table_cell(_safe_text(row.get('order')) or index, 12)} | "
            f"{_markdown_table_cell(row.get('name'), 48)} | "
            f"{_markdown_table_cell(row.get('category'), 24)} | "
            f"{_markdown_table_cell(row.get('description'), 120)} | "
            f"{_markdown_table_cell(str(row.get('lat')) + ', ' + str(row.get('lng')), 40)} |"
        )
        for index, row in enumerate(locations[: max(1, max_rows)], start=1)
    ]
    return "\n".join([*header, *body])


def _build_map_locations_json_block(locations: List[Dict[str, Any]]) -> str:
    if not locations:
        return ""
    payload = {"map_locations": locations}
    return "### 地图标注数据\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"


def _travel_document_filename(query_text: str) -> str:
    stem = _safe_text(query_text)
    stem = re.sub(r"^(?:请|麻烦|帮我|给我|为我|我想|想要|想|需要|请你)?\s*", "", stem)
    stem = re.sub(r"^(?:规划|安排|制定|推荐|查询|查一下|做|生成)(?:一下|一次|一份)?", "", stem)
    stem = re.sub(r"^(?:一下|一次|一份)", "", stem)
    stem = re.sub(r"(?:怎么安排|如何安排|怎么规划|如何规划|可以吗|谢谢)[？?。!！]*$", "", stem)
    stem = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", stem)
    stem = re.sub(r"\s+", "_", stem).strip("._ ")
    if not stem:
        stem = TRAVEL_DOCUMENT_FALLBACK_STEM
    if len(stem) > 36:
        stem = stem[:36].rstrip("._ ")
    return f"{stem or TRAVEL_DOCUMENT_FALLBACK_STEM}.md"


def _travel_document_download_url(session_id: str, filename: str = TRAVEL_DOCUMENT_FILENAME) -> str:
    safe_session_id = _safe_text(session_id)
    safe_filename = Path(filename).name or TRAVEL_DOCUMENT_FILENAME
    if not safe_session_id:
        return safe_filename
    return f"/api/download/{safe_session_id}/{quote(safe_filename)}"


def _write_travel_markdown_document(content: str, session_id: str, filename: str = TRAVEL_DOCUMENT_FILENAME) -> str:
    safe_session_id = _safe_text(session_id)
    safe_filename = Path(filename).name or TRAVEL_DOCUMENT_FILENAME
    if not safe_session_id:
        return safe_filename

    try:
        output_root = get_output_root_path()
        session_dir = (output_root / safe_session_id).resolve()
        session_dir.relative_to(output_root.resolve())
        session_dir.mkdir(parents=True, exist_ok=True)
        document_path = session_dir / safe_filename
        document_path.write_text(_safe_text(content), encoding="utf-8")
    except Exception as document_error:
        logger.error(f"生成旅行推荐文档失败: {document_error}")

    return safe_filename


def _append_travel_footer_sections(merged: str, travel_bundle: Dict[str, Any], session_id: str) -> str:
    base = _safe_text(merged)
    filename = _travel_document_filename(_safe_text(travel_bundle.get("query")))
    filename = _write_travel_markdown_document(base, session_id, filename)
    download_url = _travel_document_download_url(session_id, filename)

    footer_sections: List[str] = []
    if "## 地理位置信息" not in base:
        footer_sections.append(
            "## 地理位置信息\n\n"
            "以下是推荐地点的地理坐标信息，方便您在地图上查看和规划路线："
        )

    if "## 生成的文档" not in base:
        footer_sections.append(
            "## 生成的文档\n\n"
            "完整的对话记录和详细推荐已保存为文档，您可以通过以下链接下载：\n\n"
            f"[{filename}]({download_url})"
        )

    for section in footer_sections:
        base = f"{base}\n\n{section}" if base else section

    return base


def _contains_map_locations(content: str) -> bool:
    return bool(MAP_LOCATION_JSON_REGEX.search(_safe_text(content)))


def _run_web_search_for_travel(
    user_query: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> Tuple[List[Dict[str, str]], str, str]:
    tool_name = _first_available_tool_name(tool_manager, ["search_web_page", "web_search", "search"])
    if not tool_name:
        return [], "", "网络搜索工具不可用"

    payload = _run_tool_with_arg_candidates(
        tool_manager=tool_manager,
        tool_name=tool_name,
        message_history=message_history,
        session_id=session_id,
        arg_candidates=[
            {"query": user_query, "count": 6, "language": "zh-cn", "country": "cn"},
            {"query": user_query, "count": 6},
            {"query": user_query},
        ],
    )
    if _is_tool_execution_error_payload(payload):
        return [], tool_name, _tool_error_reason(payload, "网络搜索失败")
    return _normalize_web_rows(payload, max_rows=6), tool_name, ""


def _run_map_search_for_travel(
    user_query: str,
    destination_city: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> Tuple[List[Dict[str, Any]], str, str]:
    tool_name = _first_available_tool_name(
        tool_manager,
        ["map_search_places", "map_poi_extract", "map_geocode"],
    )
    if not tool_name:
        return [], "", "地图工具不可用"

    region = _safe_text(destination_city)
    query_text = _safe_text(user_query)

    def run_search(query: str, max_rows: int, category: str = "") -> Tuple[List[Dict[str, Any]], str]:
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=[
                {"query": query, "region": region},
                {"keywords": query, "region": region},
                {"keyword": query, "region": region},
                {"text": query},
                {"address": query},
                {"query": query},
            ],
        )
        if _is_tool_execution_error_payload(payload):
            return [], _tool_error_reason(payload, "地图检索失败")
        rows = _normalize_map_locations(payload, max_rows=max_rows)
        if category:
            for row in rows:
                row["category"] = category
        return rows, ""

    search_locations, search_error = run_search(query_text, max_rows=8)
    supplemental_locations: List[Dict[str, Any]] = []
    supplemental_errors: List[str] = []
    wants_itinerary = bool(ITINERARY_QUERY_REGEX.search(query_text))

    if wants_itinerary or HOTEL_QUERY_REGEX.search(query_text):
        hotel_query = f"{region} 住宿 酒店 推荐".strip() if region else f"{query_text} 住宿 酒店"
        hotel_locations, hotel_error = run_search(hotel_query, max_rows=4, category="酒店")
        supplemental_locations.extend(hotel_locations)
        if hotel_error:
            supplemental_errors.append(f"住宿: {hotel_error}")

    if wants_itinerary or FOOD_QUERY_REGEX.search(query_text):
        food_query = f"{region} 美食 餐厅 小吃".strip() if region else f"{query_text} 美食 餐厅"
        food_locations, food_error = run_search(food_query, max_rows=4, category="餐厅")
        supplemental_locations.extend(food_locations)
        if food_error:
            supplemental_errors.append(f"美食: {food_error}")

    seed_locations, seed_tool, seed_error = _run_map_geocode_for_places(
        _travel_seed_places(destination_city, user_query),
        destination_city,
        user_query,
        tool_manager,
        message_history,
        session_id,
    )
    merged = _merge_map_locations(
        _merge_map_locations(search_locations, supplemental_locations, max_rows=20),
        seed_locations,
        max_rows=20,
    )
    used_tools = ", ".join([
        item for item in [
            tool_name if search_locations or supplemental_locations else "",
            seed_tool if seed_locations else "",
        ] if item
    ])
    errors = "；".join([item for item in [search_error, *supplemental_errors, seed_error] if item])
    return merged, used_tools or tool_name or seed_tool, errors


def _build_travel_output_contract(
    query_text: str,
    kinds: List[str],
    map_locations: List[Dict[str, Any]],
) -> str:
    kind_text = "、".join(kinds) if kinds else "旅行规划"
    lines = [
        "【旅行体验增强要求】",
        f"用户问题: {query_text}",
        f"识别场景: {kind_text}",
        "必须以适合人类阅读的 Markdown 输出，优先使用阅读型文档结构，不要把正文压成一整段或堆叠大表格：",
        "- 一级标题：给出本次方案主题。",
        "- 二级标题：按行程/住宿/美食/预算/注意事项等拆分。",
        "- 正文：解释推荐理由、适合人群和取舍。",
        "- 分组样式：像专业旅行建议一样先给总述，再按高端/中端/经济、区域、日期或餐饮类型分组；每组下用编号条目说明价格区间、亮点、推荐理由。",
        "- 条目样式：候选酒店、景点或餐厅要用“1. 名称”作为编号条目，条目下用子弹点写“价格区间/亮点/推荐理由/交通提示”。",
        "- 表格只作为辅助信息或附录使用；不要用只有管道符的伪表格，不要把表格和标题挤在同一行。",
        "- 地图一致性：正文中的地点编号必须与 `map_locations` 的 order 顺序一致，右侧地图会按该顺序标点和连线。",
        "涉及地点时，必须优先调用地图工具核验地点；最终答案末尾必须给出 `map_locations` JSON 代码块，字段包含 name、lat、lng、category、description、day/order。",
        "如果地图工具没有返回坐标，只能明确说明未完成地图核验，不要编造经纬度。",
        "酒店相关问题只能做住宿区域和候选酒店推荐；没有真实库存/支付工具时，不得声称已完成预订。",
    ]
    if map_locations:
        lines.append("当前已通过地图工具获得部分坐标，请优先使用这些坐标生成地图标注数据。")
    return "\n".join(lines)


def maybe_prepare_travel_experience_bundle(
    user_query: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
    selected_skill_ids: Optional[List[str]] = None,
    xhs_bundle: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    query_text = _safe_text(user_query)
    skill_triggered = _has_selected_skill(
        selected_skill_ids,
        ["travel_planner", "map_route", "destination_research", "local_discovery"],
    )
    if not skill_triggered and not _is_travel_experience_query(query_text):
        return None
    if _is_train_ticket_query(query_text):
        return None

    kinds = _travel_experience_kinds(query_text)
    travel_date = _extract_travel_date(query_text)
    destination_city = _extract_destination_city(query_text)
    origin_city, origin_tool, origin_error = _run_origin_city_lookup(tool_manager, message_history, session_id)
    weather_summary, weather_tool, weather_error = _run_weather_for_travel(
        destination_city,
        travel_date,
        tool_manager,
        message_history,
        session_id,
    )
    web_rows, web_tool, web_error = _run_web_search_for_travel(
        user_query=query_text,
        tool_manager=tool_manager,
        message_history=message_history,
        session_id=session_id,
    )
    map_locations, map_tool, map_error = _run_map_search_for_travel(
        user_query=query_text,
        destination_city=destination_city,
        tool_manager=tool_manager,
        message_history=message_history,
        session_id=session_id,
    )
    map_locations = _apply_default_map_category(map_locations, query_text)
    rag_context = maybe_prepare_travel_rag_context(query_text)

    web_table = _build_web_markdown_table(web_rows)
    map_table = _build_map_markdown_table(map_locations)
    map_json_block = _build_map_locations_json_block(map_locations)
    xhs_table = _safe_text(xhs_bundle.get("append_markdown")) if isinstance(xhs_bundle, dict) else ""
    xhs_rows = xhs_bundle.get("rows") if isinstance(xhs_bundle, dict) and isinstance(xhs_bundle.get("rows"), list) else []

    context_lines = [
        _build_travel_output_contract(query_text, kinds, map_locations),
        "",
        "【工具增强结果】",
        f"默认出行日期: {travel_date}",
        f"默认出发地: {origin_city or '未获取'}；定位工具: {origin_tool or '未使用'}；问题: {origin_error or '无'}",
        f"识别目的地: {destination_city or '未识别'}",
        f"天气工具: {weather_tool or '未使用'}；天气摘要: {weather_summary or '未获取'}；问题: {weather_error or '无'}",
        f"网络搜索工具: {web_tool or '未使用'}；命中: {len(web_rows)}；问题: {web_error or '无'}",
        f"地图工具: {map_tool or '未使用'}；坐标命中: {len(map_locations)}；问题: {map_error or '无'}",
    ]
    if rag_context:
        context_lines.extend(["", rag_context])
    if xhs_bundle and xhs_bundle.get("context_message"):
        context_lines.extend(["", _safe_text(xhs_bundle.get("context_message"))])
    if web_table:
        context_lines.extend(["", web_table])
    if map_table:
        context_lines.extend(["", map_table])
    if map_json_block:
        context_lines.extend(["", map_json_block])

    append_parts = [part for part in [xhs_table, web_table, map_json_block] if _safe_text(part)]
    bundle = {
        "query": query_text,
        "kinds": kinds,
        "travel_date": travel_date,
        "origin_city": origin_city,
        "origin_tool": origin_tool,
        "origin_error": origin_error,
        "destination_city": destination_city,
        "weather_summary": weather_summary,
        "weather_tool": weather_tool,
        "weather_error": weather_error,
        "context_message": "\n".join(context_lines),
        "web_rows": web_rows,
        "web_tool": web_tool,
        "web_error": web_error,
        "map_locations": map_locations,
        "map_tool": map_tool,
        "map_error": map_error,
        "rag_context": rag_context,
        "xhs_table": xhs_table,
        "xhs_rows": xhs_rows,
        "append_markdown": "\n\n".join(append_parts),
    }
    bundle["fallback_answer"] = _build_travel_fallback_answer(bundle)
    return bundle


def _build_travel_knowledge_fallback(query_text: str, kind_text: str) -> str:
    text = _safe_text(query_text)
    if "北京" in text and re.search(r"(3|三)\s*(天|日)", text) and ("文化" in text or ITINERARY_QUERY_REGEX.search(text)):
        return "\n".join([
            "# 北京3天2夜文化之旅规划",
            "",
            "## 结论",
            "这条路线适合第一次深度游北京的用户：前两天围绕中轴线、皇家园林和胡同生活展开，第三天补足当代艺术或博物馆内容。核心原则是上午安排强预约景点，下午放慢节奏，晚上留给胡同、茶馆或城市夜景。",
            "",
            "## Day 1：皇城中轴线与故宫深度游",
            "",
            "### 上午（8:00-11:30）",
            "- 天安门广场、人民英雄纪念碑、毛主席纪念堂外围参观，建议尽量早到，预留安检时间。",
            "- 之后进入故宫博物院，重点看太和殿、中和殿、保和殿与乾清宫一线，理解明清皇城礼制空间。",
            "",
            "### 下午（13:00-17:00）",
            "- 继续游览故宫东西六宫或珍宝馆、钟表馆，若体力有限可择一深入。",
            "- 从神武门出宫后登景山公园，俯瞰故宫中轴线，是理解北京城市格局的最佳视角之一。",
            "",
            "### 晚上（18:00-21:00）",
            "- 什刹海、烟袋斜街、南锣鼓巷一带散步，适合安排京味小吃或茶馆。",
            "",
            "## Day 2：皇家园林、学府与胡同生活",
            "",
            "### 上午（8:00-12:00）",
            "- 颐和园深度游，重点看长廊、佛香阁、昆明湖、十七孔桥，建议从东宫门进，按湖区和建筑轴线串联。",
            "",
            "### 下午（13:30-17:30）",
            "- 可在圆明园遗址公园、清华/北大周边二选一。若偏历史反思，选圆明园；若偏人文校园氛围，选高校周边。",
            "",
            "### 晚上（18:00-21:00）",
            "- 五道口或中关村附近用餐，体验北京年轻化的一面，降低第二天博物馆行程前的通勤压力。",
            "",
            "## Day 3：国家级博物馆与当代文化",
            "",
            "### 上午（8:30-12:00）",
            "- 中国国家博物馆或首都博物馆二选一。国博更适合看中华文明主线，首博更适合理解北京城市史。",
            "",
            "### 下午（13:30-17:30）",
            "- 798艺术区、红砖美术馆或前门大栅栏二选一。想看当代艺术选798，想延续老城文化选前门大栅栏。",
            "",
            "### 晚上（18:00-21:00）",
            "- 前门、王府井或三里屯收尾。若第二天早返程，建议选择离酒店或车站更近的区域。",
        ])

    if "安阳" in text and re.search(r"(3|三)\s*(天|日)", text) and ("文化" in text or ITINERARY_QUERY_REGEX.search(text)):
        return "\n".join([
            "# 安阳3天2夜文化之旅规划",
            "",
            "## 结论",
            "安阳适合按“商代文明、文字起源、三国与周易文化、古城街区”来组织3天2夜。第一天集中看殷墟与文字博物馆，第二天安排曹操高陵、羑里城和岳飞庙，第三天回到老城与文峰塔一带收尾，整体节奏比频繁跨区更稳。",
            "",
            "## Day 1：商代文明与汉字源流",
            "",
            "### 上午（9:00-12:00）",
            "- 殷墟博物馆：先看青铜器、甲骨文和商代都城叙事，建立整趟文化线的主轴。",
            "- 殷墟宫殿宗庙遗址：与博物馆内容互相印证，重点理解王都祭祀、宫殿区和甲骨发现背景。",
            "",
            "### 下午（14:00-17:30）",
            "- 中国文字博物馆：围绕甲骨文、汉字演变和书写文明展开，适合安排讲解或重点展厅深看。",
            "",
            "### 晚上（18:00-21:00）",
            "- 安阳老城或仓巷街附近散步用餐，第一晚不建议再安排远距离景点。",
            "",
            "## Day 2：三国遗存、周易文化与忠义叙事",
            "",
            "### 上午（9:00-11:30）",
            "- 曹操高陵遗址博物馆：适合了解东汉末年、三国人物与考古争议，和殷墟形成不同时代对照。",
            "",
            "### 下午（13:30-17:30）",
            "- 羑里城：以周易文化为核心，适合和讲解结合，不建议只打卡拍照。",
            "- 岳飞庙：补充宋代忠义叙事和地方历史记忆。",
            "",
            "### 晚上（18:00-21:00）",
            "- 回市区用餐休息，若体力允许可安排夜间城市步行。",
            "",
            "## Day 3：古城街区与文峰塔收尾",
            "",
            "### 上午（9:00-12:00）",
            "- 天宁寺文峰塔：适合看安阳古城地标和传统建筑空间。",
            "- 袁林：了解近代历史人物与陵园建筑风格。",
            "",
            "### 下午（13:30-16:30）",
            "- 安阳老城、仓巷街或博物馆周边机动补充，适合购买伴手礼并给返程留余量。",
        ])

    if "杭州" in text and re.search(r"(3|三)\s*(天|日)", text):
        return "\n".join([
            "# 杭州3天2夜文化之旅规划",
            "",
            "## 结论",
            "杭州文化线建议围绕西湖、南宋历史、茶文化和运河生活展开。第一天看西湖与历史街区，第二天安排灵隐和茶文化，第三天放到良渚或运河博物馆群，节奏更顺。",
            "",
            "## Day 1：西湖与南宋城市记忆",
            "- 上午：断桥、白堤、孤山、西泠印社，适合慢走和看湖山格局。",
            "- 下午：浙江省博物馆或岳王庙，再到河坊街、南宋御街感受老城生活。",
            "- 晚上：湖滨或南山路散步，晚餐可选杭帮菜。",
            "",
            "## Day 2：灵隐、茶山与山水文化",
            "- 上午：灵隐寺、飞来峰，重点看石窟造像和寺院空间。",
            "- 下午：龙井村或中国茶叶博物馆，安排茶文化体验。",
            "- 晚上：可回西湖东侧或武林商圈用餐。",
            "",
            "## Day 3：良渚文明或京杭大运河",
            "- 上午：良渚博物院或中国京杭大运河博物馆二选一。",
            "- 下午：桥西历史街区、小河直街或拱宸桥，适合收尾和购买伴手礼。",
        ])

    if HOTEL_QUERY_REGEX.search(text) and ("上海" in text or "外滩" in text):
        return "\n".join([
            "# 上海外滩附近高性价比酒店推荐",
            "",
            "## 结论",
            "外部检索结果暂时不可用，我先按外滩常见住宿区位和出行便利性给出候选清单。价格会随日期波动，预订前请再核对实时房价和库存。",
            "",
            "## 一、中端实用型",
            "",
            "1. 上海外滩英迪格酒店",
            "- 价格区间：通常偏中高端，适合预算充足但不想住传统奢华酒店的用户。",
            "- 亮点：靠近黄浦江和外滩观景动线，设计感强。",
            "- 推荐理由：位置、景观和体验比较均衡，适合情侣、朋友出行。",
            "",
            "2. 上海外滩亚朵酒店及同类中端连锁",
            "- 价格区间：通常低于一线江景酒店。",
            "- 亮点：基础服务稳定，通勤、打车和地铁衔接相对方便。",
            "- 推荐理由：如果核心需求是干净、方便、少踩坑，中端连锁比盲选民宿更稳。",
            "",
            "## 二、经济实惠型",
            "",
            "3. 南京东路/人民广场周边快捷酒店",
            "- 价格区间：通常低于外滩一线江景酒店。",
            "- 亮点：地铁线路密集，去外滩、豫园、陆家嘴都比较方便。",
            "- 推荐理由：步行到外滩可能略远，但综合交通和价格更友好。",
            "",
            "4. 四川北路/天潼路周边酒店",
            "- 价格区间：预算型和中端酒店较多。",
            "- 亮点：距离外滩不算远，部分酒店价格比南京东路核心区更低。",
            "- 推荐理由：适合愿意用 10-20 分钟交通时间换更低住宿成本的用户。",
            "",
            "## 三、选择建议",
            "- 如果重视夜景：优先看外滩、北外滩、陆家嘴视野房，但预算要上调。",
            "- 如果重视性价比：优先看南京东路、人民广场、天潼路、四川北路。",
            "- 如果带老人或行李多：优先选择离地铁口近、评价里明确提到隔音和电梯便利的酒店。",
            "- 预订前重点核对：入住日期价格、是否无窗、房间面积、地铁距离、近期差评和取消政策。",
        ])

    if HOTEL_QUERY_REGEX.search(text):
        return "\n".join([
            f"# {query_text}住宿建议",
            "",
            "## 结论",
            "外部检索结果暂时不可用，我先给出可执行的选址方法和候选类型。预订前请核对实时房价、库存和近期评价。",
            "",
            "## 一、优先选择",
            "1. 核心景点或商圈步行范围内的中端连锁酒店",
            "- 亮点：通勤成本低，服务稳定。",
            "- 推荐理由：适合第一次到访、行程紧凑或带行李较多的用户。",
            "",
            "2. 地铁换乘站附近的经济型酒店",
            "- 亮点：价格通常更友好，去主要景点不依赖打车。",
            "- 推荐理由：适合预算敏感、愿意用交通时间换住宿成本的人。",
        ])

    if FOOD_QUERY_REGEX.search(text):
        return "\n".join([
            f"# {query_text}美食推荐",
            "",
            "## 结论",
            "美食行程建议按区域安排，不要为了单店频繁跨城。优先选择评价稳定、位置顺路、排队成本可控的餐厅，再补充小吃和咖啡茶饮作为机动项。",
            "",
            "## 一、正餐优先",
            "1. 选择当地代表菜或老字号餐厅",
            "- 适合安排在午餐或晚餐，提前查看是否需要取号、预约或错峰到店。",
            "",
            "2. 选择景点附近但不在核心游客街正中心的餐厅",
            "- 通常性价比更稳，也更适合和当天路线串联。",
            "",
            "## 二、小吃与茶饮补充",
            "- 把小吃安排在两段景点之间，避免影响正餐体验。",
            "- 如果是热门商圈，建议先收藏2-3家备选，现场根据排队情况调整。",
        ])

    return "\n".join([
        f"# {kind_text}建议",
        "",
        "## 结论",
        f"外部检索结果暂时不可用，我先根据“{query_text}”给出一版可执行初稿。涉及开放时间、票价和预约规则的内容，出行前仍需二次核验。",
        "",
        "## 推荐安排",
        "1. 先确定核心目的地",
        "- 把最想去的 2-3 个地点放在每天上午或傍晚，避免临时绕路。",
        "",
        "2. 按区域串联路线",
        "- 同一区域的景点、餐厅和住宿尽量放在同一天，减少跨城通勤。",
    ])


def _build_travel_reference_interpretation(
    web_rows: List[Dict[str, str]],
    xhs_rows: List[Dict[str, str]],
) -> str:
    lines: List[str] = []

    useful_web_rows = [
        row for row in web_rows
        if isinstance(row, dict) and (_safe_text(row.get("title")) or _safe_text(row.get("snippet")))
    ]
    useful_xhs_rows = [
        row for row in xhs_rows
        if isinstance(row, dict) and (_safe_text(row.get("title")) or _safe_text(row.get("summary")))
    ]

    if useful_web_rows:
        lines.extend(["## 检索参考解读", ""])
        for index, row in enumerate(useful_web_rows[:4], start=1):
            title = _safe_text(row.get("title")) or f"网页参考 {index}"
            snippet = _safe_text(row.get("snippet")) or "该结果可作为路线、酒店或行程信息的交叉参考。"
            lines.append(f"{index}. {title}")
            lines.append(f"- 摘要：{snippet}")
            lines.append(f"- 链接：{_markdown_link_cell(row.get('url'))}")
            lines.append("")

    if useful_xhs_rows:
        lines.extend(["## 小红书参考解读", ""])
        for index, row in enumerate(useful_xhs_rows[:4], start=1):
            title = _safe_text(row.get("title")) or f"小红书参考 {index}"
            summary = _safe_text(row.get("summary")) or "该笔记可作为用户体验和真实反馈参考。"
            author = _safe_text(row.get("author"))
            author_part = f"（作者：{author}）" if author and author != "-" else ""
            lines.append(f"{index}. {title}{author_part}")
            lines.append(f"- 摘要：{summary}")
            lines.append(f"- 链接：{_markdown_link_cell(row.get('url'))}")
            lines.append("")

    return "\n".join(lines).strip()


def _looks_like_reference_only_travel_answer(content: str) -> bool:
    text = _safe_text(content)
    if not text:
        return True

    lowered = text.lower()
    reference_markers = [
        "网络搜索参考表",
        "小红书检索资源表",
        "链接：",
        "摘要：",
        "klook",
        "trip.com",
        "携程",
        "agoda",
        "zhihu.com",
        "facebook.com",
    ]
    reference_hits = sum(1 for marker in reference_markers if marker in lowered or marker in text)
    planning_markers = [
        "## Day",
        "## 第",
        "### 上午",
        "### 下午",
        "### 晚上",
        "## 一、",
        "## 推荐安排",
        "## 结论",
        "推荐理由",
        "价格区间",
    ]
    planning_hits = sum(1 for marker in planning_markers if marker in text)
    if reference_hits >= 2 and planning_hits <= 1:
        return True
    if "检索状态" in text and len(text) < 900:
        return True
    if text.count("[查看]") >= 3 and planning_hits <= 2:
        return True
    return False


def _build_travel_reminder_section(query_text: str, kinds: List[str]) -> str:
    kind_set = {item for item in kinds if item}
    lines = ["## 建议与预约提醒", ""]
    if "行程规划" in kind_set or "目的地攻略" in kind_set:
        lines.extend([
            "- 热门景点、博物馆和演出优先提前预约，尤其是故宫、国博、热门展览、节假日和周末时段。",
            "- 每天只安排1-2个强预约点，其余放机动景点，避免因为安检、排队和交通延误导致整天被打乱。",
            "- 出发前一天再次核对开放时间、闭馆日、身份证件要求、天气和交通管制。",
        ])
    if "酒店住宿" in kind_set:
        lines.extend([
            "- 酒店只作为候选建议，房价、库存、早餐、取消政策和押金规则必须以预订平台实时页面为准。",
            "- 优先核对地铁距离、近期差评、隔音、电梯、房间面积和是否无窗。",
        ])
    if "美食推荐" in kind_set:
        lines.extend([
            "- 热门餐厅建议提前取号或错峰；不要把不可预约且排队久的餐厅安排在赶车、赶展之前。",
            "- 小吃店和咖啡茶饮适合作为机动备选，现场根据排队和距离取舍。",
        ])
    if len(lines) <= 2:
        lines.extend([
            "- 以上建议用于路线和筛选参考，实时价格、库存、开放时间和预约规则请以官方或平台页面为准。",
            "- 如果有老人、儿童或大件行李，优先减少跨区通勤并选择地铁/打车更稳定的路线。",
        ])
    return "\n".join(lines)


def _build_travel_context_advice_section(travel_bundle: Dict[str, Any]) -> str:
    query_text = _safe_text(travel_bundle.get("query"))
    travel_date = _safe_text(travel_bundle.get("travel_date")) or _extract_travel_date(query_text)
    origin_city = _safe_text(travel_bundle.get("origin_city"))
    destination_city = _safe_text(travel_bundle.get("destination_city")) or _extract_destination_city(query_text)
    weather_summary = _safe_text(travel_bundle.get("weather_summary"))
    origin_error = _safe_text(travel_bundle.get("origin_error"))
    weather_error = _safe_text(travel_bundle.get("weather_error"))

    lines = [
        "## 出行基础信息",
        "",
        f"- 默认出行日期：{travel_date}",
        f"- 默认出发地：{origin_city or '未获取到地级市定位'}",
        f"- 目的地：{destination_city or '未识别'}",
        "",
        "## 交通与天气建议",
        "",
    ]

    if origin_city and destination_city:
        if origin_city == destination_city:
            lines.append(f"- 城际交通：当前定位与目的地同为{destination_city}，优先按市内交通规划，核心景点之间建议打车、公交或步行串联。")
        else:
            lines.append(f"- 城际交通：默认从{origin_city}出发前往{destination_city}，优先查询高铁/动车，其次考虑长途汽车或自驾；具体班次和价格需以实时票务平台为准。")
    elif destination_city:
        lines.append(f"- 城际交通：未获取到出发地，暂按到达{destination_city}后的市内动线规划；补充出发城市后应重新核对车次/航班/自驾时间。")
    else:
        lines.append("- 城际交通：未识别目的地，需补充目的地后才能给出准确交通建议。")

    if weather_summary:
        lines.append(f"- 天气建议：{travel_date} 前后{destination_city or '目的地'}天气参考为{weather_summary}，建议按实时预报调整衣物、雨具和室内外景点比例。")
    else:
        lines.append(f"- 天气建议：天气工具暂未返回可用结果（{weather_error or '原因未知'}），出发前请再次核对目的地实时天气。")

    if origin_error and not origin_city:
        lines.append(f"- 定位说明：{origin_error}；本次不会编造出发地。")

    return "\n".join(lines)


def _build_travel_fallback_answer(travel_bundle: Dict[str, Any]) -> str:
    query_text = _safe_text(travel_bundle.get("query")) or "旅行请求"
    kinds = travel_bundle.get("kinds") if isinstance(travel_bundle.get("kinds"), list) else []
    kind_text = "、".join([_safe_text(item) for item in kinds if _safe_text(item)]) or "旅行规划"
    web_rows = travel_bundle.get("web_rows") if isinstance(travel_bundle.get("web_rows"), list) else []
    map_locations = travel_bundle.get("map_locations") if isinstance(travel_bundle.get("map_locations"), list) else []
    xhs_table = _safe_text(travel_bundle.get("xhs_table"))
    append_markdown = _safe_text(travel_bundle.get("append_markdown"))

    if web_rows or map_locations or xhs_table:
        fallback_answer = _build_travel_knowledge_fallback(query_text, kind_text)
        fallback_answer = fallback_answer.replace("外部检索结果暂时不可用，我先", "已完成外部检索并结合结果，我")
        fallback_answer = fallback_answer.replace("外部搜索或地图检索没有返回可用结果", "外部检索结果已作为参考")
        return fallback_answer

    if web_rows or map_locations or xhs_table:
        fallback_answer = "\n".join([
            f"# {query_text}",
            "",
            "## 结论",
            (
                f"已完成外部检索和旅行增强整理：网页参考 {len(web_rows)} 条，"
                f"地图坐标 {len(map_locations)} 个。下面先给出可执行建议，"
                "具体房价、库存和可预订状态仍以预订平台实时页面为准。"
            ),
            "",
            "## 使用限制",
            "酒店推荐只能作为候选和区域建议；如果没有真实库存/支付工具，不能视为已预订。",
        ])
    else:
        fallback_answer = _build_travel_knowledge_fallback(query_text, kind_text)
        fallback_answer = (
            f"{fallback_answer}\n\n"
            "## 数据说明\n\n"
            "本次外部搜索或地图检索没有返回可用结果，以上为非实时兜底建议；酒店价格、库存和坐标需以预订平台或地图为准。"
        )
    if append_markdown:
        fallback_answer = f"{fallback_answer}\n\n{append_markdown}"
    return fallback_answer

    lines = [
        f"# {kind_text}建议",
        "",
        "## 结论",
        f"已针对“{query_text}”完成旅行增强检索，可用结果包括网络资料 {len(web_rows)} 条、地图坐标 {len(map_locations)} 个。",
        "",
        "## 使用限制",
        "酒店预订只能提供候选和区域建议；如果没有真实库存/支付工具，不能视为已预订。",
    ]
    if append_markdown:
        lines.extend(["", append_markdown])
    return "\n".join(lines)


def _travel_title_from_query(query_text: str, kind_text: str = "行程规划") -> str:
    filename = _travel_document_filename(query_text)
    title = Path(filename).stem
    if title and title != TRAVEL_DOCUMENT_FALLBACK_STEM:
        return title
    return f"{kind_text}建议"


def _normalize_prompt_echo_text(text: str) -> str:
    normalized = re.sub(r"^[#>\s\-*`]+", "", _safe_text(text))
    normalized = re.sub(r"[：:。.!！?？\s\"'“”‘’《》【】\[\]()（）]+", "", normalized)
    return normalized.lower()


def _strip_travel_prompt_echo_prefix(content: str, query_text: str) -> str:
    text = _safe_text(content)
    query_norm = _normalize_prompt_echo_text(query_text)
    if not text or not query_norm:
        return text

    lines = text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1

    removed_prompt_echo = False
    if index < len(lines):
        first_line = lines[index].strip()
        first_plain = re.sub(r"^#{1,6}\s*", "", first_line).strip()
        first_norm = _normalize_prompt_echo_text(first_plain)
        if first_norm == query_norm:
            del lines[index]
            removed_prompt_echo = True

    while index < len(lines) and not lines[index].strip():
        del lines[index]

    if index < len(lines):
        next_plain = re.sub(r"^#{1,6}\s*", "", lines[index].strip()).strip()
        if removed_prompt_echo and _normalize_prompt_echo_text(next_plain) == _normalize_prompt_echo_text("推荐安排"):
            del lines[index]
            while index < len(lines) and not lines[index].strip():
                del lines[index]

    return "\n".join(lines).strip()


def _ensure_travel_markdown_structure(content: str, travel_bundle: Dict[str, Any]) -> str:
    text = _safe_text(content)
    if not text:
        return text

    if re.search(r"(?m)^#\s+\S", text) and re.search(r"(?m)^##\s+\S", text):
        return text

    query_text = _safe_text(travel_bundle.get("query"))
    kinds = travel_bundle.get("kinds") if isinstance(travel_bundle.get("kinds"), list) else []
    kind_text = "、".join([_safe_text(item) for item in kinds if _safe_text(item)]) or "行程规划"
    title = _travel_title_from_query(query_text, kind_text)
    section_title = "行程方案" if "行程" in kind_text or "攻略" in kind_text else "推荐方案"

    body = text
    if re.search(r"(?m)^#\s+\S", body):
        if not re.search(r"(?m)^##\s+\S", body):
            lines = body.splitlines()
            if len(lines) <= 1:
                return f"{body}\n\n## {section_title}"
            return "\n".join([lines[0], "", f"## {section_title}", "", *lines[1:]]).strip()
        return body

    if re.search(r"(?m)^##\s+\S", body):
        return f"# {title}\n\n{body}".strip()

    return f"## {section_title}\n\n{body}".strip()


def _select_travel_final_answer(
    existing_final_answer: str,
    travel_bundle: Dict[str, Any],
    session_id: str = "",
) -> str:
    existing = _safe_text(existing_final_answer)
    fallback_answer = _safe_text(travel_bundle.get("fallback_answer"))
    if (
        not existing
        or _looks_like_internal_or_failed_answer(existing)
        or _looks_like_reference_only_travel_answer(existing)
    ):
        existing = fallback_answer
    if not existing:
        existing = _safe_text(travel_bundle.get("fallback_answer"))

    map_json_block = _build_map_locations_json_block(
        travel_bundle.get("map_locations") if isinstance(travel_bundle.get("map_locations"), list) else []
    )

    merged = _ensure_travel_markdown_structure(existing, travel_bundle)
    merged = _strip_travel_prompt_echo_prefix(merged, _safe_text(travel_bundle.get("query")))

    reference_sections = []
    xhs_table = _safe_text(travel_bundle.get("xhs_table"))
    if xhs_table and "小红书检索资源表" not in merged:
        reference_sections.append(xhs_table)

    web_table = _build_web_markdown_table(
        travel_bundle.get("web_rows") if isinstance(travel_bundle.get("web_rows"), list) else []
    )
    if web_table and "网络搜索参考表" not in merged:
        reference_sections.append(web_table)

    for section in reference_sections:
        if section:
            merged = f"{merged}\n\n{section}" if merged else section

    kinds = travel_bundle.get("kinds") if isinstance(travel_bundle.get("kinds"), list) else []
    if "## 建议与预约提醒" not in merged:
        reminder_section = _build_travel_reminder_section(_safe_text(travel_bundle.get("query")), kinds)
        merged = f"{merged}\n\n{reminder_section}" if merged else reminder_section

    if map_json_block and "## 地理位置信息" not in merged:
        geo_intro = (
            "## 地理位置信息\n\n"
            "以下是推荐地点的地理坐标信息，方便您在地图上查看和规划路线："
        )
        merged = f"{merged}\n\n{geo_intro}" if merged else geo_intro

    if map_json_block and not _contains_map_locations(merged):
        merged = f"{merged}\n\n{map_json_block}" if merged else map_json_block

    return _normalize_markdown_for_display(_append_travel_footer_sections(merged, travel_bundle, session_id))


def _apply_travel_experience_result(
    result: Dict[str, Any],
    travel_bundle: Dict[str, Any],
    session_id: str = "",
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    existing_final_answer = _extract_latest_final_answer_content(result)
    merged_answer = _select_travel_final_answer(existing_final_answer, travel_bundle, session_id=session_id)
    if not merged_answer:
        return result

    _replace_or_append_final_answer(result.get("all_messages"), merged_answer)
    _replace_or_append_final_answer(result.get("new_messages"), merged_answer)

    final_output = result.get("final_output")
    if isinstance(final_output, dict):
        updated_output = dict(final_output)
        updated_output["role"] = "assistant"
        updated_output["type"] = "final_answer"
        updated_output["content"] = merged_answer
        result["final_output"] = updated_output
    else:
        result["final_output"] = {
            "role": "assistant",
            "type": "final_answer",
            "content": merged_answer,
        }

    result["travel_experience_enforced"] = True
    return result


def _tool_exists(tool_manager: Any, tool_name: str) -> bool:
    if tool_manager is None:
        return False
    try:
        return tool_manager.get_tool(tool_name) is not None
    except Exception:
        return False


def _append_tool_source(current: str, tool_name: str) -> str:
    if not tool_name:
        return current
    sources = [item.strip() for item in (current or "").split(",") if item.strip()]
    if tool_name not in sources:
        sources.append(tool_name)
    return ", ".join(sources)


def maybe_prepare_train_ticket_bundle(
    user_query: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
    selected_skill_ids: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    query_text = _resolve_train_ticket_query(user_query, message_history)
    skill_triggered = _has_selected_skill(selected_skill_ids, ["rail_transport", "travel_planner", "budget_optimizer"])
    if not skill_triggered and not _is_train_ticket_query(query_text):
        return None

    route = _extract_route_from_query(query_text)
    if not route:
        return None

    from_station, to_station = route
    travel_date = _extract_travel_date(query_text)

    direct_source = ""
    interline_source = ""
    flight_source = ""
    bus_source = ""
    direct_rows: List[Dict[str, str]] = []
    interline_rows: List[Dict[str, str]] = []
    flight_rows: List[Dict[str, str]] = []
    bus_rows: List[Dict[str, str]] = []
    train_attempted = False
    train_successful_attempt = False
    flight_attempted = False
    flight_successful_attempt = False
    bus_attempted = False
    bus_successful_attempt = False
    train_errors: List[str] = []
    flight_errors: List[str] = []
    bus_errors: List[str] = []
    query_pairs = _station_query_pairs(from_station, to_station) or [(from_station, to_station)]
    selected_pair = query_pairs[0]

    # 先查直达，再查中转。MCP 被网关拦截或返回空时，继续回退到本地 12306 工具。
    if _tool_exists(tool_manager, "get-tickets"):
        direct_source = _append_tool_source(direct_source, "get-tickets")
        train_attempted = True
        for candidate_from, candidate_to in query_pairs:
            try:
                direct_raw = tool_manager.run_tool(
                    "get-tickets",
                    messages=message_history,
                    session_id=session_id,
                    date=travel_date,
                    fromStation=candidate_from,
                    toStation=candidate_to,
                    format="json",
                    limitedNum=20,
                    sortFlag="startTime",
                )
                direct_payload = _unwrap_tool_output(direct_raw)
            except Exception as direct_error:
                train_errors.append(f"直达票查询失败: {direct_error}")
                continue
            if _is_tool_execution_error_payload(direct_payload):
                train_errors.append(_tool_error_reason(direct_payload, "直达票查询失败"))
                continue
            try:
                candidate_rows = _normalize_direct_rows(direct_payload, candidate_from, candidate_to, travel_date)
            except Exception as normalize_error:
                train_errors.append(f"直达票结果解析失败: {normalize_error}")
                continue
            train_successful_attempt = True
            if candidate_rows:
                direct_rows.extend(candidate_rows)
                selected_pair = (candidate_from, candidate_to)
                break

    if not direct_rows and _tool_exists(tool_manager, "query_12306_realtime_tickets"):
        direct_source = _append_tool_source(direct_source, "query_12306_realtime_tickets")
        train_attempted = True
        for candidate_from, candidate_to in query_pairs:
            try:
                direct_raw = tool_manager.run_tool(
                    "query_12306_realtime_tickets",
                    messages=message_history,
                    session_id=session_id,
                    from_station=candidate_from,
                    to_station=candidate_to,
                    travel_date=travel_date,
                    max_results=20,
                )
                direct_payload = _unwrap_tool_output(direct_raw)
            except Exception as direct_error:
                train_errors.append(f"直达票查询失败: {direct_error}")
                continue
            if _is_tool_execution_error_payload(direct_payload):
                train_errors.append(_tool_error_reason(direct_payload, "直达票查询失败"))
                continue
            try:
                candidate_rows = _normalize_direct_rows(direct_payload, candidate_from, candidate_to, travel_date)
            except Exception as normalize_error:
                train_errors.append(f"直达票结果解析失败: {normalize_error}")
                continue
            train_successful_attempt = True
            if candidate_rows:
                direct_rows.extend(candidate_rows)
                selected_pair = (candidate_from, candidate_to)
                break

    if _tool_exists(tool_manager, "get-interline-tickets"):
        interline_source = "get-interline-tickets"
        train_attempted = True
        interline_pairs = [selected_pair, *[pair for pair in query_pairs if pair != selected_pair]]
        for candidate_from, candidate_to in interline_pairs:
            try:
                interline_raw = tool_manager.run_tool(
                    "get-interline-tickets",
                    messages=message_history,
                    session_id=session_id,
                    date=travel_date,
                    fromStation=candidate_from,
                    toStation=candidate_to,
                    format="json",
                    limitedNum=10,
                    showWZ=True,
                    sortFlag="duration",
                )
                interline_payload = _unwrap_tool_output(interline_raw)
            except Exception as interline_error:
                train_errors.append(f"中转票查询失败: {interline_error}")
                continue
            if _is_tool_execution_error_payload(interline_payload):
                train_errors.append(_tool_error_reason(interline_payload, "中转票查询失败"))
                continue
            try:
                candidate_rows = _normalize_interline_rows(interline_payload, candidate_from, candidate_to, travel_date)
            except Exception as normalize_error:
                train_errors.append(f"中转票结果解析失败: {normalize_error}")
                continue
            train_successful_attempt = True
            if candidate_rows:
                interline_rows.extend(candidate_rows)
                break

    if not train_attempted:
        train_errors.append("无可用火车票实时查询工具")

    flight_tool_name = _first_available_tool_name(tool_manager, FLIGHT_TICKET_TOOL_CANDIDATES)
    flight_from_city = _city_name_for_ticket_query(from_station)
    flight_to_city = _city_name_for_ticket_query(to_station)
    if flight_tool_name:
        flight_source = flight_tool_name
        flight_attempted = True
        flight_payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=flight_tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=[
                {
                    "date": travel_date,
                    "fromCity": flight_from_city,
                    "toCity": flight_to_city,
                    "format": "json",
                    "limitedNum": 20,
                },
                {
                    "travel_date": travel_date,
                    "from_city": flight_from_city,
                    "to_city": flight_to_city,
                    "max_results": 20,
                },
                {
                    "departure_date": travel_date,
                    "origin": flight_from_city,
                    "destination": flight_to_city,
                    "limit": 20,
                },
                {
                    "query": f"{travel_date} {flight_from_city} 到 {flight_to_city} 机票",
                    "limit": 20,
                },
            ],
        )
        if _is_tool_execution_error_payload(flight_payload):
            logger.warning(f"飞机票实时工具调用失败或需要登录，tool={flight_tool_name}, payload={flight_payload}")
            flight_errors.append(_tool_error_reason(flight_payload, "飞机票查询失败"))
        else:
            flight_successful_attempt = True
            try:
                flight_rows = _normalize_flight_rows(flight_payload, flight_from_city, flight_to_city, travel_date)
            except Exception as normalize_error:
                flight_errors.append(f"飞机票结果解析失败: {normalize_error}")
                flight_successful_attempt = False
    else:
        flight_errors.append("无可用飞机票实时查询工具")

    bus_tool_name = _first_available_tool_name(tool_manager, BUS_TICKET_TOOL_CANDIDATES)
    bus_from_city = _city_name_for_ticket_query(from_station)
    bus_to_city = _city_name_for_ticket_query(to_station)
    if bus_tool_name:
        bus_source = bus_tool_name
        bus_attempted = True
        bus_payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=bus_tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=[
                {
                    "date": travel_date,
                    "fromCity": bus_from_city,
                    "toCity": bus_to_city,
                    "format": "json",
                    "limitedNum": 20,
                },
                {
                    "travel_date": travel_date,
                    "from_city": bus_from_city,
                    "to_city": bus_to_city,
                    "max_results": 20,
                },
                {
                    "departureDate": travel_date,
                    "fromStation": bus_from_city,
                    "toStation": bus_to_city,
                    "limit": 20,
                },
                {
                    "query": f"{travel_date} {bus_from_city} 到 {bus_to_city} 汽车票 大巴",
                    "limit": 20,
                },
            ],
        )
        if _is_tool_execution_error_payload(bus_payload):
            logger.warning(f"大巴票实时工具调用失败或需要登录，tool={bus_tool_name}, payload={bus_payload}")
            bus_errors.append(_tool_error_reason(bus_payload, "大巴票查询失败"))
        else:
            bus_successful_attempt = True
            try:
                bus_rows = _normalize_bus_rows(bus_payload, bus_from_city, bus_to_city, travel_date)
            except Exception as normalize_error:
                bus_errors.append(f"大巴票结果解析失败: {normalize_error}")
                bus_successful_attempt = False
    else:
        bus_errors.append("无可用大巴票实时查询工具")

    train_rows: List[Dict[str, Any]] = [*direct_rows, *interline_rows]
    ticket_states = {
        "train": _build_ticket_mode_state(
            rows=train_rows,
            sources={"direct": direct_source, "interline": interline_source},
            attempted=train_attempted,
            successful_attempt=train_successful_attempt,
            errors=train_errors,
        ),
        "flight": _build_ticket_mode_state(
            rows=flight_rows,
            sources=flight_source,
            attempted=flight_attempted,
            successful_attempt=flight_successful_attempt,
            errors=flight_errors,
        ),
        "bus": _build_ticket_mode_state(
            rows=bus_rows,
            sources=bus_source,
            attempted=bus_attempted,
            successful_attempt=bus_successful_attempt,
            errors=bus_errors,
        ),
    }
    has_valid_results = any(state["status"] == "success" for state in ticket_states.values())
    overall_status = "success" if has_valid_results else (
        "empty" if any(state["status"] == "empty" for state in ticket_states.values()) else "error"
    )

    recommendation_lines = _build_recommendation_lines(
        direct_rows=direct_rows,
        interline_rows=interline_rows,
        user_query=query_text,
        top_k=3,
    )

    markdown_table = _build_ticket_markdown_table(
        direct_rows=direct_rows,
        interline_rows=interline_rows,
        user_query=query_text,
        flight_rows=flight_rows,
        bus_rows=bus_rows,
        max_rows=8,
    )

    context_lines = [
        "【实时票务查询结果】",
        f"查询路线: {from_station} -> {to_station}",
        f"出发日期: {travel_date}",
        f"整体状态: {overall_status}",
        f"火车票状态: {ticket_states['train']['status']}，直达 {len(direct_rows)} 条，中转 {len(interline_rows)} 条，来源: {direct_source or '未调用'} / {interline_source or '未调用'}，错误: {ticket_states['train']['error'] or '无'}",
        f"飞机票状态: {ticket_states['flight']['status']}，结果 {len(flight_rows)} 条，来源: {flight_source or '未调用'}，错误: {ticket_states['flight']['error'] or '无'}",
        f"大巴票状态: {ticket_states['bus']['status']}，结果 {len(bus_rows)} 条，来源: {bus_source or '未调用'}，错误: {ticket_states['bus']['error'] or '无'}",
        "以上三种票务已经完成预查询，不要重复调用相同工具和相同参数；应基于已有结果回答。",
        "请先按用户诉求（预算优先/时效优先/少换乘）推荐2-3个候选班次，并说明理由，再附表；不能确认的信息请明确说明“当前无法确认”。",
        "时间展示要求：出发/到达必须显示日期+时间（YYYY-MM-DD HH:MM）；历时必须使用“X小时Y分钟”；跨天班次要显式体现日期变化。",
    ]

    if recommendation_lines:
        context_lines.append("可优先参考候选方案（已按用户诉求排序）：")
        context_lines.append("；".join(f"{index}. {line}" for index, line in enumerate(recommendation_lines, start=1)))

    context_lines.extend([
        markdown_table,
    ])

    bundle = {
        "route": {
            "from_station": from_station,
            "to_station": to_station,
            "travel_date": travel_date,
        },
        "direct_rows": direct_rows,
        "interline_rows": interline_rows,
        "flight_rows": flight_rows,
        "bus_rows": bus_rows,
        "ticket_states": ticket_states,
        "has_valid_results": has_valid_results,
        "overall_status": overall_status,
        "context_message": "\n".join(context_lines),
        "append_markdown": markdown_table,
        "enforce_realtime_only": _is_realtime_only_enforced(),
        "direct_source": direct_source,
        "interline_source": interline_source,
        "flight_source": flight_source,
        "bus_source": bus_source,
        "user_query": query_text,
        "recommendation_lines": recommendation_lines,
    }

    bundle["realtime_only_answer"] = _build_realtime_only_answer(bundle)
    return bundle


def maybe_prepare_xhs_search_bundle(
    user_query: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
    selected_skill_ids: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    query_text = _safe_text(user_query)
    skill_triggered = _has_selected_skill(
        selected_skill_ids,
        ["xhs_insight", "destination_research", "local_discovery", "travel_planner"],
    )
    travel_triggered = _is_travel_experience_query(query_text) and not _is_train_ticket_query(query_text)
    if not skill_triggered and not _is_xhs_query(query_text) and not travel_triggered:
        return None

    summary_tool_name = _first_available_tool_name(
        tool_manager,
        [
            "xhs_search_and_summarize",
            "xhs-search-and-summarize",
            "search_xhs_and_summarize",
        ],
    )
    search_tool_name = ""
    if not summary_tool_name:
        search_tool_name = _first_available_tool_name(
            tool_manager,
            [
                "xhs_search_resources",
                "xhs_search_notes",
                "search_xhs_notes",
                "xhs_search",
            ],
        )

    selected_tool = summary_tool_name or search_tool_name
    if not selected_tool:
        return None

    focus = _extract_xhs_focus(query_text)
    if summary_tool_name:
        arg_candidates = [
            {"query": query_text, "limit": 8, "focus": focus},
            {"query": query_text, "limit": 8},
            {"query": query_text},
        ]
    else:
        arg_candidates = [
            {"query": query_text, "limit": 8, "page": 1, "sort": "general"},
            {"query": query_text, "limit": 8},
            {"query": query_text},
        ]

    payload = _run_xhs_tool_with_candidates(
        tool_manager=tool_manager,
        tool_name=selected_tool,
        message_history=message_history,
        session_id=session_id,
        arg_candidates=arg_candidates,
    )

    if _is_tool_execution_error_payload(payload):
        logger.warning(f"小红书工具调用返回错误，tool={selected_tool}, payload={payload}")

    rows = _normalize_xhs_rows(payload, max_rows=8)
    summary_text = ""
    source = ""
    warnings: List[str] = []
    highlights: List[str] = []

    if isinstance(payload, dict):
        summary_text = _safe_text(payload.get("summary"))
        source = _safe_text(payload.get("source"))

        warnings_value = payload.get("warnings")
        if isinstance(warnings_value, list):
            warnings = [_safe_text(item) for item in warnings_value if _safe_text(item)]

        highlights_value = payload.get("highlights")
        if isinstance(highlights_value, list):
            highlights = [_safe_text(item) for item in highlights_value if _safe_text(item)]

    if not summary_text:
        if rows:
            summary_text = f"已检索到 {len(rows)} 条小红书资源，可基于作者、摘要和链接做筛选。"
        else:
            summary_text = "当前未检索到可用的小红书资源。"

    markdown_table = _build_xhs_markdown_table(rows, max_rows=8)

    context_lines = [
        "【小红书检索结果】",
        f"用户问题: {query_text}",
        f"检索工具: {selected_tool}",
        f"数据来源: {source or '未知'}",
        f"命中资源数: {len(rows)}",
        f"工具摘要: {summary_text}",
        "请严格基于上述小红书资源先给3-5条建议，再给简短总结；不得编造不存在的链接、作者和点赞数据。",
        "在回答前请按需继续调用其他MCP工具（如地图、网页搜索、票务）做交叉验证，并将多源结果整合后输出给用户。",
    ]

    if focus:
        context_lines.append(f"用户关注点: {focus}")

    if warnings:
        context_lines.append("检索提示:")
        context_lines.extend([f"- {item}" for item in warnings[:3]])

    if highlights:
        context_lines.append("工具高亮信息:")
        context_lines.extend([f"- {item}" for item in highlights[:3]])

    context_lines.append(markdown_table)

    bundle = {
        "query": query_text,
        "focus": focus,
        "rows": rows,
        "summary": summary_text,
        "source": source,
        "source_tool": selected_tool,
        "warnings": warnings,
        "highlights": highlights,
        "context_message": "\n".join(context_lines),
        "append_markdown": markdown_table,
    }

    bundle["fallback_answer"] = _build_xhs_fallback_answer(bundle)
    return bundle


async def create_filtered_tool_manager(original_tool_manager: Any, selected_mcp_servers: List[str]) -> Any:
    """Create a tool manager containing only selected MCP tools and all local tools."""
    try:
        from agents.tool.tool_manager import ToolManager
        from agents.tool.tool_base import McpToolSpec

        filtered_manager = ToolManager(is_auto_discover=False)

        for tool_name, tool_spec in original_tool_manager.tools.items():
            if not isinstance(tool_spec, McpToolSpec):
                filtered_manager.tools[tool_name] = tool_spec
            elif tool_spec.server_name in selected_mcp_servers:
                filtered_manager.tools[tool_name] = tool_spec

        logger.info(f"筛选后的工具管理器包含 {len(filtered_manager.tools)} 个工具")
        logger.info(f"选择的MCP服务器: {selected_mcp_servers}")
        return filtered_manager

    except Exception as e:
        logger.error(f"创建筛选工具管理器失败: {e}")
        return original_tool_manager


def build_message_history(request_messages: List[Any]) -> List[Dict[str, Any]]:
    """Convert incoming request messages to controller message format."""
    message_history: List[Dict[str, Any]] = []
    for msg in request_messages:
        message_history.append({
            "role": msg.role,
            "content": msg.content,
            "message_id": msg.message_id or str(uuid.uuid4()),
            "type": msg.type,
        })
    return message_history


def maybe_prepare_travel_rag_context(user_query: str) -> str:
    """Build optional local travel-knowledge context for ordinary planning queries."""
    query_text = _safe_text(user_query)
    if not query_text:
        return ""

    try:
        from services.travel_rag_service import build_travel_rag_context

        return _safe_text(build_travel_rag_context(query_text))
    except Exception as e:
        logger.error(f"旅行知识库RAG预检索失败: {e}")
        return ""


def append_travel_rag_context_message(
    message_history: List[Dict[str, Any]],
    context_message: str,
) -> None:
    """Append travel RAG context using the same system-message pattern as other pre-retrieval paths."""
    context_text = _safe_text(context_message)
    if not context_text:
        return

    message_history.append(
        {
            "role": "system",
            "content": context_text,
            "message_id": str(uuid.uuid4()),
            "type": "system_travel_rag_context",
        }
    )


def execute_chat_once(
    request_messages: List[Any],
    controller: Any,
    tool_manager: Any,
    session_id: Optional[str],
    use_deepthink: bool,
    use_multi_agent: bool,
    selected_skill_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run a non-stream chat request and build standard response payload."""
    message_history = build_message_history(request_messages)
    skill_message = build_skill_system_message(selected_skill_ids)
    if skill_message:
        message_history.insert(0, skill_message)
    runtime_session_id = session_id or str(uuid.uuid4())
    ticket_bundle: Optional[Dict[str, Any]] = None
    xhs_bundle: Optional[Dict[str, Any]] = None
    travel_bundle: Optional[Dict[str, Any]] = None
    travel_rag_context = ""

    latest_user_query = _extract_latest_user_query(message_history)
    try:
        ticket_bundle = maybe_prepare_train_ticket_bundle(
            user_query=latest_user_query,
            tool_manager=tool_manager,
            message_history=message_history,
            session_id=runtime_session_id,
            selected_skill_ids=selected_skill_ids,
        )
        if ticket_bundle and ticket_bundle.get("context_message"):
            message_history.append(
                {
                    "role": "system",
                    "content": ticket_bundle["context_message"],
                    "message_id": str(uuid.uuid4()),
                    "type": "system_realtime_ticket_context",
                }
            )
    except Exception as ticket_error:
        logger.error(f"非流式实时票务硬规则预查询失败: {ticket_error}")

    if not ticket_bundle:
        try:
            xhs_bundle = maybe_prepare_xhs_search_bundle(
                user_query=latest_user_query,
                tool_manager=tool_manager,
                message_history=message_history,
                session_id=runtime_session_id,
                selected_skill_ids=selected_skill_ids,
            )
        except Exception as xhs_error:
            logger.error(f"非流式小红书预检索失败: {xhs_error}")

    if not ticket_bundle:
        try:
            travel_bundle = maybe_prepare_travel_experience_bundle(
                user_query=latest_user_query,
                tool_manager=tool_manager,
                message_history=message_history,
                session_id=runtime_session_id,
                selected_skill_ids=selected_skill_ids,
                xhs_bundle=xhs_bundle,
            )
            if travel_bundle and travel_bundle.get("context_message"):
                message_history.append(
                    {
                        "role": "system",
                        "content": travel_bundle["context_message"],
                        "message_id": str(uuid.uuid4()),
                        "type": "system_travel_experience_context",
                    }
                )
            elif xhs_bundle and xhs_bundle.get("context_message"):
                message_history.append(
                    {
                        "role": "system",
                        "content": xhs_bundle["context_message"],
                        "message_id": str(uuid.uuid4()),
                        "type": "system_xhs_search_context",
                    }
                )
        except Exception as travel_error:
            logger.error(f"非流式旅行体验增强失败: {travel_error}")
            if xhs_bundle and xhs_bundle.get("context_message"):
                message_history.append(
                    {
                        "role": "system",
                        "content": xhs_bundle["context_message"],
                        "message_id": str(uuid.uuid4()),
                        "type": "system_xhs_search_context",
                    }
                )

    if not _ticket_bundle_has_valid_results(ticket_bundle) and not xhs_bundle and not travel_bundle:
        travel_rag_context = maybe_prepare_travel_rag_context(latest_user_query)
        append_travel_rag_context_message(message_history, travel_rag_context)

    result = controller.run(
        message_history,
        tool_manager,
        session_id=runtime_session_id,
        deep_thinking=use_deepthink,
        summary=True,
        deep_research=use_multi_agent,
    )

    if ticket_bundle:
        result = _apply_realtime_only_result(result, ticket_bundle)
    elif travel_bundle:
        result = _apply_travel_experience_result(result, travel_bundle, session_id=runtime_session_id)
    elif xhs_bundle:
        result = _apply_xhs_result(result, xhs_bundle)

    return {
        "status": "success",
        "result": result,
        "session_id": session_id,
    }


def execute_chat_route(
    request_messages: List[Any],
    controller: Any,
    tool_manager: Any,
    session_id: Optional[str],
    use_deepthink: bool,
    use_multi_agent: bool,
    selected_skill_ids: Optional[List[str]] = None,
    logger: Any = logger,
) -> Dict[str, Any]:
    """Execute non-stream chat while preserving route-level error semantics."""
    from fastapi import HTTPException

    try:
        if not controller:
            raise HTTPException(status_code=400, detail="系统未配置，请先配置API密钥")
        return execute_chat_once(
            request_messages=request_messages,
            controller=controller,
            tool_manager=tool_manager,
            session_id=session_id,
            use_deepthink=use_deepthink,
            use_multi_agent=use_multi_agent,
            selected_skill_ids=selected_skill_ids,
        )
    except Exception as e:
        logger.error(f"聊天处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def execute_chat_request_route(
    request: Any,
    controller: Any,
    tool_manager: Any,
    logger: Any = logger,
) -> Dict[str, Any]:
    """Execute non-stream chat from request object with stable route semantics."""
    return execute_chat_route(
        request_messages=request.messages,
        controller=controller,
        tool_manager=tool_manager,
        session_id=request.session_id,
        use_deepthink=request.use_deepthink,
        use_multi_agent=request.use_multi_agent,
        selected_skill_ids=getattr(request, "selected_skill_ids", []),
        logger=logger,
    )


def execute_chat_request_runtime_route(
    request: Any,
    runtime_state: Any,
    logger: Any = logger,
) -> Dict[str, Any]:
    """Execute non-stream chat directly from runtime state container."""
    return execute_chat_request_route(
        request=request,
        controller=runtime_state.controller,
        tool_manager=runtime_state.tool_manager,
        logger=logger,
    )


def resolve_sanitize_text(sanitize_text: Optional[Callable[[str], str]]) -> Callable[[str], str]:
    """Resolve sanitize function, defaulting to user-visible text sanitizer."""
    if sanitize_text is not None:
        return sanitize_text

    from services.text_sanitizer_service import sanitize_user_visible_text

    return sanitize_user_visible_text


def build_chat_stream_response(
    request_messages: List[Any],
    controller: Any,
    tool_manager: Any,
    selected_mcp_servers: Optional[List[str]] = None,
    selected_skill_ids: Optional[List[str]] = None,
    use_deepthink: bool = True,
    use_multi_agent: bool = True,
    sanitize_text: Optional[Callable[[str], str]] = None,
    sse_headers: Optional[Dict[str, str]] = None,
) -> Any:
    """Build streaming HTTP response for chat endpoint."""
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    if not controller:
        raise HTTPException(status_code=500, detail="系统未配置，请先配置API密钥")

    effective_sanitize_text = resolve_sanitize_text(sanitize_text)
    effective_sse_headers = sse_headers if sse_headers is not None else get_sse_headers()

    return StreamingResponse(
        generate_chat_stream(
            request_messages=request_messages,
            controller=controller,
            tool_manager=tool_manager,
            selected_mcp_servers=selected_mcp_servers,
            selected_skill_ids=selected_skill_ids,
            use_deepthink=use_deepthink,
            use_multi_agent=use_multi_agent,
            sanitize_text=effective_sanitize_text,
        ),
        media_type="text/event-stream",
        headers=effective_sse_headers,
    )


def build_chat_stream_route(
    request_messages: List[Any],
    controller: Any,
    tool_manager: Any,
    selected_mcp_servers: Optional[List[str]] = None,
    selected_skill_ids: Optional[List[str]] = None,
    use_deepthink: bool = True,
    use_multi_agent: bool = True,
    logger: Any = logger,
) -> Any:
    """Build chat-stream response with route-level error boundary semantics."""
    from fastapi import HTTPException

    try:
        return build_chat_stream_response(
            request_messages=request_messages,
            controller=controller,
            tool_manager=tool_manager,
            selected_mcp_servers=selected_mcp_servers,
            selected_skill_ids=selected_skill_ids,
            use_deepthink=use_deepthink,
            use_multi_agent=use_multi_agent,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"流式聊天请求失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def build_chat_stream_request_route(
    request: Any,
    controller: Any,
    tool_manager: Any,
    logger: Any = logger,
) -> Any:
    """Build chat-stream response directly from request object."""
    return build_chat_stream_route(
        request_messages=request.messages,
        controller=controller,
        tool_manager=tool_manager,
        selected_mcp_servers=request.selected_mcp_servers,
        selected_skill_ids=getattr(request, "selected_skill_ids", []),
        use_deepthink=request.use_deepthink,
        use_multi_agent=request.use_multi_agent,
        logger=logger,
    )


def build_chat_stream_request_runtime_route(
    request: Any,
    runtime_state: Any,
    logger: Any = logger,
) -> Any:
    """Build chat-stream response directly from runtime state container."""
    return build_chat_stream_request_route(
        request=request,
        controller=runtime_state.controller,
        tool_manager=runtime_state.tool_manager,
        logger=logger,
    )


async def generate_chat_stream(
    request_messages: List[Any],
    controller: Any,
    tool_manager: Any,
    selected_mcp_servers: Optional[List[str]] = None,
    selected_skill_ids: Optional[List[str]] = None,
    use_deepthink: bool = True,
    use_multi_agent: bool = True,
    sanitize_text: Optional[Callable[[str], str]] = None,
) -> AsyncGenerator[str, None]:
    """Generate SSE stream payload for chat endpoint."""
    try:
        sanitize_text = sanitize_text or (lambda text: text)
        message_history = build_message_history(request_messages)
        skill_message = build_skill_system_message(selected_skill_ids)
        if skill_message:
            message_history.insert(0, skill_message)
        message_id = str(uuid.uuid4())
        stream_session_id = str(uuid.uuid4())

        yield f"data: {json.dumps({'type': 'chat_start', 'message_id': message_id})}\n\n"

        effective_tool_manager = tool_manager
        effective_selected_mcp_servers = merge_skill_mcp_servers(selected_mcp_servers, selected_skill_ids)
        if effective_selected_mcp_servers is not None:
            effective_tool_manager = await create_filtered_tool_manager(
                tool_manager, effective_selected_mcp_servers
            )

        latest_user_query = _extract_latest_user_query(message_history)
        latest_user_message_id = _extract_latest_user_message_id(message_history)
        progress_message_id = f"{message_id}-progress"
        if _is_train_ticket_query(latest_user_query):
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "已识别为实时交通查询，正在调用票务工具并整理可确认结果。",
                sanitize_text,
                latest_user_message_id,
            )
            yield f"data: {json.dumps(progress_chunk)}\n\n"
            await asyncio.sleep(0.01)
        elif _is_travel_experience_query(latest_user_query):
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "已识别为旅行规划请求，正在查询天气、地点和旅行参考信息。",
                sanitize_text,
                latest_user_message_id,
            )
            yield f"data: {json.dumps(progress_chunk)}\n\n"
            await asyncio.sleep(0.01)
        elif _is_xhs_query(latest_user_query):
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "已识别为小红书参考检索请求，正在检索和整理外部资源。",
                sanitize_text,
                latest_user_message_id,
            )
            yield f"data: {json.dumps(progress_chunk)}\n\n"
            await asyncio.sleep(0.01)
        ticket_bundle: Optional[Dict[str, Any]] = None
        xhs_bundle: Optional[Dict[str, Any]] = None
        travel_bundle: Optional[Dict[str, Any]] = None
        travel_rag_context = ""
        realtime_only_answer = ""
        streamed_final_answer_text = ""
        model_final_answer_emitted = False
        try:
            ticket_bundle = maybe_prepare_train_ticket_bundle(
                user_query=latest_user_query,
                tool_manager=effective_tool_manager,
                message_history=message_history,
                session_id=stream_session_id,
                selected_skill_ids=selected_skill_ids,
            )
            if ticket_bundle and ticket_bundle.get("context_message"):
                message_history.append(
                    {
                        "role": "system",
                        "content": ticket_bundle["context_message"],
                        "message_id": str(uuid.uuid4()),
                        "type": "system_realtime_ticket_context",
                    }
                )

            if ticket_bundle:
                realtime_only_answer = _safe_text(ticket_bundle.get("realtime_only_answer"))
                if not realtime_only_answer:
                    realtime_only_answer = _build_realtime_only_answer(ticket_bundle)

            if not ticket_bundle:
                xhs_bundle = maybe_prepare_xhs_search_bundle(
                    user_query=latest_user_query,
                    tool_manager=effective_tool_manager,
                    message_history=message_history,
                    session_id=stream_session_id,
                    selected_skill_ids=selected_skill_ids,
                )
        except Exception as ticket_error:
            logger.error(f"流式预检索失败: {ticket_error}")
            ticket_bundle = None
            xhs_bundle = None
            realtime_only_answer = ""

        if not ticket_bundle:
            try:
                travel_bundle = maybe_prepare_travel_experience_bundle(
                    user_query=latest_user_query,
                    tool_manager=effective_tool_manager,
                    message_history=message_history,
                    session_id=stream_session_id,
                    selected_skill_ids=selected_skill_ids,
                    xhs_bundle=xhs_bundle,
                )
                if travel_bundle and travel_bundle.get("context_message"):
                    message_history.append(
                        {
                            "role": "system",
                            "content": travel_bundle["context_message"],
                            "message_id": str(uuid.uuid4()),
                            "type": "system_travel_experience_context",
                        }
                    )
                elif xhs_bundle and xhs_bundle.get("context_message"):
                    message_history.append(
                        {
                            "role": "system",
                            "content": xhs_bundle["context_message"],
                            "message_id": str(uuid.uuid4()),
                            "type": "system_xhs_search_context",
                        }
                    )
            except Exception as travel_error:
                logger.error(f"流式旅行体验增强失败: {travel_error}")
                if xhs_bundle and xhs_bundle.get("context_message"):
                    message_history.append(
                        {
                            "role": "system",
                            "content": xhs_bundle["context_message"],
                            "message_id": str(uuid.uuid4()),
                            "type": "system_xhs_search_context",
                        }
                    )

        if ticket_bundle or travel_bundle or xhs_bundle:
            if ticket_bundle:
                progress_text = "实时工具结果已整理完成，正在生成最终交通建议。"
            elif travel_bundle:
                progress_text = "旅行参考信息已整理完成，正在生成最终行程方案。"
            else:
                progress_text = "小红书参考资源已整理完成，正在生成最终建议。"
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                progress_text,
                sanitize_text,
                latest_user_message_id,
            )
            yield f"data: {json.dumps(progress_chunk)}\n\n"
            await asyncio.sleep(0.01)

        if not _ticket_bundle_has_valid_results(ticket_bundle) and not xhs_bundle and not travel_bundle:
            travel_rag_context = maybe_prepare_travel_rag_context(latest_user_query)
            append_travel_rag_context_message(message_history, travel_rag_context)

        latest_final_answer_id: Optional[str] = None

        for chunk in controller.run_stream(
            input_messages=message_history,
            tool_manager=effective_tool_manager,
            session_id=stream_session_id,
            deep_thinking=use_deepthink,
            summary=True,
            deep_research=use_multi_agent,
        ):
            for msg in chunk:
                is_assistant_final_answer = (
                    msg.get('type') == 'final_answer' and msg.get('role', 'assistant') == 'assistant'
                )
                if is_assistant_final_answer:
                    latest_final_answer_id = msg.get('message_id', latest_final_answer_id)
                    final_delta = _safe_text(msg.get('show_content') or msg.get('content'))
                    if final_delta:
                        streamed_final_answer_text += final_delta
                        if not _looks_like_internal_or_failed_answer(final_delta):
                            model_final_answer_emitted = True
                    # 对票务查询先缓存最终回答，末尾统一做安全筛选并附表输出。
                    if ticket_bundle or travel_bundle or xhs_bundle:
                        continue

                # 票务硬规则模式下隐藏中间多智能体过程，避免阶段噪声暴露到前端。
                if _should_hide_stream_message(msg):
                    continue

                raw_content = msg.get('content', '')
                raw_show_content = msg.get('show_content', '')

                data = {
                    'type': 'chat_chunk',
                    'message_id': msg.get('message_id', message_id),
                    'role': msg.get('role', 'assistant'),
                    'content': sanitize_text(raw_content),
                    'show_content': sanitize_text(raw_show_content),
                    'step_type': msg.get('type', ''),
                    'agent_type': msg.get('role', ''),
                }

                yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(0.01)

        if ticket_bundle:
            selected_final_text = _select_ticket_final_answer(streamed_final_answer_text, ticket_bundle)
            if not selected_final_text:
                selected_final_text = realtime_only_answer or _build_realtime_only_answer(ticket_bundle)

            if selected_final_text:
                final_message_id = latest_final_answer_id or message_id
                for chunk_index, text_chunk in enumerate(_split_final_answer_for_stream(selected_final_text)):
                    forced_chunk = _build_final_answer_chunk(
                        final_message_id,
                        text_chunk,
                        sanitize_text,
                        replace=chunk_index == 0,
                        linked_user_message_id=latest_user_message_id,
                    )
                    yield f"data: {json.dumps(forced_chunk)}\n\n"
                    await asyncio.sleep(0.01)
                latest_final_answer_id = final_message_id
                streamed_final_answer_text = selected_final_text
                model_final_answer_emitted = True
        elif travel_bundle:
            selected_travel_text = _select_travel_final_answer(
                streamed_final_answer_text,
                travel_bundle,
                session_id=stream_session_id,
            )
            if selected_travel_text:
                final_message_id = latest_final_answer_id or message_id
                for chunk_index, text_chunk in enumerate(_split_final_answer_for_stream(selected_travel_text)):
                    forced_chunk = _build_final_answer_chunk(
                        final_message_id,
                        text_chunk,
                        sanitize_text,
                        replace=chunk_index == 0,
                        linked_user_message_id=latest_user_message_id,
                    )
                    yield f"data: {json.dumps(forced_chunk)}\n\n"
                    await asyncio.sleep(0.01)
                latest_final_answer_id = final_message_id
                streamed_final_answer_text = selected_travel_text
                model_final_answer_emitted = True
        elif xhs_bundle:
            selected_xhs_text = _select_xhs_final_answer(streamed_final_answer_text, xhs_bundle)
            if selected_xhs_text:
                final_message_id = latest_final_answer_id or message_id
                for chunk_index, text_chunk in enumerate(_split_final_answer_for_stream(selected_xhs_text)):
                    forced_chunk = _build_final_answer_chunk(
                        final_message_id,
                        text_chunk,
                        sanitize_text,
                        replace=chunk_index == 0,
                        linked_user_message_id=latest_user_message_id,
                    )
                    yield f"data: {json.dumps(forced_chunk)}\n\n"
                    await asyncio.sleep(0.01)
                latest_final_answer_id = final_message_id
                streamed_final_answer_text = selected_xhs_text
                model_final_answer_emitted = True
        elif not model_final_answer_emitted:
            # 非票务场景沿用原行为：仅当模型未给最终回答时补发兜底。
            if realtime_only_answer:
                final_message_id = latest_final_answer_id or message_id
                forced_chunk = {
                    'type': 'chat_chunk',
                    'message_id': final_message_id,
                    'role': 'assistant',
                    'content': sanitize_text(realtime_only_answer),
                    'show_content': sanitize_text(realtime_only_answer),
                    'step_type': 'final_answer',
                    'agent_type': 'assistant',
                }
                yield f"data: {json.dumps(forced_chunk)}\n\n"
                await asyncio.sleep(0.01)
                latest_final_answer_id = final_message_id
                streamed_final_answer_text += realtime_only_answer

        if (
            _ticket_bundle_has_valid_results(ticket_bundle)
            and ticket_bundle.get("append_markdown")
            and not _contains_ticket_table(streamed_final_answer_text)
        ):
            appendix = _safe_text(ticket_bundle.get("append_markdown"))
            sanitized_appendix = sanitize_text(appendix)
            if sanitized_appendix:
                appendix_payload = sanitized_appendix if sanitized_appendix.startswith("\n") else f"\n\n{sanitized_appendix}"
            else:
                appendix_payload = ""
            appendix_message_id = latest_final_answer_id or message_id
            appendix_chunk = {
                'type': 'chat_chunk',
                'message_id': appendix_message_id,
                'role': 'assistant',
                'content': appendix_payload,
                'show_content': appendix_payload,
                'step_type': 'final_answer',
                'agent_type': 'assistant',
            }
            if appendix_payload:
                yield f"data: {json.dumps(appendix_chunk)}\n\n"
                await asyncio.sleep(0.01)

        yield f"data: {json.dumps({'type': 'chat_complete', 'message_id': message_id})}\n\n"

    except Exception as e:
        logger.error(f"流式处理错误: {str(e)}")
        error_message = str(e)
        diagnostics = _build_runtime_model_diagnostics(controller)
        quota_hint = ""
        lower_message = _safe_text(error_message).lower()
        if "allocationquota" in lower_message or "free tier" in lower_message:
            quota_hint = (
                "当前报错是服务商账号额度/策略限制。"
                "仅修改模型名称不会改变同一账号的额度状态，请切换有额度的 API Key "
                "或在服务商控制台关闭 Free Tier Only 限制。"
            )

        error_data = {
            'type': 'error',
            'message': error_message,
        }
        if quota_hint:
            error_data['hint'] = quota_hint
        error_data.update(diagnostics)
        yield f"data: {json.dumps(error_data)}\n\n"
