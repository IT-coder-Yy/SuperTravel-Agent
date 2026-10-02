import asyncio
import copy
import datetime
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

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
    approximate_coordinates_for_place,
    DESTINATION_APPROXIMATE_CENTERS,
    INTERNATIONAL_DESTINATIONS,
    INTERNATIONAL_DESTINATION_META,
    canonical_destination,
    classic_places_for_city,
    destination_aliases,
    domestic_city_names,
    is_invalid_poi_name,
)
from services.trip_product_service import adapt_v1_document_to_v2, export_trip_markdown
from services.planning_errors import PlanningPipelineError
from services.ticket_search_service import transport_query_guidance, transport_section_from_bundle
from services.budget_service import apply_reference_costs, calculate_budget_summary
from services.meal_schedule_service import apply_meal_schedule, meal_targets_by_day, meal_window_for_opening_hours
from services.international_restaurant_service import fetch_osm_restaurants
from services.international_place_search_service import search_international_places
from services.travel_date_service import (
    canonicalize_planning_date_range,
    is_flexible_date_range,
    resolve_planning_date_contract,
)
from services.wikimedia_image_service import (
    fetch_wikimedia_activity_images,
    fetch_wikipedia_place_coordinates,
)
from services.image_asset_service import build_external_image_asset, build_unsplash_cover_asset
from services.image_source_service import fetch_image_source_descriptions
from services.route_geometry_service import build_day_route


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
    r"(行程|攻略|规划|旅行|旅游|之旅|游玩|路线|景点|打卡|citywalk|自由行|周末游|"
    r"三天两夜|3天2夜|\d+\s*(?:天|日游)|[一二三四五六七八九十]+天|itinerary|trip)",
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
    "武汉": [
        ("黄鹤楼", 1),
        ("武汉长江大桥", 1),
        ("昙华林", 1),
        ("湖北省博物馆", 2),
        ("东湖生态旅游风景区", 2),
        ("武汉大学", 2),
        ("汉口江滩", 3),
    ],
    "成都": [
        ("武侯祠", 1),
        ("杜甫草堂", 1),
        ("金沙遗址博物馆", 1),
        ("宽窄巷子", 1),
        ("成都大熊猫繁育研究基地", 2),
        ("成都博物馆", 2),
        ("人民公园", 2),
        ("都江堰景区", 3),
        ("青城山景区", 3),
    ],
    "西安": [
        ("陕西历史博物馆", 1),
        ("大雁塔", 1),
        ("西安碑林博物馆", 1),
        ("西安城墙", 1),
        ("秦始皇帝陵博物院", 2),
        ("华清宫", 2),
        ("西安博物院", 3),
        ("大唐芙蓉园", 3),
        ("大明宫国家遗址公园", 3),
        ("回民街", 3),
    ],
    "南京": [
        ("南京博物院", 1),
        ("中山陵", 1),
        ("明孝陵", 1),
        ("玄武湖公园", 1),
        ("南京城墙博物馆", 1),
        ("鸡鸣汤包", 1),
        ("南京大牌档", 1),
        ("总统府", 2),
        ("夫子庙秦淮风光带", 2),
        ("中华门城堡", 2),
        ("老门东历史文化街区", 2),
        ("瞻园", 2),
        ("夫子庙美食街", 2),
        ("老门东美食街", 2),
        ("侵华日军南京大屠杀遇难同胞纪念馆", 3),
        ("阅江楼", 3),
        ("鸡鸣寺", 3),
        ("雨花台风景区", 3),
        ("牛首山文化旅游区", 3),
        ("狮子桥美食街", 3),
        ("马祥兴菜馆", 3),
    ],
}

DESTINATION_SEED_PLACE_SUMMARIES: Dict[str, str] = {
    "南京博物院": "以六朝、明清与近现代藏品见长，适合作为南京历史线的开场。",
    "中山陵": "钟山风景区的代表性纪念建筑，步行台阶较多，建议安排在上午。",
    "明孝陵": "明代帝陵遗存与钟山林景相连，可与中山陵同日安排并预留步行时间。",
    "鸡鸣汤包": "可作为本地小吃餐点候选，鸭血粉丝汤与汤包是常见搭配。",
    "南京大牌档": "以金陵菜与小吃为主，适合作为朋友同行的晚餐候选。",
    "总统府": "串联近代中国历史与民国建筑，可与新街口片区同日安排。",
    "夫子庙秦淮风光带": "秦淮河沿线的人文街区，适合傍晚至夜间步行体验。",
    "中华门城堡": "保存较完整的明城墙城门遗存，适合从城防建筑视角认识南京。",
    "夫子庙美食街": "可集中体验鸭血粉丝汤、盐水鸭等南京小吃，具体店铺按当日排队情况选择。",
    "老门东美食街": "老城南历史街区内餐饮选择集中，适合作为古城漫步后的用餐候选。",
    "侵华日军南京大屠杀遇难同胞纪念馆": "主题严肃，建议预留完整参观时段并提前核验预约规则。",
    "阅江楼": "登高可看长江与城北风景，适合作为第三天的城市景观节点。",
    "鸡鸣寺": "位于玄武湖与城墙周边，可作为晨间人文散步的候选节点。",
    "狮子桥美食街": "湖南路片区的餐饮聚集地，可作为晚餐和夜间小吃候选。",
    "马祥兴菜馆": "金陵菜老字号参考，可作为盐水鸭等地方菜的正餐候选。",
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
    text = _safe_text(query_text)
    if not text:
        return ""

    route = _extract_route_from_query(text)
    if route:
        route_destination = canonical_destination(route[1]) or _clean_route_name(route[1])
        if route_destination:
            return route_destination

    catalog_match = canonical_destination(text)
    if catalog_match:
        return catalog_match

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


def _resolve_trip_destination(query_text: str) -> str:
    """Use the structured intent as the single destination source when possible."""
    try:
        intent = extract_trip_intent(_safe_text(query_text))
        destination = canonical_destination(_safe_text(intent.destination)) or _safe_text(intent.destination)
        if destination:
            return destination
    except Exception:
        pass
    return _extract_destination_city(query_text)


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


def _normalize_ticket_seat_options(prices: Any, fallback_seat: str = "-", fallback_left: str = "-", fallback_price: str = "-") -> List[Dict[str, str]]:
    """Preserve every provider-returned class for the formal transport card."""
    options: List[Dict[str, str]] = []
    seen = set()
    if isinstance(prices, list):
        for entry in prices:
            if not isinstance(entry, dict):
                continue
            seat_name = _extract_first_value(entry, ["seat_name", "seatName", "seat", "name"], default="-")
            if not seat_name or seat_name == "-" or seat_name in seen:
                continue
            seen.add(seat_name)
            options.append({
                "name": seat_name,
                "remaining_text": _extract_first_value(entry, ["num", "left", "left_num", "leftNum", "remaining"], default=""),
                "price": _format_price(_extract_first_value(entry, ["price", "ticketPrice", "amount", "price_num"], default="")),
            })
    if not options and fallback_seat and fallback_seat != "-":
        options.append({
            "name": fallback_seat,
            "remaining_text": "" if fallback_left == "-" else fallback_left,
            "price": fallback_price,
        })
    return options[:12]


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


def _normalize_direct_rows(payload: Any, from_station: str, to_station: str, travel_date: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

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
            "seat_options": _normalize_ticket_seat_options(
                item.get("prices"),
                fallback_seat=seat,
                fallback_left=seat_left,
                fallback_price=price,
            ),
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
    if isinstance(payload, str):
        message = payload.strip().lower()
        return any(
            marker in message
            for marker in (" failed:", "parameter invalid", "tool error", "unauthorized", "forbidden")
        )
    if not isinstance(payload, dict):
        return False

    if payload.get("isError") is True or payload.get("is_error") is True:
        return True

    if payload.get("error") is True:
        return True

    if _safe_text(payload.get("status")).lower() in {"error", "failed", "failure"}:
        return True

    if _safe_text(payload.get("error_type")):
        return True

    unwrapped = _unwrap_tool_output(payload)
    if unwrapped is not payload and isinstance(unwrapped, str):
        message = unwrapped.lower()
        if any(
            keyword in message
            for keyword in (
                "tool error",
                "request failed",
                "unauthorized",
                "forbidden",
                "invalid api key",
                "服务调用失败",
                "请求失败",
            )
        ):
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
    request_priority: Optional[str] = None,
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
            call_args = dict(args)
            if request_priority:
                call_args["_baidu_priority"] = request_priority
            raw_result = _run_tool_with_deadline(
                tool_manager=tool_manager,
                tool_name=tool_name,
                message_history=message_history,
                session_id=session_id,
                deadline=deadline,
                timeout_label=timeout_label,
                **call_args,
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
    tool_name = _first_available_tool_name(
        tool_manager,
        ["maps_ip_location", "map_ip_location", "ip_location", "geo_ip_location"],
    )
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
    tool_name = _first_available_tool_name(
        tool_manager,
        ["maps_weather", "map_weather", "weather", "get_weather"],
    )
    if not tool_name:
        return "", "", "天气工具不可用"
    if tool_name in {"maps_weather", "map_weather"}:
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

    # tavily-mcp 0.2.x returns a readable text block instead of a JSON result:
    # ``Detailed Results:\n\nTitle: ...\nURL: ...\nContent: ...``.
    # Keep the parser strict so arbitrary model prose is never treated as a source.
    if isinstance(payload, str):
        tavily_items: List[Dict[str, str]] = []
        result_pattern = re.compile(
            r"(?:^|\n)Title:\s*(?P<title>[^\r\n]+)\r?\n"
            r"URL:\s*(?P<url>https?://[^\s]+)\r?\n"
            r"(?:Content|Snippet):\s*(?P<content>[\s\S]*?)"
            r"(?=\r?\n\r?\nTitle:|\Z)",
            re.IGNORECASE,
        )
        for match in result_pattern.finditer(payload.strip()):
            tavily_items.append({
                "title": match.group("title").strip(),
                "url": match.group("url").strip(),
                "content": re.sub(r"\s+", " ", match.group("content")).strip(),
            })
        if tavily_items:
            payload = {"results": tavily_items}

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
        if source == "-" and url != "-":
            source = (urlparse(url).hostname or "").removeprefix("www.") or "-"
        if snippet != "-" and len(snippet) > 320:
            snippet = f"{snippet[:317]}..."
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "snippet": snippet, "url": url, "source": source})
        if len(rows) >= max(1, max_rows):
            break

    return rows


def _canonical_web_url(value: Any) -> str:
    url = _safe_text(value)
    if not url or url == "-":
        return ""
    parsed = urlparse(url)
    if not parsed.netloc:
        return url.casefold().rstrip("/")
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.netloc.casefold().removeprefix('www.')}{path.casefold()}"


def _web_query_terms(value: Any) -> set[str]:
    text = _safe_text(value).casefold()
    terms = set(re.findall(r"[a-z0-9]{2,}", text))
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return terms - {"帮我", "一下", "推荐", "攻略", "旅行", "旅游", "行程", "怎么", "如何"}


def _web_row_quality_score(row: Dict[str, Any], query_terms: set[str]) -> float:
    title = _safe_text(row.get("title")).casefold()
    snippet = _safe_text(row.get("snippet")).casefold()
    title_terms = _web_query_terms(title)
    snippet_terms = _web_query_terms(snippet)
    score = len(query_terms & title_terms) * 3.0 + len(query_terms & snippet_terms) * 0.8
    url = _safe_text(row.get("url"))
    hostname = (urlparse(url).hostname or "").casefold()
    if url.startswith("https://"):
        score += 0.5
    if hostname.endswith(".gov.cn") or hostname == "gov.cn":
        score += 3.0
    elif hostname.endswith(".edu.cn") or hostname == "edu.cn":
        score += 2.0
    if 60 <= len(snippet) <= 320:
        score += 1.0
    providers = {
        provider.strip()
        for provider in _safe_text(row.get("search_providers")).split(",")
        if provider.strip()
    }
    if len(providers) > 1:
        score += 2.0
    return score


def _merge_web_search_rows(
    rows_by_tool: List[Tuple[str, List[Dict[str, str]]]],
    user_query: str,
    max_rows: int = 8,
) -> List[Dict[str, str]]:
    """Deduplicate two search providers and keep the most relevant, diverse references."""
    merged: List[Dict[str, str]] = []
    for tool_name, rows in rows_by_tool:
        provider = "Tavily" if "tavily" in tool_name.casefold() else "Serper"
        for raw_row in rows:
            row = dict(raw_row)
            row["search_providers"] = provider
            canonical_url = _canonical_web_url(row.get("url"))
            normalized_title = re.sub(r"[^\w\u4e00-\u9fff]+", "", _safe_text(row.get("title"))).casefold()
            existing = next(
                (
                    candidate
                    for candidate in merged
                    if (
                        canonical_url
                        and canonical_url == _canonical_web_url(candidate.get("url"))
                    )
                    or (
                        normalized_title
                        and SequenceMatcher(
                            None,
                            normalized_title,
                            re.sub(r"[^\w\u4e00-\u9fff]+", "", _safe_text(candidate.get("title"))).casefold(),
                        ).ratio() >= 0.9
                    )
                ),
                None,
            )
            if existing is None:
                merged.append(row)
                continue
            providers = list(dict.fromkeys([
                *[item.strip() for item in _safe_text(existing.get("search_providers")).split(",") if item.strip()],
                provider,
            ]))
            existing["search_providers"] = ", ".join(providers)
            if len(_safe_text(row.get("snippet"))) > len(_safe_text(existing.get("snippet"))):
                existing["snippet"] = row.get("snippet", "")
            if _safe_text(existing.get("url")) in {"", "-"} and _safe_text(row.get("url")) not in {"", "-"}:
                existing["url"] = row.get("url", "")
                existing["source"] = row.get("source", "")

    query_terms = _web_query_terms(user_query)
    ranked = sorted(
        merged,
        key=lambda row: (
            _web_row_quality_score(row, query_terms),
            len(_safe_text(row.get("snippet"))),
        ),
        reverse=True,
    )
    selected = ranked[:max(1, max_rows)]
    available_providers = {
        provider.strip()
        for row in ranked
        for provider in _safe_text(row.get("search_providers")).split(",")
        if provider.strip()
    }
    for provider in sorted(available_providers):
        if any(provider in _safe_text(row.get("search_providers")) for row in selected):
            continue
        replacement = next(
            (row for row in ranked if provider in _safe_text(row.get("search_providers"))),
            None,
        )
        if replacement is not None and selected:
            selected[-1] = replacement
    return selected


def _build_xhs_travel_query(query_text: str) -> str:
    destination = _extract_destination_city(query_text)
    if not destination:
        return query_text
    if XHS_TRAVEL_RELEVANCE_REGEX.search(query_text):
        return query_text
    return f"{destination} 旅行攻略"


def _build_xhs_lodging_query(query_text: str, destination_city: str = "") -> str:
    """Keep community research scoped to lodging area and stay experience."""
    destination = canonical_destination(_safe_text(destination_city)) or _resolve_trip_destination(query_text)
    return f"{destination} 住宿 酒店 民宿 区域 入住体验" if destination else "住宿 酒店 民宿 区域 入住体验"


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
        elif isinstance(value, str):
            numbers = re.findall(r"-?\d+(?:\.\d+)?", value.replace("，", ","))
            if len(numbers) >= 2:
                lng = _extract_float(numbers[0])
                lat = _extract_float(numbers[1])
                if lat is not None and lng is not None and -90 <= lat <= 90:
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


def _is_amap_tool(tool_name: str) -> bool:
    return _safe_text(tool_name).lower().startswith("maps_")


def _gcj02_to_bd09(longitude: float, latitude: float) -> Tuple[float, float]:
    """Convert a mainland AMap GCJ-02 point for the existing Baidu map renderer."""
    x_pi = math.pi * 3000.0 / 180.0
    z = math.sqrt(longitude * longitude + latitude * latitude) + 0.00002 * math.sin(latitude * x_pi)
    theta = math.atan2(latitude, longitude) + 0.000003 * math.cos(longitude * x_pi)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def _available_tool_names(tool_manager: Any, candidates: List[str]) -> List[str]:
    available: List[str] = []
    for candidate in candidates:
        try:
            if tool_manager.get_tool(candidate) is not None:
                available.append(candidate)
        except Exception:
            continue
    return available


def _order_map_provider_tools(tool_names: List[str], destination_city: str) -> List[str]:
    canonical = canonical_destination(_safe_text(destination_city))
    prefer_amap = canonical not in INTERNATIONAL_DESTINATIONS
    return sorted(tool_names, key=lambda tool_name: _is_amap_tool(tool_name) != prefer_amap)


def _normalize_map_locations(
    payload: Any,
    max_rows: int = 10,
    require_coordinates: bool = True,
    source_tool: str = "",
) -> List[Dict[str, Any]]:
    payload = _unwrap_tool_output(payload)
    raw_items = _first_present_list(
        payload,
        ["places", "pois", "geocodes", "results", "items", "data", "content"],
    )
    # AMap ``maps_search_detail`` returns one POI object, not a list.
    if (
        not raw_items
        and isinstance(payload, dict)
        and _safe_text(payload.get("name") or payload.get("title"))
        and any(payload.get(key) not in (None, "", [], {}) for key in ("id", "uid", "location", "address"))
    ):
        raw_items = [payload]
    rows: List[Dict[str, Any]] = []
    seen = set()
    source_is_amap = _is_amap_tool(source_tool)

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _extract_first_value(
            item,
            ["name", "title", "formatted_address", "address", "uid", "id"],
            default="",
        )
        lat, lng = _extract_location_pair(item)
        has_valid_coordinates = (
            lat is not None
            and lng is not None
            and -90 <= lat <= 90
            and -180 <= lng <= 180
        )
        if not name or (require_coordinates and not has_valid_coordinates):
            continue
        if not has_valid_coordinates:
            lat, lng = None, None
        address = _extract_first_value(
            item,
            ["formatted_address", "address", "area", "district", "adname", "city", "cityname"],
            default="",
        )
        category = _extract_first_value(item, ["category", "type", "tag"], default="")
        provider_place_id = _safe_text(item.get("uid") or item.get("place_id") or item.get("id"))
        place_id = provider_place_id
        raw_coordinate_system = _safe_text(
            item.get("coordinate_system")
            or item.get("coord_type")
            or item.get("coords_type")
        ).upper()
        provider_coordinate_system = (
            raw_coordinate_system
            if raw_coordinate_system in {"WGS84", "BD09LL", "GCJ02", "GCJ-02"}
            else ("GCJ02" if source_is_amap and has_valid_coordinates else "BD09LL" if has_valid_coordinates else "")
        )
        provider_latitude = lat
        provider_longitude = lng
        if has_valid_coordinates and provider_coordinate_system in {"GCJ02", "GCJ-02"}:
            lng, lat = _gcj02_to_bd09(lng, lat)
            coordinate_system = "BD09LL"
        else:
            coordinate_system = provider_coordinate_system or None
        if not place_id and has_valid_coordinates:
            coordinate_identity = f"{name}|{lat:.6f}|{lng:.6f}"
            prefix = "amap_geo" if source_is_amap else "baidu_geo"
            place_id = f"{prefix}_{hashlib.sha1(coordinate_identity.encode('utf-8')).hexdigest()[:20]}"
        key = (
            place_id or re.sub(r"[^\w\u4e00-\u9fff]+", "", name).casefold(),
            round(lat, 6) if lat is not None else None,
            round(lng, 6) if lng is not None else None,
        )
        if key in seen:
            continue
        seen.add(key)
        detail_info = item.get("detail_info") if isinstance(item.get("detail_info"), dict) else {}
        business = item.get("business") if isinstance(item.get("business"), dict) else {}
        biz_ext = item.get("biz_ext") if isinstance(item.get("biz_ext"), dict) else {}
        images = (
            item.get("images")
            or item.get("image_urls")
            or item.get("photos")
            or detail_info.get("image")
            or detail_info.get("images")
            or []
        )
        if isinstance(images, str):
            images = [images]
        elif isinstance(images, dict):
            images = [images]
        if isinstance(images, list):
            images = [
                _safe_text(image.get("url") or image.get("image_url")) if isinstance(image, dict) else _safe_text(image)
                for image in images
            ]
            images = [image for image in images if image.startswith(("http://", "https://"))]
        city = _extract_first_value(item, ["city", "cityname"], default="")
        province = _extract_first_value(item, ["province", "pname"], default="")
        district = _extract_first_value(item, ["district", "adname"], default="")
        rows.append(
            {
                "id": f"travel_place_{len(rows) + 1}",
                "place_id": place_id,
                "provider_place_id": provider_place_id or None,
                "identity_source": (
                    "provider_uid"
                    if provider_place_id
                    else ("provider_coordinates" if has_valid_coordinates else "unresolved")
                ),
                "name": name,
                "lat": round(lat, 6) if lat is not None else None,
                "lng": round(lng, 6) if lng is not None else None,
                "coordinate_system": coordinate_system,
                "provider_coordinate_system": provider_coordinate_system or None,
                "provider_latitude": round(provider_latitude, 6) if provider_latitude is not None else None,
                "provider_longitude": round(provider_longitude, 6) if provider_longitude is not None else None,
                "source_provider": "amap" if source_is_amap else "baidu",
                "coordinates_trusted": has_valid_coordinates,
                "address": address or None,
                "city": city or None,
                "province": province or None,
                "area": district or None,
                "description": address or "地图检索命中地点",
                "category": category or "景点",
                "rating": item.get("rating") or business.get("rating") or biz_ext.get("rating") or detail_info.get("overall_rating"),
                "images": images if isinstance(images, list) else [],
                "summary": _safe_text(item.get("summary") or item.get("description") or detail_info.get("description")),
                "opening_hours": item.get("opening_hours") or item.get("opentime2") or item.get("open_time") or business.get("opentime2") or business.get("opentime_today") or detail_info.get("opening_hours") or detail_info.get("shop_hours"),
                "price": item.get("price") or business.get("cost") or biz_ext.get("cost") or detail_info.get("price"),
                "telephone": item.get("telephone") or item.get("tel") or business.get("tel") or detail_info.get("telephone"),
                "order": len(rows) + 1,
            }
        )
        if len(rows) >= max(1, max_rows):
            break

    return rows


