import re
from typing import Iterable, List, Optional, Set

from schemas.trip_models import ClarificationQuestion, TripIntent, UserTravelProfile
from services.trip_intent_service import is_trip_planning_query


MAX_CLARIFICATION_QUESTIONS = 4

_FIELD_EQUIVALENCE = {
    "people_count": "people",
    "people_type": "people",
    "budget_total": "budget",
    "budget_per_person": "budget",
    "pace": "travel_style",
    "travel_style": "travel_style",
}
_SUPPORTED_CLARIFICATION_FIELDS = frozenset(
    field for field in TripIntent.model_fields if field != "confidence"
)


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_style(intent: TripIntent) -> bool:
    return bool(intent.pace or intent.travel_style)


def _mentions_people(query: str) -> bool:
    return bool(
        re.search(
            r"([0-9一二两三四五六七八九十]\s*(?:个人|人|位)|[一二两三四五六七八九十0-9]大[一二两三四五六七八九十0-9]小|"
            r"亲子|孩子|小孩|父母|老人|长辈|情侣|对象|夫妻|朋友|同学|同事|闺蜜|独自|一个人|单人|商务)",
            query,
        )
    )


def _mentions_budget(query: str) -> bool:
    return bool(
        re.search(
            r"(预算|人均|每人|总预算|控制在|不超过|以内|不设预算|预算不限|丰俭由人|都可以|[0-9]+(?:\.[0-9]+)?\s*(?:元|块|rmb|RMB))",
            query,
        )
    )


def _mentions_date(query: str) -> bool:
    return bool(
        re.search(
            r"(\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{1,2}月\d{1,2}日|今天|明天|后天|本周末|周末|下周|五一|国庆|春节|暑假|寒假|时间未定|日期未定|时间还没定|日期还没定)",
            query,
        )
    )


def _mentions_origin(query: str) -> bool:
    return bool(re.search(r"(从[\u4e00-\u9fff]{2,8}出发|[\u4e00-\u9fff]{2,8}出发|出发地|我在[\u4e00-\u9fff]{2,8}|[\u4e00-\u9fff]{2,8}本地)", query))


def _date_range_implies_duration(value: Optional[str]) -> bool:
    text = _safe_text(value)
    return bool(re.search(r"(到|至|-|~)", text))


def canonical_clarification_field(value: object) -> str:
    field = _safe_text(value)
    return _FIELD_EQUIVALENCE.get(field, field)


def normalize_clarification_fields(values: Optional[Iterable[str]]) -> Set[str]:
    if values is None:
        return set()
    normalized: Set[str] = set()
    for value in values:
        field = _safe_text(value)
        if field not in _SUPPORTED_CLARIFICATION_FIELDS:
            continue
        normalized.add(canonical_clarification_field(field))
    return normalized


def count_clarification_fields(values: Optional[Iterable[str]]) -> int:
    return len(normalize_clarification_fields(values))


def _normalize_fields(values: Optional[Iterable[str]]) -> Set[str]:
    return normalize_clarification_fields(values)


def _field_is_answered(answered_fields: Set[str], *fields: str) -> bool:
    return any(canonical_clarification_field(field) in answered_fields for field in fields)


def _profile_default_value(profile: Optional[UserTravelProfile], field: str) -> Optional[str]:
    if profile is None:
        return None
    canonical_field = canonical_clarification_field(field)
    if canonical_field == "people":
        return _safe_text(profile.default_people_type) or None
    if canonical_field == "budget":
        return _safe_text(profile.preferred_budget_level) or None
    if canonical_field == "travel_style":
        return _safe_text(profile.pace) or None
    if field == "dietary_preferences":
        return "、".join(item for item in profile.dietary_preferences if _safe_text(item)) or None
    if field == "hotel_preference":
        return _safe_text(profile.hotel_preference) or None
    if field == "transport_preference":
        return _safe_text(profile.transport_preference) or None
    return None


def _attach_profile_default(
    question: ClarificationQuestion,
    profile: Optional[UserTravelProfile],
) -> ClarificationQuestion:
    return question.model_copy(
        update={"profile_default_value": _profile_default_value(profile, question.field)}
    )


