"""Shared helpers for extracting normalized ticket-query routes."""

import re
from typing import Optional, Tuple


_ROUTE_TOKEN = r"[A-Za-z\u4e00-\u9fa5]{1,20}"
_ROUTE_SEPARATOR = r"(?:到|至|->|→|-|—)"

_REQUEST_PREFIX_REGEX = re.compile(
    r"^(?:请)?(?:给我|帮我|为我)?(?:查一下|查询一下|查一查|查查|查询|搜索|查)\s*"
)
_DATE_REGEX = re.compile(
    r"(今天|明天|后天|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日?)"
)
_TICKET_WORDING_REGEX = re.compile(
    r"(高铁票|动车票|火车票|机票|飞机票|汽车票|大巴票|车票|票务|订票|买票|查票|票|"
    r"高铁|动车|列车|车次|余票|票价|航班|飞机|机场|大巴|客车|长途汽车|"
    r"查询|实时|官网)"
)
_TRANSPORT_WORDING_REGEX = re.compile(
    r"(最佳交通方案|最佳交通|交通方案|交通方式|出行方案|路线方案|最佳路线|"
    r"怎么去|前往|交通|路线|路程|方案|推荐|最快|最便宜|便宜|省钱)"
)
_TRAVEL_WORDING_REGEX = re.compile(r"(旅游|出差|探亲|玩|游玩|旅行|行程)")


def clean_route_name(value: str) -> str:
    """Remove request, date, and ticket wording from a route endpoint."""
    text = (value or "").strip()
    text = _REQUEST_PREFIX_REGEX.sub("", text)
    text = re.sub(r"^从\s*", "", text)
    text = _DATE_REGEX.sub("", text)
    text = _TICKET_WORDING_REGEX.sub("", text)
    text = _TRANSPORT_WORDING_REGEX.sub("", text)
    text = _TRAVEL_WORDING_REGEX.sub("", text)
    text = text.strip("的")
    return text.strip(" ,，。！!？?；;:")


def _clean_query_for_bare_route(query: str) -> str:
    text = (query or "").strip()
    text = _REQUEST_PREFIX_REGEX.sub("", text)
    text = _DATE_REGEX.sub("", text)
    return text.strip()


def _build_route(from_value: str, to_value: str) -> Optional[Tuple[str, str]]:
    from_station = clean_route_name(from_value)
    to_station = clean_route_name(to_value)
    if from_station and to_station and from_station != to_station:
        return from_station, to_station
    return None


def extract_route_from_query(query: str) -> Optional[Tuple[str, str]]:
    """Extract a normalized origin and destination from a natural-language query."""
    text = (query or "").strip()
    if not text:
        return None

    explicit_match = re.search(
        rf"从\s*({_ROUTE_TOKEN})\s*{_ROUTE_SEPARATOR}\s*({_ROUTE_TOKEN})",
        text,
    )
    if explicit_match:
        route = _build_route(explicit_match.group(1), explicit_match.group(2))
        if route:
            return route

    from_match = re.search(rf"从\s*({_ROUTE_TOKEN})\s*(?:出发|启程|动身)", text)
    to_match = re.search(rf"(?:到|去|前往)\s*({_ROUTE_TOKEN})", text)
    if from_match and to_match:
        route = _build_route(from_match.group(1), to_match.group(1))
        if route:
            return route

    reverse_match = re.search(
        rf"(?:去|到|前往)\s*({_ROUTE_TOKEN}).{{0,30}}?从\s*({_ROUTE_TOKEN})\s*(?:出发|启程|动身)",
        text,
    )
    if reverse_match:
        route = _build_route(reverse_match.group(2), reverse_match.group(1))
        if route:
            return route

    bare_text = _clean_query_for_bare_route(text)
    bare_match = re.search(
        rf"({_ROUTE_TOKEN})\s*{_ROUTE_SEPARATOR}\s*({_ROUTE_TOKEN})",
        bare_text,
    )
    if bare_match:
        return _build_route(bare_match.group(1), bare_match.group(2))

    return None
