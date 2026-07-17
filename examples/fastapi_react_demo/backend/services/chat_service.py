import asyncio
import datetime
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from agents.tool.ticket_query_utils import clean_route_name, extract_route_from_query
from agents.utils.logger import logger
from services.file_service import get_output_root_path
from services.http_response_service import get_sse_headers
from services.skill_profile_service import build_skill_system_message, merge_skill_mcp_servers
from schemas.trip_models import (
    CLARIFICATION_SKIP_SENTINEL,
    ChatCompleteEvent,
    ChatStreamErrorEvent,
    RouteLeg,
    TripActivity,
    TripDay,
    TripIntent,
    TripPlace,
    TripPlan,
    UserTravelProfile,
)
from services.clarification_service import (
    MAX_CLARIFICATION_QUESTIONS,
    build_next_clarification_question,
    count_clarification_fields,
    normalize_clarification_fields,
)
from services.trip_intent_service import (
    build_trip_context_message,
    extract_trip_intent,
    is_trip_planning_query,
    merge_semantic_trip_analysis,
    normalize_user_travel_profile,
)
from services.trip_plan_repair_service import repair_trip_plan_once
from services.trip_plan_validator import validate_trip_plan
from services.text_sanitizer_service import sanitize_user_visible_payload, sanitize_user_visible_text
from services.poi_detail_service import normalize_poi_detail, PoiDetailError
from services.destination_catalog_service import (
    INTERNATIONAL_DESTINATIONS,
    canonical_destination,
    classic_places_for_city,
    domestic_city_names,
    is_invalid_poi_name,
)
from services.trip_product_service import adapt_v1_document_to_v2, export_trip_markdown
from services.ticket_search_service import transport_section_from_bundle


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
TICKET_QUERY_TOTAL_TIMEOUT_SECONDS = 60.0
TICKET_TOOL_ATTEMPT_TIMEOUT_SECONDS = 15.0

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

XHS_TRAVEL_RELEVANCE_REGEX = re.compile(
    r"(攻略|行程|路线|景点|美食|住宿|酒店|民宿|旅行|旅游|出游|citywalk|打卡|游玩|"
    r"自由行|周末游|亲子|情侣|避雷|探店|交通|地铁|门票|咖啡|餐厅)",
    re.IGNORECASE,
)

XHS_NON_TRAVEL_REGEX = re.compile(
    r"(校园招聘|校招|招聘|求职|岗位|面试|实习|offer|简历|网申|考研|高考)",
    re.IGNORECASE,
)

