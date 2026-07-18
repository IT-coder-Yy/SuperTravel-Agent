import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from schemas.trip_models import ClarificationQuestion, TripIntent, UserTravelProfile
from services.destination_catalog_service import canonical_destination, domestic_city_names, INTERNATIONAL_DESTINATIONS
from services.travel_date_service import canonicalize_explicit_date_range


CITY_NAMES = [
    "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "成都", "西安", "武汉",
    "长沙", "郑州", "洛阳", "开封", "安阳", "石家庄", "太原", "济南", "青岛", "大连", "沈阳",
    "长春", "哈尔滨", "呼和浩特", "银川", "兰州", "西宁", "乌鲁木齐", "拉萨", "昆明", "贵阳",
    "南宁", "海口", "三亚", "福州", "厦门", "南昌", "合肥", "宁波", "无锡", "扬州", "绍兴",
    "桂林", "珠海", "佛山", "东莞", "泉州", "温州", "丽江", "大理", "张家界", "黄山",
]
CITY_NAMES = sorted(set(CITY_NAMES + domestic_city_names() + list(INTERNATIONAL_DESTINATIONS)), key=len, reverse=True)

TRIP_PLANNING_PATTERN = re.compile(
    r"(规划|安排|制定|做一份|帮我|行程|攻略|路线|几日游|一日游|二日游|两日游|三日游|四日游|五日游|周末游|自由行|旅游|旅行|出游|游玩)"
)
NON_ITINERARY_PATTERN = re.compile(r"(酒店|住宿|美食|餐厅|门票|车票|火车|高铁|航班|机票|天气|签证)")

CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [_safe_text(item) for item in value if _safe_text(item)]
    text = _safe_text(value)
    return [text] if text else []


def _number_from_text(value: str) -> Optional[int]:
    text = _safe_text(value)
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[text]
    if len(text) == 2 and text.startswith("十") and text[1] in CHINESE_NUMBERS:
        return 10 + CHINESE_NUMBERS[text[1]]
    if len(text) == 2 and text.endswith("十") and text[0] in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[text[0]] * 10
    return None


def is_trip_planning_query(query: str) -> bool:
    text = _safe_text(query)
    if not text:
        return False
    if "总结" in text and not re.search(r"(规划|行程|安排|路线|制定)", text):
        return False
    if not TRIP_PLANNING_PATTERN.search(text):
        return False
    if NON_ITINERARY_PATTERN.search(text) and not re.search(r"(行程|规划|路线|几日游|自由行)", text):
        return False
    return True


def _extract_destination(query: str) -> Optional[str]:
    destination_text = re.sub(
        r"(?:从[一-鿿A-Za-z\s]{2,30}出发|我在[一-鿿A-Za-z\s]{2,30}(?=[，,。\s]))",
        " ",
        query,
    )
    catalog_match = canonical_destination(destination_text)
    if catalog_match:
        return catalog_match
    guided_match = re.search(r"(?:规划|安排|制定|去|到)([\u4e00-\u9fff]{2,8}?)(?:\d|一|二|两|三|四|五|六|七|八|九|十|旅|游|玩|行程|攻略|路线|自由行|之旅|$)", query)
    if guided_match:
        guided_text = guided_match.group(1).strip("的了一次")
        for city in sorted(CITY_NAMES, key=len, reverse=True):
            if city in guided_text:
                return city
        if guided_text:
            return guided_text

    for city in sorted(CITY_NAMES, key=len, reverse=True):
        if city in query:
            return city
    match = re.search(r"(?:去|到|游|玩|规划)([\u4e00-\u9fff]{2,8})(?:\d|一|二|两|三|四|五|六|七|八|九|十|旅|游|玩|行程|攻略|路线|自由行|$)", query)
    if match:
        candidate = match.group(1).strip("的了一次")
        return candidate or None
    return None


def _extract_origin(query: str) -> Optional[str]:
    for pattern in [
        r"从([\u4e00-\u9fff]{2,8})出发",
        r"([\u4e00-\u9fff]{2,8})出发",
        r"出发地(?:是|为|在)?([\u4e00-\u9fff]{2,8})",
        r"我在([\u4e00-\u9fff]{2,8})",
        r"([\u4e00-\u9fff]{2,8})本地",
    ]:
        match = re.search(pattern, query)
        if not match:
            continue
        raw = match.group(1).strip("的从在")
        for city in sorted(CITY_NAMES, key=len, reverse=True):
            if city in raw:
                return city
        if raw:
            return raw
    return None