def _normalized_travel_location_category(location: Dict[str, Any]) -> str:
    """Prefer an explicit lodging/dining name over a generic search category."""
    current = _safe_text(location.get("category")) or "景点"
    search_category = _safe_text(location.get("search_category"))
    text = f"{current} {_safe_text(location.get('name'))}"
    if re.search(r"酒店|宾馆|住宿|民宿|客栈|hotel|hostel", text, re.IGNORECASE):
        return "酒店"
    if re.search(
        r"餐厅|餐馆|酒家|饭店|茶楼|茶餐厅|美食|小吃|咖啡|甜品|面包|烘焙|"
        r"火锅|烧烤|粤菜|川菜|湘菜|杭帮菜|本帮菜|江浙菜|中餐|西餐|料理|菜馆|"
        r"restaurant|food|cafe|bakery|cuisine",
        text,
        re.IGNORECASE,
    ):
        return "餐厅"
    if re.search(r"车站|机场|地铁|交通|station|airport|transport", text, re.IGNORECASE):
        return "交通"
    if search_category in {"酒店", "餐厅", "交通"}:
        return search_category
    return current


def _normalize_travel_location_categories(locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {**location, "category": _normalized_travel_location_category(location)}
        for location in locations
        if isinstance(location, dict)
    ]


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
    detail_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_search_detail", "map_place_details", "map_place_detail"],
    ), destination_city)
    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    enriched: List[Dict[str, Any]] = []
    for index, location in enumerate(locations):
        current = dict(location)
        detail_fetched = False
        place_id = _safe_text(current.get("place_id"))
        has_rich_detail = any(current.get(key) not in (None, "", [], {}) for key in ("rating", "images", "opening_hours", "price", "summary"))
        should_fetch_detail = (index < 48 and not current.get("opening_hours")
                               and current.get("source_provider") not in {"wikidata", "wikipedia", "openstreetmap"})
        preferred_detail_tools = sorted(
            detail_tools,
            key=lambda tool: _is_amap_tool(tool) != (_safe_text(current.get("source_provider")) == "amap"),
        )
        # Search responses usually carry coordinates. Keep extra detail calls
        # bounded to the first two POIs; candidates without provider-backed
        # opening hours remain in the pool with an explicit unavailable reason.
        if preferred_detail_tools and should_fetch_detail:
            detail_tool = preferred_detail_tools[0]
            provider_key = "amap" if _is_amap_tool(detail_tool) else "baidu"
            provider_ids = current.get("provider_place_ids") if isinstance(current.get("provider_place_ids"), dict) else {}
            detail_place_id = _safe_text(provider_ids.get(provider_key))
            if not detail_place_id and _safe_text(current.get("source_provider")) == provider_key:
                detail_place_id = place_id
            if not detail_place_id and not provider_ids:
                detail_place_id = place_id
            detail_payload = _run_tool_with_arg_candidates(
                tool_manager=tool_manager,
                tool_name=detail_tool,
                message_history=message_history,
                session_id=session_id,
                arg_candidates=[
                    {"id": detail_place_id} if detail_place_id else {"query": current.get("name"), "region": destination_city},
                    {"uid": detail_place_id} if detail_place_id else {"query": current.get("name"), "region": destination_city},
                    {"id": detail_place_id} if detail_place_id else {"place_name": current.get("name"), "city": destination_city},
                    {"query": current.get("name"), "region": destination_city},
                ],
                request_priority="supplemental",
            )
            if not _is_tool_execution_error_payload(detail_payload):
                detail = _normalize_place_detail_payload(detail_payload)
                detail_rows = _normalize_map_locations(
                    {"items": [detail]},
                    max_rows=1,
                    source_tool=detail_tool,
                )
                if (detail_rows
                    and _has_positive_destination_signal(detail_rows[0], destination_city)
                    and _map_locations_represent_same_place(current, detail_rows[0])):
                    detail_fetched = True
                    detail_row = detail_rows[0]
                    for key, value in detail_row.items():
                        if key in {"id", "place_id", "name", "order"} and current.get(key):
                            continue
                        if value not in (None, "", [], {}):
                            current[key] = value

        # images 只保留地图接口返回的 POI 图片。网页图片由正式活动图片
        # 管道匹配具体地点后附加，不能继承地点的地图来源而被直接信任。

        current["category"] = _normalized_travel_location_category(current)
        current["updated_at"] = fetched_at
        provider_label = "高德地图" if _safe_text(current.get("source_provider")) == "amap" else "百度地图"
        current["source"] = _safe_text(current.get("source")) or (
            f"{provider_label}地点详情" if detail_fetched else f"{provider_label}地点检索"
        )
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
    city = canonical_destination(_safe_text(destination_city)) or _safe_text(destination_city)
    explicit_seeds = list(DESTINATION_SEED_PLACES.get(city, []))
    seeds = explicit_seeds or classic_places_for_city(city)

    if seeds and not explicit_seeds:
        requested_days = max(1, extract_trip_intent(query_text).days or 1)
        seeds = [
            (name, (index % requested_days) + 1)
            for index, (name, _day) in enumerate(seeds)
        ]

    seen = set()
    deduped: List[Tuple[str, int]] = []
    for name, day in seeds:
        if name in seen:
            continue
        seen.add(name)
        deduped.append((name, day))
    return deduped


def _seed_place_category(place_name: str) -> str:
    if re.search(r"美食街|汤包|大牌档|菜馆|餐厅|餐馆|小吃|咖啡", _safe_text(place_name), re.IGNORECASE):
        return "餐厅"
    return "景点"


def _catalog_seed_location(destination_city: str, place_name: str, day: int) -> Dict[str, Any]:
    category = _seed_place_category(place_name)
    summary = _safe_text(DESTINATION_SEED_PLACE_SUMMARIES.get(place_name))
    if not summary:
        summary = f"{place_name}是{destination_city}的{('本地餐饮' if category == '餐厅' else '代表性游览')}候选；具体开放信息需在出发前复核。"
    catalog_id = f"catalog_{hashlib.sha1(f'{destination_city}|{place_name}'.encode('utf-8')).hexdigest()[:16]}"
    location = {
        "id": catalog_id,
        "poi_id": catalog_id,
        "name": place_name,
        "category": category,
        "city": destination_city,
        "day": day,
        "summary": summary,
        "description": summary,
        "source": "destination_catalog",
        "data_type": "reference_data",
        "requested_destination": destination_city,
        "destination_bound": True,
        "coordinates_trusted": False,
    }
    approximate = approximate_coordinates_for_place(destination_city, place_name)
    if approximate is not None:
        location.update({
            "lat": approximate[0],
            "lng": approximate[1],
            "coordinate_system": approximate[2],
            "coordinate_source": "catalog_city_approximate",
        })
    return location


def _daily_activity_targets(pace: Optional[str]) -> Tuple[int, int]:
    """Return (sightseeing, meals) targets without exceeding validator capacity."""
    if pace == "intensive":
        return 5, 2
    if pace == "relaxed":
        return 4, 2
    return 4, 2


