from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, List, Optional, Tuple


_EXPLICIT_DATE_PATTERN = re.compile(
    r"(?:(?P<year>\d{4})\s*(?:年|[-/.])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:月|[-/.])\s*"
    r"(?P<day>\d{1,2})\s*日?"
)


class DateRangeResolutionError(ValueError):
    field = "intent.date_range"


def explicit_dates(value: Any) -> List[date]:
    """Extract explicit calendar dates and inherit an omitted end year."""
    matches = list(_EXPLICIT_DATE_PATTERN.finditer(str(value or "")))
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
    return dates


def canonicalize_explicit_date_range(value: Any) -> Optional[str]:
    dates = explicit_dates(value)
    if not dates:
        return None
    if len(dates) == 1:
        return dates[0].isoformat()
    return f"{dates[0].isoformat()} 至 {dates[1].isoformat()}"


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