XHS_LOCATION_HINTS = (
    "外滩", "西湖", "灵隐寺", "雷峰塔", "故宫", "长城", "颐和园", "天坛",
    "宽窄巷子", "大熊猫基地", "洪崖洞", "解放碑", "鼓浪屿", "中山陵", "夫子庙",
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
COMMON_CHINESE_CITY_NAMES = sorted(set(COMMON_CHINESE_CITY_NAMES + domestic_city_names()), key=len, reverse=True)

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

TRAVEL_OUTPUT_SENSITIVE_REGEX = re.compile(
    r"(?:```(?:json)?|file://|[a-z]:\\|\\\\[^\\\s]+\\|/(?:users|home|tmp|var|etc)/|"
    r"\b(?:tool_call_id|tool_call_result|raw_result|data_type|map_locations|traceback|"
    r"stack\s+trace|debug|exception)\b|\b(?:map|web|search|geo|ip|query)_[a-z0-9_]+\b)",
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
    catalog_match = canonical_destination(query_text)
    if catalog_match:
        return catalog_match
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
            partial_error = "（部分渠道未返回）" if reason else ""
            state_parts.append(f"{label} {count} 条{partial_error}")
            continue

        if status == "error":
            missing_parts.append(f"{label}查询失败")
        else:
            missing_parts.append(f"{label}未找到可确认实时结果")

    timed_out = bool(ticket_bundle.get("timed_out"))
    if not _ticket_bundle_has_valid_results(ticket_bundle):
        detail = "；".join(missing_parts) if missing_parts else "三种票务均未返回可确认实时结果"
        timeout_note = f"实时查询已在{int(TICKET_QUERY_TOTAL_TIMEOUT_SECONDS)}秒内结束，但" if timed_out else ""
        return f"{timeout_note}未找到可确认的票务结果。{detail}。"

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
    safe_content = sanitize_user_visible_text(sanitize_text(_safe_text(content)))
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
    safe_content = sanitize_user_visible_text(sanitize_text(_safe_text(content)))
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
    deadline: Optional[float] = None,
    timeout_label: str = "工具查询",
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
            raw_result = _run_tool_with_deadline(
                tool_manager=tool_manager,
                tool_name=tool_name,
                message_history=message_history,
                session_id=session_id,
                deadline=deadline,
                timeout_label=timeout_label,
                **args,
            )
            payload = _unwrap_tool_output(raw_result)
            last_payload = payload
            if not _is_tool_execution_error_payload(payload):
                return payload
        except Exception as run_error:
            last_payload = {"error": True, "message": str(run_error)}

    return last_payload


def _ticket_deadline_remaining(deadline: Optional[float]) -> float:
    if deadline is None:
        return float("inf")
    return max(0.0, deadline - time.monotonic())


def _run_tool_with_deadline(
    tool_manager: Any,
    tool_name: str,
    message_history: List[Dict[str, Any]],
    session_id: str,
    deadline: Optional[float] = None,
    timeout_label: str = "工具查询",
    **kwargs: Any,
) -> Any:
    """Bound a synchronous external tool call without blocking the chat response indefinitely."""
    remaining = _ticket_deadline_remaining(deadline)
    if remaining <= 0:
        return {"error": True, "message": f"{timeout_label}已达到{int(TICKET_QUERY_TOTAL_TIMEOUT_SECONDS)}秒总时限"}

    if deadline is None:
        return tool_manager.run_tool(
            tool_name,
            messages=message_history,
            session_id=session_id,
            **kwargs,
        )

    timeout_seconds = min(TICKET_TOOL_ATTEMPT_TIMEOUT_SECONDS, remaining)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ticket-tool")
    future = executor.submit(
        tool_manager.run_tool,
        tool_name,
        messages=message_history,
        session_id=session_id,
        **kwargs,
    )
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError:
        return {
            "error": True,
            "message": f"{timeout_label}单次调用超过{int(timeout_seconds)}秒，已跳过该渠道",
        }
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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


def _build_xhs_travel_query(query_text: str) -> str:
    destination = _extract_destination_city(query_text)
    if not destination:
        return query_text
    if XHS_TRAVEL_RELEVANCE_REGEX.search(query_text):
        return query_text
    return f"{destination} 旅行攻略"


def _filter_xhs_travel_rows(rows: List[Dict[str, str]], query_text: str) -> Tuple[List[Dict[str, str]], int]:
    destination = _extract_destination_city(query_text)
    location_hints = [hint for hint in XHS_LOCATION_HINTS if hint in query_text]
    filtered: List[Dict[str, str]] = []
    rejected_count = 0

    for row in rows:
        searchable_text = " ".join(
            _safe_text(row.get(key)) for key in ("title", "summary", "url", "source")
        )
        if XHS_NON_TRAVEL_REGEX.search(searchable_text):
            rejected_count += 1
            continue

        destination_matches = (
            not destination
            or destination.lower() in searchable_text.lower()
            or any(hint in searchable_text for hint in location_hints)
        )
        travel_matches = len(XHS_TRAVEL_RELEVANCE_REGEX.findall(searchable_text))
        if not destination_matches or travel_matches < 1:
            rejected_count += 1
            continue

        filtered.append(row)

    return filtered, rejected_count


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
        detail_info = item.get("detail_info") if isinstance(item.get("detail_info"), dict) else {}
        images = item.get("images") or item.get("image_urls") or detail_info.get("image") or detail_info.get("images") or []
        if isinstance(images, str):
            images = [images]
        rows.append(
            {
                "id": f"travel_place_{len(rows) + 1}",
                "place_id": _safe_text(item.get("uid") or item.get("place_id") or item.get("id")),
                "name": name,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "description": address or "地图检索命中地点",
                "category": category or "景点",
                "rating": item.get("rating") or detail_info.get("overall_rating"),
                "images": images if isinstance(images, list) else [],
                "summary": _safe_text(item.get("summary") or item.get("description") or detail_info.get("description")),
                "opening_hours": item.get("opening_hours") or detail_info.get("opening_hours") or detail_info.get("shop_hours"),
                "price": item.get("price") or detail_info.get("price"),
                "telephone": item.get("telephone") or detail_info.get("telephone"),
                "order": len(rows) + 1,
            }
        )
        if len(rows) >= max(1, max_rows):
            break

    return rows


def _normalize_place_detail_payload(payload: Any) -> Dict[str, Any]:
    candidates = _first_present_list(payload, ["results", "pois", "places", "items", "data", "content"])
    if candidates and isinstance(candidates[0], dict):
        return dict(candidates[0])
    if isinstance(payload, dict):
        data = payload.get("result") or payload.get("data")
        if isinstance(data, dict):
            return dict(data)
        return dict(payload)
    return {}


def _enrich_map_locations_with_details(
    locations: List[Dict[str, Any]],
    destination_city: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> List[Dict[str, Any]]:
    detail_tool = _first_available_tool_name(tool_manager, ["map_place_details"])
    image_tool = _first_available_tool_name(tool_manager, ["search_image_from_web"])
    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    enriched: List[Dict[str, Any]] = []
    for index, location in enumerate(locations):
        current = dict(location)
        detail_fetched = False
        image_fetched = False
        place_id = _safe_text(current.get("place_id"))
        has_rich_detail = any(current.get(key) not in (None, "", [], {}) for key in ("rating", "images", "opening_hours", "price", "summary"))
        # The search response already carries scope=2 details. Only the first two
        # final POIs may trigger a separate detail lookup when those fields are absent.
        if detail_tool and index < 2 and not has_rich_detail:
            detail_payload = _run_tool_with_arg_candidates(
                tool_manager=tool_manager,
                tool_name=detail_tool,
                message_history=message_history,
                session_id=session_id,
                arg_candidates=[
                    {"uid": place_id} if place_id else {"query": current.get("name"), "region": destination_city},
                    {"id": place_id} if place_id else {"place_name": current.get("name"), "city": destination_city},
                    {"query": current.get("name"), "region": destination_city},
                ],
            )
            if not _is_tool_execution_error_payload(detail_payload):
                detail = _normalize_place_detail_payload(detail_payload)
                detail_rows = _normalize_map_locations({"items": [detail]}, max_rows=1)
                if detail_rows:
                    detail_fetched = True
                    detail_row = detail_rows[0]
                    for key, value in detail_row.items():
                        if value not in (None, "", [], {}):
                            current[key] = value

        if image_tool and index < 2 and not current.get("images") and _safe_text(current.get("category")) not in {"酒店", "餐厅"}:
            image_payload = _run_tool_with_arg_candidates(
                tool_manager=tool_manager,
                tool_name=image_tool,
                message_history=message_history,
                session_id=session_id,
                arg_candidates=[{"query": f"{destination_city} {current.get('name')} 官方 旅游", "count": 3}],
            )
            image_rows = _first_present_list(image_payload, ["images", "results", "items", "data"])
            image_urls = [
                _safe_text(item.get("image_url") or item.get("imageUrl") or item.get("url"))
                for item in image_rows if isinstance(item, dict)
            ]
            current["images"] = [url for url in image_urls if url.startswith(("http://", "https://"))][:3]
            image_fetched = bool(current["images"])

        current["updated_at"] = fetched_at
        current["source"] = _safe_text(current.get("source")) or ("百度地图地点详情" if detail_fetched else "百度地图地点检索")
        map_source_id = f"map_{index + 1}"
        sources = [{
            "source_reference_id": map_source_id,
            "title": "地图地点详情" if detail_fetched else "地图地点检索",
            "source": current["source"],
            "type": "map",
            "data_type": "confirmed_live_data" if detail_fetched else "reference_data",
            "updated_at": fetched_at,
            "related_fields": [key for key in ("coordinates", "address", "category", "rating", "summary", "opening_hours", "reservation", "price") if key == "coordinates" or current.get(key) not in (None, "", [], {})],
        }]
        field_evidence = {
            key: {"source_reference_id": map_source_id, "updated_at": fetched_at, "data_type": sources[0]["data_type"]}
            for key in sources[0]["related_fields"]
        }
        if image_fetched:
            image_source_id = f"image_{index + 1}"
            sources.append({
                "source_reference_id": image_source_id,
                "title": "公开图片检索",
                "source": "公开图片检索",
                "type": "image_search",
                "data_type": "reference_data",
                "updated_at": fetched_at,
                "related_fields": ["images"],
            })
            field_evidence["images"] = {"source_reference_id": image_source_id, "updated_at": fetched_at, "data_type": "reference_data"}
        current["sources"] = sources
        current["field_evidence"] = field_evidence
        enriched.append(current)
    return enriched


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
        if not _safe_text(updated.get("category")):
            updated["category"] = default_category
        normalized.append(updated)
    return normalized


def _travel_seed_places(destination_city: str, query_text: str) -> List[Tuple[str, int]]:
    city = _safe_text(destination_city)
    seeds = list(DESTINATION_SEED_PLACES.get(city, [])) or classic_places_for_city(city)
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


def _select_final_map_candidates(
    locations: List[Dict[str, Any]],
    query_text: str,
    max_rows: int = 8,
) -> List[Dict[str, Any]]:
    """Select the small POI set that may enter the itinerary before detail/geocode calls."""
    deduped = _merge_map_locations(locations, [], max_rows=max(8, max_rows * 3))
    wants_itinerary = bool(ITINERARY_QUERY_REGEX.search(_safe_text(query_text)))
    wants_hotel = wants_itinerary or bool(HOTEL_QUERY_REGEX.search(_safe_text(query_text)))
    wants_food = wants_itinerary or bool(FOOD_QUERY_REGEX.search(_safe_text(query_text)))

    buckets: Dict[str, List[Dict[str, Any]]] = {"attraction": [], "hotel": [], "food": []}
    for location in deduped:
        category_text = f"{_safe_text(location.get('category'))} {_safe_text(location.get('name'))}"
        if any(marker in category_text for marker in ("酒店", "宾馆", "住宿", "民宿")):
            buckets["hotel"].append(location)
        elif any(marker in category_text for marker in ("餐厅", "餐馆", "小吃", "美食", "咖啡")):
            buckets["food"].append(location)
        else:
            buckets["attraction"].append(location)

    selected: List[Dict[str, Any]] = []
    if wants_hotel and buckets["hotel"]:
        selected.append(buckets["hotel"].pop(0))
    if wants_food and buckets["food"]:
        selected.append(buckets["food"].pop(0))
    for bucket_name in ("attraction", "hotel", "food"):
        for location in buckets[bucket_name]:
            if len(selected) >= max_rows:
                break
            selected.append(location)
    return _merge_map_locations(selected, [], max_rows=max_rows)


def _filter_quality_map_locations(
    locations: List[Dict[str, Any]],
    destination_city: str,
) -> List[Dict[str, Any]]:
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    kept: List[Dict[str, Any]] = []
    seen_names = set()
    for location in locations:
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name"))
        if is_invalid_poi_name(name):
            continue
        normalized_name = re.sub(r"[^\w\u4e00-\u9fff]+", "", name).casefold()
        if normalized_name in seen_names:
            continue
        address_city = _safe_text(location.get("city") or location.get("area"))
        address = _safe_text(location.get("description") or location.get("address"))
        explicit_city = canonical_destination(address_city)
        if destination and explicit_city and explicit_city != destination:
            continue
        if destination and address_city and not explicit_city and len(address_city) <= 12 and destination not in f"{address_city}{address}":
            location = {**location, "quality_warning": "地点所属城市待确认"}
        seen_names.add(normalized_name)
        kept.append(location)
    return kept


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
    for place_name, day in place_seeds[:3]:
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
        row["source"] = "seed_fallback"
        row["data_type"] = "reference_data"
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
                {"query": query, "region": region, "scope": 2},
                {"keywords": query, "region": region, "scope": 2},
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
                current_category = _safe_text(row.get("category"))
                if not current_category or (current_category == "景点" and category != "景点"):
                    row["category"] = category
        return rows, ""

    wants_itinerary = bool(ITINERARY_QUERY_REGEX.search(query_text))
    batch_queries: List[Tuple[str, int, str]] = []
    if wants_itinerary:
        batch_queries.append((f"{region} 热门景点".strip(), 8, "景点"))
    else:
        batch_queries.append((query_text, 8, _default_map_category_for_query(query_text)))
    if wants_itinerary or HOTEL_QUERY_REGEX.search(query_text):
        batch_queries.append((f"{region} 住宿 酒店".strip(), 4, "酒店"))
    if wants_itinerary or FOOD_QUERY_REGEX.search(query_text):
        batch_queries.append((f"{region} 美食 餐厅".strip(), 4, "餐厅"))

    candidate_locations: List[Dict[str, Any]] = []
    batch_errors: List[str] = []
    for batch_query, max_rows, category in batch_queries:
        rows, batch_error = run_search(batch_query, max_rows=max_rows, category=category)
        candidate_locations.extend(rows)
        if batch_error:
            batch_errors.append(batch_error)

    candidate_locations = _filter_quality_map_locations(candidate_locations, destination_city)
    final_candidates = _select_final_map_candidates(candidate_locations, query_text, max_rows=8)
    seed_locations: List[Dict[str, Any]] = []
    seed_tool = ""
    seed_error = ""
    existing_names = {_safe_text(item.get("name")) for item in final_candidates}
    seed_candidates = [item for item in _travel_seed_places(destination_city, user_query) if item[0] not in existing_names]
    if not final_candidates:
        # Geocoding is reserved for the final fallback POIs, never the full candidate pool.
        seed_locations, seed_tool, seed_error = _run_map_geocode_for_places(
            seed_candidates[:3],
            destination_city,
            user_query,
            tool_manager,
            message_history,
            session_id,
        )
    merged = _merge_map_locations(final_candidates, seed_locations, max_rows=8)
    merged = _filter_quality_map_locations(merged, destination_city)
    merged = _enrich_map_locations_with_details(
        merged,
        destination_city,
        tool_manager,
        message_history,
        session_id,
    )
    used_tools = ", ".join([
        item for item in [
            tool_name if final_candidates else "",
            seed_tool if seed_locations else "",
        ] if item
    ])
    errors = "；".join([item for item in [*batch_errors, seed_error] if item])
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
        "- 地图一致性：正文中的地点顺序应与已核验地点一致，右侧地图会通过结构化事件单独展示。",
        "涉及地点时，必须优先核验地点；正文不得输出工具名、原始 JSON、内部字段、本地路径或调试信息。",
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
    allow_web_search: bool = True,
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
    if allow_web_search:
        web_rows, web_tool, web_error = _run_web_search_for_travel(
            user_query=query_text,
            tool_manager=tool_manager,
            message_history=message_history,
            session_id=session_id,
        )
    else:
        web_rows, web_tool, web_error = [], "", "已按用户设置关闭网页与社区检索"
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
    append_parts = [part for part in [xhs_table, web_table] if _safe_text(part)]
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
    """在外部数据不可用时提供不依赖城市硬编码的规划框架。"""
    text = _safe_text(query_text)
    title = f"# {kind_text}建议"
    unavailable = (
        "外部检索结果暂时不可用，我先提供一版不包含未核验地点、价格、库存或开放时间的规划框架。"
    )

    if HOTEL_QUERY_REGEX.search(text):
        return "\n".join([
            title,
            "",
            "## 结论",
            unavailable,
            "住宿应先按每天主要活动区域选址，再比较交通、预算和取消政策；当前不直接给出未经检索核验的酒店名称。",
            "",
            "## 筛选顺序",
            "1. 先确定主要活动区域和最晚返程地点。",
            "2. 优先选择步行可达地铁站、夜间返程稳定的住宿区域。",
            "3. 对比房型、是否无窗、隔音、电梯、早餐和取消政策。",
            "4. 最终房价、库存和可预订状态以预订平台实时页面为准。",
        ])

    if FOOD_QUERY_REGEX.search(text):
        return "\n".join([
            title,
            "",
            "## 结论",
            unavailable,
            "美食安排应服务于当天路线，当前不直接给出未经检索核验的餐厅名称。",
            "",
            "## 筛选顺序",
            "1. 每天按活动区域选择一顿代表性正餐。",
            "2. 小吃、咖啡和夜宵作为机动项，避免为单店远距离往返。",
            "3. 出发前核对营业时间、排队规则、预约方式和近期评价。",
        ])

    return "\n".join([
        title,
        "",
        "## 结论",
        unavailable,
        "当前先确定每天的区域、节奏和交通边界；具体地点必须在地图或可靠来源返回后再填入。",
        "",
        "## 行程框架",
        "1. 每天选择一个主要活动区域，安排1-2个核心活动。",
        "2. 同一区域内按地理相邻顺序串联，预留用餐、安检和排队时间。",
        "3. 多日行程补充住宿区域、返程窗口、天气核验和雨天替代方案。",
        "4. 票价、门票、营业时间、车次和库存必须以实时工具或官方页面为准。",
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
    raw_map_locations = travel_bundle.get("map_locations") if isinstance(travel_bundle.get("map_locations"), list) else []
    map_locations: List[Dict[str, Any]] = []
    map_tool = _safe_text(travel_bundle.get("map_tool"))
    for raw_location in raw_map_locations:
        if not isinstance(raw_location, dict):
            continue
        location = dict(raw_location)
        source_text = _safe_text(location.get("source")).lower()
        has_coordinates = _optional_float(location.get("lat")) is not None and _optional_float(location.get("lng")) is not None
        location["data_type"] = (
            "confirmed_live_data"
            if map_tool and has_coordinates and not any(marker in source_text for marker in ("seed", "fallback", "estimate"))
            else "reference_data"
        )
        map_locations.append(location)
    xhs_table = _safe_text(travel_bundle.get("xhs_table"))
    append_markdown = _safe_text(travel_bundle.get("append_markdown"))

    has_verified_external_data = bool(
        web_rows
        or xhs_table
        or any(location.get("data_type") == "confirmed_live_data" for location in map_locations)
    )
    fallback_answer = _build_travel_knowledge_fallback(query_text, kind_text)

    if has_verified_external_data:
        fallback_answer = fallback_answer.replace("外部检索结果暂时不可用，我先", "已完成外部检索并结合结果，我")
        fallback_answer = fallback_answer.replace("外部搜索或地图检索没有返回可用结果", "外部检索结果已作为参考")
    else:
        data_note = (
            "## 数据说明\n\n"
            "本次没有取得可验证的外部实时结果。以上仅为通用规划框架；"
            "参考地点、酒店价格、库存、开放时间和坐标均需以官方平台或地图实时结果为准。"
        )
        fallback_answer = f"{fallback_answer}\n\n{data_note}"

    if append_markdown:
        fallback_answer = f"{fallback_answer}\n\n{append_markdown}"
    return fallback_answer


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
    canonical_target = canonical_destination(to_station) or to_station
    canonical_origin = canonical_destination(from_station) or from_station
    international_route = canonical_target in INTERNATIONAL_DESTINATIONS or canonical_origin in INTERNATIONAL_DESTINATIONS
    travel_date = _extract_travel_date(query_text)
    ticket_deadline = time.monotonic() + TICKET_QUERY_TOTAL_TIMEOUT_SECONDS

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
    if not international_route and _tool_exists(tool_manager, "get-tickets"):
        direct_source = _append_tool_source(direct_source, "get-tickets")
        train_attempted = True
        for candidate_from, candidate_to in query_pairs:
            if _ticket_deadline_remaining(ticket_deadline) <= 0:
                train_errors.append(f"实时票务查询已达到{int(TICKET_QUERY_TOTAL_TIMEOUT_SECONDS)}秒总时限")
                break
            try:
                direct_raw = _run_tool_with_deadline(
                    tool_manager=tool_manager,
                    tool_name="get-tickets",
                    message_history=message_history,
                    session_id=session_id,
                    deadline=ticket_deadline,
                    timeout_label="直达火车票查询",
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

    if not international_route and not direct_rows and _tool_exists(tool_manager, "query_12306_realtime_tickets"):
        direct_source = _append_tool_source(direct_source, "query_12306_realtime_tickets")
        train_attempted = True
        for candidate_from, candidate_to in query_pairs:
            if _ticket_deadline_remaining(ticket_deadline) <= 0:
                train_errors.append(f"实时票务查询已达到{int(TICKET_QUERY_TOTAL_TIMEOUT_SECONDS)}秒总时限")
                break
            try:
                direct_raw = _run_tool_with_deadline(
                    tool_manager=tool_manager,
                    tool_name="query_12306_realtime_tickets",
                    message_history=message_history,
                    session_id=session_id,
                    deadline=ticket_deadline,
                    timeout_label="直达火车票查询",
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

    if not international_route and _tool_exists(tool_manager, "get-interline-tickets"):
        interline_source = "get-interline-tickets"
        train_attempted = True
        interline_pairs = [selected_pair, *[pair for pair in query_pairs if pair != selected_pair]]
        for candidate_from, candidate_to in interline_pairs:
            if _ticket_deadline_remaining(ticket_deadline) <= 0:
                train_errors.append(f"实时票务查询已达到{int(TICKET_QUERY_TOTAL_TIMEOUT_SECONDS)}秒总时限")
                break
            try:
                interline_raw = _run_tool_with_deadline(
                    tool_manager=tool_manager,
                    tool_name="get-interline-tickets",
                    message_history=message_history,
                    session_id=session_id,
                    deadline=ticket_deadline,
                    timeout_label="中转火车票查询",
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

    if not train_attempted and not international_route:
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
            deadline=ticket_deadline,
            timeout_label="飞机票查询",
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
    if bus_tool_name and not international_route:
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
            deadline=ticket_deadline,
            timeout_label="大巴票查询",
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
    elif not international_route:
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
        "timed_out": _ticket_deadline_remaining(ticket_deadline) <= 0,
        "elapsed_seconds": round(TICKET_QUERY_TOTAL_TIMEOUT_SECONDS - _ticket_deadline_remaining(ticket_deadline), 1),
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
    tool_query = _build_xhs_travel_query(query_text) if travel_triggered else query_text
    if summary_tool_name:
        arg_candidates = [
            {"query": tool_query, "limit": 8, "focus": focus},
            {"query": tool_query, "limit": 8},
            {"query": tool_query},
        ]
    else:
        arg_candidates = [
            {"query": tool_query, "limit": 8, "page": 1, "sort": "general"},
            {"query": tool_query, "limit": 8},
            {"query": tool_query},
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
    rows, rejected_row_count = _filter_xhs_travel_rows(rows, query_text) if travel_triggered else (rows, 0)
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

    if travel_triggered:
        if XHS_NON_TRAVEL_REGEX.search(summary_text):
            summary_text = ""
        highlights = [item for item in highlights if not XHS_NON_TRAVEL_REGEX.search(item)]

    if rejected_row_count:
        warnings.append(f"已过滤 {rejected_row_count} 条与旅行主题或目的地不相关的结果。")

    if not summary_text or (travel_triggered and not rows):
        if rows:
            summary_text = f"已检索到 {len(rows)} 条小红书资源，可基于作者、摘要和链接做筛选。"
        else:
            summary_text = "当前未检索到可用的小红书资源。"

    markdown_table = _build_xhs_markdown_table(rows, max_rows=8)

    context_lines = [
        "【小红书检索结果】",
        f"用户问题: {query_text}",
        f"检索关键词: {tool_query}",
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
        "tool_query": tool_query,
        "focus": focus,
        "rows": rows,
        "summary": summary_text,
        "source": source,
        "source_tool": selected_tool,
        "warnings": warnings,
        "highlights": highlights,
        "rejected_row_count": rejected_row_count,
        "context_message": "\n".join(context_lines),
        "append_markdown": markdown_table,
    }

    bundle["fallback_answer"] = _build_xhs_fallback_answer(bundle)
    return bundle


ONLINE_SEARCH_SERVER_NAMES = {"serper_web_search", "xhs-mcp", "fetch"}
ONLINE_SEARCH_TOOL_MARKERS = (
    "search_web",
    "web_search",
    "xhs_",
    "xhs-",
    "xiaohongshu",
    "fetch_url",
    "fetch_web",
)
SEMANTIC_TRIP_ANALYSIS_TIMEOUT_SECONDS = 8


def _is_online_search_tool(tool_name: str, tool_spec: Any) -> bool:
    normalized_name = _safe_text(tool_name).lower()
    server_name = _safe_text(getattr(tool_spec, "server_name", "")).lower()
    return server_name in ONLINE_SEARCH_SERVER_NAMES or any(
        marker in normalized_name for marker in ONLINE_SEARCH_TOOL_MARKERS
    )


def _build_filtered_tool_manager(
    original_tool_manager: Any,
    selected_mcp_servers: Optional[List[str]],
    allow_web_search: bool = True,
) -> Any:
    """Create a tool manager containing the allowed MCP and local tools."""
    try:
        from agents.tool.tool_manager import ToolManager
        from agents.tool.tool_base import McpToolSpec

        filtered_manager = ToolManager(
            is_auto_discover=False,
            map_request_governor=getattr(original_tool_manager, "map_request_governor", None),
        )
        if hasattr(original_tool_manager, "_run_mcp_tool_async"):
            filtered_manager._run_mcp_tool_async = original_tool_manager._run_mcp_tool_async

        for tool_name, tool_spec in original_tool_manager.tools.items():
            if not allow_web_search and _is_online_search_tool(tool_name, tool_spec):
                continue
            if not isinstance(tool_spec, McpToolSpec):
                filtered_manager.tools[tool_name] = tool_spec
            elif selected_mcp_servers is None or tool_spec.server_name in selected_mcp_servers:
                filtered_manager.tools[tool_name] = tool_spec

        logger.info(f"筛选后的工具管理器包含 {len(filtered_manager.tools)} 个工具")
        logger.info(f"选择的MCP服务器: {selected_mcp_servers}")
        logger.info(f"允许网页与社区检索: {allow_web_search}")
        return filtered_manager

    except Exception as e:
        logger.error(f"创建筛选工具管理器失败: {e}")
        return original_tool_manager


async def create_filtered_tool_manager(
    original_tool_manager: Any,
    selected_mcp_servers: Optional[List[str]],
    allow_web_search: bool = True,
) -> Any:
    return _build_filtered_tool_manager(
        original_tool_manager,
        selected_mcp_servers,
        allow_web_search=allow_web_search,
    )


def _begin_map_planning_scope(tool_manager: Any, session_id: str, request_id: str) -> None:
    begin_scope = getattr(tool_manager, "begin_map_request_scope", None)
    if callable(begin_scope):
        begin_scope(session_id, f"trip:{request_id}")


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


def append_online_search_policy_message(
    message_history: List[Dict[str, Any]],
    allow_web_search: bool,
) -> None:
    if allow_web_search:
        return
    message_history.append(
        {
            "role": "system",
            "content": (
                "用户已关闭网页与社区检索。本轮不得调用网页搜索、网页抓取或小红书工具；"
                "可以继续使用本地旅行知识库，以及用户明确启用的地图、天气和票务工具。"
            ),
            "message_id": str(uuid.uuid4()),
            "type": "system_online_search_policy",
        }
    )


def resolve_planning_mode_flags(
    planning_mode: Optional[str],
    use_deepthink: bool,
    use_multi_agent: bool,
) -> Tuple[bool, bool]:
    """Map product planning modes to existing controller flags."""
    mode = _safe_text(planning_mode)
    if mode == "fast_chat":
        return False, False
    if mode == "standard_plan":
        return True, False
    if mode == "deep_research":
        return True, True
    return use_deepthink, use_multi_agent


def _normalize_user_profile(profile: Optional[Dict[str, Any]]) -> UserTravelProfile:
    try:
        return normalize_user_travel_profile(profile)
    except Exception:
        return UserTravelProfile()


def _extract_json_object(text: str) -> Dict[str, Any]:
    content = _safe_text(text)
    if not content:
        return {}
    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
    if fenced_match:
        content = fenced_match.group(1).strip()
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    start = content.find("{")
    if start < 0:
        return {}
    try:
        parsed, _ = json.JSONDecoder().raw_decode(content[start:])
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _semantic_trip_analysis_messages(
    query_text: str,
    intent: Any,
    profile: UserTravelProfile,
    clarification_answers: Dict[str, Any],
) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是旅行需求澄清决策器。只输出一个 JSON 对象，不输出分析过程或 Markdown。"
                "先从用户原话和累计澄清答案中抽取明确事实；没有直接证据的字段必须留空，绝不能猜测。"
                "再判断当前最影响可执行行程的一个缺失信息。问题字段可从 TripIntent 的任意字段中选择，"
                "包括出发地、目的地、日期、天数、同行人、预算、节奏、兴趣、饮食、住宿、交通、必去项、避开项和输出偏好，"
                "但不要重复已回答字段，也不要为了凑数提问。每题提供 2 到 4 个互斥选项，并允许自定义输入。"
                "JSON 格式必须为："
                '{"intent_patch":{},"evidence":{},"next_question":'
                '{"field":"","question":"","reason":"","options":[],"allow_custom":true}}。'
                "intent_patch 中每个非空字段都必须在 evidence 中给出用户文本里的原文短句。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "query": query_text,
                    "current_intent": intent.model_dump(),
                    "clarification_answers": clarification_answers,
                    "profile_defaults": profile.model_dump(),
                    "answered_fields": list(clarification_answers.keys()),
                },
                ensure_ascii=False,
            ),
        },
    ]


async def _analyze_trip_request_with_model(
    controller: Any,
    query_text: str,
    intent: Any,
    profile: UserTravelProfile,
    clarification_answers: Dict[str, Any],
) -> Dict[str, Any]:
    analysis_agent = getattr(controller, "task_analysis_agent", None)
    call_model = getattr(analysis_agent, "_call_llm_non_streaming", None)
    if not callable(call_model):
        return {}

    messages = _semantic_trip_analysis_messages(
        query_text=query_text,
        intent=intent,
        profile=profile,
        clarification_answers=clarification_answers,
    )
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(call_model, messages),
            timeout=SEMANTIC_TRIP_ANALYSIS_TIMEOUT_SECONDS,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return {}
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        return _extract_json_object(content)
    except asyncio.TimeoutError:
        logger.warning(
            f"旅行需求语义分析超过{SEMANTIC_TRIP_ANALYSIS_TIMEOUT_SECONDS}秒，回退到确定性澄清规则"
        )
    except Exception as error:
        logger.warning(f"旅行需求语义分析失败，回退到确定性澄清规则: {error}")
    return {}


def _is_skipped_clarification_value(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == CLARIFICATION_SKIP_SENTINEL


def _normalized_clarification_answers(
    answers: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], set[str]]:
    """Split transport-only skip sentinels from values allowed into intent/model context."""
    if not isinstance(answers, dict):
        return {}, set()

    value_answers: Dict[str, Any] = {}
    skipped_fields: set[str] = set()
    for raw_field, value in answers.items():
        field = _safe_text(raw_field)
        if not field:
            continue
        if _is_skipped_clarification_value(value):
            skipped_fields.add(field)
            continue
        if _safe_text(value):
            value_answers[field] = value
    return value_answers, skipped_fields


def _encode_sse_event(payload: Dict[str, Any], request_id: str, sequence: int) -> str:
    event = dict(payload)
    event["request_id"] = request_id
    event["sequence"] = sequence
    return f"data: {json.dumps(event)}\n\n"


def _chat_complete_payload(
    *,
    request_id: str,
    message_id: str,
    finish_reason: str,
) -> Dict[str, Any]:
    return ChatCompleteEvent(
        request_id=request_id,
        message_id=message_id,
        finish_reason=finish_reason,
    ).model_dump()


def _safe_stream_error_payload(
    error: Exception,
    *,
    request_id: str,
    phase: str,
) -> Dict[str, Any]:
    error_text = _safe_text(error).lower()
    if any(token in error_text for token in ("allocationquota", "free tier", "quota", "rate limit", "429")):
        code = "MODEL_QUOTA_EXCEEDED"
        retryable = False
        user_message = "当前模型服务额度不足，请检查模型设置后重试。"
        actions = ["open_settings", "switch_fast_mode"]
    elif any(token in error_text for token in ("api key", "unauthorized", "authentication", "401", "403")):
        code = "MODEL_AUTH_FAILED"
        retryable = False
        user_message = "模型服务连接失败，请检查 API 配置后重试。"
        actions = ["open_settings", "retry"]
    elif isinstance(error, (asyncio.TimeoutError, FuturesTimeoutError, TimeoutError)) or "timeout" in error_text:
        code = "UPSTREAM_TIMEOUT"
        retryable = True
        user_message = "本次生成等待超时，已保留当前结果。"
        actions = ["retry", "switch_fast_mode"]
    else:
        code = "CHAT_STREAM_FAILED"
        retryable = True
        user_message = "本次生成暂时中断，请重试。"
        actions = ["retry"]

    return ChatStreamErrorEvent(
        request_id=request_id,
        code=code,
        phase=phase,
        retryable=retryable,
        user_message=user_message,
        actions=actions,
    ).model_dump()


def append_trip_intent_context_message(
    message_history: List[Dict[str, Any]],
    query_text: str,
    profile: Optional[Dict[str, Any]],
    clarification_answers: Optional[Dict[str, Any]],
    intent: Optional[Any] = None,
) -> None:
    if not is_trip_planning_query(query_text):
        return
    if intent is None:
        intent = extract_trip_intent(
            query=query_text,
            profile=profile,
            clarification_answers=clarification_answers,
        )
    message_history.append(
        {
            "role": "system",
            "content": build_trip_context_message(intent, profile, clarification_answers),
            "message_id": str(uuid.uuid4()),
            "type": "system_trip_intent_context",
        }
    )


def append_selected_knowledge_context_message(
    message_history: List[Dict[str, Any]],
    selected_knowledge_context: Optional[List[Dict[str, Any]]],
) -> None:
    if not isinstance(selected_knowledge_context, list):
        return

    lines = [
        "【用户选中的旅行知识库上下文】",
        "以下条目是用户主动选择用于当前规划的参考资料。优先用于目的地、景点、住宿区域、路线和注意事项判断；不要把这些参考资料说成实时确认数据。",
    ]
    count = 0
    for index, item in enumerate(selected_knowledge_context[:8], start=1):
        if not isinstance(item, dict):
            continue
        title = _safe_text(item.get("title")) or f"知识条目 {index}"
        city = _safe_text(item.get("city"))
        source = _safe_text(item.get("source"))
        snippet = _safe_text(item.get("snippet"))
        source_url = _safe_text(item.get("source_url") or item.get("url"))
        if not (title or snippet):
            continue
        count += 1
        lines.append(f"{count}. {title}")
        if city or source:
            lines.append(f"- 城市/来源: {city or '未标注'} / {source or '本地知识库'}")
        if snippet:
            lines.append(f"- 摘要: {snippet}")
        if source_url:
            lines.append(f"- 链接: {source_url}")

    if count == 0:
        return

    message_history.append(
        {
            "role": "system",
            "content": "\n".join(lines),
            "message_id": str(uuid.uuid4()),
            "type": "system_selected_knowledge_context",
        }
    )


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _structured_map_locations(travel_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_locations = (
        travel_bundle.get("map_locations")
        if isinstance(travel_bundle.get("map_locations"), list)
        else []
    )
    map_tool = _safe_text(travel_bundle.get("map_tool"))
    locations: List[Dict[str, Any]] = []
    for raw_location in raw_locations:
        if not isinstance(raw_location, dict):
            continue
        location = dict(raw_location)
        source_text = _safe_text(location.get("source")).lower()
        has_coordinates = (
            _optional_float(location.get("lat")) is not None
            and _optional_float(location.get("lng")) is not None
        )
        explicit_data_type = _safe_text(location.get("data_type"))
        if explicit_data_type not in {
            "confirmed_live_data",
            "reference_data",
            "estimated_data",
        }:
            explicit_data_type = ""
        location["data_type"] = explicit_data_type or (
            "confirmed_live_data"
            if map_tool
            and has_coordinates
            and not any(marker in source_text for marker in ("seed", "fallback", "estimate"))
            else "reference_data"
        )
        location["source"] = _safe_text(location.get("source")) or map_tool or "map_reference"
        if location["data_type"] == "confirmed_live_data" and not location.get("updated_at"):
            location["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            normalized_detail = normalize_poi_detail(location)
            coordinates = normalized_detail.pop("coordinates", None) or {}
            location.update(normalized_detail)
            location["lat"] = coordinates.get("lat", location.get("lat"))
            location["lng"] = coordinates.get("lng", location.get("lng"))
            location["description"] = _safe_text(location.get("summary") or location.get("description"))
        except PoiDetailError:
            location.setdefault("field_evidence", {})
            location.setdefault("sources", [])
        locations.append(location)
    return locations


def _trip_plan_source_references(travel_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    web_rows = travel_bundle.get("web_rows") if isinstance(travel_bundle.get("web_rows"), list) else []
    xhs_rows = travel_bundle.get("xhs_rows") if isinstance(travel_bundle.get("xhs_rows"), list) else []

    for row in web_rows[:6]:
        if not isinstance(row, dict):
            continue
        title = _safe_text(row.get("title")) or _safe_text(row.get("url")) or "网页参考"
        sources.append(
            {
                "type": "web",
                "data_type": "reference_data",
                "title": title,
                "source": _safe_text(row.get("source")) or "web",
                "url": _safe_text(row.get("url")),
                "snippet": _safe_text(row.get("snippet")),
            }
        )

    for row in xhs_rows[:6]:
        if not isinstance(row, dict):
            continue
        title = _safe_text(row.get("title")) or "小红书参考"
        sources.append(
            {
                "type": "xhs",
                "data_type": "reference_data",
                "title": title,
                "source": _safe_text(row.get("author")) or "小红书",
                "url": _safe_text(row.get("url")),
                "snippet": _safe_text(row.get("summary") or row.get("snippet")),
            }
        )

    rag_context = _safe_text(travel_bundle.get("rag_context"))
    if rag_context:
        sources.append(
            {
                "type": "local_knowledge",
                "data_type": "reference_data",
                "title": "本地旅行知识库",
                "source": "travel_knowledge_base",
                "url": "",
                "snippet": rag_context[:320],
            }
        )

    selected_context = (
        travel_bundle.get("selected_knowledge_context")
        if isinstance(travel_bundle.get("selected_knowledge_context"), list)
        else []
    )
    for row in selected_context[:8]:
        if not isinstance(row, dict):
            continue
        sources.append(
            {
                "type": "selected_knowledge",
                "data_type": "reference_data",
                "title": _safe_text(row.get("title")) or "用户选中的知识条目",
                "source": _safe_text(row.get("source")) or "travel_knowledge_base",
                "url": _safe_text(row.get("source_url") or row.get("url")),
                "snippet": _safe_text(row.get("snippet")),
            }
        )

    map_locations = _structured_map_locations(travel_bundle)
    map_tool = _safe_text(travel_bundle.get("map_tool"))
    confirmed_map_count = sum(
        location.get("data_type") == "confirmed_live_data" for location in map_locations
    )
    if map_tool and confirmed_map_count:
        sources.append(
            {
                "type": "map",
                "data_type": "confirmed_live_data",
                "title": "地图地点核验",
                "source": map_tool,
                "url": "",
                "snippet": f"地图工具已实时核验 {confirmed_map_count} 个地点坐标。",
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )

    weather_tool = _safe_text(travel_bundle.get("weather_tool"))
    weather_summary = _safe_text(travel_bundle.get("weather_summary"))
    if weather_tool and weather_summary:
        sources.append(
            {
                "type": "weather",
                "data_type": "confirmed_live_data",
                "title": "目的地天气信息",
                "source": weather_tool,
                "url": "",
                "snippet": weather_summary[:320],
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )

    return sources


def _travel_bundle_to_trip_plan(travel_bundle: Dict[str, Any]) -> TripPlan:
    query_text = _safe_text(travel_bundle.get("query")) or "旅行方案"
    raw_intent = travel_bundle.get("trip_intent")
    try:
        intent = TripIntent.model_validate(raw_intent) if raw_intent else extract_trip_intent(query_text)
    except Exception:
        intent = extract_trip_intent(query_text)
    map_locations = _structured_map_locations(travel_bundle)
    max_day = max(
        [_optional_int(location.get("day")) or 1 for location in map_locations if isinstance(location, dict)]
        or [1]
    )
    days = max(1, intent.days or max_day)
    activities: List[TripActivity] = []
    locations_per_day = max(1, (len(map_locations) + days - 1) // days)

    for index, location in enumerate(map_locations, start=1):
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name")) or f"地点 {index}"
        category = _safe_text(location.get("category")) or "地点"
        activity_type = "attraction"
        if re.search(r"餐厅|美食|小吃|restaurant|food|cafe", category, re.IGNORECASE):
            activity_type = "food"
        elif re.search(r"酒店|住宿|hotel|hostel", category, re.IGNORECASE):
            activity_type = "hotel"
        elif re.search(r"交通|车站|机场|transport|station|airport", category, re.IGNORECASE):
            activity_type = "transport"
        place = TripPlace.model_validate({
            **location,
            "name": name,
            "category": category,
            "lat": _optional_float(location.get("lat")),
            "lng": _optional_float(location.get("lng")),
            "source": _safe_text(location.get("source")) or "地图地点核验",
            "data_type": location.get("data_type", "reference_data"),
        })
        notes = [_safe_text(location.get("description"))]
        explicit_day = _optional_int(location.get("day"))
        activity_day = explicit_day if explicit_day and 1 <= explicit_day <= days else min(
            days,
            ((index - 1) // locations_per_day) + 1,
        )
        activities.append(
            TripActivity(
                activity_id=_safe_text(location.get("activity_id") or location.get("id"))
                or f"act_{uuid.uuid4().hex}",
                day=activity_day,
                start_time=_safe_text(location.get("start_time")) or None,
                end_time=_safe_text(location.get("end_time")) or None,
                duration_minutes=_optional_int(location.get("duration_minutes")),
                title=name,
                activity_type=activity_type,
                place=place,
                map_visible=True,
                transport_to_next=_safe_text(location.get("transport_to_next")) or None,
                estimated_cost=_optional_float(location.get("estimated_cost")),
                reservation=location.get("reservation") if isinstance(location.get("reservation"), dict) else None,
                evidence_refs=location.get("evidence_refs")
                if isinstance(location.get("evidence_refs"), list)
                else [],
                notes=[note for note in notes if note],
                data_type="estimated_data",
            )
        )

    people_count = max(1, intent.people_count or 1)
    category_totals = {"transport": 0.0, "accommodation": 0.0, "food": 0.0, "tickets": 0.0, "other": 0.0}
    unknown_items: List[str] = []
    for activity in activities:
        category_text = _safe_text(activity.place.category if activity.place else "")
        amount = _optional_float(activity.estimated_cost)
        if re.search(r"酒店|住宿|hotel|hostel", category_text, re.IGNORECASE):
            key, fallback = "accommodation", 350.0
        elif re.search(r"餐厅|美食|小吃|restaurant|food", category_text, re.IGNORECASE):
            key, fallback = "food", 80.0 * people_count
        elif re.search(r"交通|车站|机场|transport|station|airport", category_text, re.IGNORECASE):
            key, fallback = "transport", 30.0 * people_count
        elif re.search(r"景点|博物馆|公园|寺|attraction|museum|park", category_text, re.IGNORECASE):
            key, fallback = "tickets", 60.0 * people_count
        else:
            key, fallback = "other", None
        if amount is None and fallback is None:
            unknown_items.append(activity.title)
            continue
        category_totals[key] += amount if amount is not None else fallback or 0

    estimated_total = round(sum(category_totals.values()), 2)
    target_total = intent.budget_total
    if target_total is None and intent.budget_per_person is not None:
        target_total = intent.budget_per_person * people_count
    per_person_estimate = round(estimated_total / people_count, 2) if estimated_total else None
    overrun_amount = round(max(0.0, estimated_total - float(target_total)), 2) if target_total is not None else 0.0
    budget_summary = {
        "currency": "CNY",
        "budget_total": target_total if target_total is not None else estimated_total or None,
        "budget_per_person": intent.budget_per_person or per_person_estimate,
        "people_count": people_count,
        "estimated_total": estimated_total if estimated_total else None,
        "known_total": estimated_total if estimated_total else 0,
        "unknown_count": len(unknown_items),
        "unknown_items": unknown_items,
        "categories": category_totals,
        "over_budget": overrun_amount > 0,
        "overrun_amount": overrun_amount,
        "source_label": "基于地点类别和行程规则估算；实际价格需以对应来源实时确认",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_type": "estimated_data",
    }

    source_references = _trip_plan_source_references(travel_bundle)
    source_references.append(
        {
            "type": "plan_estimate",
            "data_type": "estimated_data",
            "title": "行程顺序与费用估算",
            "source": "trip_plan_rules",
            "url": "",
            "snippet": "未由实时工具确认的活动顺序、通勤方式和费用均为规划估算，出发前需复核。",
        }
    )
    source_defaults = {
        "web": (0.72, ["summary", "opening_hours", "price"]),
        "xhs": (0.55, ["summary", "suitable_for"]),
        "local_knowledge": (0.68, ["summary", "classic_coverage"]),
        "selected_knowledge": (0.7, ["summary", "classic_coverage"]),
        "map": (0.92, ["coordinates", "address", "category", "route"]),
        "weather": (0.9, ["weather"]),
        "plan_estimate": (0.4, ["route", "budget"]),
    }
    source_updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    unique_sources: List[Dict[str, Any]] = []
    seen_sources = set()
    for source in source_references:
        source_type = _safe_text(source.get("type")) or "reference"
        confidence, related_fields = source_defaults.get(source_type, (0.5, ["summary"]))
        source["updated_at"] = _safe_text(source.get("updated_at")) or source_updated_at
        source["reference_id"] = _safe_text(source.get("reference_id")) or f"source_{len(unique_sources) + 1}"
        source["confidence"] = source.get("confidence") if source.get("confidence") is not None else confidence
        source["related_fields"] = source.get("related_fields") or related_fields
        source["related_places"] = source.get("related_places") or [
            location.get("name") for location in map_locations[:8] if location.get("name")
        ]
        source_key = (_safe_text(source.get("url")), _safe_text(source.get("title")), source_type)
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        unique_sources.append(source)
    source_references = unique_sources

    plan = TripPlan(
        title=query_text,
        intent=intent,
        days=days,
        activities=activities,
        budget_summary=budget_summary,
        map_locations=map_locations,
        source_references=source_references,
    )
    confidence_summary = {
        "confirmed_live_data": 0,
        "reference_data": 0,
        "estimated_data": 0,
    }
    for location in plan.map_locations:
        data_type = _safe_text(location.get("data_type"))
        if data_type in confidence_summary:
            confidence_summary[data_type] += 1
    for activity in plan.activities:
        confidence_summary[activity.data_type] += 1
    for source in plan.source_references:
        confidence_summary[source.data_type] += 1
    plan.data_confidence_summary = confidence_summary
    return plan


def _ensure_estimated_route_legs(plan: TripPlan) -> None:
    """Mirror legacy transport text into typed route legs without inventing live route facts."""
    for activity in plan.activities:
        if activity.route_to_next is not None:
            continue
        mode = _safe_text(activity.transport_to_next)
        if not mode:
            continue
        activity.route_to_next = RouteLeg(
            mode=mode,
            provider="行程规划估算",
            data_type="estimated_data",
        )


def _build_trip_days(plan: TripPlan, validation: Any) -> List[TripDay]:
    issues = validation.issues if validation is not None else []
    trip_days: List[TripDay] = []
    for day_number in range(1, plan.days + 1):
        activities = [activity for activity in plan.activities if activity.day == day_number]
        day_warnings = [
            issue.message
            for issue in issues
            if f"第{day_number}天" in _safe_text(getattr(issue, "message", ""))
        ]
        trip_days.append(
            TripDay(
                day=day_number,
                theme=f"第{day_number}天行程",
                activities=activities,
                budget_subtotal=sum(float(activity.estimated_cost or 0) for activity in activities),
                warnings=day_warnings,
                revision=1,
                data_type="estimated_data",
            )
        )
    return trip_days


def _public_trip_plan_payload(plan: TripPlan) -> Dict[str, Any]:
    """Serialize the new daily contract while retaining legacy flat activities."""
    payload = plan.model_dump()
    day_count = payload.pop("days")
    payload["day_count"] = day_count
    payload["days"] = payload.pop("trip_days")
    return payload


def maybe_prepare_destination_cover(
    *, destination: str, tool_manager: Any, message_history: List[Dict[str, Any]], session_id: str,
) -> Optional[Dict[str, Any]]:
    """Select one compliant destination cover when the optional image service is configured."""
    if not destination or not os.getenv("UNSPLASH_ACCESS_KEY", "").strip() or tool_manager is None:
        return None
    try:
        tool_name = next(
            (
                str(item.get("name") or "")
                for item in tool_manager.list_tools()
                if isinstance(item, dict) and "unsplash_search_photos" in str(item.get("name") or "").casefold()
            ),
            "",
        )
        if not tool_name:
            return None
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=[{"query": f"{destination} travel landmark", "per_page": 8}],
            deadline=time.monotonic() + 8.0,
            timeout_label="目的地图片查询",
        )
        photos = payload.get("photos") if isinstance(payload, dict) else None
        if not isinstance(photos, list):
            return None
        photo = next((item for item in photos if isinstance(item, dict) and item.get("url")), None)
        if not photo:
            return None
        return {
            "url": str(photo.get("url") or ""),
            "alt": str(photo.get("alt") or destination),
            "photographer_name": str(photo.get("photographer_name") or "Unsplash photographer"),
            "photographer_url": str(photo.get("photographer_url") or "https://unsplash.com"),
            "unsplash_url": str(photo.get("unsplash_url") or "https://unsplash.com"),
            "download_location": str(photo.get("download_location") or "") or None,
            "source_reference_id": "source_cover_unsplash",
        }
    except Exception as error:
        logger.warning(f"目的地封面查询降级: {error}")
        return None


def _build_travel_structured_result(travel_bundle: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    initial_plan = _travel_bundle_to_trip_plan(travel_bundle)
    initial_validation = validate_trip_plan(initial_plan)
    repair_result = repair_trip_plan_once(initial_plan, initial_validation)
    plan = repair_result.plan
    final_validation = repair_result.remaining_validation
    _ensure_estimated_route_legs(plan)
    plan.trip_days = _build_trip_days(plan, final_validation)
    plan_payload = _public_trip_plan_payload(plan)
    document = adapt_v1_document_to_v2({
        "schema_version": "1.0", "plan": plan.model_dump(mode="json"),
        "budget": plan.budget_summary, "sources": [item.model_dump(mode="json") for item in plan.source_references],
        "cover_image": travel_bundle.get("cover_image"),
    })
    scope = document["outbound_transport"]["scope"]
    outbound_date = document["outbound_transport"].get("travel_date")
    return_date = document["return_transport"].get("travel_date")
    document["outbound_transport"] = transport_section_from_bundle(
        travel_bundle.get("ticket_bundle"), "outbound", scope, outbound_date,
    )
    document["return_transport"] = transport_section_from_bundle(
        travel_bundle.get("return_ticket_bundle"), "return", scope, return_date,
    )
    ticket_source_labels = {"train": "铁路票务平台", "intercity_bus": "城际客运平台", "flight": "航班票务平台"}
    existing_source_ids = {str(item.get("reference_id") or "") for item in document["sources"]}
    for section_name in ("outbound_transport", "return_transport"):
        for option in document[section_name].get("options", []):
            reference_id = str(option.get("source_reference_id") or "")
            if not reference_id or reference_id in existing_source_ids:
                continue
            existing_source_ids.add(reference_id)
            document["sources"].append({
                "reference_id": reference_id, "type": "ticket", "title": f"{ticket_source_labels.get(option['mode'], '票务平台')}查询结果",
                "source": ticket_source_labels.get(option["mode"], "票务平台"), "url": "", "snippet": "班次、时间和价格来自本次票务查询。",
                "data_type": option.get("data_type") or "reference_data", "updated_at": option.get("queried_at"),
                "confidence": 0.9 if option.get("data_type") == "confirmed_live_data" else 0.65,
                "related_fields": [section_name, "price", "availability"], "related_places": [],
            })
    repair_payload = repair_result.model_dump(exclude={"plan", "remaining_validation"})
    events = [
        {
            "type": "trip_plan_delta",
            "operation": "initialize_plan",
            "plan": {
                "plan_id": plan.plan_id,
                "version": plan.version,
                "title": plan.title,
                "intent": plan.intent.model_dump(),
                "day_count": plan.days,
                "days": [],
                "activities": [],
                "map_locations": [],
            },
            "delta": {
                "plan_id": plan.plan_id,
                "version": plan.version,
                "title": plan.title,
                "day_count": plan.days,
                "days": [],
                "activities": [],
                "map_locations": [],
            },
        },
        *[
            {
                "type": "trip_day_upsert",
                "plan_id": plan.plan_id,
                "version": plan.version,
                "day": trip_day.model_dump(),
                "revision": trip_day.revision,
            }
            for trip_day in plan.trip_days
        ],
        {
            "type": "trip_locations",
            "locations": plan_payload["map_locations"],
        },
        {
            "type": "trip_budget",
            "budget": plan.budget_summary,
        },
        {
            "type": "trip_sources",
            "sources": plan_payload["source_references"],
        },
        {
            "type": "trip_plan_repair",
            "repair": repair_payload,
            "initial_validation": initial_validation.model_dump(),
            "final_validation": final_validation.model_dump(),
        },
        {
            "type": "trip_validation",
            "validation": final_validation.model_dump(),
            "initial_validation": initial_validation.model_dump(),
            "final_validation": final_validation.model_dump(),
            "repair": repair_payload,
        },
        {
            "type": "trip_plan",
            "version": plan.version,
            "document": document,
            "plan": {
                **plan_payload,
                "validation": final_validation.model_dump(),
                "initial_validation": initial_validation.model_dump(),
                "repair": repair_payload,
            },
        },
    ]
    return sanitize_user_visible_payload(events), export_trip_markdown(document)


def _build_travel_structured_events(travel_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    events, _ = _build_travel_structured_result(travel_bundle)
    return events


def _structured_trip_plan_context(events: List[Dict[str, Any]]) -> str:
    validation_event = next(
        (event for event in events if event.get("type") == "trip_validation"),
        {},
    )
    validation = validation_event.get("final_validation") or validation_event.get("validation") or {}
    repair = validation_event.get("repair") if isinstance(validation_event.get("repair"), dict) else {}
    issues = validation.get("issues") if isinstance(validation.get("issues"), list) else []
    issue_messages = [
        _safe_text(issue.get("message"))
        for issue in issues
        if isinstance(issue, dict) and _safe_text(issue.get("message"))
    ]
    resolved_codes = repair.get("resolved_issue_codes") if isinstance(repair.get("resolved_issue_codes"), list) else []
    lines = [
        "【结构化行程校验结果】",
        "最终回答必须与已发送给前端的结构化行程一致，不得恢复已移除的实时价格、库存或预订断言。",
    ]
    if resolved_codes:
        lines.append(f"已自动修复：{', '.join(str(code) for code in resolved_codes)}。")
    if issue_messages:
        lines.append("仍需用户出发前确认：" + "；".join(issue_messages[:6]))
    else:
        lines.append("结构化校验未发现剩余问题。")
    return "\n".join(lines)


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


def _sanitize_non_stream_result(result: Any) -> Any:
    """清洗非流式响应中真正展示给用户的最终回答字段。"""
    if not isinstance(result, dict):
        return result

    from services.text_sanitizer_service import sanitize_user_visible_text

    def sanitize_message(message: Any) -> None:
        if not isinstance(message, dict):
            return
        if message.get("type") != "final_answer" or message.get("role", "assistant") != "assistant":
            return

        original_content = message.get("content")
        if isinstance(original_content, str):
            safe_content = sanitize_user_visible_text(original_content)
            message["content"] = safe_content or "当前回答包含无法安全展示的内部结果，请稍后重试。"

        original_show_content = message.get("show_content")
        if isinstance(original_show_content, str):
            message["show_content"] = sanitize_user_visible_text(original_show_content)

    for key in ("all_messages", "new_messages"):
        messages = result.get(key)
        if isinstance(messages, list):
            for message in messages:
                sanitize_message(message)

    sanitize_message(result.get("final_output"))
    return result


def execute_chat_once(
    request_messages: List[Any],
    controller: Any,
    tool_manager: Any,
    session_id: Optional[str],
    use_deepthink: bool,
    use_multi_agent: bool,
    selected_mcp_servers: Optional[List[str]] = None,
    selected_skill_ids: Optional[List[str]] = None,
    selected_knowledge_context: Optional[List[Dict[str, Any]]] = None,
    profile: Optional[Dict[str, Any]] = None,
    planning_mode: Optional[str] = None,
    allow_web_search: bool = True,
) -> Dict[str, Any]:
    """Run a non-stream chat request and build standard response payload."""
    message_history = build_message_history(request_messages)
    skill_message = build_skill_system_message(selected_skill_ids)
    if skill_message:
        message_history.insert(0, skill_message)
    append_selected_knowledge_context_message(message_history, selected_knowledge_context)
    append_online_search_policy_message(message_history, allow_web_search)
    runtime_session_id = session_id or str(uuid.uuid4())
    ticket_bundle: Optional[Dict[str, Any]] = None
    xhs_bundle: Optional[Dict[str, Any]] = None
    travel_bundle: Optional[Dict[str, Any]] = None
    travel_rag_context = ""

    latest_user_query = _extract_latest_user_query(message_history)
    effective_use_deepthink, effective_use_multi_agent = resolve_planning_mode_flags(
        planning_mode=planning_mode,
        use_deepthink=use_deepthink,
        use_multi_agent=use_multi_agent,
    )
    effective_selected_mcp_servers = merge_skill_mcp_servers(selected_mcp_servers, selected_skill_ids)
    effective_tool_manager = tool_manager
    if effective_selected_mcp_servers is not None or not allow_web_search:
        effective_tool_manager = _build_filtered_tool_manager(
            tool_manager,
            effective_selected_mcp_servers,
            allow_web_search=allow_web_search,
        )
    if _is_travel_experience_query(latest_user_query) or is_trip_planning_query(latest_user_query):
        _begin_map_planning_scope(
            effective_tool_manager,
            runtime_session_id,
            str(uuid.uuid4()),
        )
    if is_trip_planning_query(latest_user_query):
        append_trip_intent_context_message(
            message_history=message_history,
            query_text=latest_user_query,
            profile=profile,
            clarification_answers={},
        )
    try:
        ticket_bundle = maybe_prepare_train_ticket_bundle(
            user_query=latest_user_query,
            tool_manager=effective_tool_manager,
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

    if ticket_bundle and not is_trip_planning_query(latest_user_query):
        ticket_answer = _safe_text(ticket_bundle.get("realtime_only_answer")) or _build_realtime_only_answer(ticket_bundle)
        final_output = {
            "role": "assistant",
            "type": "final_answer",
            "content": ticket_answer,
            "show_content": ticket_answer,
        }
        direct_result = {
            "all_messages": [*message_history, final_output],
            "new_messages": [final_output],
            "final_output": final_output,
            "realtime_ticket_enforced": True,
        }
        return {
            "status": "success",
            "result": _sanitize_non_stream_result(direct_result),
        }

    if not ticket_bundle and allow_web_search:
        try:
            xhs_bundle = maybe_prepare_xhs_search_bundle(
                user_query=latest_user_query,
                tool_manager=effective_tool_manager,
                message_history=message_history,
                session_id=runtime_session_id,
                selected_skill_ids=selected_skill_ids,
            )
        except Exception as xhs_error:
            logger.error(f"非流式小红书预检索失败: {xhs_error}")

    if not ticket_bundle or is_trip_planning_query(latest_user_query):
        try:
            travel_bundle = maybe_prepare_travel_experience_bundle(
                user_query=latest_user_query,
                tool_manager=effective_tool_manager,
                message_history=message_history,
                session_id=runtime_session_id,
                selected_skill_ids=selected_skill_ids,
                xhs_bundle=xhs_bundle,
                allow_web_search=allow_web_search,
            )
            if travel_bundle:
                if ticket_bundle:
                    travel_bundle["ticket_bundle"] = ticket_bundle
                destination = _safe_text(travel_bundle.get("destination_city")) or _extract_destination_city(latest_user_query)
                travel_bundle["cover_image"] = maybe_prepare_destination_cover(
                    destination=destination,
                    tool_manager=effective_tool_manager,
                    message_history=message_history,
                    session_id=runtime_session_id,
                )
            if travel_bundle and travel_bundle.get("context_message"):
                append_travel_rag_context_message(
                    message_history,
                    _safe_text(travel_bundle.get("rag_context")),
                )
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
        effective_tool_manager,
        session_id=runtime_session_id,
        deep_thinking=effective_use_deepthink,
        summary=True,
        deep_research=effective_use_multi_agent,
    )

    if travel_bundle:
        result = _apply_travel_experience_result(result, travel_bundle, session_id=runtime_session_id)
    elif ticket_bundle:
        result = _apply_realtime_only_result(result, ticket_bundle)
    elif xhs_bundle:
        result = _apply_xhs_result(result, xhs_bundle)

    result = _sanitize_non_stream_result(result)

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
    selected_mcp_servers: Optional[List[str]] = None,
    selected_skill_ids: Optional[List[str]] = None,
    selected_knowledge_context: Optional[List[Dict[str, Any]]] = None,
    profile: Optional[Dict[str, Any]] = None,
    planning_mode: Optional[str] = None,
    allow_web_search: bool = True,
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
            selected_mcp_servers=selected_mcp_servers,
            selected_skill_ids=selected_skill_ids,
            selected_knowledge_context=selected_knowledge_context,
            profile=profile,
            planning_mode=planning_mode,
            allow_web_search=allow_web_search,
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
    route_kwargs = {
        "request_messages": request.messages,
        "controller": controller,
        "tool_manager": tool_manager,
        "session_id": request.session_id,
        "use_deepthink": request.use_deepthink,
        "use_multi_agent": request.use_multi_agent,
        "selected_mcp_servers": getattr(request, "selected_mcp_servers", None),
        "selected_skill_ids": getattr(request, "selected_skill_ids", []),
        "profile": getattr(request, "profile", {}),
        "planning_mode": getattr(request, "planning_mode", None),
        "allow_web_search": getattr(request, "allow_web_search", True),
        "logger": logger,
    }
    if hasattr(request, "selected_knowledge_context"):
        route_kwargs["selected_knowledge_context"] = getattr(request, "selected_knowledge_context")
    return execute_chat_route(**route_kwargs)


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
    profile: Optional[Dict[str, Any]] = None,
    planning_mode: Optional[str] = None,
    allow_web_search: bool = True,
    clarification_answers: Optional[Dict[str, Any]] = None,
    selected_knowledge_context: Optional[List[Dict[str, Any]]] = None,
    request_id: Optional[str] = None,
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

    stream_kwargs = {
        "request_messages": request_messages,
        "controller": controller,
        "tool_manager": tool_manager,
        "selected_mcp_servers": selected_mcp_servers,
        "selected_skill_ids": selected_skill_ids,
        "use_deepthink": use_deepthink,
        "use_multi_agent": use_multi_agent,
        "allow_web_search": allow_web_search,
        "sanitize_text": effective_sanitize_text,
    }
    if request_id is not None:
        stream_kwargs["request_id"] = request_id
    if profile is not None:
        stream_kwargs["profile"] = profile
    if planning_mode is not None:
        stream_kwargs["planning_mode"] = planning_mode
    if clarification_answers is not None:
        stream_kwargs["clarification_answers"] = clarification_answers
    if selected_knowledge_context is not None:
        stream_kwargs["selected_knowledge_context"] = selected_knowledge_context

    return StreamingResponse(
        generate_chat_stream(**stream_kwargs),
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
    profile: Optional[Dict[str, Any]] = None,
    planning_mode: Optional[str] = None,
    allow_web_search: bool = True,
    clarification_answers: Optional[Dict[str, Any]] = None,
    selected_knowledge_context: Optional[List[Dict[str, Any]]] = None,
    request_id: Optional[str] = None,
    logger: Any = logger,
) -> Any:
    """Build chat-stream response with route-level error boundary semantics."""
    from fastapi import HTTPException

    try:
        response_kwargs = {
            "request_messages": request_messages,
            "controller": controller,
            "tool_manager": tool_manager,
            "selected_mcp_servers": selected_mcp_servers,
            "selected_skill_ids": selected_skill_ids,
            "use_deepthink": use_deepthink,
            "use_multi_agent": use_multi_agent,
            "allow_web_search": allow_web_search,
        }
        if request_id is not None:
            response_kwargs["request_id"] = request_id
        if profile is not None:
            response_kwargs["profile"] = profile
        if planning_mode is not None:
            response_kwargs["planning_mode"] = planning_mode
        if clarification_answers is not None:
            response_kwargs["clarification_answers"] = clarification_answers
        if selected_knowledge_context is not None:
            response_kwargs["selected_knowledge_context"] = selected_knowledge_context
        return build_chat_stream_response(**response_kwargs)
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
    route_kwargs = {
        "request_messages": request.messages,
        "controller": controller,
        "tool_manager": tool_manager,
        "selected_mcp_servers": request.selected_mcp_servers,
        "selected_skill_ids": getattr(request, "selected_skill_ids", []),
        "use_deepthink": request.use_deepthink,
        "use_multi_agent": request.use_multi_agent,
        "logger": logger,
    }
    if hasattr(request, "profile"):
        route_kwargs["profile"] = getattr(request, "profile")
    if hasattr(request, "planning_mode"):
        route_kwargs["planning_mode"] = getattr(request, "planning_mode")
    if hasattr(request, "allow_web_search"):
        route_kwargs["allow_web_search"] = getattr(request, "allow_web_search")
    if hasattr(request, "clarification_answers"):
        route_kwargs["clarification_answers"] = getattr(request, "clarification_answers")
    if hasattr(request, "selected_knowledge_context"):
        route_kwargs["selected_knowledge_context"] = getattr(request, "selected_knowledge_context")
    if hasattr(request, "request_id"):
        route_kwargs["request_id"] = getattr(request, "request_id")
    return build_chat_stream_route(**route_kwargs)


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
    profile: Optional[Dict[str, Any]] = None,
    planning_mode: Optional[str] = None,
    allow_web_search: bool = True,
    clarification_answers: Optional[Dict[str, Any]] = None,
    selected_knowledge_context: Optional[List[Dict[str, Any]]] = None,
    request_id: Optional[str] = None,
    sanitize_text: Optional[Callable[[str], str]] = None,
) -> AsyncGenerator[str, None]:
    """Generate SSE stream payload for chat endpoint."""
    stream_request_id = _safe_text(request_id) or str(uuid.uuid4())
    error_phase = "initializing"
    event_sequence = 0

    def encode_event(payload: Dict[str, Any]) -> str:
        nonlocal event_sequence
        event_sequence += 1
        public_payload = dict(payload)
        event_type = _safe_text(public_payload.get("type"))
        if event_type not in {"error", "chat_complete"}:
            stage_by_type = {
                "chat_start": "initializing",
                "trip_intent": "understanding",
                "clarification_required": "clarifying",
                "trip_plan_delta": "planning",
                "trip_day_upsert": "planning",
                "trip_locations": "planning",
                "trip_budget": "planning",
                "trip_sources": "researching",
                "trip_plan_repair": "validating",
                "trip_validation": "validating",
                "trip_plan": "finalizing",
            }
            public_payload.setdefault("stage", stage_by_type.get(event_type, error_phase))
        return _encode_sse_event(public_payload, stream_request_id, event_sequence)

    try:
        sanitize_text = sanitize_text or (lambda text: text)
        message_history = build_message_history(request_messages)
        skill_message = build_skill_system_message(selected_skill_ids)
        if skill_message:
            message_history.insert(0, skill_message)
        append_selected_knowledge_context_message(message_history, selected_knowledge_context)
        append_online_search_policy_message(message_history, allow_web_search)
        message_id = str(uuid.uuid4())
        stream_session_id = str(uuid.uuid4())

        yield encode_event({"type": "chat_start", "message_id": message_id})
        error_phase = "understanding"

        effective_tool_manager = tool_manager
        effective_selected_mcp_servers = merge_skill_mcp_servers(selected_mcp_servers, selected_skill_ids)
        if effective_selected_mcp_servers is not None or not allow_web_search:
            effective_tool_manager = await create_filtered_tool_manager(
                tool_manager,
                effective_selected_mcp_servers,
                allow_web_search=allow_web_search,
            )

        latest_user_query = _extract_latest_user_query(message_history)
        latest_user_message_id = _extract_latest_user_message_id(message_history)
        if _is_travel_experience_query(latest_user_query) or is_trip_planning_query(latest_user_query):
            _begin_map_planning_scope(
                effective_tool_manager,
                stream_session_id,
                stream_request_id,
            )
        progress_message_id = f"{message_id}-progress"
        effective_use_deepthink, effective_use_multi_agent = resolve_planning_mode_flags(
            planning_mode=planning_mode,
            use_deepthink=use_deepthink,
            use_multi_agent=use_multi_agent,
        )
        resolved_trip_intent = None

        trip_product_flow_enabled = (
            profile is not None
            or planning_mode is not None
            or clarification_answers is not None
        )

        if trip_product_flow_enabled and is_trip_planning_query(latest_user_query):
            cumulative_answers, skipped_fields = _normalized_clarification_answers(clarification_answers)
            processed_fields = set(cumulative_answers) | skipped_fields
            answered_count = count_clarification_fields(processed_fields)
            analysis_progress = (
                f"已处理第{answered_count}项补充信息，正在重新分析是否还缺关键条件。"
                if processed_fields
                else "正在分析你的旅行需求，先识别已知信息和还缺哪些关键条件。"
            )
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                analysis_progress,
                sanitize_text,
                latest_user_message_id,
            )
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.2)

            user_profile = _normalize_user_profile(profile)
            trip_intent = extract_trip_intent(
                query=latest_user_query,
                profile=None,
                clarification_answers=cumulative_answers,
            )
            semantic_analysis = await _analyze_trip_request_with_model(
                controller=controller,
                query_text=latest_user_query,
                intent=trip_intent,
                profile=user_profile,
                clarification_answers=cumulative_answers,
            )
            evidence_text = "\n".join(
                [latest_user_query, *[_safe_text(value) for value in cumulative_answers.values()]]
            )
            trip_intent, semantic_explicit_fields, preferred_question = merge_semantic_trip_analysis(
                intent=trip_intent,
                analysis=semantic_analysis,
                evidence_text=evidence_text,
            )
            resolved_trip_intent = trip_intent
            yield encode_event({"type": "trip_intent", "intent": trip_intent.model_dump()})
            await asyncio.sleep(0.01)

            answered_fields = processed_fields
            if answered_count < MAX_CLARIFICATION_QUESTIONS:
                next_question = build_next_clarification_question(
                    query=latest_user_query,
                    intent=trip_intent,
                    profile=user_profile,
                    answered_fields=answered_fields,
                    explicit_fields=semantic_explicit_fields,
                    preferred_question=preferred_question,
                )
                if next_question is not None:
                    clarification_payload = {
                        "type": "clarification_required",
                        "session_id": stream_session_id,
                        "message_id": message_id,
                        "linked_user_message_id": latest_user_message_id,
                        "intent": trip_intent.model_dump(),
                        "questions": [next_question.model_dump()],
                        "answered_count": answered_count,
                        "max_questions": MAX_CLARIFICATION_QUESTIONS,
                    }
                    yield encode_event(clarification_payload)
                    await asyncio.sleep(0.01)
                    yield encode_event(
                        _chat_complete_payload(
                            request_id=stream_request_id,
                            message_id=message_id,
                            finish_reason="clarification_required",
                        )
                    )
                    return

            append_trip_intent_context_message(
                message_history=message_history,
                query_text=latest_user_query,
                profile=None,
                clarification_answers=cumulative_answers,
                intent=trip_intent,
            )

        if _is_train_ticket_query(latest_user_query):
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "已识别为实时交通查询，正在调用票务工具并整理可确认结果。",
                sanitize_text,
                latest_user_message_id,
            )
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.01)
        elif _is_travel_experience_query(latest_user_query):
            research_scope = "天气、地点和旅行参考信息" if allow_web_search else "天气、地点和本地旅行知识"
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                f"已识别为旅行规划请求，正在整理{research_scope}。",
                sanitize_text,
                latest_user_message_id,
            )
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.01)
        elif allow_web_search and _is_xhs_query(latest_user_query):
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "已识别为小红书参考检索请求，正在检索和整理外部资源。",
                sanitize_text,
                latest_user_message_id,
            )
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.01)
        ticket_bundle: Optional[Dict[str, Any]] = None
        return_ticket_bundle: Optional[Dict[str, Any]] = None
        xhs_bundle: Optional[Dict[str, Any]] = None
        travel_bundle: Optional[Dict[str, Any]] = None
        travel_rag_context = ""
        realtime_only_answer = ""
        streamed_final_answer_text = ""
        structured_trip_final_text = ""
        model_final_answer_emitted = False
        error_phase = "researching"
        try:
            ticket_kwargs = {
                "tool_manager": effective_tool_manager,
                "message_history": message_history,
                "session_id": stream_session_id,
                "selected_skill_ids": selected_skill_ids,
            }
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", _safe_text(resolved_trip_intent.date_range) if resolved_trip_intent else "")
            can_query_return = bool(
                is_trip_planning_query(latest_user_query)
                and resolved_trip_intent and resolved_trip_intent.origin and resolved_trip_intent.destination
                and len(dates) >= 2
            )
            if can_query_return:
                outbound_task = asyncio.create_task(asyncio.to_thread(
                    maybe_prepare_train_ticket_bundle, user_query=latest_user_query, **ticket_kwargs,
                ))
                return_query = (
                    f"{resolved_trip_intent.destination}到{resolved_trip_intent.origin} "
                    f"{dates[-1]} 火车票 机票 大巴票"
                )
                return_task = asyncio.create_task(asyncio.to_thread(
                    maybe_prepare_train_ticket_bundle, user_query=return_query, **ticket_kwargs,
                ))
                done, pending = await asyncio.wait(
                    {outbound_task, return_task}, timeout=TICKET_QUERY_TOTAL_TIMEOUT_SECONDS,
                )
                for task in pending:
                    task.cancel()
                ticket_bundle = outbound_task.result() if outbound_task in done and not outbound_task.exception() else None
                return_ticket_bundle = return_task.result() if return_task in done and not return_task.exception() else None
            else:
                ticket_bundle = await asyncio.to_thread(
                    maybe_prepare_train_ticket_bundle, user_query=latest_user_query, **ticket_kwargs,
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

            if not ticket_bundle and allow_web_search:
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

        transport_only_request = bool(ticket_bundle) and not is_trip_planning_query(latest_user_query)
        if transport_only_request:
            selected_ticket_text = realtime_only_answer or _build_realtime_only_answer(ticket_bundle)
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "实时票务查询已结束，正在返回可确认的交通建议。",
                sanitize_text,
                latest_user_message_id,
            )
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.01)
            for chunk_index, text_chunk in enumerate(_split_final_answer_for_stream(selected_ticket_text)):
                final_chunk = _build_final_answer_chunk(
                    message_id,
                    text_chunk,
                    sanitize_text,
                    replace=chunk_index == 0,
                    linked_user_message_id=latest_user_message_id,
                )
                yield encode_event(final_chunk)
                await asyncio.sleep(0.01)
            yield encode_event(
                _chat_complete_payload(
                    request_id=stream_request_id,
                    message_id=message_id,
                    finish_reason="completed",
                )
            )
            return

        if not ticket_bundle or is_trip_planning_query(latest_user_query):
            try:
                travel_bundle = maybe_prepare_travel_experience_bundle(
                    user_query=latest_user_query,
                    tool_manager=effective_tool_manager,
                    message_history=message_history,
                    session_id=stream_session_id,
                    selected_skill_ids=selected_skill_ids,
                    xhs_bundle=xhs_bundle,
                    allow_web_search=allow_web_search,
                )
                if travel_bundle:
                    if ticket_bundle:
                        travel_bundle["ticket_bundle"] = ticket_bundle
                    if return_ticket_bundle:
                        travel_bundle["return_ticket_bundle"] = return_ticket_bundle
                    if resolved_trip_intent is not None:
                        travel_bundle["trip_intent"] = resolved_trip_intent.model_dump()
                    if selected_knowledge_context:
                        travel_bundle["selected_knowledge_context"] = selected_knowledge_context
                    destination = _safe_text(travel_bundle.get("destination_city")) or (
                        _safe_text(resolved_trip_intent.destination) if resolved_trip_intent else ""
                    )
                    travel_bundle["cover_image"] = await asyncio.to_thread(
                        maybe_prepare_destination_cover,
                        destination=destination,
                        tool_manager=effective_tool_manager,
                        message_history=message_history,
                        session_id=stream_session_id,
                    )
                if travel_bundle and travel_bundle.get("context_message"):
                    append_travel_rag_context_message(
                        message_history,
                        _safe_text(travel_bundle.get("rag_context")),
                    )
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

        logger.info(f"旅行增强结果状态: {'已生成' if travel_bundle else '未生成'}")
        if travel_bundle:
            try:
                structured_events, structured_trip_final_text = _build_travel_structured_result(travel_bundle)
                logger.info(
                    f"结构化行程已渲染: {len(structured_events)} 个事件, "
                    f"Markdown {len(structured_trip_final_text)} 字符"
                )
                for event in structured_events:
                    yield encode_event(event)
                    await asyncio.sleep(0.01)
                message_history.append(
                    {
                        "role": "system",
                        "content": _structured_trip_plan_context(structured_events),
                        "message_id": str(uuid.uuid4()),
                        "type": "system_trip_plan_validation_context",
                    }
                )
            except Exception as structured_error:
                logger.error(f"旅行结构化事件生成失败: {structured_error}")

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
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.01)

        if structured_trip_final_text:
            error_phase = "finalizing"
            for chunk_index, text_chunk in enumerate(_split_final_answer_for_stream(structured_trip_final_text)):
                final_chunk = _build_final_answer_chunk(
                    message_id,
                    text_chunk,
                    sanitize_text,
                    replace=chunk_index == 0,
                    linked_user_message_id=latest_user_message_id,
                )
                yield encode_event(final_chunk)
                await asyncio.sleep(0.01)
            yield encode_event(
                _chat_complete_payload(
                    request_id=stream_request_id,
                    message_id=message_id,
                    finish_reason="completed",
                )
            )
            return

        if not _ticket_bundle_has_valid_results(ticket_bundle) and not xhs_bundle and not travel_bundle:
            travel_rag_context = maybe_prepare_travel_rag_context(latest_user_query)
            append_travel_rag_context_message(message_history, travel_rag_context)

        latest_final_answer_id: Optional[str] = None
        error_phase = "drafting"

        for chunk in controller.run_stream(
            input_messages=message_history,
            tool_manager=effective_tool_manager,
            session_id=stream_session_id,
            deep_thinking=effective_use_deepthink,
            summary=True,
            deep_research=effective_use_multi_agent,
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

                # 中间消息只映射为固定的用户进度，不转发思维链、工具名或原始载荷。
                if not is_assistant_final_answer:
                    message_type = _safe_text(msg.get('type')).lower()
                    if any(token in message_type for token in ('observation', 'validation', 'repair')):
                        public_progress = '正在校验行程信息'
                    elif any(token in message_type for token in ('search', 'research', 'tool')):
                        public_progress = '正在检索并核对旅行资料'
                    else:
                        public_progress = '正在整理行程方案'
                    progress_chunk = _build_progress_chunk(
                        msg.get('message_id', message_id),
                        public_progress,
                        sanitize_text,
                        latest_user_message_id,
                    )
                    yield encode_event(progress_chunk)
                    await asyncio.sleep(0.01)
                    continue

                if _should_hide_stream_message(msg):
                    continue

                raw_content = msg.get('content', '')
                raw_show_content = msg.get('show_content', '')

                data = {
                    'type': 'chat_chunk',
                    'message_id': msg.get('message_id', message_id),
                    'role': msg.get('role', 'assistant'),
                    'content': sanitize_user_visible_text(sanitize_text(raw_content)),
                    'show_content': sanitize_user_visible_text(sanitize_text(raw_show_content)),
                    'step_type': msg.get('type', ''),
                    'agent_type': msg.get('role', ''),
                }

                yield encode_event(data)
                await asyncio.sleep(0.01)

        error_phase = "finalizing"
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
                    yield encode_event(forced_chunk)
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
                    yield encode_event(forced_chunk)
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
                    yield encode_event(forced_chunk)
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
                yield encode_event(forced_chunk)
                await asyncio.sleep(0.01)
                latest_final_answer_id = final_message_id
                streamed_final_answer_text += realtime_only_answer

        if (
            _ticket_bundle_has_valid_results(ticket_bundle)
            and ticket_bundle.get("append_markdown")
            and not _contains_ticket_table(streamed_final_answer_text)
        ):
            appendix = _safe_text(ticket_bundle.get("append_markdown"))
            sanitized_appendix = sanitize_user_visible_text(sanitize_text(appendix))
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
                yield encode_event(appendix_chunk)
                await asyncio.sleep(0.01)

        yield encode_event(
            _chat_complete_payload(
                request_id=stream_request_id,
                message_id=message_id,
                finish_reason="completed",
            )
        )

    except Exception as e:
        diagnostics = _build_runtime_model_diagnostics(controller)
        logger.error(f"流式处理错误: {e}; phase={error_phase}; diagnostics={diagnostics}")
        error_data = _safe_stream_error_payload(
            e,
            request_id=stream_request_id,
            phase=error_phase,
        )
        yield encode_event(error_data)
        yield encode_event(
            _chat_complete_payload(
                request_id=stream_request_id,
                message_id="",
                finish_reason="failed",
            )
        )