def _lodging_breakfast_included(travel_bundle: Dict[str, Any]) -> Optional[bool]:
    candidates = travel_bundle.get("lodging_candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("breakfast_included")
        if isinstance(value, bool):
            return value
    return None


def _meal_transport_context(
    intent: TripIntent,
    *,
    destination_city: str,
    travel_bundle: Dict[str, Any],
) -> Tuple[str, Optional[str], Optional[str]]:
    try:
        date_mode, start_date, end_date, _ = resolve_planning_date_contract(intent.date_range, intent.days)
    except Exception:
        return ("flexible" if is_flexible_date_range(intent.date_range) else "fixed", None, None)
    if date_mode != "fixed" or start_date is None or end_date is None:
        return date_mode, None, None

    scope = "international" if canonical_destination(destination_city) in INTERNATIONAL_DESTINATIONS else "domestic"
    outbound = transport_section_from_bundle(
        travel_bundle.get("ticket_bundle"),
        "outbound",
        scope,
        start_date.isoformat(),
    )
    returning = transport_section_from_bundle(
        travel_bundle.get("return_ticket_bundle"),
        "return",
        scope,
        end_date.isoformat(),
    )

    def selected_clock(section: Dict[str, Any], field: str) -> Optional[str]:
        if section.get("status") != "ready":
            return None
        options = section.get("options")
        if not isinstance(options, list) or not options:
            return None
        option = next((item for item in options if item.get("option_id") == section.get("recommended_option_id")), None)
        if not isinstance(option, dict) or option.get("data_type") != "confirmed_live_data":
            return None
        value = _safe_text(option.get(field))
        return value or None

    return date_mode, selected_clock(outbound, "arrival_time"), selected_clock(returning, "departure_time")


def _ensure_daily_activity_coverage(
    locations: List[Dict[str, Any]],
    *,
    destination_city: str,
    query_text: str,
    days: int,
    pace: Optional[str],
    meal_targets_by_day: Optional[Dict[int, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """Create a readable formal agenda even when map verification is partial.

    Catalog entries remain reference data. When all map lookups fail, they carry a
    clearly marked city-level approximate point so the workbench can still display
    them and offer name-based navigation without claiming an exact POI coordinate.
    """
    target_attractions, default_target_food = _daily_activity_targets(pace)
    normalized = [
        {**location, "category": _normalized_travel_location_category(location)}
        for location in locations
        if isinstance(location, dict)
        and _normalized_travel_location_category(location) not in {"酒店", "交通"}
    ]
    city = canonical_destination(destination_city) or _safe_text(destination_city)
    city_name_key = _normalized_poi_match_text(city)

    def itinerary_name_key(value: Any) -> str:
        key = _normalized_poi_match_text(value)
        if city_name_key and key.startswith(city_name_key) and len(key) > len(city_name_key) + 1:
            key = key[len(city_name_key):]
        return key

    seen_name_keys = {itinerary_name_key(location.get("name")) for location in normalized}
    seed_locations = [
        _catalog_seed_location(destination_city, name, day)
        for name, day in _travel_seed_places(destination_city, query_text)
        if itinerary_name_key(name) not in seen_name_keys
    ]

    center = DESTINATION_APPROXIMATE_CENTERS.get(city)
    explicit_seed_days = {
        _normalized_poi_match_text(name): day
        for name, day in DESTINATION_SEED_PLACES.get(city, [])
    }
    explicit_seed_order = {
        _normalized_poi_match_text(name): index
        for index, (name, _day) in enumerate(DESTINATION_SEED_PLACES.get(city, []))
    }

    def coordinates(location: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        lat = _optional_float(location.get("lat"))
        lng = _optional_float(location.get("lng"))
        return (lat, lng) if lat is not None and lng is not None else None

    def distance_km(left: Tuple[float, float], right: Tuple[float, float]) -> float:
        lat1, lng1 = left
        lat2, lng2 = right
        lat_delta = math.radians(lat2 - lat1)
        lng_delta = math.radians(lng2 - lng1)
        value = (
            math.sin(lat_delta / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(lng_delta / 2) ** 2
        )
        return 2 * 6371.0 * math.asin(math.sqrt(value))

    def distance_to(location: Dict[str, Any], target: Optional[Tuple[float, float]]) -> float:
        point = coordinates(location)
        return distance_km(point, target) if point is not None and target is not None else math.inf

    def day_hint(location: Dict[str, Any]) -> Optional[int]:
        return (
            explicit_seed_days.get(_normalized_poi_match_text(location.get("name")))
            or _optional_int(location.get("day"))
        )

    def seed_order(location: Dict[str, Any]) -> int:
        return explicit_seed_order.get(
            _normalized_poi_match_text(location.get("name")),
            len(explicit_seed_order) + 1,
        )

    available = {"景点": [], "餐厅": []}
    for location in normalized:
        category = "餐厅" if _normalized_travel_location_category(location) == "餐厅" else "景点"
        if category == "餐厅" and not (
            _safe_text(location.get("poi_id") or location.get("place_id") or location.get("uid") or location.get("id"))
            and _optional_float(location.get("lat")) is not None
            and _optional_float(location.get("lng")) is not None
        ):
            continue
        available[category].append(location)
    available["景点"].sort(
        key=lambda location: (
            0 if day_hint(location) is not None else 1,
            day_hint(location) or days + 1,
            seed_order(location),
            distance_to(location, center),
            _safe_text(location.get("name")),
        )
    )
    seed_by_day = {day: {"景点": [], "餐厅": []} for day in range(1, days + 1)}
    for location in seed_locations:
        day = _optional_int(location.get("day")) or 1
        if 1 <= day <= days:
            seed_by_day[day][location["category"]].append(location)

    final_locations: List[Dict[str, Any]] = []
    used_name_keys = set()

    def take_next(
        pool: List[Dict[str, Any]],
        wanted: int,
        day: int,
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for location in pool:
            name = _safe_text(location.get("name"))
            name_key = itinerary_name_key(name)
            if (
                not name
                or not name_key
                or name_key in used_name_keys
                or len(selected) >= wanted
                or (predicate is not None and not predicate(location))
            ):
                continue
            used_name_keys.add(name_key)
            selected.append({**location, "day": day})
        return selected

    for day in range(1, days + 1):
        preferred_pool = [
            location for location in available["景点"]
            if day_hint(location) == day
        ]
        day_attractions = take_next(preferred_pool, target_attractions, day)
        selected_points = [
            point
            for item in day_attractions
            if item.get("coordinates_trusted") is not False
            and (point := coordinates(item)) is not None
        ]
        day_center = (
            (
                sum(point[0] for point in selected_points) / len(selected_points),
                sum(point[1] for point in selected_points) / len(selected_points),
            )
            if selected_points
            else center
        )
        if len(day_attractions) < target_attractions:
            fill_pool = sorted(
                available["景点"],
                key=lambda location: (
                    0 if day_hint(location) in {None, day} else 1,
                    distance_to(location, day_center),
                    distance_to(location, center),
                    _safe_text(location.get("name")),
                ),
            )
            day_attractions.extend(take_next(fill_pool, target_attractions - len(day_attractions), day))
        if len(day_attractions) < target_attractions:
            day_attractions.extend(take_next(seed_by_day[day]["景点"], target_attractions - len(day_attractions), day))

        selected_points = [
            point
            for item in day_attractions
            if item.get("coordinates_trusted") is not False
            and (point := coordinates(item)) is not None
        ]
        meal_center = (
            (
                sum(point[0] for point in selected_points) / len(selected_points),
                sum(point[1] for point in selected_points) / len(selected_points),
            )
            if selected_points
            else center
        )
        meal_targets = (meal_targets_by_day or {}).get(day)
        target_meal_types = list(meal_targets) if meal_targets is not None else ["lunch", "dinner"][:default_target_food]
        domestic = canonical_destination(destination_city) not in INTERNATIONAL_DESTINATIONS
        day_food: List[Dict[str, Any]] = []
        for meal_type in target_meal_types:
            if meal_type == "breakfast" and selected_points:
                meal_target = selected_points[0]
            elif meal_type == "dinner" and selected_points:
                meal_target = selected_points[-1]
            elif selected_points:
                middle_index = max(0, len(selected_points) // 2 - 1)
                nearby_points = selected_points[middle_index:middle_index + 2]
                meal_target = (
                    sum(point[0] for point in nearby_points) / len(nearby_points),
                    sum(point[1] for point in nearby_points) / len(nearby_points),
                )
            else:
                meal_target = meal_center
            meal_pool = sorted(
                available["餐厅"],
                key=lambda location: (
                    distance_to(location, meal_target),
                    distance_to(location, center),
                    _safe_text(location.get("name")),
                ),
            )
            selected = take_next(
                meal_pool,
                1,
                day,
                predicate=lambda location, current_meal=meal_type: meal_window_for_opening_hours(
                    location.get("opening_hours"),
                    current_meal,
                    domestic=domestic,
                ) is not None,
            )
            if selected:
                selected[0]["meal_type"] = meal_type
                day_food.extend(selected)

        final_locations.extend([*day_attractions, *day_food])
    return final_locations


def _select_final_map_candidates(
    locations: List[Dict[str, Any]],
    query_text: str,
    max_rows: int = 8,
) -> List[Dict[str, Any]]:
    """Select the small POI set that may enter the itinerary before detail/geocode calls."""
    deduped = _merge_map_locations(
        locations,
        [],
        max_rows=max(8, max_rows * 3),
        require_coordinates=False,
    )
    wants_itinerary = bool(ITINERARY_QUERY_REGEX.search(_safe_text(query_text)))
    requested_days = max(1, extract_trip_intent(_safe_text(query_text)).days or 1)
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
    # Lodging is rendered in its own formal section.  It becomes a map/workbench
    # anchor in the later map phase, not a sightseeing activity in daily cards.
    if wants_hotel and not wants_itinerary and buckets["hotel"]:
        selected.append(buckets["hotel"].pop(0))
    if wants_itinerary:
        food_quota = min(len(buckets["food"]), requested_days * 2 + 4, max_rows)
        selected.extend(buckets["food"][:food_quota])
        buckets["food"] = buckets["food"][food_quota:]
    elif wants_food and buckets["food"]:
        selected.append(buckets["food"].pop(0))
    bucket_order = ("attraction", "food") if wants_itinerary else ("attraction", "hotel", "food")
    for bucket_name in bucket_order:
        for location in buckets[bucket_name]:
            if len(selected) >= max_rows:
                break
            selected.append(location)
    return _merge_map_locations(
        selected,
        [],
        max_rows=max_rows,
        require_coordinates=False,
    )


UNSUITABLE_TRAVEL_POI_REGEX = re.compile(
    r"公墓|墓园|殡仪|旅行社|旅游服务(?:中心|公司)?|(?:有限|股份)?公司|"
    r"文化创意|充电站|写字楼|办公楼|营业厅|停车场|公共厕所|卫生间|"
    r"售票处|游客服务中心|购物服务|生活服务|礼品店|纪念品店|服装店|"
    r"汉服(?:体验|妆造|租赁)?|妆造馆|写真馆|摄影工作室|影楼|旅拍|"
    r"柱础|门墩|石构件|圆雕卧狮",
    re.IGNORECASE,
)
UNAVAILABLE_TRAVEL_POI_REGEX = re.compile(
    r"暂停开放|暂停营业|永久关闭|已关闭|已停业|停止营业|尚未开放|暂不开放",
    re.IGNORECASE,
)


def _is_unsuitable_travel_poi(location: Dict[str, Any]) -> bool:
    """Do not turn generic map search noise into a formal sightseeing activity."""
    text = " ".join(
        _safe_text(location.get(key))
        for key in ("name", "category", "description", "address", "type", "status", "business_status")
    )
    return bool(
        UNSUITABLE_TRAVEL_POI_REGEX.search(text)
        or UNAVAILABLE_TRAVEL_POI_REGEX.search(text)
    )


def _mentioned_destinations(value: Any) -> set[str]:
    """Return every catalog destination explicitly present in POI text."""
    text = _safe_text(value).casefold()
    if not text:
        return set()
    return {
        city
        for alias, city in destination_aliases().items()
        if alias and alias in text
    }


def _explicit_city_mentions(value: Any) -> set[str]:
    """Identify city names that appear as locality names rather than street names."""
    text = _safe_text(value).casefold()
    if not text:
        return set()
    return {
        city
        for alias, city in destination_aliases().items()
        if alias and re.search(f"{re.escape(alias)}(?:市|地区|特别行政区)", text)
    }


def _has_positive_destination_signal(location: Dict[str, Any], destination_city: str) -> bool:
    """Require locality evidence before a POI becomes a formal destination item.

    A search result containing only a street named after another city (for example,
    Shanghai's Nanjing West Road) must not enter a Nanjing itinerary.
    """
    canonical_city = canonical_destination(destination_city)
    destination = canonical_city or _safe_text(destination_city)
    if not destination:
        return False
    declared_locality = " ".join(
        _safe_text(location.get(key))
        for key in ("city", "province", "area")
    )
    locality = " ".join([
        declared_locality,
        _safe_text(location.get("address")),
        _safe_text(location.get("description")),
    ])
    local_mentions = _mentioned_destinations(declared_locality) | _explicit_city_mentions(locality)
    requested_destination = (
        canonical_destination(_safe_text(location.get("requested_destination")))
        or _safe_text(location.get("requested_destination"))
    )
    destination_bound = (
        bool(location.get("destination_bound"))
        and requested_destination == destination
    )
    if not canonical_city:
        return destination_bound or destination in local_mentions
    return (
        (destination in local_mentions or destination_bound)
        and not any(city != destination for city in local_mentions)
    )


def _filter_quality_map_locations(
    locations: List[Dict[str, Any]],
    destination_city: str,
    *,
    require_positive_destination: bool = False,
) -> List[Dict[str, Any]]:
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    kept: List[Dict[str, Any]] = []
    seen_names = set()
    for location in locations:
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name"))
        if is_invalid_poi_name(name) or _is_unsuitable_travel_poi(location):
            continue
        normalized_name = re.sub(r"[^\w\u4e00-\u9fff]+", "", name).casefold()
        if normalized_name in seen_names:
            continue
        address_city = _safe_text(location.get("city") or location.get("area"))
        address = _safe_text(location.get("description") or location.get("address"))
        explicit_city = canonical_destination(address_city)
        if destination and explicit_city and explicit_city != destination:
            continue
        if require_positive_destination and not _has_positive_destination_signal(location, destination):
            continue
        if destination and address_city and not explicit_city and len(address_city) <= 12 and destination not in f"{address_city}{address}":
            location = {**location, "quality_warning": "地点所属城市待确认"}
        seen_names.add(normalized_name)
        kept.append(location)
    return kept


def _filter_destination_lodging_locations(
    locations: List[Dict[str, Any]],
    destination_city: str,
) -> List[Dict[str, Any]]:
    """A lodging POI must positively identify the destination; region hints are not enough."""
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    if not destination:
        return []
    verified: List[Dict[str, Any]] = []
    for location in _filter_quality_map_locations(locations, destination):
        if not _has_positive_destination_signal(location, destination):
            continue
        verified.append(location)
    return verified


def _is_destination_scoped_reference(row: Dict[str, Any], destination_city: str) -> bool:
    """Reject a web/community result only when it explicitly belongs to another city."""
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    if not destination:
        return False
    text = " ".join(
        _safe_text(row.get(key))
        for key in ("title", "summary", "snippet", "url", "city", "address")
    )
    mentions = _mentioned_destinations(text) | _explicit_city_mentions(text)
    return not any(city != destination for city in mentions)


def _filter_destination_reference_rows(
    rows: List[Dict[str, Any]],
    destination_city: str,
) -> List[Dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if isinstance(row, dict) and _is_destination_scoped_reference(row, destination_city)
    ]


def _is_destination_formal_location(location: Dict[str, Any], destination_city: str) -> bool:
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    if not destination:
        return False
    if location.get("coordinates_trusted") is False:
        return False
    if _safe_text(location.get("source")).startswith("destination_catalog"):
        return False
    return _has_positive_destination_signal(location, destination)


def _merge_map_locations(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
    max_rows: int = 20,
    require_coordinates: bool = True,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for location in [*primary, *secondary]:
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name"))
        lat = _extract_float(location.get("lat"))
        lng = _extract_float(location.get("lng"))
        has_coordinates = lat is not None and lng is not None
        if not name or (require_coordinates and not has_coordinates):
            continue
        existing = next(
            (
                candidate
                for candidate in merged
                if _map_locations_represent_same_place(candidate, location)
            ),
            None,
        )
        if existing is not None:
            _merge_map_location_fields(existing, location)
            continue
        updated = dict(location)
        _initialize_map_provider_evidence(updated)
        updated["id"] = updated.get("id") or f"travel_place_{len(merged) + 1}"
        updated["order"] = updated.get("order") or len(merged) + 1
        merged.append(updated)
    for index, location in enumerate(merged, start=1):
        location["id"] = f"travel_place_{index}"
        location["order"] = index
    return merged[: max(1, max_rows)]


def _map_location_distance_meters(first: Dict[str, Any], second: Dict[str, Any]) -> Optional[float]:
    first_lat = _extract_float(first.get("lat"))
    first_lng = _extract_float(first.get("lng"))
    second_lat = _extract_float(second.get("lat"))
    second_lng = _extract_float(second.get("lng"))
    if None in {first_lat, first_lng, second_lat, second_lng}:
        return None
    lat1, lat2 = math.radians(first_lat), math.radians(second_lat)
    delta_lat = lat2 - lat1
    delta_lng = math.radians(second_lng - first_lng)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return 6371000.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))


def _map_locations_represent_same_place(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    first_provider = _safe_text(first.get("source_provider"))
    second_provider = _safe_text(second.get("source_provider"))
    first_id = _safe_text(first.get("provider_place_id") or first.get("place_id") or first.get("uid"))
    second_id = _safe_text(second.get("provider_place_id") or second.get("place_id") or second.get("uid"))
    if first_id and second_id and first_provider == second_provider and first_id == second_id:
        return True

    first_name = _normalized_poi_match_text(first.get("name"))
    second_name = _normalized_poi_match_text(second.get("name"))
    if not first_name or not second_name:
        return False
    name_matches = (
        first_name == second_name
        or (
            min(len(first_name), len(second_name)) >= 3
            and (
                first_name in second_name
                or second_name in first_name
                or SequenceMatcher(None, first_name, second_name).ratio() >= 0.86
            )
        )
    )
    if not name_matches:
        return False

    first_address = re.sub(r"[^\w\u4e00-\u9fff]+", "", _safe_text(first.get("address"))).casefold()
    second_address = re.sub(r"[^\w\u4e00-\u9fff]+", "", _safe_text(second.get("address"))).casefold()
    address_matches = bool(
        first_address
        and second_address
        and (
            first_address in second_address
            or second_address in first_address
            or SequenceMatcher(None, first_address, second_address).ratio() >= 0.72
        )
    )
    distance = _map_location_distance_meters(first, second)
    if distance is not None:
        return distance <= 500.0 or (address_matches and distance <= 2000.0)
    if first_address and second_address:
        return address_matches
    first_city = canonical_destination(_safe_text(first.get("city") or first.get("requested_destination")))
    second_city = canonical_destination(_safe_text(second.get("city") or second.get("requested_destination")))
    return bool(first_city and first_city == second_city and first_name == second_name)


def _initialize_map_provider_evidence(location: Dict[str, Any]) -> None:
    provider = _safe_text(location.get("source_provider"))
    providers = location.get("source_providers") if isinstance(location.get("source_providers"), list) else []
    location["source_providers"] = list(dict.fromkeys([*providers, *([provider] if provider else [])]))
    provider_ids = location.get("provider_place_ids") if isinstance(location.get("provider_place_ids"), dict) else {}
    provider_id = _safe_text(location.get("provider_place_id") or location.get("place_id"))
    if provider and provider_id:
        provider_ids[provider] = provider_id
    location["provider_place_ids"] = provider_ids
    provider_images = location.get("provider_images") if isinstance(location.get("provider_images"), dict) else {}
    images = [url for url in location.get("images", []) if _safe_text(url)]
    if provider and images:
        provider_images[provider] = list(dict.fromkeys(images))
    location["provider_images"] = provider_images
    evidence = location.get("provider_evidence") if isinstance(location.get("provider_evidence"), dict) else {}
    if provider:
        evidence[provider] = {
            key: location.get(key)
            for key in ("rating", "address", "opening_hours", "price", "telephone")
            if location.get(key) not in (None, "", [], {})
        }
    location["provider_evidence"] = evidence


def _merge_map_location_fields(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    _initialize_map_provider_evidence(target)
    incoming_copy = dict(incoming)
    _initialize_map_provider_evidence(incoming_copy)
    target["source_providers"] = list(dict.fromkeys([
        *target.get("source_providers", []),
        *incoming_copy.get("source_providers", []),
    ]))
    target["provider_place_ids"] = {
        **target.get("provider_place_ids", {}),
        **incoming_copy.get("provider_place_ids", {}),
    }
    target["provider_images"] = {
        **target.get("provider_images", {}),
        **incoming_copy.get("provider_images", {}),
    }
    target["provider_evidence"] = {
        **target.get("provider_evidence", {}),
        **incoming_copy.get("provider_evidence", {}),
    }
    target["images"] = list(dict.fromkeys([
        *[url for url in target.get("images", []) if _safe_text(url)],
        *[url for url in incoming_copy.get("images", []) if _safe_text(url)],
    ]))[:8]
    for key, value in incoming_copy.items():
        if key in {"id", "order", "place_id", "provider_place_id", "source_provider", "source_providers", "provider_place_ids", "provider_images", "provider_evidence", "images"}:
            continue
        current = target.get(key)
        if current in (None, "", [], {}) and value not in (None, "", [], {}):
            target[key] = value
        elif key in {"summary", "description", "address", "opening_hours"} and len(_safe_text(value)) > len(_safe_text(current)):
            target[key] = value


def _map_location_quality_score(location: Dict[str, Any], query: str) -> float:
    query_terms = _web_query_terms(query)
    name_terms = _web_query_terms(location.get("name"))
    score = len(query_terms & name_terms) * 2.0
    score += sum(
        location.get(key) not in (None, "", [], {})
        for key in ("address", "rating", "opening_hours", "price", "telephone", "images", "summary")
    )
    if _has_reusable_poi_coordinates(location):
        score += 2.0
    if len(location.get("source_providers") or []) > 1:
        score += 3.0
    category_text = " ".join(
        _safe_text(location.get(key))
        for key in ("name", "category", "type", "description")
    )
    if re.search(
        r"风景名胜|旅游景点|博物馆|纪念馆|美术馆|科技馆|公园|寺|庙|塔|古镇|"
        r"古城|古迹|遗址|历史街区|步行街|江滩|湖|山|桥|园林|故居|书院|宫|城墙",
        category_text,
        re.IGNORECASE,
    ):
        score += 6.0
    destination = canonical_destination(query)
    if destination:
        candidate_name = _normalized_poi_match_text(location.get("name"))
        if any(
            candidate_name == _normalized_poi_match_text(classic_name)
            for classic_name, _day in classic_places_for_city(destination)
        ):
            score += 20.0
    if _is_unsuitable_travel_poi(location):
        score -= 100.0
    rating = _extract_float(location.get("rating"))
    if rating is not None and 0 <= rating <= 5:
        score += rating / 2
    return score


def _has_reusable_poi_coordinates(location: Dict[str, Any]) -> bool:
    lat = _extract_float(location.get("lat"))
    lng = _extract_float(location.get("lng"))
    return bool(
        lat is not None
        and lng is not None
        and -90 <= lat <= 90
        and -180 <= lng <= 180
        and location.get("coordinates_trusted") is not False
    )


def _map_request_priority_for_location(
    location: Dict[str, Any],
    *,
    supplemental: bool = False,
) -> str:
    if supplemental:
        return "supplemental"
    if location.get("candidate") is True or location.get("status") == "candidate":
        return "candidate"
    category = f"{_safe_text(location.get('category'))} {_safe_text(location.get('name'))}"
    if re.search(r"餐厅|美食|小吃|咖啡|restaurant|food|cafe", category, re.IGNORECASE):
        return "scheduled_dining"
    return "formal"


def _verify_selected_map_candidates(
    locations: List[Dict[str, Any]],
    destination_city: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Reuse trusted search coordinates and verify only selected unresolved POIs."""
    detail_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_search_detail", "map_place_details", "map_place_detail"],
    ), destination_city)
    search_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_text_search", "map_search_places", "map_place_search", "map_poi_extract"],
    ), destination_city)
    geocode_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_geo", "map_geocode", "map_geocoding"],
    ), destination_city)
    verified: List[Dict[str, Any]] = []
    used_tools: List[str] = []
    errors: List[str] = []
    canonical_destination_city = canonical_destination(destination_city) or _safe_text(destination_city)
    wikipedia_coordinates = (
        fetch_wikipedia_place_coordinates(
            [(_safe_text(location.get("name")), canonical_destination_city) for location in locations]
        )
        if canonical_destination_city in INTERNATIONAL_DESTINATIONS
        else {}
    )

    for location in locations:
        current = dict(location)
        if _has_reusable_poi_coordinates(current):
            if not _has_positive_destination_signal(current, destination_city):
                errors.append(f"{_safe_text(current.get('name'))}: 地点不属于目的地，已拒绝写入正式行程")
                continue
            current["coordinate_source"] = current.get("coordinate_source") or "search_reused"
            verified.append(current)
            continue

        name = _safe_text(current.get("name"))
        wikipedia_match = wikipedia_coordinates.get(name)
        if wikipedia_match:
            current.update(wikipedia_match)
            current["requested_destination"] = canonical_destination_city
            current["destination_bound"] = True
            current["evidence_refs"] = [wikipedia_match["source_reference_id"]]
            if _has_positive_destination_signal(current, canonical_destination_city):
                used_tools.append("wikipedia_coordinates")
                verified.append(current)
                continue
        place_id = _safe_text(current.get("place_id") or current.get("uid"))
        priority = _map_request_priority_for_location(current)
        resolved_row: Dict[str, Any] = {}
        resolved_by = ""

        if place_id:
            preferred_detail_tools = sorted(
                detail_tools,
                key=lambda tool: _is_amap_tool(tool) != (_safe_text(current.get("source_provider")) == "amap"),
            )
            for detail_tool in preferred_detail_tools:
                provider_key = "amap" if _is_amap_tool(detail_tool) else "baidu"
                provider_ids = current.get("provider_place_ids") if isinstance(current.get("provider_place_ids"), dict) else {}
                detail_place_id = _safe_text(provider_ids.get(provider_key))
                if not detail_place_id and _safe_text(current.get("source_provider")) == provider_key:
                    detail_place_id = place_id
                if not detail_place_id and not _safe_text(current.get("source_provider")) and not provider_ids:
                    detail_place_id = place_id
                if not detail_place_id:
                    continue
                detail_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=detail_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=[{"id": detail_place_id}, {"uid": detail_place_id}],
                    request_priority=priority,
                )
                detail_rows = _normalize_map_locations(
                    detail_payload,
                    max_rows=1,
                    source_tool=detail_tool,
                )
                if detail_rows:
                    resolved_row = detail_rows[0]
                    resolved_by = detail_tool
                    break

        if not resolved_row:
            for search_tool in search_tools:
                search_args = (
                    [{"keywords": name, "city": destination_city}]
                    if search_tool == "maps_text_search"
                    else [
                        {"query": name, "region": destination_city},
                        {"keywords": name, "region": destination_city},
                        {"query": f"{destination_city}{name}"},
                    ]
                )
                search_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=search_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=search_args,
                    request_priority=priority,
                )
                search_rows = _normalize_map_locations(
                    search_payload,
                    max_rows=1,
                    source_tool=search_tool,
                )
                if search_rows:
                    resolved_row = search_rows[0]
                    resolved_by = search_tool
                    break

        # AMap text search commonly returns a stable POI id but omits coordinates.
        # Resolve that id through the matching provider detail tool before falling
        # back to address geocoding so the formal activity keeps the real POI id.
        if resolved_row and not _has_reusable_poi_coordinates(resolved_row):
            provider_place_id = _safe_text(
                resolved_row.get("provider_place_id")
                or resolved_row.get("place_id")
                or resolved_row.get("uid")
            )
            preferred_detail_tools = sorted(
                detail_tools,
                key=lambda tool: _is_amap_tool(tool) != (_safe_text(resolved_row.get("source_provider")) == "amap"),
            )
            for detail_tool in preferred_detail_tools:
                expected_provider = "amap" if _is_amap_tool(detail_tool) else "baidu"
                if (
                    provider_place_id
                    and _safe_text(resolved_row.get("source_provider"))
                    and _safe_text(resolved_row.get("source_provider")) != expected_provider
                ):
                    continue
                if not provider_place_id:
                    continue
                detail_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=detail_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=[{"id": provider_place_id}, {"uid": provider_place_id}],
                    request_priority=priority,
                )
                detail_rows = _normalize_map_locations(
                    detail_payload,
                    max_rows=1,
                    require_coordinates=False,
                    source_tool=detail_tool,
                )
                if not detail_rows:
                    continue
                _merge_map_location_fields(resolved_row, detail_rows[0])
                if _has_reusable_poi_coordinates(resolved_row):
                    resolved_by = detail_tool
                    break

        if (not resolved_row or not _has_reusable_poi_coordinates(resolved_row)) and geocode_tools:
            query = f"{destination_city}{name}" if destination_city and destination_city not in name else name
            for geocode_tool in geocode_tools:
                geocode_args = (
                    [{"address": query, "city": destination_city}]
                    if geocode_tool == "maps_geo"
                    else [{"address": query, "city": destination_city}, {"address": query}]
                )
                geocode_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=geocode_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=geocode_args,
                    request_priority=priority,
                )
                geocode_rows = _normalize_map_locations(
                    geocode_payload,
                    max_rows=1,
                    source_tool=geocode_tool,
                )
                if geocode_rows:
                    if resolved_row:
                        _merge_map_location_fields(resolved_row, geocode_rows[0])
                    else:
                        resolved_row = geocode_rows[0]
                    resolved_by = geocode_tool
                    break

        if not resolved_row:
            errors.append(f"{name}: 地点坐标核验失败")
            continue

        merged = dict(current)
        for key, value in resolved_row.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        merged["name"] = name or _safe_text(resolved_row.get("name"))
        merged["place_id"] = place_id or _safe_text(resolved_row.get("place_id"))
        merged["coordinates_trusted"] = _has_reusable_poi_coordinates(resolved_row)
        merged["coordinate_source"] = "poi_detail" if resolved_by in detail_tools else "verification"
        merged["requested_destination"] = destination_city
        merged["destination_bound"] = False
        if not _has_positive_destination_signal(merged, destination_city):
            errors.append(f"{name}: 地点不属于目的地，已拒绝写入正式行程")
            continue
        if not _has_reusable_poi_coordinates(merged):
            errors.append(f"{name}: 未获得稳定 POI 与可信坐标")
            continue
        used_tools.append(resolved_by)
        verified.append(merged)

    return verified, used_tools, errors


def _normalized_poi_match_text(value: Any) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", _safe_text(value)).casefold()
    for suffix in ("风景名胜区", "生态旅游风景区", "旅游风景区", "风景区", "景区"):
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)]
            break
    return text


def _select_matching_seed_location(
    rows: List[Dict[str, Any]],
    place_name: str,
    destination_city: str,
) -> Optional[Dict[str, Any]]:
    """Select an exact-enough attraction hit and reject unrelated provider rows."""
    expected = _normalized_poi_match_text(place_name)
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _is_unsuitable_travel_poi(row):
            continue
        candidate = _normalized_poi_match_text(row.get("name"))
        if not expected or not candidate:
            continue
        if expected == candidate:
            name_score = 100
        elif expected in candidate or candidate in expected:
            name_score = 80
        else:
            continue
        if (
            _seed_place_category(place_name) == "景点"
            and _normalized_travel_location_category(row) in {"酒店", "餐厅", "交通"}
        ):
            continue
        row_destination = " ".join(
            _safe_text(row.get(key))
            for key in ("city", "area", "address", "description")
        )
        scoped_row = {
            **row,
            "requested_destination": destination,
            "destination_bound": bool(destination),
        }
        if destination and not _has_positive_destination_signal(scoped_row, destination):
            continue
        city_score = 10 if destination and destination in row_destination else 0
        coordinate_score = 5 if _has_reusable_poi_coordinates(row) else 0
        uid_score = 3 if _safe_text(row.get("provider_place_id")) else 0
        ranked.append((name_score + city_score + coordinate_score + uid_score, row))
    if not ranked:
        return None
    return dict(max(ranked, key=lambda item: item[0])[1])


def _resolve_seed_location_from_payload(
    payload: Any,
    place_name: str,
    destination_city: str,
    *,
    max_rows: int = 8,
    source_tool: str = "",
) -> Optional[Dict[str, Any]]:
    rows = _normalize_map_locations(
        payload,
        max_rows=max_rows,
        require_coordinates=False,
        source_tool=source_tool,
    )
    return _select_matching_seed_location(rows, place_name, destination_city)


def _direct_baidu_place_search(
    query: str,
    destination_city: str,
    *,
    max_rows: int = 20,
    category: str = "",
) -> List[Dict[str, Any]]:
    """Use the official Place API as a bounded fallback for a sparse MCP result."""
    if os.getenv("BAIDU_DIRECT_PLACE_FALLBACK_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
        return []
    api_key = os.getenv("BAIDU_MAP_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://api.map.baidu.com/place/v2/search",
            params={
                "query": query,
                "region": destination_city,
                "city_limit": "true",
                "output": "json",
                "scope": 2,
                "page_size": min(20, max(1, max_rows)),
                "page_num": 0,
                "ak": api_key,
            },
            timeout=max(10.0, float(os.getenv("BAIDU_MAP_NETWORK_TIMEOUT_SECONDS", "35"))),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or int(payload.get("status", -1)) != 0:
            return []
        rows = _normalize_map_locations(
            payload,
            max_rows=min(20, max(1, max_rows)),
            require_coordinates=True,
        )
        normalized: List[Dict[str, Any]] = []
        for row in rows:
            current = dict(row)
            current_category = _normalized_travel_location_category(current)
            if category and (
                not _safe_text(current.get("category"))
                or current_category in {category, "景点"}
            ):
                current["category"] = category
            current["requested_destination"] = destination_city
            current["destination_bound"] = False
            current["source"] = "百度地图地点检索"
            current["data_type"] = "reference_data"
            current["coordinates_trusted"] = _has_reusable_poi_coordinates(current)
            current["coordinate_source"] = (
                "provider_poi"
                if _safe_text(current.get("provider_place_id") or current.get("place_id") or current.get("uid"))
                else "provider_coordinates"
            )
            normalized.append(current)
        return normalized
    except (requests.RequestException, ValueError, TypeError):
        return []


def _direct_baidu_place_lookup(place_name: str, destination_city: str) -> Optional[Dict[str, Any]]:
    """Use Baidu Place Search only after the configured MCP lookup chain failed."""
    rows = _direct_baidu_place_search(
        place_name,
        destination_city,
        max_rows=10,
    )
    return _select_matching_seed_location(rows, place_name, destination_city)


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
    canonical_destination_city = canonical_destination(destination_city) or _safe_text(destination_city)
    wikipedia_coordinates = (
        fetch_wikipedia_place_coordinates(
            [(place_name, canonical_destination_city) for place_name, _day in place_seeds]
        )
        if canonical_destination_city in INTERNATIONAL_DESTINATIONS
        else {}
    )

    def wikipedia_seed(place_name: str, day: int) -> Optional[Dict[str, Any]]:
        match = wikipedia_coordinates.get(place_name)
        if not match:
            return None
        location = _catalog_seed_location(canonical_destination_city, place_name, day)
        location.update(match)
        location.update({
            "id": match["poi_id"],
            "day": day,
            "category": _seed_place_category(place_name),
            "requested_destination": canonical_destination_city,
            "destination_bound": True,
            "evidence_refs": [match["source_reference_id"]],
            "sources": [{"source_reference_id": match["source_reference_id"]}],
            "field_evidence": {
                "coordinates": {"source_reference_id": match["source_reference_id"]},
            },
        })
        return location

    search_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_text_search", "map_search_places", "map_place_search", "map_poi_extract"],
    ), destination_city)
    geocode_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_geo", "map_geocode", "map_geocoding"],
    ), destination_city)
    nearby_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_around_search", "map_nearby_search", "map_search_nearby"],
    ), destination_city)
    detail_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_search_detail", "map_place_details", "map_place_detail"],
    ), destination_city)
    if not search_tools and not geocode_tools and canonical_destination(destination_city) not in INTERNATIONAL_DESTINATIONS:
        locations = [
            wikipedia_seed(place_name, day)
            or _catalog_seed_location(destination_city, place_name, day)
            for place_name, day in place_seeds
        ]
        unresolved = sum(not _has_reusable_poi_coordinates(location) for location in locations)
        error = (
            f"地图地理编码工具不可用，{unresolved} 个地点已保留为无坐标目录候选"
            if unresolved
            else ""
        )
        used_tool = "wikipedia_coordinates" if unresolved < len(locations) else ""
        return locations, used_tool, error

    locations: List[Dict[str, Any]] = []
    errors: List[str] = []
    used_tools: List[str] = []
    for place_name, day in place_seeds:
        resolved_wikipedia_seed = wikipedia_seed(place_name, day)
        if resolved_wikipedia_seed:
            locations.append(resolved_wikipedia_seed)
            used_tools.append("wikipedia_coordinates")
            continue
        query = f"{destination_city}{place_name}" if destination_city and destination_city not in place_name else place_name
        row: Optional[Dict[str, Any]] = None
        resolved_by = ""
        resolved_tools: List[str] = []

        # A named POI search has the best chance of returning both Baidu uid and
        # coordinates. Geocoding is the second choice because it may only return
        # an approximate point for the address string.
        def search_seed_provider(search_tool: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            search_args = (
                [{"keywords": place_name, "city": destination_city}]
                if search_tool == "maps_text_search"
                else [
                    {"query": place_name, "region": destination_city, "count": 8},
                    {"keywords": place_name, "region": destination_city, "count": 8},
                    {"keyword": place_name, "region": destination_city},
                    {"query": query, "region": destination_city},
                    {"text": query},
                ]
            )
            search_payload = _run_tool_with_arg_candidates(
                tool_manager=tool_manager,
                tool_name=search_tool,
                message_history=message_history,
                session_id=session_id,
                arg_candidates=search_args,
                request_priority="formal",
            )
            if _is_tool_execution_error_payload(search_payload):
                return search_tool, None
            matched = _resolve_seed_location_from_payload(
                search_payload,
                place_name,
                destination_city,
                source_tool=search_tool,
            )
            return search_tool, matched

        if search_tools:
            with ThreadPoolExecutor(max_workers=len(search_tools), thread_name_prefix="map-seed") as executor:
                search_results = [
                    future.result()
                    for future in [executor.submit(search_seed_provider, tool) for tool in search_tools]
                ]
            matched_rows = [
                (tool_name, matched)
                for tool_name, matched in search_results
                if matched is not None
            ]
            if matched_rows:
                fused_rows = _merge_map_locations(
                    [matched for _tool_name, matched in matched_rows],
                    [],
                    max_rows=len(matched_rows),
                    require_coordinates=False,
                )
                row = fused_rows[0] if fused_rows else matched_rows[0][1]
                resolved_tools.extend(tool_name for tool_name, _matched in matched_rows)
                resolved_by = matched_rows[0][0]

        provider_uid = _safe_text(row.get("provider_place_id")) if row else ""
        if row and provider_uid and not _has_reusable_poi_coordinates(row):
            preferred_detail_tools = sorted(
                detail_tools,
                key=lambda tool: _is_amap_tool(tool) != (_safe_text(row.get("source_provider")) == "amap"),
            )
            for detail_tool in preferred_detail_tools:
                detail_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=detail_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=[{"id": provider_uid}, {"uid": provider_uid}],
                    request_priority="formal",
                )
                detail_row = _resolve_seed_location_from_payload(
                    detail_payload,
                    place_name,
                    destination_city,
                    max_rows=3,
                    source_tool=detail_tool,
                )
                if detail_row:
                    row = {**row, **{key: value for key, value in detail_row.items() if value not in (None, "", [], {})}}
                    resolved_by = detail_tool
                    break

        if not row or not _has_reusable_poi_coordinates(row):
            def geocode_seed_provider(geocode_tool: str) -> Tuple[str, Optional[Dict[str, Any]]]:
                geocode_args = (
                    [{"address": query, "city": destination_city}]
                    if geocode_tool == "maps_geo"
                    else [{"address": query, "city": destination_city}, {"address": query}]
                )
                geocode_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=geocode_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=geocode_args,
                    request_priority="formal",
                )
                if _is_tool_execution_error_payload(geocode_payload):
                    return geocode_tool, None
                geocode_row = _resolve_seed_location_from_payload(
                    geocode_payload,
                    place_name,
                    destination_city,
                    max_rows=3,
                    source_tool=geocode_tool,
                )
                return geocode_tool, geocode_row

            if geocode_tools:
                with ThreadPoolExecutor(max_workers=len(geocode_tools), thread_name_prefix="map-geocode") as executor:
                    geocode_results = [
                        future.result()
                        for future in [executor.submit(geocode_seed_provider, tool) for tool in geocode_tools]
                    ]
                matched_geocodes = [
                    (tool_name, matched)
                    for tool_name, matched in geocode_results
                    if matched is not None
                ]
                if matched_geocodes:
                    fused_geocodes = _merge_map_locations(
                        [matched for _tool_name, matched in matched_geocodes],
                        [],
                        max_rows=len(matched_geocodes),
                        require_coordinates=False,
                    )
                    row = fused_geocodes[0] if fused_geocodes else matched_geocodes[0][1]
                    resolved_tools.extend(tool_name for tool_name, _matched in matched_geocodes)
                    resolved_by = matched_geocodes[0][0]

        # Some geocoders return a point but no uid. Prefer a nearby exact-name
        # POI when available; otherwise keep the provider-returned point and its
        # deterministic identity so the navigation entry remains usable.
        if (
            row
            and _has_reusable_poi_coordinates(row)
            and not _safe_text(row.get("provider_place_id"))
            and nearby_tools
        ):
            for nearby_tool in nearby_tools:
                nearby_args = (
                    [{
                        "location": (
                            f"{row.get('provider_longitude')},{row.get('provider_latitude')}"
                            if row.get("provider_longitude") is not None and row.get("provider_latitude") is not None
                            else f"{row.get('lng')},{row.get('lat')}"
                        ),
                        "radius": "1200",
                        "keywords": place_name,
                    }]
                    if nearby_tool == "maps_around_search"
                    else [
                        {
                            "location": f"{row.get('lng')},{row.get('lat')}",
                            "radius": 1200,
                            "query": place_name,
                        },
                        {
                            "location": f"{row.get('lng')},{row.get('lat')}",
                            "radius": 1200,
                            "keyword": place_name,
                        },
                    ]
                )
                nearby_payload = _run_tool_with_arg_candidates(
                    tool_manager=tool_manager,
                    tool_name=nearby_tool,
                    message_history=message_history,
                    session_id=session_id,
                    arg_candidates=nearby_args,
                    request_priority="formal",
                )
                nearby_row = _resolve_seed_location_from_payload(
                    nearby_payload,
                    place_name,
                    destination_city,
                    max_rows=8,
                    source_tool=nearby_tool,
                )
                if nearby_row and _has_reusable_poi_coordinates(nearby_row):
                    row = nearby_row
                    resolved_by = nearby_tool
                    break

        if not row or not _has_reusable_poi_coordinates(row):
            direct_row = _direct_baidu_place_lookup(place_name, destination_city)
            if direct_row and _has_reusable_poi_coordinates(direct_row):
                row = direct_row
                resolved_by = "baidu_place_api"

        if not row or not _has_reusable_poi_coordinates(row):
            errors.append(f"{place_name}: 未获得可导航坐标，已降级为目录候选")
            locations.append(_catalog_seed_location(destination_city, place_name, day))
            continue

        row["name"] = place_name
        row["category"] = _seed_place_category(place_name)
        row["day"] = day
        row["summary"] = _safe_text(row.get("summary")) or _safe_text(DESTINATION_SEED_PLACE_SUMMARIES.get(place_name))
        row["description"] = _safe_text(row.get("description")) or row["summary"] or f"{destination_city}{place_name}"
        row["source"] = "高德地图地点检索" if _safe_text(row.get("source_provider")) == "amap" else "百度地图地点检索"
        row["requested_destination"] = destination_city
        row["destination_bound"] = True
        row["data_type"] = "reference_data"
        row["coordinates_trusted"] = True
        row["coordinate_source"] = "provider_poi" if _safe_text(row.get("provider_place_id")) else "provider_coordinates"
        if resolved_tools:
            used_tools.extend(resolved_tools)
        elif resolved_by:
            used_tools.append(resolved_by)
        locations.append(row)

    return locations, ", ".join(dict.fromkeys(used_tools)), "；".join(errors)


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
    provider_candidates = [
        ["tavily_search", "tavily-search"],
        ["search_web_page", "serper_web_search", "serper_site_search", "web_search", "search"],
    ]
    available_tools: List[str] = []
    for candidates in provider_candidates:
        tool_name = _first_available_tool_name(tool_manager, candidates)
        if tool_name:
            available_tools.append(tool_name)
    if not available_tools:
        return [], "", "网络搜索工具不可用"

    def run_provider(tool_name: str) -> Tuple[str, List[Dict[str, str]], str]:
        if tool_name in {"tavily_search", "tavily-search"}:
            tavily_args = {
                "query": user_query,
                "search_depth": "basic",
                "max_results": 8,
                "topic": "general",
                "include_answer": False,
                "include_images": False,
                "include_raw_content": False,
            }
            try:
                parameter_names = set(getattr(tool_manager.get_tool(tool_name), "parameters", {}).keys())
            except Exception:
                parameter_names = set()
            if parameter_names:
                tavily_args = {
                    key: value for key, value in tavily_args.items()
                    if key in parameter_names
                }
            arg_candidates = [tavily_args]
        else:
            arg_candidates = [
                {"query": user_query, "count": 6, "language": "zh-cn", "country": "cn"},
                {"query": user_query, "count": 6},
                {"query": user_query},
            ]
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=arg_candidates,
        )
        if _is_tool_execution_error_payload(payload):
            return tool_name, [], _tool_error_reason(payload, "网络搜索失败")
        rows = _normalize_web_rows(payload, max_rows=8)
        if rows:
            return tool_name, rows, ""
        return tool_name, [], "未返回可引用结果"

    with ThreadPoolExecutor(max_workers=len(available_tools), thread_name_prefix="web-search") as executor:
        futures = [executor.submit(run_provider, tool_name) for tool_name in available_tools]
        provider_results = [future.result() for future in futures]

    successful_rows = [
        (tool_name, rows)
        for tool_name, rows, _error in provider_results
        if rows
    ]
    if successful_rows:
        return (
            _merge_web_search_rows(successful_rows, user_query, max_rows=8),
            ", ".join(tool_name for tool_name, _rows in successful_rows),
            "",
        )
    errors = [f"{tool_name}: {error}" for tool_name, _rows, error in provider_results if error]
    return [], ", ".join(available_tools), "；".join(errors) or "网络搜索失败"


def _run_map_search_for_travel(
    user_query: str,
    destination_city: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
    research_role: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str, str, List[Dict[str, Any]]]:
    search_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_text_search", "map_search_places", "map_place_search", "map_poi_extract"],
    ), destination_city)
    geocode_tools = _order_map_provider_tools(_available_tool_names(
        tool_manager,
        ["maps_geo", "map_geocode", "map_geocoding"],
    ), destination_city)
    if not search_tools and not geocode_tools and canonical_destination(destination_city) not in INTERNATIONAL_DESTINATIONS:
        return [], "", "地图工具不可用", []

    region = _safe_text(destination_city)
    query_text = _safe_text(user_query)
    used_search_tools: List[str] = []

    def run_search(query: str, max_rows: int, category: str = "") -> Tuple[List[Dict[str, Any]], str]:
        if not search_tools:
            return [], ""
        request_priority = _map_request_priority_for_location({"category": category})
        if not wants_itinerary and request_priority == "formal":
            request_priority = "candidate"

        def run_provider(search_tool: str) -> Tuple[str, List[Dict[str, Any]], str]:
            arg_candidates = (
                [{"keywords": query, "city": region}]
                if search_tool == "maps_text_search"
                else [
                    {"query": query, "region": region, "count": max_rows},
                    {"keywords": query, "region": region, "count": max_rows},
                    {"keyword": query, "region": region, "count": max_rows},
                    {"query": query, "region": region},
                    {"keywords": query, "region": region},
                    {"keyword": query, "region": region},
                    {"text": query},
                    {"address": query},
                    {"query": query},
                ]
            )
            payload = _run_tool_with_arg_candidates(
                tool_manager=tool_manager,
                tool_name=search_tool,
                message_history=message_history,
                session_id=session_id,
                arg_candidates=arg_candidates,
                request_priority=request_priority,
            )
            if _is_tool_execution_error_payload(payload):
                return search_tool, [], _tool_error_reason(payload, "地图检索失败")
            rows = _normalize_map_locations(
                payload,
                max_rows=max_rows,
                require_coordinates=False,
                source_tool=search_tool,
            )
            for row in rows:
                row["requested_destination"] = destination_city
                # The requested region is query context, not locality evidence.
                row["destination_bound"] = False
            if category:
                for row in rows:
                    current_category = _safe_text(row.get("category"))
                    if not current_category or (current_category == "景点" and category != "景点"):
                        row["category"] = category
                    row["search_category"] = category
            if category == "景点":
                rows = [
                    row for row in rows
                    if _normalized_travel_location_category(row) == "景点"
                ]
            if rows:
                return search_tool, rows, ""
            return search_tool, [], "未返回匹配地点"

        with ThreadPoolExecutor(max_workers=len(search_tools), thread_name_prefix="map-search") as executor:
            futures = [executor.submit(run_provider, search_tool) for search_tool in search_tools]
            provider_results = [future.result() for future in futures]
        successful = [
            (tool_name, rows)
            for tool_name, rows, _error in provider_results
            if rows
        ]
        if successful:
            used_search_tools.extend(tool_name for tool_name, _rows in successful)
            combined = [row for _tool_name, rows in successful for row in rows]
            fused = _merge_map_locations(
                combined,
                [],
                max_rows=max_rows * max(1, len(successful)),
                require_coordinates=False,
            )
            fused.sort(
                key=lambda location: _map_location_quality_score(location, query),
                reverse=True,
            )
            return fused[:max_rows], ""
        provider_errors = [
            f"{tool_name}: {error}"
            for tool_name, _rows, error in provider_results
            if error
        ]
        return [], "；".join(provider_errors)

    wants_itinerary = bool(ITINERARY_QUERY_REGEX.search(query_text))
    requested_days = max(1, extract_trip_intent(query_text).days or 1)
    # Reserve enough verified POIs for the formal daily plan plus 7–15 candidates.
    # The provider may return fewer items; we never pad this pool with guessed places.
    # Formal schedules consume about six verified places per day once meals are
    # included.  Keep a small verification-loss margin as provider fusion can
    # remove duplicates or off-destination rows before the 7–15 item candidate
    # pool is built.
    poi_source_limit = min(65, max(42, requested_days * 6 + 18))
    batch_queries: List[Tuple[str, int, str]] = []
    if wants_itinerary:
        # A single broad Baidu query commonly returns only one page (about 7–10
        # distinct POIs).  Query complementary attraction families so a
        # multi-day plan can fill every day and still retain 7–15 candidates.
        batch_queries.extend([
            (f"{region} 热门景点".strip(), min(25, poi_source_limit), "景点"),
            (f"{region} 博物馆 公园 人文景观".strip(), min(20, poi_source_limit), "景点"),
            (f"{region} 古迹 寺庙 古镇 历史街区".strip(), min(20, poi_source_limit), "景点"),
        ])
    else:
        batch_queries.append((query_text, 8, _default_map_category_for_query(query_text)))
    if wants_itinerary or HOTEL_QUERY_REGEX.search(query_text):
        batch_queries.append((f"{region} 住宿 酒店".strip(), 4, "酒店"))
    if wants_itinerary or FOOD_QUERY_REGEX.search(query_text):
        batch_queries.append((f"{region} 美食 餐厅".strip(), min(15, max(10, poi_source_limit // 3)), "餐厅"))
        if wants_itinerary:
            batch_queries.append((f"{region} 特色餐厅 老字号 本地菜".strip(), 15, "餐厅"))

    role_category = {"research": "景点", "lodging": "酒店", "dining": "餐厅"}.get(research_role)
    if role_category:
        batch_queries = [item for item in batch_queries if item[2] == role_category]

    candidate_locations: List[Dict[str, Any]] = []
    lodging_rows: List[Dict[str, Any]] = []
    batch_errors: List[str] = []
    for batch_query, max_rows, category in batch_queries:
        rows, batch_error = run_search(batch_query, max_rows=max_rows, category=category)
        candidate_locations.extend(rows)
        if category == "酒店":
            lodging_rows.extend(dict(row) for row in rows if isinstance(row, dict))
        if batch_error:
            batch_errors.append(batch_error)

    if (
        canonical_destination(destination_city) in INTERNATIONAL_DESTINATIONS
        and research_role in {None, "lodging"}
        and (wants_itinerary or HOTEL_QUERY_REGEX.search(query_text))
        and not _filter_destination_lodging_locations(lodging_rows, destination_city)
    ):
        international_lodging = search_international_places("hotel", destination_city, "hotel")
        lodging_rows.extend(international_lodging)
        candidate_locations.extend(international_lodging)
        if international_lodging:
            used_search_tools.append("openstreetmap_nominatim")

    if wants_itinerary:
        density_preview = _merge_map_locations(
            candidate_locations,
            [],
            max_rows=max(40, poi_source_limit * 3),
            require_coordinates=False,
        )
        food_count = sum(
            _normalized_travel_location_category(location) == "餐厅"
            and _has_reusable_poi_coordinates(location)
            for location in _filter_quality_map_locations(
                density_preview, destination_city, require_positive_destination=True,
            )
        )
        attraction_count = sum(
            _normalized_travel_location_category(location) == "景点"
            for location in density_preview
        )
        if candidate_locations and research_role in {None, "research"}:
            for supplemental_query in (
                f"{region} 城市地标 夜游 步行街".strip(),
                f"{region} 美术馆 科技馆 动物园 植物园".strip(),
            ):
                rows, supplemental_error = run_search(
                    supplemental_query,
                    max_rows=min(20, poi_source_limit),
                    category="景点",
                )
                candidate_locations.extend(rows)
                if supplemental_error:
                    batch_errors.append(supplemental_error)
            density_preview = _merge_map_locations(
                candidate_locations,
                [],
                max_rows=max(40, poi_source_limit * 3),
                require_coordinates=False,
            )
            attraction_count = sum(
                _normalized_travel_location_category(location) == "景点"
                for location in density_preview
            )
        if food_count < requested_days * 2 + 4 and research_role in {None, "dining"}:
            candidate_locations.extend(
                _direct_baidu_place_search(
                    "特色餐厅",
                    destination_city,
                    max_rows=20,
                    category="餐厅",
                )
            )
            if canonical_destination(destination_city) in INTERNATIONAL_DESTINATIONS:
                osm_restaurants = fetch_osm_restaurants(
                    destination_city,
                    limit=min(20, requested_days * 2 + 4),
                )
                candidate_locations.extend(osm_restaurants)
                if osm_restaurants:
                    used_search_tools.append("openstreetmap_nominatim")
        if attraction_count < requested_days * 4 + 7 and research_role in {None, "research"}:
            candidate_locations.extend(
                _direct_baidu_place_search(
                    "热门景点",
                    destination_city,
                    max_rows=20,
                    category="景点",
                )
            )

    candidate_locations = _merge_map_locations(
        candidate_locations,
        [],
        max_rows=max(80, poi_source_limit * 4),
        require_coordinates=False,
    )
    candidate_locations = _normalize_travel_location_categories(
        _filter_quality_map_locations(
            candidate_locations,
            destination_city,
            require_positive_destination=wants_itinerary,
        )
    )
    lodging_rows = _merge_map_locations(
        lodging_rows,
        [],
        max_rows=12,
        require_coordinates=False,
    )
    lodging_preselected = [
        dict(location)
        for location in _normalize_travel_location_categories(
            _filter_destination_lodging_locations(lodging_rows, destination_city)
        )
        if re.search(
            r"酒店|宾馆|住宿|民宿|客栈|hotel|hostel",
            f"{_safe_text(location.get('category'))} {_safe_text(location.get('name'))}",
            re.IGNORECASE,
        )
    ][:3]
    lodging_candidates, lodging_verification_tools, lodging_verification_errors = _verify_selected_map_candidates(
        lodging_preselected,
        destination_city,
        tool_manager,
        message_history,
        session_id,
    )
    lodging_candidates = [
        location
        for location in _normalize_travel_location_categories(lodging_candidates)
        if _normalized_travel_location_category(location) == "酒店"
        and _has_reusable_poi_coordinates(location)
    ][:3]
    final_candidates = _select_final_map_candidates(candidate_locations, query_text, max_rows=poi_source_limit)
    verified_candidates, verification_tools, verification_errors = _verify_selected_map_candidates(
        final_candidates,
        destination_city,
        tool_manager,
        message_history,
        session_id,
    )
    seed_locations: List[Dict[str, Any]] = []
    seed_tool = ""
    seed_error = ""
    existing_names = {
        _normalized_poi_match_text(item.get("name"))
        for item in verified_candidates
    }
    all_seed_candidates = [
        item
        for item in _travel_seed_places(destination_city, user_query)
        if _normalized_poi_match_text(item[0]) not in existing_names
    ]
    # Named classics are acceptance anchors, not merely padding for an empty
    # generic search. Resolve enough of them for every itinerary and merge them
    # before provider-discovered candidates so they reach the formal schedule.
    if wants_itinerary and canonical_destination(destination_city) in INTERNATIONAL_DESTINATIONS:
        attraction_target, _food_target = _daily_activity_targets(extract_trip_intent(query_text).pace)
        classic_target = min(len(all_seed_candidates), requested_days * attraction_target + 15)
    else:
        classic_target = min(
            len(all_seed_candidates),
            max(4, min(8, requested_days * 2 + 1)),
        ) if wants_itinerary else min(2, len(all_seed_candidates))
    if research_role in {"lodging", "dining"}:
        classic_target = 0
    if classic_target:
        seed_locations, seed_tool, seed_error = _run_map_geocode_for_places(
            all_seed_candidates[:classic_target],
            destination_city,
            user_query,
            tool_manager,
            message_history,
            session_id,
        )
        seed_locations = [item for item in seed_locations if _has_reusable_poi_coordinates(item)]
    merged = _merge_map_locations(seed_locations, verified_candidates, max_rows=poi_source_limit)
    if wants_itinerary and research_role in {None, "research"}:
        attraction_target, food_target = _daily_activity_targets(extract_trip_intent(query_text).pace)
        minimum_location_count = requested_days * (attraction_target + food_target) + 7
        if len(merged) < minimum_location_count:
            existing_names = {
                _normalized_poi_match_text(location.get("name"))
                for location in merged
            }
            catalog_fallbacks: List[Dict[str, Any]] = []
            for unresolved in final_candidates:
                unresolved_name = _safe_text(unresolved.get("name"))
                normalized_name = _normalized_poi_match_text(unresolved_name)
                if (
                    not unresolved_name
                    or normalized_name in existing_names
                    or _normalized_travel_location_category(unresolved) != "景点"
                    or _is_unsuitable_travel_poi(unresolved)
                ):
                    continue
                fallback = _catalog_seed_location(
                    destination_city,
                    unresolved_name,
                    (len(catalog_fallbacks) % requested_days) + 1,
                )
                fallback.update({
                    "address": _safe_text(unresolved.get("address") or unresolved.get("description")) or fallback.get("address"),
                    "summary": _safe_text(unresolved.get("summary")) or fallback.get("summary"),
                    "requested_destination": destination_city,
                    "destination_bound": True,
                    "source": "destination_catalog_after_provider_failure",
                    "data_type": "reference_data",
                })
                catalog_fallbacks.append(fallback)
                existing_names.add(normalized_name)
                if len(merged) + len(catalog_fallbacks) >= minimum_location_count:
                    break
            if catalog_fallbacks:
                merged = _merge_map_locations(
                    merged,
                    catalog_fallbacks,
                    max_rows=poi_source_limit,
                )
    merged = _normalize_travel_location_categories(
        _filter_quality_map_locations(
            merged,
            destination_city,
            require_positive_destination=wants_itinerary,
        )
    )
    merged = _enrich_map_locations_with_details(
        merged,
        destination_city,
        tool_manager,
        message_history,
        session_id,
    )
    used_tools = ", ".join([
        item for item in [
            *dict.fromkeys(used_search_tools),
            *verification_tools,
            *lodging_verification_tools,
            seed_tool if seed_locations else "",
        ] if item
    ])
    errors = "；".join([
        item for item in [
            *batch_errors,
            *verification_errors,
            *lodging_verification_errors,
            seed_error,
        ] if item
    ])
    fallback_tool = next(iter(search_tools or geocode_tools), "")
    return merged, used_tools or fallback_tool or seed_tool, errors, lodging_candidates


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
    allow_date_sensitive: bool = True,
) -> Optional[Dict[str, Any]]:
    query_text = _safe_text(user_query)
    skill_triggered = _has_selected_skill(
        selected_skill_ids,
        ["travel_planner", "map_route", "destination_research", "local_discovery"],
    )
    full_trip_plan = is_trip_planning_query(query_text)
    if not skill_triggered and not _is_travel_experience_query(query_text) and not full_trip_plan:
        return None
    if _is_train_ticket_query(query_text) and not full_trip_plan:
        return None

    kinds = _travel_experience_kinds(query_text)
    travel_date = _extract_travel_date(query_text) if allow_date_sensitive else "日期暂未确定"
    destination_city = _resolve_trip_destination(query_text)
    origin_city, origin_tool, origin_error = _run_origin_city_lookup(tool_manager, message_history, session_id)
    if allow_date_sensitive:
        weather_summary, weather_tool, weather_error = _run_weather_for_travel(
            destination_city,
            travel_date,
            tool_manager,
            message_history,
            session_id,
        )
    else:
        weather_summary, weather_tool = "", ""
        weather_error = "日期暂未确定，已跳过具体日期天气查询"
    if allow_web_search:
        research_query = (
            f"{destination_city} 旅行攻略 景点 美食 住宿" if full_trip_plan and destination_city else query_text
        )
        web_rows, web_tool, web_error = _run_web_search_for_travel(
            user_query=research_query,
            tool_manager=tool_manager,
            message_history=message_history,
            session_id=session_id,
        )
        lodging_web_rows, lodging_web_tool, lodging_web_error = (
            _run_web_search_for_travel(
                user_query=f"{destination_city} 住宿 酒店 民宿 区域 入住体验 房型 价格参考",
                tool_manager=tool_manager,
                message_history=message_history,
                session_id=session_id,
            )
            if full_trip_plan and destination_city
            else ([], "", "")
        )
    else:
        web_rows, web_tool, web_error = [], "", "已按用户设置关闭网页与社区检索"
        lodging_web_rows, lodging_web_tool, lodging_web_error = [], "", "已按用户设置关闭网页与社区检索"
    if destination_city:
        web_rows = _filter_destination_reference_rows(web_rows, destination_city)
        lodging_web_rows = _filter_destination_reference_rows(lodging_web_rows, destination_city)
    map_locations, map_tool, map_error, lodging_candidates = _run_map_search_for_travel(
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
    xhs_rows = xhs_bundle.get("rows") if isinstance(xhs_bundle, dict) and isinstance(xhs_bundle.get("rows"), list) else []
    if destination_city:
        xhs_rows = _filter_destination_reference_rows(xhs_rows, destination_city)
    xhs_table = (
        _build_xhs_markdown_table(xhs_rows)
        if destination_city
        else _safe_text(xhs_bundle.get("append_markdown")) if isinstance(xhs_bundle, dict) else ""
    )

    context_lines = [
        _build_travel_output_contract(query_text, kinds, map_locations),
        "",
        "【工具增强结果】",
        f"出行日期: {travel_date}",
        f"默认出发地: {origin_city or '未获取'}；定位工具: {origin_tool or '未使用'}；问题: {origin_error or '无'}",
        f"识别目的地: {destination_city or '未识别'}",
        f"天气工具: {weather_tool or '未使用'}；天气摘要: {weather_summary or '未获取'}；问题: {weather_error or '无'}",
        f"网络搜索工具: {web_tool or '未使用'}；命中: {len(web_rows)}；问题: {web_error or '无'}",
        f"地图工具: {map_tool or '未使用'}；坐标命中: {len(map_locations)}；问题: {map_error or '无'}",
    ]
    if rag_context:
        context_lines.extend(["", rag_context])
    if not destination_city and xhs_bundle and xhs_bundle.get("context_message"):
        context_lines.extend(["", _safe_text(xhs_bundle.get("context_message"))])
    elif xhs_table:
        context_lines.extend(["", xhs_table])
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
        "trip_intent": extract_trip_intent(query_text).model_dump(),
        "weather_summary": weather_summary,
        "weather_tool": weather_tool,
        "weather_error": weather_error,
        "context_message": "\n".join(context_lines),
        "web_rows": web_rows,
        "web_tool": web_tool,
        "web_error": web_error,
        "lodging_web_rows": lodging_web_rows,
        "lodging_web_tool": lodging_web_tool,
        "lodging_web_error": lodging_web_error,
        "map_locations": map_locations,
        "lodging_candidates": lodging_candidates,
        "map_tool": map_tool,
        "map_error": map_error,
        "rag_context": rag_context,
        "xhs_table": xhs_table,
        "xhs_rows": xhs_rows,
        "lodging_reference_rows": [
            *[
                {**row, "reference_type": "xhs"}
                for row in xhs_rows
                if isinstance(row, dict)
                and re.search(
                    r"酒店|住宿|住哪|民宿|客栈|入住|房间|区域",
                    f"{_safe_text(row.get('title'))} {_safe_text(row.get('summary'))}",
                    re.IGNORECASE,
                )
            ],
            *[
                {**row, "reference_type": "web"}
                for row in lodging_web_rows
                if isinstance(row, dict)
            ],
        ][:3],
        "append_markdown": "\n\n".join(append_parts),
        "date_sensitive": allow_date_sensitive,
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
                    # 供应商先按发车时间排序；只取前 20 条会丢失晚间返程。
                    limitedNum=0,
                    sortFlag="startTime",
                )
                direct_payload = _unwrap_tool_output(direct_raw)
            except Exception as direct_error:
                train_errors.append(f"直达票查询失败: {direct_error}")
                break
            if _is_tool_execution_error_payload(direct_payload):
                train_errors.append(_tool_error_reason(direct_payload, "直达票查询失败"))
                # Provider/network execution failures are route-wide; only a
                # successful empty result should continue with station aliases.
                break
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
                break
            if _is_tool_execution_error_payload(direct_payload):
                train_errors.append(_tool_error_reason(direct_payload, "直达票查询失败"))
                break
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
    accommodation_reference: bool = False,
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

    focus = "住宿区域与入住体验" if accommodation_reference else _extract_xhs_focus(query_text)
    tool_query = (
        _build_xhs_lodging_query(query_text)
        if accommodation_reference
        else _build_xhs_travel_query(query_text) if travel_triggered else query_text
    )
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
        "小红书内容仅可作为区域与入住体验的参考；不得将其表述为酒店库存、价格、房型或可预订状态。",
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


ONLINE_SEARCH_SERVER_NAMES = {"tavily-mcp", "serper_web_search", "xhs-mcp", "fetch"}
ONLINE_SEARCH_TOOL_MARKERS = (
    "tavily_",
    "tavily-",
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
    """Create a filtered view while sharing the original MCP runtime and processes."""
    try:
        from agents.tool.tool_base import McpToolSpec

        # A fresh ToolManager owns a fresh stdio runtime and starts another copy of
        # every selected MCP server. A shallow view keeps filtering request-local
        # while all connection/session/process state remains application-scoped.
        filtered_manager = copy.copy(original_tool_manager)
        filtered_manager.tools = {}

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


def _trip_intent_from_structured_request(structured_trip_request: Optional[Any]) -> Optional[TripIntent]:
    if structured_trip_request is None:
        return None
    if hasattr(structured_trip_request, "model_dump"):
        payload = structured_trip_request.model_dump()
    elif isinstance(structured_trip_request, dict):
        payload = structured_trip_request
    else:
        return None

    try:
        start_date = datetime.date.fromisoformat(str(payload.get("start_date")))
        end_date = datetime.date.fromisoformat(str(payload.get("end_date")))
        people_count = sum(int(payload.get(field) or 0) for field in ("adults", "children", "seniors"))
        preferences = [
            _safe_text(item).strip()
            for item in (payload.get("preferences") or [])
            if _safe_text(item).strip()
        ]
        return TripIntent(
            origin=_safe_text(payload.get("origin")).strip(),
            destination=_safe_text(payload.get("destination")).strip(),
            date_range=f"{start_date.isoformat()} 至 {end_date.isoformat()}",
            days=(end_date - start_date).days + 1,
            people_count=people_count,
            adult_count=int(payload.get("adults") or 0),
            child_count=int(payload.get("children") or 0),
            senior_count=int(payload.get("seniors") or 0),
            people_type=_safe_text(payload.get("party_type")).strip() or None,
            budget_total=float(payload.get("budget")),
            travel_style="、".join(preferences) or None,
            pace="relaxed" if "轻松慢游" in preferences else "balanced",
            interests=preferences,
            confidence=1.0,
        )
    except (TypeError, ValueError):
        return None


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
    destination_city = _safe_text(travel_bundle.get("destination_city"))
    return _filter_quality_map_locations(
        locations,
        destination_city,
        require_positive_destination=bool(destination_city),
    )


def _candidate_places_for_document(
    travel_bundle: Dict[str, Any],
    plan: TripPlan,
) -> List[Dict[str, Any]]:
    """Keep unused, verified map POIs for the V3 candidate pool.

    The formal agenda remains the only source of V2 map locations.  Candidate
    places travel beside it as explicit data so V2 validation cannot accidentally
    put them on the formal route layer.
    """
    scheduled_poi_ids = {
        _safe_text(activity.place.poi_id)
        for activity in plan.activities
        if activity.place and _safe_text(activity.place.poi_id)
    }
    scheduled_names = {
        _safe_text(activity.place.name if activity.place else activity.title)
        for activity in plan.activities
        if activity.place or _safe_text(activity.title)
    }
    candidates: List[Dict[str, Any]] = []
    seen_poi_ids: set[str] = set()
    raw_candidate_locations = travel_bundle.get("candidate_map_locations")
    candidate_bundle = (
        {**travel_bundle, "map_locations": raw_candidate_locations}
        if isinstance(raw_candidate_locations, list)
        else travel_bundle
    )
    # 有营业证据的地点优先进入有上限的候选池，避免检索顺序挤掉可排程候选。
    candidate_locations = sorted(_structured_map_locations(candidate_bundle),
        key=lambda location: not bool(location.get("opening_hours")))
    for location in candidate_locations:
        poi_id = _safe_text(location.get("poi_id") or location.get("place_id") or location.get("uid"))
        name = _safe_text(location.get("name"))
        category = _normalized_travel_location_category(location)
        coordinates_trusted = location.get("coordinates_trusted") is not False
        latitude = _optional_float(location.get("lat")) if coordinates_trusted else None
        longitude = _optional_float(location.get("lng")) if coordinates_trusted else None
        if (
            not poi_id
            or not name
            or poi_id in scheduled_poi_ids
            or poi_id in seen_poi_ids
            or name in scheduled_names
            or category in {"酒店", "交通"}
        ):
            continue
        seen_poi_ids.add(poi_id)
        candidates.append({
            "name": name,
            "category": category,
            "poi_id": poi_id,
            "lat": latitude,
            "lng": longitude,
            "address": _safe_text(location.get("address") or location.get("description")) or None,
            "city": _safe_text(location.get("city") or travel_bundle.get("destination_city")) or None,
            "summary": _safe_text(location.get("summary")) or None,
            "opening_hours": location.get("opening_hours"),
            "suggested_duration_minutes": _optional_int(location.get("duration_minutes")),
            "price": location.get("estimated_cost") if location.get("estimated_cost") is not None else location.get("price"),
            "source": _safe_text(location.get("source") or travel_bundle.get("map_tool")) or "地图地点核验",
            "data_type": location.get("data_type") or "reference_data",
            "sources": location.get("sources") if isinstance(location.get("sources"), list) else [],
            "field_evidence": location.get("field_evidence") if isinstance(location.get("field_evidence"), dict) else {},
            "coordinate_system": location.get("coordinate_system"),
            "coordinate_source": location.get("coordinate_source"),
            "coordinates_trusted": location.get("coordinates_trusted"),
        })
        if len(candidates) >= 15:
            break
    return candidates


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
        destination_summary = _build_destination_overview_summary(travel_bundle)
        sources.append(
            {
                "type": "local_knowledge",
                "data_type": "reference_data",
                "title": "本地旅行知识库",
                "source": "travel_knowledge_base",
                "url": "",
                "snippet": destination_summary or rag_context[:320],
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

    activity_image_sources = (
        travel_bundle.get("activity_image_sources")
        if isinstance(travel_bundle.get("activity_image_sources"), list)
        else []
    )
    sources.extend(dict(source) for source in activity_image_sources if isinstance(source, dict))

    map_locations = _structured_map_locations(travel_bundle)
    seen_wikimedia_sources: set[str] = set()
    for location in map_locations:
        provider = _safe_text(location.get("source_provider")) if isinstance(location, dict) else ""
        if provider not in {"wikipedia", "wikidata"}:
            continue
        reference_id = _safe_text(location.get("source_reference_id"))
        if not reference_id or reference_id in seen_wikimedia_sources:
            continue
        seen_wikimedia_sources.add(reference_id)
        place_name = _safe_text(location.get("name")) or "目的地地点"
        source_name = "Wikidata" if provider == "wikidata" else "Wikipedia"
        sources.append({
            "reference_id": reference_id,
            "type": "map",
            "data_type": "reference_data",
            "title": f"{place_name}地点坐标",
            "source": source_name,
            "url": _safe_text(location.get("source_url")),
            "snippet": f"地点名称与 WGS84 坐标来自对应 {source_name} 条目；开放时间、票价与营业状态仍需出发前核验。",
            "related_fields": ["coordinates", "address"],
            "related_places": [place_name],
        })
    seen_osm_sources: set[str] = set()
    for location in map_locations:
        if not isinstance(location, dict) or _safe_text(location.get("source_provider")) != "openstreetmap":
            continue
        reference_id = _safe_text(location.get("source_reference_id"))
        if not reference_id or reference_id in seen_osm_sources:
            continue
        seen_osm_sources.add(reference_id)
        place_name = _safe_text(location.get("name")) or "目的地餐厅"
        sources.append({
            "reference_id": reference_id,
            "type": "map",
            "data_type": "reference_data",
            "title": f"{place_name}地点坐标",
            "source": "OpenStreetMap Nominatim",
            "url": _safe_text(location.get("source_url")),
            "snippet": "餐厅名称与 WGS84 坐标来自 OpenStreetMap；营业时间、菜单、价格和营业状态需在出发前复核。",
            "related_fields": ["coordinates", "address", "category"],
            "related_places": [place_name],
        })
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


def _clean_destination_research_text(value: Any) -> str:
    """Turn a RAG record into a short user-facing destination description.

    The raw RAG envelope contains retrieval rules, ids and multiple entries.  It
    is useful to the model but must never leak into the formal plan overview.
    """
    text = _safe_text(value)
    if not text:
        return ""
    text = re.sub(r"(?s)^.*?\]\s*", "", text, count=1)
    text = re.split(r"\n\s*\d+\.\s*\[", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("旅行知识库检索结果") or text.startswith("使用规则"):
        return ""
    # Research guides describe generic itineraries, not this trip's final schedule.
    # Keep destination facts without promising activities on an arrival/departure day.
    schedule_pattern = re.compile(
        r"第\s*[一二三四五六七八九十百\d]+\s*[天日]|"
        r"\bday\s*\d+\b|"
        r"(?:建议|推荐|适合)\s*(?:安排|游玩|停留)?\s*"
        r"[一二三四五六七八九十\d]+\s*(?:[到至~～—-]\s*[一二三四五六七八九十\d]+\s*)?[天日]",
        re.IGNORECASE,
    )
    text = "".join(
        sentence for sentence in re.findall(r"[^。！？.!?]+[。！？.!?]?", text)
        if not schedule_pattern.search(sentence)
    ).strip()
    bounded = text[:260].rstrip("，。；; ")
    return bounded + ("。" if bounded else "")


def _build_destination_overview_summary(travel_bundle: Dict[str, Any]) -> str:
    """Choose a bounded destination description and never reuse trip metadata."""
    selected_context = travel_bundle.get("selected_knowledge_context")
    if isinstance(selected_context, list):
        for row in selected_context:
            if isinstance(row, dict):
                summary = _clean_destination_research_text(row.get("snippet") or row.get("summary"))
                if summary:
                    return summary

    summary = _clean_destination_research_text(travel_bundle.get("rag_context"))
    if summary:
        return summary

    for row in travel_bundle.get("web_rows") or []:
        if isinstance(row, dict):
            summary = _clean_destination_research_text(row.get("snippet"))
            if summary:
                return summary
    return ""


def _travel_bundle_to_trip_plan(travel_bundle: Dict[str, Any]) -> TripPlan:
    query_text = _safe_text(travel_bundle.get("query")) or "旅行方案"
    raw_intent = travel_bundle.get("trip_intent")
    try:
        intent = TripIntent.model_validate(raw_intent) if raw_intent else extract_trip_intent(query_text)
    except Exception:
        intent = extract_trip_intent(query_text)
    date_sensitive = bool(travel_bundle.get("date_sensitive", True))
    destination_city = (
        canonical_destination(_safe_text(intent.destination))
        or canonical_destination(_safe_text(travel_bundle.get("destination_city")))
        or _safe_text(intent.destination)
        or _safe_text(travel_bundle.get("destination_city"))
    )
    map_locations = _structured_map_locations(travel_bundle)
    map_locations = [
        {
            **location,
            **(
                {
                    "requested_destination": destination_city,
                    "destination_bound": True,
                }
                if destination_city and not _safe_text(location.get("requested_destination"))
                else {}
            ),
        }
        for location in map_locations
        if isinstance(location, dict)
    ]
    max_day = max(
        [_optional_int(location.get("day")) or 1 for location in map_locations if isinstance(location, dict)]
        or [1]
    )
    days = max(1, intent.days or max_day)
    meal_date_mode, outbound_arrival, return_departure = _meal_transport_context(
        intent,
        destination_city=destination_city,
        travel_bundle=travel_bundle,
    )
    meal_targets = meal_targets_by_day(
        days=days,
        date_mode=meal_date_mode,
        outbound_arrival=outbound_arrival,
        return_departure=return_departure,
        breakfast_requested=bool(re.search(r"(?:早餐|早饭|breakfast)", query_text, re.IGNORECASE)),
        lodging_breakfast_included=_lodging_breakfast_included(travel_bundle),
    )
    if ITINERARY_QUERY_REGEX.search(query_text):
        discovered_locations = list(map_locations)
        covered_locations = _ensure_daily_activity_coverage(
            map_locations,
            destination_city=destination_city,
            query_text=query_text,
            days=days,
            pace=intent.pace,
            meal_targets_by_day=meal_targets,
        )
        travel_bundle["candidate_map_locations"] = [*discovered_locations, *covered_locations]
        map_locations = covered_locations
    map_locations = [
        location
        for location in map_locations
        if isinstance(location, dict) and _is_destination_formal_location(location, destination_city)
    ]
    activities: List[TripActivity] = []
    locations_per_day = max(1, (len(map_locations) + days - 1) // days)
    activities_per_day: Dict[int, int] = {}
    activity_type_counts_by_day: Dict[Tuple[int, str], int] = {}

    def suggested_window(activity_type: str, position: int) -> Tuple[str, str, int]:
        """Provide a clearly labelled planning window, never a claimed opening time."""
        if activity_type == "food":
            return ("11:30", "13:00", 90) if position < 2 else ("17:30", "19:00", 90)
        windows = [
            ("09:00", "10:15", 75),
            ("10:30", "11:30", 60),
            ("13:15", "14:45", 90),
            ("15:15", "16:45", 90),
            ("19:15", "20:30", 75),
        ]
        return windows[min(position, len(windows) - 1)]

    for index, location in enumerate(map_locations, start=1):
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name")) or f"地点 {index}"
        category = _normalized_travel_location_category(location)
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
            "poi_id": _safe_text(
                location.get("poi_id") or location.get("place_id") or location.get("uid") or location.get("id")
            ) or None,
            "city": _safe_text(location.get("city")) or destination_city,
            "address": _safe_text(location.get("address") or location.get("description")) or None,
            "lat": _optional_float(location.get("lat")),
            "lng": _optional_float(location.get("lng")),
            "source": _safe_text(location.get("source")) or "地图地点核验",
            "data_type": location.get("data_type", "reference_data"),
        })
        summary = _safe_text(location.get("summary"))
        address = _safe_text(location.get("description") or location.get("address"))
        notes = [summary] if summary and summary != address else []
        if not notes:
            if activity_type == "food":
                notes = ["已纳入当天用餐候选；菜品特色、排队情况和营业时间需在出发前按地图详情复核。"]
            elif activity_type == "hotel":
                notes = ["作为住宿候选参与行程衔接；房型、价格、库存和入住政策需以预订页面为准。"]
            else:
                notes = ["地点已进入当日规划；具体特色与开放信息待地图详情补全后确认。"]
        explicit_day = _optional_int(location.get("day"))
        activity_day = explicit_day if explicit_day and 1 <= explicit_day <= days else min(
            days,
            ((index - 1) // locations_per_day) + 1,
        )
        day_position = activities_per_day.get(activity_day, 0)
        activities_per_day[activity_day] = day_position + 1
        type_position_key = (activity_day, activity_type)
        type_position = activity_type_counts_by_day.get(type_position_key, 0)
        activity_type_counts_by_day[type_position_key] = type_position + 1
        suggested_start, suggested_end, suggested_duration = suggested_window(activity_type, type_position)
        start_time = _safe_text(location.get("start_time")) or suggested_start
        end_time = _safe_text(location.get("end_time")) or suggested_end
        duration_minutes = _optional_int(location.get("duration_minutes")) or suggested_duration
        raw_official_traveler_prices = location.get("official_traveler_prices")
        official_traveler_prices = {
            traveler_type: price
            for traveler_type in ("adult", "child", "senior")
            if isinstance(raw_official_traveler_prices, dict)
            and (price := _optional_float(raw_official_traveler_prices.get(traveler_type))) is not None
        }
        raw_cost_unit = _safe_text(location.get("estimated_cost_unit"))
        raw_meal_type = _safe_text(location.get("meal_type"))
        activities.append(
            TripActivity(
                activity_id=_safe_text(location.get("activity_id") or location.get("id"))
                or f"act_{uuid.uuid4().hex}",
                day=activity_day,
                start_time=start_time,
                end_time=end_time,
                duration_minutes=duration_minutes,
                title=name,
                activity_type=activity_type,
                meal_type=(
                    raw_meal_type
                    if activity_type == "food" and raw_meal_type in {"breakfast", "lunch", "dinner"}
                    else None
                ),
                place=place,
                map_visible=True,
                transport_to_next=_safe_text(location.get("transport_to_next")) or None,
                estimated_cost=_optional_float(location.get("estimated_cost")),
                estimated_cost_currency=_safe_text(
                    location.get("estimated_cost_currency") or location.get("currency")
                ) or None,
                estimated_cost_cny_reference_amount=_optional_float(
                    location.get("estimated_cost_cny_reference_amount")
                    or location.get("cny_reference_amount")
                ),
                estimated_cost_exchange_rate_as_of=_safe_text(
                    location.get("estimated_cost_exchange_rate_as_of")
                    or location.get("exchange_rate_as_of")
                ) or None,
                estimated_cost_unit=raw_cost_unit if raw_cost_unit in {"group", "per_traveler"} else "group",
                official_traveler_prices=official_traveler_prices,
                reservation=(
                    location.get("reservation")
                    if date_sensitive and isinstance(location.get("reservation"), dict)
                    else None
                ),
                evidence_refs=location.get("evidence_refs")
                if isinstance(location.get("evidence_refs"), list)
                else [],
                notes=[note for note in notes if note],
                data_type="estimated_data",
            )
        )

    destination_meta = INTERNATIONAL_DESTINATION_META.get(canonical_destination(destination_city) or "", {})
    destination_currency = _safe_text(destination_meta.get("currency")) or "CNY"
    budget_warnings = apply_reference_costs(
        activities,
        destination_currency=destination_currency,
    )

    source_references = _trip_plan_source_references(travel_bundle)
    exchange_rate_date = next((
        _safe_text(activity.estimated_cost_exchange_rate_as_of)
        for activity in activities
        if _safe_text(activity.estimated_cost_currency) == destination_currency
        and activity.estimated_cost_cny_reference_amount is not None
        and _safe_text(activity.estimated_cost_exchange_rate_as_of)
    ), "")
    if destination_currency != "CNY" and exchange_rate_date:
        exchange_reference_id = f"source_ecb_exchange_{exchange_rate_date.replace('-', '')}"
        source_references.append({
            "reference_id": exchange_reference_id,
            "type": "budget",
            "data_type": "reference_data",
            "title": f"{destination_currency}/CNY 预算参考汇率",
            "source": "European Central Bank",
            "url": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
            "snippet": f"采用 {exchange_rate_date} 欧洲央行欧元参考汇率交叉换算，仅用于人民币预算参考。",
            "updated_at": exchange_rate_date,
            "related_fields": ["budget", "estimated_cost"],
            "related_places": [destination_city],
        })
        for activity in activities:
            if (
                _safe_text(activity.estimated_cost_currency) == destination_currency
                and activity.estimated_cost_cny_reference_amount is not None
                and exchange_reference_id not in activity.evidence_refs
            ):
                activity.evidence_refs.append(exchange_reference_id)
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
        explicit_reference_id = _safe_text(source.get("reference_id"))
        confidence, related_fields = source_defaults.get(source_type, (0.5, ["summary"]))
        source["updated_at"] = _safe_text(source.get("updated_at")) or source_updated_at
        source["reference_id"] = _safe_text(source.get("reference_id")) or f"source_{len(unique_sources) + 1}"
        source["confidence"] = source.get("confidence") if source.get("confidence") is not None else confidence
        source["related_fields"] = source.get("related_fields") or related_fields
        source["related_places"] = source.get("related_places") or [
            location.get("name") for location in map_locations[:8] if location.get("name")
        ]
        # Different assets may cite the same page with distinct reference IDs.
        # Keep those IDs resolvable when the formal document is adapted to V3.
        source_key = (
            _safe_text(source.get("url")), _safe_text(source.get("title")),
            source_type, explicit_reference_id,
        )
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        unique_sources.append(source)
    source_references = unique_sources

    # Keep the verified planning lodging on the formal map as a route anchor.
    # It is not a sightseeing activity and does not count toward daily capacity.
    lodging_candidates = travel_bundle.get("lodging_candidates")
    planning_lodging_rows = [
        {**dict(location), "category": "酒店"}
        for location in (lodging_candidates if isinstance(lodging_candidates, list) else [])[:1]
        if isinstance(location, dict) and _has_reusable_poi_coordinates(location)
    ]
    plan_map_locations = _merge_map_locations(
        map_locations,
        planning_lodging_rows,
        max_rows=len(map_locations) + len(planning_lodging_rows),
        require_coordinates=True,
    )

    plan = TripPlan(
        title=query_text,
        intent=intent,
        days=days,
        activities=activities,
        budget_summary=calculate_budget_summary(intent, activities),
        map_locations=plan_map_locations,
        source_references=source_references,
        warnings=budget_warnings,
    )
    domestic = canonical_destination(destination_city) not in INTERNATIONAL_DESTINATIONS
    plan.warnings.extend(
        apply_meal_schedule(
            plan,
            targets_by_day=meal_targets,
            domestic=domestic,
        )
    )
    plan.budget_summary = calculate_budget_summary(intent, plan.activities)
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


def _normalize_image_search_candidates(payload: Any, tool_name: str) -> List[Dict[str, str]]:
    if isinstance(payload, str):
        # Parse tavily-mcp's numbered image section before the generic JSON
        # fragment parser mistakes ``[1]`` for a complete JSON array.
        formatted_items: List[Dict[str, str]] = []
        for match in re.finditer(
            r"(?:^|\n)\[\d+\]\s+URL:\s*(?P<url>https://\S+)"
            r"(?:\r?\n\s*Description:\s*(?P<title>[^\r\n]+))?",
            payload,
            re.IGNORECASE,
        ):
            formatted_items.append({
                "title": _safe_text(match.group("title")),
                "image_url": match.group("url").rstrip(".,;"),
            })
        if formatted_items:
            payload = {"images": formatted_items}
        else:
            parsed = _safe_json_loads(payload)
            if parsed is not None:
                payload = parsed
    if isinstance(payload, dict) and ("content" in payload or "result" in payload):
        unwrapped = _unwrap_tool_output(payload)
        if unwrapped is not payload:
            payload = unwrapped
    if isinstance(payload, str):
        text_payload = payload
        parsed_items: List[Dict[str, str]] = []
        for match in re.finditer(
            r"(?:description|title|alt)\s*[:=]\s*[\"'](?P<title>[^\"']+)[\"'][\s\S]{0,240}?"
            r"(?:image_url|url)\s*[:=]\s*[\"'](?P<url>https://[^\"']+)[\"']",
            text_payload,
            re.IGNORECASE,
        ):
            parsed_items.append({"title": match.group("title"), "image_url": match.group("url")})
        for match in re.finditer(
            r"\[(?P<title>[^\]\r\n]{2,160})\]\((?P<url>https://[^)\s]+)\)",
            text_payload,
        ):
            parsed_items.append({"title": match.group("title"), "image_url": match.group("url")})
        if parsed_items:
            payload = {"images": parsed_items}
    raw_items = _first_present_list(payload, ["images", "results", "photos", "items", "data"])
    provider = "Tavily 图片检索" if "tavily" in tool_name.casefold() else "Serper 图片检索"
    candidates: List[Dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, str):
            item = {"image_url": item}
        if not isinstance(item, dict):
            continue
        image_url = _safe_text(
            item.get("image_url")
            or item.get("imageUrl")
            or item.get("url")
            or item.get("src")
        )
        if not image_url.startswith("https://"):
            continue
        # 图片说明比所在网页标题更具体；综合攻略标题可能列出多个地点。
        title = _safe_text(item.get("alt") or item.get("description") or item.get("title"))
        source_url = _safe_text(
            item.get("source_url")
            or item.get("page_url")
            or item.get("link")
            or item.get("provider_url")
        )
        candidates.append({
            "image_url": image_url,
            "title": title,
            # CDN 根域不能证明图片属于哪个地点，也不能伪装成来源页。
            "source_url": source_url if source_url.startswith("https://") else "",
            "provider": provider,
        })
    return candidates


def _image_candidate_matches_place(candidate: Dict[str, str], place_name: str, city: str) -> bool:
    expected = _normalized_poi_match_text(place_name)
    # 只匹配图片说明的主语，不把攻略目录中提及的地点当作图片主体。
    # 例如“东京跨年攻略｜迪士尼・增上寺・涩谷”中的配图可能来自横滨。
    subject = re.split(r"[|｜·・•+＋,，;；\n]", _safe_text(candidate.get("title")), maxsplit=1)[0]
    title = _normalized_poi_match_text(subject)
    if not expected or not title:
        return False
    name_matches = expected in title
    if not name_matches:
        return False
    destination = canonical_destination(city) or _safe_text(city)
    mentioned = _mentioned_destinations(candidate.get("title"))
    return not destination or not any(item != destination for item in mentioned)


def _search_activity_image_candidates(
    *,
    place_name: str,
    city: str,
    category: str,
    tool_manager: Any,
    message_history: List[Dict[str, Any]],
    session_id: str,
) -> List[Dict[str, str]]:
    if tool_manager is None:
        return []
    tools = [
        tool_name
        for tool_name in [
            _first_available_tool_name(tool_manager, ["tavily_search", "tavily-search"]),
            _first_available_tool_name(tool_manager, ["search_image_from_web"]),
        ]
        if tool_name
    ]
    if not tools:
        return []
    scene = "餐厅菜品与门店实景" if category == "餐厅" else "景点建筑与环境实景"
    query = f'"{place_name}" {city} {scene}'

    def run_provider(tool_name: str) -> Tuple[str, Any]:
        if "tavily" in tool_name.casefold():
            args = {
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": False,
                "include_images": True,
                "include_image_descriptions": True,
                "include_raw_content": False,
            }
            try:
                parameter_names = set(getattr(tool_manager.get_tool(tool_name), "parameters", {}).keys())
            except Exception:
                parameter_names = set()
            if parameter_names:
                args = {key: value for key, value in args.items() if key in parameter_names}
            arg_candidates = [args]
        else:
            arg_candidates = [
                {"query": query, "count": 8, "country": "cn"},
                {"query": query, "count": 8},
                {"query": query},
            ]
        payload = _run_tool_with_arg_candidates(
            tool_manager=tool_manager,
            tool_name=tool_name,
            message_history=message_history,
            session_id=session_id,
            arg_candidates=arg_candidates,
        )
        return tool_name, payload

    with ThreadPoolExecutor(max_workers=len(tools), thread_name_prefix="place-image") as executor:
        results = [future.result() for future in [executor.submit(run_provider, tool) for tool in tools]]
    candidates = [
        candidate
        for tool_name, payload in results
        if not _is_tool_execution_error_payload(payload)
        for candidate in _normalize_image_search_candidates(payload, tool_name)
        if _image_candidate_matches_place(candidate, place_name, city)
    ]
    to_verify: List[Dict[str, str]] = []
    seen_sources = set()
    for candidate in candidates:
        key = (_canonical_web_url(candidate.get("image_url")), candidate.get("source_url"))
        if not all(key) or key in seen_sources:
            continue
        seen_sources.add(key)
        to_verify.append(candidate)
        if len(to_verify) == 6:
            break

    def verify_source(candidate: Dict[str, str]) -> Optional[Dict[str, str]]:
        # 图片检索 description 可能只是综合攻略标题。必须在原网页找到
        # 相同图片 URL 的具体说明，避免将“涩谷”攻略配的浅草寺图当成涩谷。
        descriptions = fetch_image_source_descriptions(candidate.get("source_url", ""), candidate["image_url"])
        matching_description = next((description for description in descriptions
            if _image_candidate_matches_place({"title": description}, place_name, city)), "")
        return {**candidate, "title": matching_description} if matching_description else None

    # 来源网页访问有界且并行，避免逐图片累积等待拖慢整份行程。
    if not to_verify:
        return []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="image-source") as executor:
        verified = list(executor.map(verify_source, to_verify))
    unique: List[Dict[str, str]] = []
    seen_urls = set()
    for candidate in verified:
        if candidate and candidate["image_url"] not in seen_urls:
            seen_urls.add(candidate["image_url"])
            unique.append(candidate)
    return unique[:3]


def _external_activity_image(
    *,
    place_name: str,
    image_url: str,
    provider_name: str,
    provider_url: str,
    image_description: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    digest = hashlib.sha1(f"{place_name}|{image_url}".encode("utf-8")).hexdigest()[:16]
    source_ref = f"source_image_external_{digest}"
    asset = build_external_image_asset(
        image_id=f"img_external_{digest}",
        url=image_url,
        alt=image_description or f"{place_name}实景参考图片",
        provider_name=provider_name,
        provider_url=provider_url,
        source_ref=source_ref,
        checked_at=datetime.datetime.now(datetime.timezone.utc),
    )
    if asset is None:
        return None, None
    return asset.model_dump(mode="json"), {
        "reference_id": source_ref,
        "type": "image",
        "title": f"{place_name}实景图片",
        "source": provider_name,
        "url": provider_url or image_url,
        "snippet": (f"来源页中该图片的说明：{image_description}" if image_description
                    else "图片来自对应地点的数据来源，用于用户授权的私人展示与导出。"),
        "data_type": "reference_data",
        "related_fields": ["images"],
        "related_places": [place_name],
    }


def maybe_prepare_activity_images(
    travel_bundle: Dict[str, Any],
    tool_manager: Any = None,
    message_history: Optional[List[Dict[str, Any]]] = None,
    session_id: str = "",
) -> int:
    """Attach exact-POI images first, then exact article and strict web-search fallbacks."""
    raw_locations = travel_bundle.get("map_locations")
    if not isinstance(raw_locations, list) or not raw_locations:
        return 0
    preliminary_plan = _travel_bundle_to_trip_plan(travel_bundle)
    formal_metadata = [
        {
            "name": activity.place.name,
            "city": activity.place.city or _safe_text(preliminary_plan.intent.destination),
            "category": "餐厅" if activity.activity_type == "food" else "景点",
        }
        for activity in preliminary_plan.activities
        if activity.place is not None and activity.activity_type in {"attraction", "food"}
    ][:28]
    formal_by_name = {
        _normalized_poi_match_text(item["name"]): item
        for item in formal_metadata
    }
    assets_by_name = fetch_wikimedia_activity_images([
        (item["name"], item["city"])
        for item in formal_metadata
    ])
    image_sources: List[Dict[str, Any]] = []
    attached = 0
    search_limit = max(0, int(os.getenv("TRAVEL_WEB_IMAGE_SEARCH_LIMIT", "28")))
    searched = 0
    for location in raw_locations:
        if not isinstance(location, dict):
            continue
        name = _safe_text(location.get("name"))
        metadata = formal_by_name.get(_normalized_poi_match_text(name))
        if not metadata:
            continue
        image_assets: List[Dict[str, Any]] = []
        provider_images = location.get("provider_images") if isinstance(location.get("provider_images"), dict) else {}
        for provider in ("amap", "baidu"):
            provider_name = "高德地图 POI 图片" if provider == "amap" else "百度地图 POI 图片"
            provider_url = "https://www.amap.com/" if provider == "amap" else "https://map.baidu.com/"
            urls = provider_images.get(provider) if isinstance(provider_images.get(provider), list) else []
            for image_url in urls[:3]:
                image_asset, source = _external_activity_image(
                    place_name=name,
                    image_url=_safe_text(image_url),
                    provider_name=provider_name,
                    provider_url=provider_url,
                )
                if image_asset and source:
                    image_assets.append(image_asset)
                    image_sources.append(source)
        if not image_assets:
            fallback_urls = location.get("images") if isinstance(location.get("images"), list) else []
            fallback_provider = _safe_text(location.get("source_provider"))
            if fallback_provider in {"amap", "baidu"}:
                provider_name = "高德地图 POI 图片" if fallback_provider == "amap" else "百度地图 POI 图片"
                provider_url = "https://www.amap.com/" if fallback_provider == "amap" else "https://map.baidu.com/"
                for image_url in fallback_urls[:3]:
                    image_asset, source = _external_activity_image(
                        place_name=name,
                        image_url=_safe_text(image_url),
                        provider_name=provider_name,
                        provider_url=provider_url,
                    )
                    if image_asset and source:
                        image_assets.append(image_asset)
                        image_sources.append(source)
        wikimedia_asset = assets_by_name.get(name)
        if wikimedia_asset:
            source = wikimedia_asset.get("source")
            if isinstance(source, dict):
                image_sources.append(dict(source))
            image_assets.append({key: value for key, value in wikimedia_asset.items() if key != "source"})
        if not any(asset.get("export_allowed") for asset in image_assets) and searched < search_limit:
            searched += 1
            for candidate in _search_activity_image_candidates(
                place_name=name,
                city=_safe_text(metadata.get("city")),
                category=_safe_text(metadata.get("category")),
                tool_manager=tool_manager,
                message_history=message_history or [],
                session_id=session_id,
            ):
                image_asset, source = _external_activity_image(
                    place_name=name,
                    image_url=candidate["image_url"],
                    provider_name=candidate["provider"],
                    provider_url=candidate["source_url"],
                    image_description=candidate["title"],
                )
                if image_asset and source:
                    image_assets.append(image_asset)
                    image_sources.append(source)
        deduped_assets: List[Dict[str, Any]] = []
        seen_image_urls = set()
        for asset in image_assets:
            image_url = _safe_text(asset.get("url"))
            if not image_url or image_url in seen_image_urls:
                continue
            seen_image_urls.add(image_url)
            deduped_assets.append(asset)
        if deduped_assets:
            location["image_assets"] = deduped_assets[:3]
            attached += 1
    unique_sources: List[Dict[str, Any]] = []
    seen_source_refs = set()
    for source in image_sources:
        source_ref = _safe_text(source.get("reference_id"))
        if not source_ref or source_ref in seen_source_refs:
            continue
        seen_source_refs.add(source_ref)
        unique_sources.append(source)
    travel_bundle["activity_image_sources"] = unique_sources
    return attached


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


def _activity_clock_minutes(value: Any) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", _safe_text(value))
    if not match:
        return 24 * 60
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return 24 * 60
    return hour * 60 + minute


def _format_activity_clock(total_minutes: int) -> str:
    bounded = max(0, min(int(total_minutes), 23 * 60 + 59))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def _selected_transport_clock(section: Dict[str, Any], field: str) -> Optional[int]:
    options = section.get("options") if isinstance(section, dict) else None
    if not isinstance(options, list):
        return None
    selected_id = _safe_text(section.get("recommended_option_id") or section.get("selected_option_id"))
    if not selected_id:
        return None
    selected = next(
        (
            option for option in options
            if isinstance(option, dict) and _safe_text(option.get("option_id")) == selected_id
        ),
        None,
    )
    if not isinstance(selected, dict):
        return None
    raw = _safe_text(selected.get(field))
    match = re.search(r"(?:T|\s|^)([01]?\d|2[0-3]):([0-5]\d)", raw)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _apply_transport_day_windows(document: Dict[str, Any], minimum_transfer_minutes: int = 30) -> set[str]:
    """Keep first/last-day activities inside the selected transport window."""
    itinerary = document.get("itinerary")
    days = itinerary.get("days") if isinstance(itinerary, dict) else None
    if not isinstance(days, list) or not days:
        return set()
    arrival = _selected_transport_clock(document.get("outbound_transport") or {}, "arrival_time")
    departure = _selected_transport_clock(document.get("return_transport") or {}, "departure_time")
    dropped_ids: set[str] = set()

    def normalize_day(day: Dict[str, Any], *, lower: int = 0, upper: int = 24 * 60) -> None:
        activities = [item for item in day.get("activities", []) if isinstance(item, dict)]
        activities.sort(key=lambda item: (
            _activity_clock_minutes(item.get("start_time") or item.get("start_at")),
            _safe_text(item.get("activity_id") or item.get("id")),
        ))
        cursor = max(0, lower)
        kept: List[Dict[str, Any]] = []
        for activity in activities:
            raw_start = _activity_clock_minutes(activity.get("start_time") or activity.get("start_at"))
            raw_end = _activity_clock_minutes(activity.get("end_time") or activity.get("end_at"))
            duration = _optional_int(activity.get("duration_minutes"))
            if duration is None or duration <= 0:
                duration = raw_end - raw_start if raw_end < 24 * 60 and raw_end > raw_start else 60
            start = max(raw_start if raw_start < 24 * 60 else cursor, cursor)
            end = start + duration
            if end > upper:
                activity_id = _safe_text(activity.get("activity_id") or activity.get("id"))
                if activity_id:
                    dropped_ids.add(activity_id)
                continue
            start_text, end_text = _format_activity_clock(start), _format_activity_clock(end)
            if "start_at" in activity:
                activity["start_at"], activity["end_at"] = start_text, end_text
            else:
                activity["start_time"], activity["end_time"] = start_text, end_text
            activity["duration_minutes"] = duration
            kept.append(activity)
            cursor = end + 15
        day["activities"] = kept

    first_day = next((day for day in days if isinstance(day, dict)), None)
    last_day = next((day for day in reversed(days) if isinstance(day, dict)), None)
    if first_day is not None and arrival is not None:
        normalize_day(first_day, lower=min(24 * 60, arrival + minimum_transfer_minutes))
    if last_day is not None and departure is not None:
        normalize_day(last_day, upper=max(0, departure - minimum_transfer_minutes))
    return dropped_ids


def _sort_plan_activities_by_schedule(plan: TripPlan) -> None:
    """Keep the formal array order identical to the displayed local-time order."""
    plan.activities.sort(
        key=lambda activity: (
            activity.day,
            _activity_clock_minutes(activity.start_time),
            _activity_clock_minutes(activity.end_time),
            activity.activity_id,
        )
    )


def _shift_same_day_schedule_forward(
    plan: TripPlan,
    *,
    reference_time: Optional[datetime.datetime] = None,
) -> bool:
    """Never emit already elapsed activity times for a trip starting today."""
    try:
        date_mode, start_date, _end_date, _days = resolve_planning_date_contract(
            plan.intent.date_range,
            plan.intent.days,
        )
    except Exception:
        return False
    if date_mode != "fixed" or start_date is None:
        return False
    now = reference_time or datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    if start_date != now.date():
        return False
    first_day = [activity for activity in plan.activities if activity.day == 1]
    if not first_day:
        return False
    earliest = ((now.hour * 60 + now.minute + 59) // 15) * 15 + 30
    first_start = min(_activity_clock_minutes(activity.start_time) for activity in first_day)
    if first_start >= earliest:
        return False
    delta = earliest - first_start
    for activity in first_day:
        start = _activity_clock_minutes(activity.start_time)
        end = _activity_clock_minutes(activity.end_time)
        if start < 24 * 60:
            activity.start_time = _format_activity_clock(start + delta)
        if end < 24 * 60:
            activity.end_time = _format_activity_clock(end + delta)
    plan.warnings.append("首日为当天出发，已按北京时间将日程整体顺延到当前时间之后。")
    return True


def _build_trip_days(plan: TripPlan, validation: Any) -> List[TripDay]:
    issues = validation.issues if validation is not None else []
    trip_days: List[TripDay] = []
    for day_number in range(1, plan.days + 1):
        activities = sorted(
            (activity for activity in plan.activities if activity.day == day_number),
            key=lambda activity: (
                _activity_clock_minutes(activity.start_time),
                _activity_clock_minutes(activity.end_time),
                activity.activity_id,
            ),
        )
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


def _route_feasible_time_updates(
    days: List[Dict[str, Any]],
    routes: List[Dict[str, Any]],
) -> Tuple[Dict[str, Tuple[str, str]], set[str]]:
    """Shift flexible activities so every provider route fits before the next card."""
    route_minutes: Dict[Tuple[str, str], int] = {}
    for route in routes:
        for leg in route.get("legs", []) if isinstance(route, dict) else []:
            if not isinstance(leg, dict) or leg.get("status") != "ready":
                continue
            duration = _optional_int(leg.get("duration_minutes"))
            if duration is None:
                continue
            route_minutes[(
                _safe_text(leg.get("from_activity_id")),
                _safe_text(leg.get("to_activity_id")),
            )] = max(0, duration)

    updates: Dict[str, Tuple[str, str]] = {}
    dropped_ids: set[str] = set()
    for day in days:
        if not isinstance(day, dict) or not isinstance(day.get("activities"), list):
            continue
        activities = [item for item in day["activities"] if isinstance(item, dict)]
        activities.sort(
            key=lambda item: (
                _activity_clock_minutes(item.get("start_time") or item.get("start_at")),
                _safe_text(item.get("activity_id") or item.get("id")),
            )
        )
        kept: List[Dict[str, Any]] = []
        previous: Optional[Dict[str, Any]] = None
        previous_end: Optional[int] = None
        for activity in activities:
            activity_id = _safe_text(activity.get("activity_id") or activity.get("id"))
            start = _activity_clock_minutes(activity.get("start_time") or activity.get("start_at"))
            end = _activity_clock_minutes(activity.get("end_time") or activity.get("end_at"))
            duration = _optional_int(activity.get("duration_minutes"))
            if duration is None or duration <= 0:
                duration = max(30, end - start) if end < 24 * 60 and start < 24 * 60 else 60
            if start >= 24 * 60:
                start = previous_end + 30 if previous_end is not None else 9 * 60
            if previous is not None and previous_end is not None:
                previous_id = _safe_text(previous.get("activity_id") or previous.get("id"))
                travel = route_minutes.get((previous_id, activity_id))
                if travel is not None:
                    earliest = ((previous_end + travel + 10 + 14) // 15) * 15
                    start = max(start, earliest)
            end = start + duration
            if start >= 23 * 60 + 59 or end > 23 * 60 + 59:
                if activity_id:
                    dropped_ids.add(activity_id)
                continue
            start_text, end_text = _format_activity_clock(start), _format_activity_clock(end)
            activity["start_time"] = start_text
            activity["end_time"] = end_text
            updates[activity_id] = (start_text, end_text)
            previous, previous_end = activity, end
            kept.append(activity)
        day["activities"] = kept
    return updates, dropped_ids


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


def _is_destination_lodging_recommendation(
    recommendation: Dict[str, Any],
    destination_city: str,
    verified_candidate_names: set[str],
) -> bool:
    name = _safe_text(recommendation.get("name"))
    if name in verified_candidate_names:
        return True
    destination = canonical_destination(destination_city) or _safe_text(destination_city)
    if not destination:
        return False
    return _has_positive_destination_signal({
        **recommendation,
        "description": " ".join(
            _safe_text(item)
            for item in recommendation.get("reasons", [])
            if isinstance(item, str)
        ),
    }, destination)


def _transport_fallback_guidance(travel_bundle: Dict[str, Any], direction: str) -> str:
    raw_intent = travel_bundle.get("trip_intent")
    intent = raw_intent if isinstance(raw_intent, dict) else {}
    origin = _safe_text(intent.get("origin") or travel_bundle.get("origin_city"))
    destination = _safe_text(intent.get("destination") or travel_bundle.get("destination_city"))
    if direction == "return":
        origin, destination = destination, origin
    return transport_query_guidance(origin, destination)


def _prepare_travel_plan(travel_bundle: Dict[str, Any]):
    initial_plan = _travel_bundle_to_trip_plan(travel_bundle)
    initial_validation = validate_trip_plan(initial_plan)
    repair_result = repair_trip_plan_once(initial_plan, initial_validation)
    plan = repair_result.plan
    _sort_plan_activities_by_schedule(plan)
    _shift_same_day_schedule_forward(plan)
    final_validation = validate_trip_plan(plan)
    _ensure_estimated_route_legs(plan)
    plan.trip_days = _build_trip_days(plan, final_validation)
    return plan, initial_validation, final_validation, repair_result


def _document_from_travel_plan(plan, travel_bundle: Dict[str, Any]) -> Dict[str, Any]:
    document = adapt_v1_document_to_v2({
        "schema_version": "1.0", "plan": plan.model_dump(mode="json"),
        "budget": plan.budget_summary, "sources": [item.model_dump(mode="json") for item in plan.source_references],
        "cover_image": travel_bundle.get("cover_image"),
    })
    document["candidate_places"] = _candidate_places_for_document(travel_bundle, plan)
    destination_summary = _build_destination_overview_summary(travel_bundle)
    if destination_summary:
        document["destination_overview"]["area_overview"] = destination_summary
    raw_lodging_candidates = travel_bundle.get("lodging_candidates")
    destination_city = (
        canonical_destination(_safe_text(plan.intent.destination))
        or canonical_destination(_safe_text(travel_bundle.get("destination_city")))
        or _safe_text(plan.intent.destination)
        or _safe_text(travel_bundle.get("destination_city"))
    )
    lodging_candidates = _filter_destination_lodging_locations(
        [dict(candidate) for candidate in raw_lodging_candidates if isinstance(candidate, dict)],
        destination_city,
    ) if isinstance(raw_lodging_candidates, list) else []
    verified_lodging_names = {
        _safe_text(candidate.get("name"))
        for candidate in lodging_candidates
        if isinstance(candidate, dict) and _safe_text(candidate.get("name"))
    } if isinstance(lodging_candidates, list) else set()
    existing_lodgings = document["hotel_recommendations"].get("recommendations")
    if isinstance(existing_lodgings, list):
        document["hotel_recommendations"]["recommendations"] = [
            recommendation
            for recommendation in existing_lodgings
            if isinstance(recommendation, dict)
            and _is_destination_lodging_recommendation(
                recommendation,
                destination_city,
                verified_lodging_names,
            )
        ]
    if (
        isinstance(lodging_candidates, list)
        and lodging_candidates
        and not document["hotel_recommendations"].get("recommendations")
    ):
        reference_time = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        lodging_items: List[Dict[str, Any]] = []
        for index, candidate in enumerate(lodging_candidates[:3], start=1):
            if not isinstance(candidate, dict):
                continue
            name = _safe_text(candidate.get("name"))
            if not name:
                continue
            source_reference_id = f"source_lodging_candidate_{index}"
            description = _safe_text(candidate.get("description") or candidate.get("address"))
            poi_id = _safe_text(candidate.get("poi_id") or candidate.get("place_id") or candidate.get("uid"))
            lat = _optional_float(candidate.get("lat"))
            lng = _optional_float(candidate.get("lng"))
            place = (
                {
                    "name": name,
                    "category": "住宿",
                    "poi_id": poi_id,
                    "lat": lat,
                    "lng": lng,
                    "address": _safe_text(candidate.get("address") or candidate.get("description")) or None,
                    "city": _safe_text(candidate.get("city")) or destination_city,
                    "rating": _optional_float(candidate.get("rating")),
                    "summary": description or None,
                    "source": _safe_text(candidate.get("source")) or "地图住宿专项检索",
                    "data_type": candidate.get("data_type") or "reference_data",
                }
                if poi_id and lat is not None and lng is not None
                else None
            )
            lodging_items.append({
                "hotel_id": poi_id or f"lodging_candidate_{index}",
                "area": _safe_text(
                    candidate.get("area")
                    or candidate.get("city")
                    or candidate.get("address")
                    or candidate.get("description")
                ) or "区域待确认",
                "name": name,
                "place": place,
                "nightly_price": None,
                "total_price": None,
                "currency": "CNY",
                "rating": _optional_float(candidate.get("rating")),
                "reasons": [description] if description else ["来自目的地住宿专项检索，位置、房型与价格需在预订前复核"],
                "booking_url": None,
                "source_reference_id": source_reference_id,
                "data_type": "reference_data",
                "updated_at": reference_time,
            })
            document["sources"].append({
                "reference_id": source_reference_id,
                "type": "map",
                "title": f"{name}住宿候选",
                "source": _safe_text(candidate.get("source")) or "地图住宿专项检索",
                "url": "",
                "snippet": description or "住宿候选名称来自本次地图专项检索，库存与价格未核验。",
                "data_type": "reference_data",
                "updated_at": reference_time,
                "confidence": 0.65,
                "related_fields": ["lodging_plan"],
                "related_places": [name],
            })
        if lodging_items:
            document["hotel_recommendations"] = {
                "status": "needs_confirmation",
                "status_reason": "住宿候选来自地图专项检索；价格、库存与预订状态待确认",
                "recommendations": lodging_items,
            }
    lodging_reference_rows = travel_bundle.get("lodging_reference_rows")
    if isinstance(lodging_reference_rows, list) and document["hotel_recommendations"].get("recommendations"):
        reference_time = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        lodging_reference_ids: List[str] = []
        for index, row in enumerate(lodging_reference_rows, start=1):
            if not isinstance(row, dict):
                continue
            title = _safe_text(row.get("title")) or f"住宿体验参考 {index}"
            summary = _safe_text(row.get("summary"))
            source_url = _safe_text(row.get("url"))
            reference_type = _safe_text(row.get("reference_type")) or "xhs"
            if reference_type not in {"xhs", "web"}:
                reference_type = "web"
            source_reference_id = next(
                (
                    _safe_text(source.get("reference_id"))
                    for source in document["sources"]
                    if isinstance(source, dict)
                    and (_safe_text(source.get("url")) == source_url or _safe_text(source.get("title")) == title)
                ),
                "",
            )
            if not source_reference_id:
                source_reference_id = f"source_lodging_reference_{index}"
                document["sources"].append({
                    "reference_id": source_reference_id,
                    "type": reference_type,
                    "title": title,
                    "source": _safe_text(row.get("author") or row.get("source")) or ("小红书" if reference_type == "xhs" else "网页住宿参考"),
                    "url": source_url,
                    "snippet": summary or "该条资料仅作为目的地住宿体验与区域参考。",
                    "data_type": "reference_data",
                    "updated_at": reference_time,
                    "confidence": 0.55,
                    "related_fields": ["lodging_plan"],
                    "related_places": [],
                })
            lodging_reference_ids.append(source_reference_id)
        for item in document["hotel_recommendations"]["recommendations"]:
            reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
            if not any("社区住宿体验参考" in _safe_text(reason) for reason in reasons):
                reasons.append("社区与网页住宿参考仅用于了解区域、入住体验、房型或价格线索；具体位置、价格和库存须以地图与预订页核验。")
            item["reasons"] = reasons
            item["source_reference_ids"] = lodging_reference_ids
    scope = document["outbound_transport"]["scope"]
    date_sensitive = bool(travel_bundle.get("date_sensitive", True))
    for direction, bundle_key in (("outbound", "ticket_bundle"), ("return", "return_ticket_bundle")):
        section_name = f"{direction}_transport"
        document[section_name] = transport_section_from_bundle(
            travel_bundle.get(bundle_key) if date_sensitive else None, direction, scope,
            document[section_name].get("travel_date") if date_sensitive else None,
            _transport_fallback_guidance(travel_bundle, direction) if date_sensitive else None,
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
    return document


def build_travel_document(travel_bundle: Dict[str, Any]) -> Dict[str, Any]:
    """正式规划直接取得文档，避免构建并丢弃旧版 SSE 与 Markdown。"""
    plan, _, _, _ = _prepare_travel_plan(travel_bundle)
    return sanitize_user_visible_payload(_document_from_travel_plan(plan, travel_bundle))


def _build_travel_structured_result(travel_bundle: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """旧版聊天协议适配：同一构建核心输出增量事件和正文。"""
    plan, initial_validation, final_validation, repair_result = _prepare_travel_plan(travel_bundle)
    document = _document_from_travel_plan(plan, travel_bundle)
    plan_payload = _public_trip_plan_payload(plan)
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


async def _build_travel_structured_result_with_routes(
    travel_bundle: Dict[str, Any],
    tool_manager: Any,
) -> Tuple[List[Dict[str, Any]], str]:
    """Build the formal document and persist provider routes in the same snapshot."""
    events, markdown = _build_travel_structured_result(travel_bundle)
    trip_event = next((event for event in events if event.get("type") == "trip_plan"), None)
    if not isinstance(trip_event, dict):
        return events, markdown
    plan = trip_event.get("plan")
    document = trip_event.get("document")
    if not isinstance(plan, dict) or not isinstance(document, dict):
        return events, markdown
    days = plan.get("days")
    if not isinstance(days, list) or not days:
        return events, markdown

    destination = canonical_destination(
        _safe_text((plan.get("intent") or {}).get("destination"))
        if isinstance(plan.get("intent"), dict)
        else ""
    )
    scope = "international" if destination in INTERNATIONAL_DESTINATIONS else "domestic"
    dispatcher = getattr(tool_manager, "baidu_request_dispatcher", None)
    route_tasks = [
        build_day_route(
            day=max(1, _optional_int(day.get("day")) or index),
            plan_version=max(1, _optional_int(plan.get("version")) or 1),
            activities=day.get("activities") if isinstance(day.get("activities"), list) else [],
            scope=scope,
            baidu_dispatcher=dispatcher,
        )
        for index, day in enumerate(days, start=1)
        if isinstance(day, dict)
    ]
    if not route_tasks:
        return events, markdown
    raw_routes = await asyncio.gather(*route_tasks, return_exceptions=True)
    routes: List[Dict[str, Any]] = []
    for index, route in enumerate(raw_routes, start=1):
        if isinstance(route, dict):
            routes.append(route)
            continue
        routes.append({
            "day": index,
            "plan_version": max(1, _optional_int(plan.get("version")) or 1),
            "provider": "openrouteservice" if scope == "international" else "baidu_directionlite",
            "coordinate_system": "WGS84" if scope == "international" else "BD09LL",
            "legs": [],
            "bbox": None,
            "status": "unavailable",
        })
    ready_count = sum(route.get("status") == "ready" for route in routes)
    partial_count = sum(route.get("status") == "partial" for route in routes)
    if ready_count == len(routes):
        route_status = "ready"
        status_reason = "已按日计算真实道路路线"
    elif ready_count or partial_count:
        route_status = "degraded"
        status_reason = "部分真实路线暂不可用，已保留核验成功的路段"
    else:
        route_status = "unavailable"
        status_reason = "真实路线暂不可用，当前仅显示已核验地点"

    time_updates, route_dropped_ids = _route_feasible_time_updates(days, routes)
    map_guidance = document.get("map_guidance")
    if not isinstance(map_guidance, dict):
        map_guidance = {}
        document["map_guidance"] = map_guidance
    map_guidance.update({
        "status": route_status,
        "status_reason": status_reason,
        "day_routes": routes,
        "unavailable_segments": [
            leg
            for route in routes
            for leg in route.get("legs", [])
            if isinstance(leg, dict) and leg.get("status") == "unavailable"
        ],
    })

    # Persist each adjacent provider leg on the originating activity as well as
    # in map_guidance. The workbench activity card, V2 plan and later V3 adapter
    # must all read the same route fact instead of leaving route_to_next empty.
    route_by_activity_id: Dict[str, Dict[str, Any]] = {}
    for route in routes:
        for leg in route.get("legs", []):
            if not isinstance(leg, dict):
                continue
            from_id = _safe_text(leg.get("from_activity_id"))
            if not from_id:
                continue
            ready = leg.get("status") == "ready"
            if not ready:
                continue
            route_by_activity_id[from_id] = {
                "mode": _safe_text(leg.get("mode")) or "步行",
                "distance_meters": _optional_int(leg.get("distance_meters")),
                "duration_minutes": _optional_int(leg.get("duration_minutes")),
                "estimated_cost": _optional_float(leg.get("estimated_cost")),
                "provider": _safe_text(leg.get("provider")) or None,
                "data_type": "confirmed_live_data",
                "calculated_at": _safe_text(leg.get("calculated_at")) or None,
            }

    activity_collections: List[Any] = [plan.get("activities")]
    activity_collections.extend(
        day.get("activities")
        for day in plan.get("days", [])
        if isinstance(day, dict)
    )
    itinerary = document.get("itinerary")
    if isinstance(itinerary, dict):
        activity_collections.extend(
            day.get("activities")
            for day in itinerary.get("days", [])
            if isinstance(day, dict)
        )
    for event in events:
        if event.get("type") == "trip_day_upsert" and isinstance(event.get("day"), dict):
            activity_collections.append(event["day"].get("activities"))

    def preserve_dropped_as_candidates(activity_ids: set[str]) -> None:
        if not activity_ids:
            return
        candidate_places = document.get("candidate_places")
        if not isinstance(candidate_places, list):
            candidate_places = []
            document["candidate_places"] = candidate_places
        known_poi_ids = {
            _safe_text(item.get("poi_id") or item.get("place_id") or item.get("uid"))
            for item in candidate_places
            if isinstance(item, dict)
        }
        activities_by_id: Dict[str, Dict[str, Any]] = {}
        for collection in activity_collections:
            if not isinstance(collection, list):
                continue
            for activity in collection:
                if not isinstance(activity, dict):
                    continue
                activity_id = _safe_text(activity.get("activity_id") or activity.get("id"))
                if activity_id in activity_ids:
                    activities_by_id.setdefault(activity_id, activity)
        for activity in activities_by_id.values():
            place = activity.get("place")
            if not isinstance(place, dict):
                continue
            poi_id = _safe_text(place.get("poi_id") or place.get("place_id") or place.get("uid"))
            if not poi_id or poi_id in known_poi_ids:
                continue
            # 路线可行性修复可能把原正式活动降为候选；V2 合同最多允许
            # 15 个候选，因此满额时让新降级的正式活动替换末尾普通候选。
            if len(candidate_places) >= 15:
                candidate_places.pop()
            candidate_places.append({
                **place,
                "suggested_duration_minutes": _optional_int(activity.get("duration_minutes")),
            })
            known_poi_ids.add(poi_id)
        for collection in activity_collections:
            if isinstance(collection, list):
                collection[:] = [
                    activity
                    for activity in collection
                    if not isinstance(activity, dict)
                    or _safe_text(activity.get("activity_id") or activity.get("id")) not in activity_ids
                ]

    preserve_dropped_as_candidates(route_dropped_ids)
    for collection in activity_collections:
        if not isinstance(collection, list):
            continue
        for activity in collection:
            if not isinstance(activity, dict):
                continue
            activity_id = _safe_text(activity.get("activity_id") or activity.get("id"))
            if activity_id in time_updates:
                start_time, end_time = time_updates[activity_id]
                if "start_at" in activity:
                    activity["start_at"] = start_time
                    activity["end_at"] = end_time
                else:
                    activity["start_time"] = start_time
                    activity["end_time"] = end_time
            route_summary = route_by_activity_id.get(activity_id)
            if route_summary:
                activity["route_to_next"] = dict(route_summary)
                activity["transport_to_next"] = route_summary["mode"]

    transport_dropped_ids = _apply_transport_day_windows(document)
    preserve_dropped_as_candidates(transport_dropped_ids)

    # 活动被移入候选池后，地图正式地点必须同步到当前日程快照；否则
    # map_guidance 仍引用已删除地点，整份正式文档会在最终校验时失败。
    formal_map_locations: List[Dict[str, Any]] = []
    itinerary = document.get("itinerary")
    itinerary_days = itinerary.get("days") if isinstance(itinerary, dict) else []
    for day in itinerary_days if isinstance(itinerary_days, list) else []:
        if not isinstance(day, dict):
            continue
        for order, activity in enumerate(day.get("activities", []), start=1):
            if not isinstance(activity, dict) or activity.get("map_visible", True) is False:
                continue
            place = activity.get("place")
            if not isinstance(place, dict) or place.get("lat") is None or place.get("lng") is None:
                continue
            formal_map_locations.append({
                **place,
                "id": place.get("poi_id") or activity.get("activity_id"),
                "day": day.get("day"),
                "order": order,
                "activity_id": activity.get("activity_id"),
            })
    map_guidance["location_ids"] = [
        _safe_text(location.get("id")) for location in formal_map_locations
    ]
    plan["map_locations"] = formal_map_locations
    for event in events:
        if event.get("type") == "trip_locations":
            event["locations"] = formal_map_locations

    sanitized_events = sanitize_user_visible_payload(events)
    return sanitized_events, export_trip_markdown(document)


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


def _prepare_chat_history(request_messages, selected_skill_ids, selected_knowledge_context, allow_web_search):
    message_history = build_message_history(request_messages)
    skill_message = build_skill_system_message(selected_skill_ids)
    if skill_message:
        message_history.insert(0, skill_message)
    append_selected_knowledge_context_message(message_history, selected_knowledge_context)
    append_online_search_policy_message(message_history, allow_web_search)
    return message_history


def _append_bundle_context(message_history, bundle, message_type):
    if bundle and bundle.get("context_message"):
        message_history.append({
            "role": "system", "content": bundle["context_message"],
            "message_id": str(uuid.uuid4()), "type": message_type,
        })


def _prepare_travel_context(*, user_query, tool_manager, message_history, session_id,
                            selected_skill_ids, xhs_bundle, ticket_bundle, allow_web_search,
                            allow_date_sensitive=True, trip_intent=None, profile=None,
                            selected_knowledge_context=None, fallback_destination=""):
    """两种聊天入口共用资料准备；可选增强失败时保留已取得的旅行资料。"""
    common = dict(tool_manager=tool_manager, message_history=message_history, session_id=session_id)
    travel_bundle = None
    try:
        travel_bundle = maybe_prepare_travel_experience_bundle(
            user_query=user_query, selected_skill_ids=selected_skill_ids, xhs_bundle=xhs_bundle,
            allow_web_search=allow_web_search, allow_date_sensitive=allow_date_sensitive, **common,
        )
        if travel_bundle:
            if trip_intent is not None:
                travel_bundle["trip_intent"] = trip_intent.model_dump()
            if ticket_bundle:
                travel_bundle["ticket_bundle"] = ticket_bundle
            if profile:
                travel_bundle["user_profile"] = _normalize_user_profile(profile).model_dump()
            if selected_knowledge_context:
                travel_bundle["selected_knowledge_context"] = selected_knowledge_context
            travel_bundle["cover_image"] = maybe_prepare_destination_cover(
                destination=_safe_text(travel_bundle.get("destination_city")) or fallback_destination, **common,
            )
            maybe_prepare_activity_images(travel_bundle, **common)
        if travel_bundle and travel_bundle.get("context_message"):
            append_travel_rag_context_message(message_history, _safe_text(travel_bundle.get("rag_context")))
            _append_bundle_context(message_history, travel_bundle, "system_travel_experience_context")
            return travel_bundle
    except Exception as error:
        logger.error("旅行体验增强失败: %s", error)
    _append_bundle_context(message_history, xhs_bundle, "system_xhs_search_context")
    return travel_bundle


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
    message_history = _prepare_chat_history(
        request_messages, selected_skill_ids, selected_knowledge_context, allow_web_search,
    )
    runtime_session_id = session_id or str(uuid.uuid4())
    ticket_bundle: Optional[Dict[str, Any]] = None
    xhs_bundle: Optional[Dict[str, Any]] = None
    travel_bundle: Optional[Dict[str, Any]] = None
    travel_rag_context = ""

    latest_user_query = _extract_latest_user_query(message_history)
    non_stream_trip_intent = (
        extract_trip_intent(latest_user_query, profile=profile)
        if is_trip_planning_query(latest_user_query)
        else None
    )
    date_sensitive_enabled = not bool(
        non_stream_trip_intent
        and is_flexible_date_range(non_stream_trip_intent.date_range)
    )
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
        if date_sensitive_enabled:
            ticket_bundle = maybe_prepare_train_ticket_bundle(
                user_query=latest_user_query,
                tool_manager=effective_tool_manager,
                message_history=message_history,
                session_id=runtime_session_id,
                selected_skill_ids=selected_skill_ids,
            )
        _append_bundle_context(message_history, ticket_bundle, "system_realtime_ticket_context")

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

    if allow_web_search and (not ticket_bundle or is_trip_planning_query(latest_user_query)):
        try:
            xhs_bundle = maybe_prepare_xhs_search_bundle(
                user_query=latest_user_query,
                tool_manager=effective_tool_manager,
                message_history=message_history,
                session_id=runtime_session_id,
                selected_skill_ids=selected_skill_ids,
                accommodation_reference=is_trip_planning_query(latest_user_query),
            )
        except Exception as xhs_error:
            logger.error(f"非流式小红书预检索失败: {xhs_error}")

    travel_bundle = _prepare_travel_context(
        user_query=latest_user_query, tool_manager=effective_tool_manager,
        message_history=message_history, session_id=runtime_session_id,
        selected_skill_ids=selected_skill_ids, xhs_bundle=xhs_bundle, ticket_bundle=ticket_bundle,
        allow_web_search=allow_web_search, allow_date_sensitive=date_sensitive_enabled,
        trip_intent=non_stream_trip_intent, fallback_destination=_extract_destination_city(latest_user_query),
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
    structured_trip_request: Optional[Any] = None,
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
    stream_kwargs.update({
        key: value for key, value in {
            "request_id": request_id,
            "profile": profile,
            "planning_mode": planning_mode,
            "clarification_answers": clarification_answers,
            "selected_knowledge_context": selected_knowledge_context,
            "structured_trip_request": structured_trip_request,
        }.items() if value is not None
    })

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
    structured_trip_request: Optional[Any] = None,
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
        response_kwargs.update({
            key: value for key, value in {
                "request_id": request_id,
                "profile": profile,
                "planning_mode": planning_mode,
                "clarification_answers": clarification_answers,
                "selected_knowledge_context": selected_knowledge_context,
                "structured_trip_request": structured_trip_request,
            }.items() if value is not None
        })
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
    route_kwargs.update({
        field: getattr(request, field)
        for field in (
            "profile", "planning_mode", "allow_web_search", "clarification_answers",
            "selected_knowledge_context", "structured_trip_request", "request_id",
        )
        if hasattr(request, field)
    })
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
    structured_trip_request: Optional[Any] = None,
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
        message_history = _prepare_chat_history(
            request_messages, selected_skill_ids, selected_knowledge_context, allow_web_search,
        )
        message_id = str(uuid.uuid4())
        stream_session_id = stream_request_id

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
        structured_trip_intent = _trip_intent_from_structured_request(structured_trip_request)
        latest_user_message_id = _extract_latest_user_message_id(message_history)
        if structured_trip_intent or _is_travel_experience_query(latest_user_query) or is_trip_planning_query(latest_user_query):
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

        if structured_trip_intent is not None:
            progress_chunk = _build_progress_chunk(
                progress_message_id,
                "已收到完整表单，正在核对条件并开始规划。",
                sanitize_text,
                latest_user_message_id,
            )
            yield encode_event(progress_chunk)
            await asyncio.sleep(0.01)
            resolved_trip_intent = structured_trip_intent
            yield encode_event({"type": "trip_intent", "intent": structured_trip_intent.model_dump(), "task_graph": True})
            await asyncio.sleep(0.01)
            message_history.append(
                {
                    "role": "system",
                    "content": build_trip_context_message(structured_trip_intent, None, {}),
                    "message_id": str(uuid.uuid4()),
                    "type": "system_trip_intent_context",
                }
            )
        elif trip_product_flow_enabled and is_trip_planning_query(latest_user_query):
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
                profile=profile,
                clarification_answers=cumulative_answers,
            )
            normalized_date_range = canonicalize_planning_date_range(
                trip_intent.date_range,
                trip_intent.days,
            )
            if normalized_date_range:
                trip_intent.date_range = normalized_date_range
            elif "date_range" in processed_fields:
                processed_fields.discard("date_range")
                answered_count = count_clarification_fields(processed_fields)
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
                protected_fields=set(cumulative_answers),
            )
            normalized_date_range = canonicalize_planning_date_range(
                trip_intent.date_range,
                trip_intent.days,
            )
            if normalized_date_range:
                trip_intent.date_range = normalized_date_range
            resolved_trip_intent = trip_intent
            yield encode_event({"type": "trip_intent", "intent": trip_intent.model_dump(), "task_graph": True})
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
        xhs_bundle: Optional[Dict[str, Any]] = None
        travel_bundle: Optional[Dict[str, Any]] = None
        travel_rag_context = ""
        realtime_only_answer = ""
        streamed_final_answer_text = ""
        structured_trip_final_text = ""
        model_final_answer_emitted = False
        error_phase = "researching"
        if resolved_trip_intent is not None and (structured_trip_intent is not None or is_trip_planning_query(latest_user_query)):
            from services.production_planning_service import stream_production_plan
            async for event in stream_production_plan(intent=resolved_trip_intent, query=latest_user_query,
                tool_manager=effective_tool_manager, message_history=message_history, session_id=stream_session_id,
                allow_web_search=allow_web_search, selected_skill_ids=selected_skill_ids, profile=profile,
                selected_knowledge_context=selected_knowledge_context):
                yield encode_event(event)
            yield encode_event(_chat_complete_payload(request_id=stream_request_id, message_id=message_id, finish_reason="completed"))
            return
        try:
            ticket_kwargs = {
                "tool_manager": effective_tool_manager,
                "message_history": message_history,
                "session_id": stream_session_id,
                "selected_skill_ids": selected_skill_ids,
            }
            ticket_bundle = await asyncio.to_thread(
                maybe_prepare_train_ticket_bundle, user_query=latest_user_query, **ticket_kwargs,
            )
            _append_bundle_context(message_history, ticket_bundle, "system_realtime_ticket_context")

            if ticket_bundle:
                realtime_only_answer = _safe_text(ticket_bundle.get("realtime_only_answer"))
                if not realtime_only_answer:
                    realtime_only_answer = _build_realtime_only_answer(ticket_bundle)

            if allow_web_search and (not ticket_bundle or is_trip_planning_query(latest_user_query)):
                xhs_bundle = await asyncio.to_thread(
                    maybe_prepare_xhs_search_bundle,
                    user_query=latest_user_query,
                    tool_manager=effective_tool_manager,
                    message_history=message_history,
                    session_id=stream_session_id,
                    selected_skill_ids=selected_skill_ids,
                    accommodation_reference=is_trip_planning_query(latest_user_query),
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

        travel_bundle = await asyncio.to_thread(
            _prepare_travel_context, user_query=latest_user_query, tool_manager=effective_tool_manager,
            message_history=message_history, session_id=stream_session_id,
            selected_skill_ids=selected_skill_ids, xhs_bundle=xhs_bundle, ticket_bundle=ticket_bundle,
            allow_web_search=allow_web_search, profile=profile,
            selected_knowledge_context=selected_knowledge_context,
        )

        logger.info(f"旅行增强结果状态: {'已生成' if travel_bundle else '未生成'}")
        if travel_bundle:
            try:
                structured_events, structured_trip_final_text = await _build_travel_structured_result_with_routes(
                    travel_bundle,
                    effective_tool_manager,
                )
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
                raw_diagnostics = getattr(structured_error, "diagnostics", None)
                diagnostics = (
                    [dict(item) for item in raw_diagnostics[:8] if isinstance(item, dict)]
                    if isinstance(raw_diagnostics, list)
                    else [{
                        "location": "document",
                        "type": type(structured_error).__name__,
                        "message": "结构化旅行文档生成失败",
                    }]
                )
                logger.error(
                    "旅行结构化事件生成失败 diagnostics=%s",
                    json.dumps(diagnostics, ensure_ascii=False),
                )
                raise PlanningPipelineError(
                    code="FORMAL_DOCUMENT_BUILD_FAILED",
                    user_message="方案结构校验失败，未保存不完整内容。请重试本次规划。",
                    diagnostics=diagnostics,
                    retryable=True,
                ) from structured_error

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
                # 简化工作流的直接回答使用 do_subtask_result；多智能体模式仍将其视为中间结果。
                if not effective_use_multi_agent and msg.get("type") == "do_subtask_result":
                    msg = {**msg, "type": "final_answer"}
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

        if not model_final_answer_emitted and not realtime_only_answer:
            raise RuntimeError("模型未返回有效回答")

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