def _query_focuses_on_food(query: str) -> bool:
    return bool(re.search(r"(美食|餐厅|小吃|探店|夜市|吃什么|咖啡|甜品)", query))


def _query_focuses_on_hotel(query: str) -> bool:
    return bool(re.search(r"(酒店|住宿|民宿|客栈|住哪|入住)", query))


def _query_focuses_on_transport(query: str) -> bool:
    return bool(re.search(r"(自驾|公共交通|地铁|打车|交通|通勤|高铁|飞机|航班)", query))


def _replace_with_preferred_question(
    questions: List[ClarificationQuestion],
    preferred_question: Optional[ClarificationQuestion],
) -> List[ClarificationQuestion]:
    if preferred_question is None:
        return questions

    def fields_match(left: str, right: str) -> bool:
        return canonical_clarification_field(left) == canonical_clarification_field(right)

    for index, question in enumerate(questions):
        if not fields_match(question.field, preferred_question.field):
            continue
        options = [option for option in preferred_question.options if _safe_text(option)][:4]
        if len(options) < 2:
            options = question.options
        replacement = ClarificationQuestion(
            id=f"q_{preferred_question.field}",
            field=preferred_question.field,
            question=_safe_text(preferred_question.question) or question.question,
            reason=_safe_text(preferred_question.reason) or question.reason,
            options=options,
            allow_custom=preferred_question.allow_custom,
            profile_default_value=question.profile_default_value,
        )
        return [replacement, *questions[:index], *questions[index + 1 :]]
    return questions


