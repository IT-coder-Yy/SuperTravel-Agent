from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, List, Literal, Optional, Tuple


FLEXIBLE_DATE_RANGE_VALUE = "日期暂未确定"
_FLEXIBLE_DATE_PATTERN = re.compile(
    r"(?:日期|时间)(?:暂时|暂|还)?(?:未确定|没确定|没定)|日期还没定|时间还没定|__flexible__"
)


_EXPLICIT_DATE_PATTERN = re.compile(
    r"(?:(?P<year>\d{4})\s*(?:年|[-/.])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:月|[-/.])\s*"
    r"(?P<day>\d{1,2})\s*日?"
)


class DateRangeResolutionError(ValueError):
    field = "intent.date_range"


def explicit_dates(value: Any) -> List[date]:
    """Extract explicit calendar dates and inherit an omitted end year."""
    text = str(value or "")
    matches = list(_EXPLICIT_DATE_PATTERN.finditer(text))
    if not matches:
        return []
    first_explicit_year = next(
        (int(match.group("year")) for match in matches if match.group("year")),
        None,
    )
    if first_explicit_year is None:
        return []

    dates: List[date] = []
    for match in matches:
        has_explicit_year = bool(match.group("year"))
        year = int(match.group("year")) if has_explicit_year else dates[-1].year if dates else first_explicit_year
        month = int(match.group("month"))
        day = int(match.group("day"))
        try:
            parsed = date(year, month, day)
            if dates and not has_explicit_year and parsed < dates[-1]:
                parsed = date(year + 1, month, day)
        except ValueError:
            return []
        dates.append(parsed)

    # 中文日期区间经常省略结束日期的年份和月份，例如
    # “2026年8月15日至17日”。通用日期正则只能识别前半段，
    # 因此仅在明确的区间连接符后继承开始日期的年月。
    if len(dates) == 1:
        suffix = text[matches[0].end():]
        abbreviated_end = re.match(
            r"\s*(?:到|至|~|—|–)\s*"
            r"(?:(?P<year>\d{4})\s*年\s*)?"
            r"(?:(?P<month>\d{1,2})\s*月\s*)?"
            r"(?P<day>\d{1,2})\s*日?",
            suffix,
        )
        if abbreviated_end:
            first = dates[0]
            year = int(abbreviated_end.group("year") or first.year)
            month = int(abbreviated_end.group("month") or first.month)
            day = int(abbreviated_end.group("day"))
            try:
                parsed_end = date(year, month, day)
                if parsed_end < first and not abbreviated_end.group("year"):
                    parsed_end = date(year + 1, month, day)
            except ValueError:
                return []
            dates.append(parsed_end)
    return dates


def canonicalize_explicit_date_range(value: Any) -> Optional[str]:
    dates = explicit_dates(value)
    if not dates:
        return None
    if len(dates) == 1:
        return dates[0].isoformat()
    return f"{dates[0].isoformat()} 至 {dates[1].isoformat()}"


def is_flexible_date_range(value: Any) -> bool:
    return bool(_FLEXIBLE_DATE_PATTERN.search(str(value or "").strip()))


def canonicalize_planning_date_range(value: Any, requested_days: Any = None) -> Optional[str]:
    if is_flexible_date_range(value):
        return FLEXIBLE_DATE_RANGE_VALUE
    try:
        start, end, _ = resolve_date_range(value, requested_days)
    except DateRangeResolutionError:
        return None
    return f"{start.isoformat()} 至 {end.isoformat()}"


def resolve_planning_date_contract(
    value: Any,
    requested_days: Any = None,
) -> Tuple[Literal["fixed", "flexible"], Optional[date], Optional[date], int]:
    if is_flexible_date_range(value):
        try:
            days = int(requested_days)
        except (TypeError, ValueError) as exc:
            raise DateRangeResolutionError("日期待定行程必须明确天数") from exc
        if not 1 <= days <= 7:
            raise DateRangeResolutionError("行程天数必须在 1 至 7 天之间")
        return "flexible", None, None, days
    start, end, days = resolve_date_range(value, requested_days)
    return "fixed", start, end, days


def build_clarification_date_options(
    requested_days: Any,
    reference_date: Optional[date] = None,
) -> List[str]:
    try:
        days = max(1, min(int(requested_days or 1), 7))
    except (TypeError, ValueError):
        days = 1

    today = reference_date or date.today()
    days_until_monday = (7 - today.weekday()) % 7
    next_monday = today + timedelta(days=days_until_monday or 7)
    starts = [today, next_monday, next_monday + timedelta(days=7)]
    labels = ["近期", "下周", "再下一周"]
    options: List[str] = []
    seen_ranges = set()
    for start, label in zip(starts, labels):
        end = start + timedelta(days=days - 1)
        date_range = (start, end)
        if date_range in seen_ranges:
            continue
        seen_ranges.add(date_range)
        options.append(
            f"{start.year}年{start.month}月{start.day}日至"
            f"{end.year}年{end.month}月{end.day}日（{label}）"
        )
    return [*options[:3], FLEXIBLE_DATE_RANGE_VALUE]


def resolve_date_range(value: Any, requested_days: Any = None) -> Tuple[date, date, int]:
    dates = explicit_dates(value)
    if not dates:
        raise DateRangeResolutionError("V2 行程缺少明确日期，不能迁移为正式 V3 文档")

    start = dates[0]
    try:
        days_hint = int(requested_days or 0)
    except (TypeError, ValueError):
        days_hint = 0
    end = dates[1] if len(dates) >= 2 else start + timedelta(days=max(days_hint, 1) - 1)
    days = (end - start).days + 1
    if days < 1 or days > 7:
        raise DateRangeResolutionError("V2 行程日期必须位于 1～7 天范围内")
    return start, end, days