def _extract_date_range(query: str) -> Optional[str]:
    text = _safe_text(query)
    if not text:
        return None
    explicit_range = canonicalize_explicit_date_range(text)
    if explicit_range:
        return explicit_range
    date_patterns = [
        r"\d{1,2}月\d{1,2}日(?:到|至|-|~)\d{1,2}月?\d{1,2}日?",
        r"\d{1,2}月\d{1,2}日",
        r"(?:今天|明天|后天|本周末|周末|下周|五一|国庆|春节|暑假|寒假|时间未定|日期未定|时间还没定|日期还没定)",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def _extract_days(query: str) -> Optional[int]:
    match = re.search(
        r"(?<![0-9一二两三四五六七八九十月])([0-9一二两三四五六七八九十]{1,3})\s*(?:天[两二三四五六七八九十0-9]?夜|天|日|晚)",
        query,
    )
    if not match:
        return None
    return _number_from_text(match.group(1))


def _extract_people_count(query: str) -> Optional[int]:
    matches = re.findall(
        r"([0-9一二两三四五六七八九十]{1,3})\s*(?:(?:名|个|位)\s*)?(?:成人|儿童|孩子|老人|人)",
        query,
    )
    counts = [_number_from_text(value) for value in matches]
    normalized = [value for value in counts if value is not None]
    if normalized:
        return sum(normalized)
    generic_match = re.search(r"([0-9一二两三四五六七八九十]{1,3})\s*(?:个人|人|位)", query)
    return _number_from_text(generic_match.group(1)) if generic_match else None


def _extract_people_type(query: str) -> Optional[str]:
    pairs = [
        ("亲子", "亲子"),
        ("孩子", "亲子"),
        ("父母", "带老人"),
        ("老人", "带老人"),
        ("情侣", "情侣"),
        ("对象", "情侣"),
        ("朋友", "朋友"),
        ("闺蜜", "朋友"),
        ("独自", "独行"),
        ("一个人", "独行"),
        ("商务", "商务"),
    ]
    for keyword, value in pairs:
        if keyword in query:
            return value
    return None


def _extract_budget(query: str) -> Dict[str, Optional[float]]:
    per_person = None
    total = None
    per_match = re.search(r"(?:人均|每人)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|rmb|RMB)?", query)
    if per_match:
        per_person = float(per_match.group(1))
    total_match = re.search(r"(?:预算|总预算|控制在|不超过|以内)\D{0,6}([0-9]+(?:\.[0-9]+)?)\s*(?:元|块|rmb|RMB)?", query)
    if total_match:
        total = float(total_match.group(1))
    return {"budget_total": total, "budget_per_person": per_person}


def _extract_pace(query: str) -> Optional[str]:
    if re.search(r"(轻松|松弛|舒缓|慢游|休闲|不赶)", query):
        return "relaxed"
    if re.search(r"(特种兵|紧凑|多玩|尽量多|高强度|打卡)", query):
        return "intensive"
    if re.search(r"(适中|平衡|正常)", query):
        return "balanced"
    return None


def _extract_interests(query: str) -> List[str]:
    mapping = {
        "自然风光": ["自然", "山水", "海边", "草原", "湖"],
        "历史文化": ["历史", "文化", "古城", "博物馆", "寺", "故宫"],
        "美食": ["美食", "小吃", "吃"],
        "亲子": ["亲子", "孩子"],
        "拍照": ["拍照", "出片", "摄影"],
        "购物": ["购物", "商场"],
        "小众路线": ["小众", "避开人群"],
    }
    result = []
    for label, keywords in mapping.items():
        if any(keyword in query for keyword in keywords):
            result.append(label)
    return result


def normalize_user_travel_profile(profile: Optional[Any]) -> UserTravelProfile:
    if isinstance(profile, UserTravelProfile):
        model = profile
    elif isinstance(profile, dict):
        model = UserTravelProfile(**profile)
    else:
        model = UserTravelProfile()

    return UserTravelProfile(
        user_id=_safe_text(model.user_id) or "local",
        preferred_budget_level=_safe_text(model.preferred_budget_level) or None,
        default_people_type=_safe_text(model.default_people_type) or None,
        travel_style=_as_list(model.travel_style),
        dietary_preferences=_as_list(model.dietary_preferences),
        pace=_safe_text(model.pace) or None,
        hotel_preference=_safe_text(model.hotel_preference) or None,
        transport_preference=_safe_text(model.transport_preference) or None,
        disliked_items=_as_list(model.disliked_items),
        accessibility_needs=_as_list(model.accessibility_needs),
        updated_at=_safe_text(model.updated_at) or None,
    )


def _normalize_profile(profile: Optional[Any]) -> UserTravelProfile:
    return normalize_user_travel_profile(profile)


def _normalize_answer_map(answers: Optional[Any]) -> Dict[str, Any]:
    if not isinstance(answers, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for key, value in answers.items():
        field = _safe_text(key)
        if not field:
            continue
        normalized[field] = value
    return normalized


def _first(values: Iterable[Any]) -> Optional[str]:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return None


def _pace_from_any(value: Any) -> Optional[str]:
    text = _safe_text(value)
    if not text:
        return None
    if text in {"relaxed", "balanced", "intensive"}:
        return text
    if re.search(r"(轻松|松弛|舒缓|休闲|慢|2-3)", text):
        return "relaxed"
    if re.search(r"(紧凑|多玩|特种兵|打卡)", text):
        return "intensive"
    if re.search(r"(适中|平衡|标准)", text):
        return "balanced"
    return None


def _budget_from_answer(value: Any) -> Dict[str, Optional[float]]:
    text = _safe_text(value)
    result = _extract_budget(text)
    if result["budget_total"] is not None or result["budget_per_person"] is not None:
        return result
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return {"budget_total": None, "budget_per_person": None}
    amount = float(match.group(1))
    if "人均" in text or "每人" in text:
        return {"budget_total": None, "budget_per_person": amount}
    return {"budget_total": amount, "budget_per_person": None}


def _answer_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return _as_list(value)
    text = _safe_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,，、;/；]", text) if item.strip()]


def _apply_answer_values(intent: TripIntent, answers: Dict[str, Any], *, override: bool = False) -> None:
    def can_set(field: str) -> bool:
        value = getattr(intent, field)
        return override or value is None or value == []

    if "destination" in answers and can_set("destination"):
        intent.destination = _safe_text(answers["destination"]) or intent.destination
    if "origin" in answers and can_set("origin"):
        origin = _safe_text(answers["origin"])
        if origin in {"目的地本地出发", "本地出发"} and intent.destination:
            origin = intent.destination
        intent.origin = _extract_origin(origin) or origin or intent.origin
    if "days" in answers and can_set("days"):
        raw_days = answers.get("days")
        parsed_days = _number_from_text(_safe_text(raw_days)) or _extract_days(_safe_text(raw_days))
        if parsed_days:
            intent.days = parsed_days
    if "date_range" in answers and can_set("date_range"):
        raw_date_range = _safe_text(answers.get("date_range"))
        intent.date_range = (
            canonicalize_explicit_date_range(raw_date_range)
            or raw_date_range
            or intent.date_range
        )
    if "people_count" in answers and can_set("people_count"):
        raw_people = _safe_text(answers.get("people_count", ""))
        intent.people_count = _number_from_text(raw_people) or _extract_people_count(raw_people) or intent.people_count
    if "people_type" in answers and can_set("people_type"):
        raw_people_type = _safe_text(answers.get("people_type"))
        intent.people_type = raw_people_type or _extract_people_type(raw_people_type) or intent.people_type
    if "budget_total" in answers or "budget_per_person" in answers:
        raw_budget = answers.get("budget_total", answers.get("budget_per_person"))
        parsed_budget = _budget_from_answer(raw_budget)
        if can_set("budget_total"):
            intent.budget_total = parsed_budget["budget_total"] or intent.budget_total
        if can_set("budget_per_person"):
            intent.budget_per_person = parsed_budget["budget_per_person"] or intent.budget_per_person
    if "pace" in answers and can_set("pace"):
        intent.pace = _pace_from_any(answers["pace"]) or intent.pace
    if "travel_style" in answers and can_set("travel_style"):
        intent.travel_style = _safe_text(answers["travel_style"]) or intent.travel_style
    if "hotel_preference" in answers and can_set("hotel_preference"):
        intent.hotel_preference = _safe_text(answers["hotel_preference"]) or intent.hotel_preference
    if "transport_preference" in answers and can_set("transport_preference"):
        intent.transport_preference = _safe_text(answers["transport_preference"]) or intent.transport_preference
    if "output_preference" in answers and can_set("output_preference"):
        intent.output_preference = _safe_text(answers["output_preference"]) or intent.output_preference

    for field in ["interests", "dietary_preferences", "must_visit", "avoid"]:
        if field in answers and can_set(field):
            setattr(intent, field, _answer_list(answers[field]))


def merge_semantic_trip_analysis(
    intent: TripIntent,
    analysis: Optional[Dict[str, Any]],
    evidence_text: str,
) -> Tuple[TripIntent, Set[str], Optional[ClarificationQuestion]]:
    if not isinstance(analysis, dict):
        return intent, set(), None

    patch = analysis.get("intent_patch")
    evidence = analysis.get("evidence")
    if not isinstance(patch, dict) or not isinstance(evidence, dict):
        patch = {}
        evidence = {}

    source = _safe_text(evidence_text)
    accepted: Dict[str, Any] = {}
    explicit_fields: Set[str] = set()
    allowed_fields = set(TripIntent.model_fields)
    for field, value in patch.items():
        if field not in allowed_fields or field == "confidence" or value in (None, "", []):
            continue
        quote = _safe_text(evidence.get(field))
        if not quote or quote not in source:
            continue
        accepted[field] = value
        explicit_fields.add(field)

    if accepted:
        _apply_answer_values(intent, accepted, override=True)

    preferred_question = None
    raw_question = analysis.get("next_question")
    if isinstance(raw_question, dict):
        field = _safe_text(raw_question.get("field"))
        options = _as_list(raw_question.get("options"))[:4]
        question_text = _safe_text(raw_question.get("question"))
        reason = _safe_text(raw_question.get("reason"))
        if field in allowed_fields and field != "confidence" and question_text and reason and len(options) >= 2:
            preferred_question = ClarificationQuestion(
                id=f"q_{field}",
                field=field,
                question=question_text,
                reason=reason,
                options=options,
                allow_custom=bool(raw_question.get("allow_custom", True)),
            )

    return intent, explicit_fields, preferred_question


def extract_trip_intent(
    query: str,
    profile: Optional[Any] = None,
    clarification_answers: Optional[Any] = None,
) -> TripIntent:
    text = _safe_text(query)
    profile_model = _normalize_profile(profile)
    answers = _normalize_answer_map(clarification_answers)
    budget = _extract_budget(text)

    intent = TripIntent(
        origin=_extract_origin(text),
        destination=_extract_destination(text),
        date_range=_extract_date_range(text),
        days=_extract_days(text),
        people_count=_extract_people_count(text),
        people_type=_extract_people_type(text),
        budget_total=budget["budget_total"],
        budget_per_person=budget["budget_per_person"],
        pace=_extract_pace(text),
        interests=_extract_interests(text),
    )

    _apply_answer_values(intent, answers)

    if not intent.people_type:
        intent.people_type = profile_model.default_people_type
    if not intent.pace:
        intent.pace = _pace_from_any(profile_model.pace)
    if not intent.travel_style:
        intent.travel_style = _first(profile_model.travel_style)
    if not intent.dietary_preferences:
        intent.dietary_preferences = list(profile_model.dietary_preferences)
    if not intent.hotel_preference:
        intent.hotel_preference = profile_model.hotel_preference
    if not intent.transport_preference:
        intent.transport_preference = profile_model.transport_preference
    if not intent.avoid:
        intent.avoid = list(profile_model.disliked_items)

    filled = sum(
        1
        for value in [
            intent.destination,
            intent.origin,
            intent.days or intent.date_range,
            intent.people_count or intent.people_type,
            intent.budget_total or intent.budget_per_person or profile_model.preferred_budget_level,
            intent.pace or intent.travel_style,
        ]
        if value
    )
    intent.confidence = round(filled / 6, 2)
    return intent


def build_trip_context_message(intent: TripIntent, profile: Optional[Any], answers: Optional[Any]) -> str:
    profile_model = _normalize_profile(profile)
    lines = [
        "旅行规划结构化上下文：",
        f"- 出发地: {intent.origin or '未明确'}",
        f"- 目的地: {intent.destination or '未明确'}",
        f"- 天数/日期: {intent.days or intent.date_range or '未明确'}",
        f"- 人数/同行类型: {intent.people_count or '未明确'} / {intent.people_type or '未明确'}",
        f"- 预算: 总预算 {intent.budget_total or '未明确'}，人均 {intent.budget_per_person or '未明确'}",
        f"- 节奏: {intent.pace or '未明确'}",
        f"- 旅行风格: {intent.travel_style or '未明确'}",
        f"- 兴趣: {', '.join(intent.interests) if intent.interests else '未明确'}",
        f"- 饮食偏好: {', '.join(intent.dietary_preferences) if intent.dietary_preferences else '未明确'}",
        f"- 住宿偏好: {intent.hotel_preference or '未明确'}",
        f"- 交通偏好: {intent.transport_preference or '未明确'}",
        f"- 避免事项: {', '.join(intent.avoid) if intent.avoid else '未明确'}",
        f"- 无障碍需求: {', '.join(profile_model.accessibility_needs) if profile_model.accessibility_needs else '未明确'}",
    ]
    if profile_model.preferred_budget_level:
        lines.append(f"- 用户画像预算档位: {profile_model.preferred_budget_level}")
    if answers:
        answer_pairs = [f"{field}={_safe_text(value)}" for field, value in _normalize_answer_map(answers).items()]
        lines.append(f"- 本轮澄清答案: {'；'.join(answer_pairs)}")
        lines.append("- 本轮澄清答案优先级高于用户画像默认值。")
    lines.append("请优先遵循用户本轮明确输入，其次遵循澄清答案，最后参考用户画像。")
    return "\n".join(lines)