def build_clarification_questions(
    query: str,
    intent: TripIntent,
    profile: Optional[UserTravelProfile] = None,
    max_questions: int = MAX_CLARIFICATION_QUESTIONS,
    answered_fields: Optional[Iterable[str]] = None,
    explicit_fields: Optional[Iterable[str]] = None,
    preferred_question: Optional[ClarificationQuestion] = None,
) -> List[ClarificationQuestion]:
    if not is_trip_planning_query(query):
        return []

    questions: List[ClarificationQuestion] = []
    query_text = _safe_text(query)
    answered = _normalize_fields(answered_fields)
    explicit = _normalize_fields(explicit_fields)

    if not intent.destination and not _field_is_answered(answered, "destination"):
        questions.append(
            ClarificationQuestion(
                id="q_destination",
                field="destination",
                question="这次主要想去哪个目的地？",
                reason="目的地决定路线、交通、住宿区域和景点组合。",
                options=["国内城市游", "周边短途", "自然风光目的地", "还没想好"],
            )
        )

    if (
        not intent.origin
        and not _mentions_origin(query_text)
        and "origin" not in explicit
        and not _field_is_answered(answered, "origin")
    ):
        local_option = f"{intent.destination}本地出发" if intent.destination else "目的地本地出发"
        questions.append(
            ClarificationQuestion(
                id="q_origin",
                field="origin",
                question="你从哪里出发？",
                reason="出发地会影响往返交通、第一天到达时间和整体预算。",
                options=[local_option, "出发地还没定"],
            )
        )

    if (
        not intent.date_range
        and not _mentions_date(query_text)
        and "date_range" not in explicit
        and not _field_is_answered(answered, "date_range")
    ):
        questions.append(
            ClarificationQuestion(
                id="q_date_range",
                field="date_range",
                question="大概什么时候出行？",
                reason="日期会影响天气、开放时间、票务紧张程度和节假日拥挤度。",
                options=["本周末", "下周", "五一/国庆假期", "日期还没定"],
            )
        )

    if (
        not intent.days
        and not _date_range_implies_duration(intent.date_range)
        and "days" not in explicit
        and not _field_is_answered(answered, "days")
    ):
        questions.append(
            ClarificationQuestion(
                id="q_days",
                field="days",
                question="计划玩几天？",
                reason="天数会影响每天安排密度和是否需要近郊路线。",
                options=["1天", "2天", "3天", "5天及以上"],
            )
        )

    if (
        not (intent.people_count or intent.people_type)
        and not _mentions_people(query_text)
        and not _field_is_answered(answered, "people_count", "people_type")
        and not _field_is_answered(explicit, "people_count", "people_type")
    ):
        questions.append(
            ClarificationQuestion(
                id="q_people",
                field="people_type",
                question="这次和谁一起出行？",
                reason="同行人会影响酒店、餐厅、交通强度和景点选择。",
                options=["独自出行", "情侣", "朋友", "亲子/带老人"],
            )
        )

    if (
        not (intent.budget_total or intent.budget_per_person)
        and not _mentions_budget(query_text)
        and not _field_is_answered(answered, "budget_total", "budget_per_person")
        and not _field_is_answered(explicit, "budget_total", "budget_per_person")
    ):
        budget_options = ["人均1000以内", "人均1000-2500", "人均2500-5000", "不设严格预算"]
        questions.append(
            ClarificationQuestion(
                id="q_budget",
                field="budget_total",
                question="这次旅行大概预算是多少？",
                reason="预算会影响酒店档位、交通方式和餐厅选择。",
                options=budget_options,
            )
        )

    if (
        not _has_style(intent)
        and not _field_is_answered(answered, "pace", "travel_style")
        and not _field_is_answered(explicit, "pace", "travel_style")
    ):
        questions.append(
            ClarificationQuestion(
                id="q_pace",
                field="pace",
                question="希望旅行节奏怎样？",
                reason="节奏会影响每天安排几个景点和通勤强度。",
                options=["轻松休闲", "适中平衡", "尽量多玩"],
            )
        )

    if (
        _query_focuses_on_food(query_text)
        and not intent.dietary_preferences
        and not _field_is_answered(answered, "dietary_preferences")
        and "dietary_preferences" not in explicit
    ):
        questions.append(
            ClarificationQuestion(
                id="q_dietary_preferences",
                field="dietary_preferences",
                question="饮食方面有什么需要优先照顾的吗？",
                reason="美食是这次规划重点，口味和饮食限制会直接影响餐厅选择。",
                options=["没有特别限制", "少辣/清淡", "素食", "清真"],
            )
        )

    if (
        _query_focuses_on_hotel(query_text)
        and not intent.hotel_preference
        and not _field_is_answered(answered, "hotel_preference")
        and "hotel_preference" not in explicit
    ):
        questions.append(
            ClarificationQuestion(
                id="q_hotel_preference",
                field="hotel_preference",
                question="住宿最看重哪一点？",
                reason="住宿位置和档位会影响每天路线、通勤和整体预算。",
                options=["交通方便", "靠近核心景点", "安静舒适", "性价比优先"],
            )
        )

    if (
        _query_focuses_on_transport(query_text)
        and not intent.transport_preference
        and not _field_is_answered(answered, "transport_preference")
        and "transport_preference" not in explicit
    ):
        questions.append(
            ClarificationQuestion(
                id="q_transport_preference",
                field="transport_preference",
                question="行程内更偏好哪种交通方式？",
                reason="交通偏好会改变景点组合、通勤时间和预算分配。",
                options=["公共交通优先", "打车优先", "自驾", "都可以"],
            )
        )

    questions = [_attach_profile_default(question, profile) for question in questions]
    questions = _replace_with_preferred_question(questions, preferred_question)
    return questions[: min(1, max_questions)] if max_questions > 0 else []


def build_next_clarification_question(
    query: str,
    intent: TripIntent,
    profile: Optional[UserTravelProfile] = None,
    answered_fields: Optional[Iterable[str]] = None,
    explicit_fields: Optional[Iterable[str]] = None,
    preferred_question: Optional[ClarificationQuestion] = None,
) -> Optional[ClarificationQuestion]:
    questions = build_clarification_questions(
        query=query,
        intent=intent,
        profile=profile,
        max_questions=MAX_CLARIFICATION_QUESTIONS,
        answered_fields=answered_fields,
        explicit_fields=explicit_fields,
        preferred_question=preferred_question,
    )
    return questions[0] if questions else None
