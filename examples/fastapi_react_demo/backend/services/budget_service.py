"""Deterministic, conservative budget calculation for formal travel plans."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional

try:
    from services.exchange_rate_service import quote_currency_to_cny
except ModuleNotFoundError:  # Package import used by focused tests.
    from backend.services.exchange_rate_service import quote_currency_to_cny


_TRAVELER_TYPES = ("adult", "child", "senior")
_DEFAULT_PER_TRAVELER_CNY = {
    "food": 80.0,
    "transport": 30.0,
    "activities": 60.0,
}
_CATEGORY_MAP = {
    "hotel": "lodging",
    "lodging": "lodging",
    "住宿": "lodging",
    "餐厅": "food",
    "美食": "food",
    "food": "food",
    "交通": "transport",
    "车站": "transport",
    "机场": "transport",
    "transport": "transport",
    "景点": "activities",
    "博物馆": "activities",
    "公园": "activities",
    "attraction": "activities",
    "museum": "activities",
    "购物": "shopping",
    "shopping": "shopping",
}
_SOURCE_STATUS = {
    "confirmed_live_data": "realtime_verified",
    "reference_data": "map_reference",
    "estimated_data": "user_confirmation_required",
}


def _number(value: Any) -> Optional[float]:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _currency(value: Any, default: str = "CNY") -> str:
    normalized = _text(value).upper()
    return normalized if len(normalized) == 3 else default


def _traveler_counts(intent: Any) -> Dict[str, int]:
    explicit = {
        "adult": _number(getattr(intent, "adult_count", None)),
        "child": _number(getattr(intent, "child_count", None)),
        "senior": _number(getattr(intent, "senior_count", None)),
    }
    if any(value is not None for value in explicit.values()):
        counts = {key: int(value or 0) for key, value in explicit.items()}
        if sum(counts.values()) > 0:
            return counts
    return {"adult": max(int(_number(getattr(intent, "people_count", None)) or 1), 1), "child": 0, "senior": 0}


def _category(activity: Any) -> str:
    direct = _text(getattr(activity, "activity_type", "")).lower()
    if direct in _CATEGORY_MAP:
        return _CATEGORY_MAP[direct]
    place = getattr(activity, "place", None)
    place_category = _text(getattr(place, "category", "")).lower()
    for keyword, category in _CATEGORY_MAP.items():
        if keyword in direct or keyword in place_category:
            return category
    return "other"


def _money_payload(
    amount: float,
    currency: str,
    *,
    cny_reference_amount: Optional[float],
    exchange_rate_as_of: Optional[str],
    source_status: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "amount": round(amount, 2),
        "currency": currency,
        "source_status": source_status,
    }
    if cny_reference_amount is not None:
        result["cny_reference_amount"] = round(cny_reference_amount, 2)
    if exchange_rate_as_of:
        result["exchange_rate_as_of"] = exchange_rate_as_of
    return result


def _cny_amount(amount: float, currency: str, cny_reference_amount: Optional[float]) -> Optional[float]:
    if currency == "CNY":
        return amount
    return cny_reference_amount


def _scaled_cny_reference(
    amount: float,
    base_amount: float,
    base_cny_reference_amount: Optional[float],
) -> Optional[float]:
    if base_amount <= 0 or base_cny_reference_amount is None:
        return None
    return base_cny_reference_amount * amount / base_amount


def _official_prices(activity: Any) -> Mapping[str, Any]:
    value = getattr(activity, "official_traveler_prices", None)
    return value if isinstance(value, Mapping) else {}


def apply_reference_costs(
    activities: Iterable[Any],
    *,
    destination_currency: str,
) -> List[str]:
    """Fill missing conservative activity estimates in destination currency.

    Default planning amounts are maintained in CNY, then converted with an
    official ECB dated reference.  This mutates only missing estimated fields;
    provider-supplied prices are never overwritten.
    """

    currency = _currency(destination_currency)
    quote = quote_currency_to_cny(currency) if currency != "CNY" else None
    if currency != "CNY" and quote is None:
        return [f"{currency} 汇率参考暂不可用，未将人民币估算伪装为目的地原币价格"]
    changed = 0
    for activity in activities:
        if _number(getattr(activity, "estimated_cost", None)) is not None:
            continue
        base_cny = _DEFAULT_PER_TRAVELER_CNY.get(_category(activity))
        if base_cny is None:
            continue
        if currency == "CNY":
            amount = base_cny
            cny_reference = None
            as_of = None
        else:
            assert quote is not None
            raw_amount = base_cny / quote.cny_per_unit
            amount = round(raw_amount) if currency in {"JPY", "KRW"} else round(raw_amount, 2)
            cny_reference = base_cny
            as_of = quote.as_of.isoformat()
        setattr(activity, "estimated_cost", amount)
        setattr(activity, "estimated_cost_currency", currency)
        setattr(activity, "estimated_cost_cny_reference_amount", cny_reference)
        setattr(activity, "estimated_cost_exchange_rate_as_of", as_of)
        setattr(activity, "estimated_cost_unit", "per_traveler")
        changed += 1
    if currency != "CNY" and changed:
        assert quote is not None
        return [
            f"{currency}/CNY 参考换算采用欧洲央行 {quote.as_of.isoformat()} 每日参考汇率；仅用于预算估算，不用于交易。"
        ]
    return []


def _aggregate_category_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregated: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    order: List[tuple[str, str, str, str]] = []
    for entry in entries:
        amount = entry.get("amount") if isinstance(entry.get("amount"), dict) else {}
        key = (
            _text(entry.get("category")) or "other",
            _currency(amount.get("currency")),
            _text(amount.get("exchange_rate_as_of")),
            _text(amount.get("source_status")) or "user_confirmation_required",
        )
        if key not in aggregated:
            aggregated[key] = {
                "amount": 0.0,
                "cny_reference_amount": 0.0,
                "has_cny_reference": True,
            }
            order.append(key)
        bucket = aggregated[key]
        bucket["amount"] += _number(amount.get("amount")) or 0.0
        cny_reference = _number(amount.get("cny_reference_amount"))
        if cny_reference is None:
            bucket["has_cny_reference"] = False
        else:
            bucket["cny_reference_amount"] += cny_reference
    result: List[Dict[str, Any]] = []
    for category, currency, as_of, source_status in order:
        bucket = aggregated[(category, currency, as_of, source_status)]
        result.append({
            "category": category,
            "amount": _money_payload(
                bucket["amount"],
                currency,
                cny_reference_amount=(
                    bucket["cny_reference_amount"]
                    if bucket["has_cny_reference"]
                    else None
                ),
                exchange_rate_as_of=as_of or None,
                source_status=source_status,
            ),
        })
    return result


def _add_per_traveler_cost(
    *,
    base_amount: float,
    currency: str,
    cny_reference_amount: Optional[float],
    exchange_rate_as_of: Optional[str],
    source_status: str,
    counts: Mapping[str, int],
    official_prices: Mapping[str, Any],
    category: str,
    category_entries: List[Dict[str, Any]],
    category_cny_totals: Dict[str, float],
    traveler_cny_totals: Dict[str, float],
    traveler_statuses: Dict[str, str],
    warnings: List[str],
) -> None:
    for traveler_type in _TRAVELER_TYPES:
        count = counts[traveler_type]
        if count <= 0:
            continue
        official_price = _number(official_prices.get(traveler_type))
        unit_price = official_price if official_price is not None else base_amount
        total_amount = unit_price * count
        total_cny_reference = _scaled_cny_reference(
            total_amount,
            base_amount,
            cny_reference_amount,
        )
        category_entries.append({
            "category": category,
            "amount": _money_payload(
                total_amount,
                currency,
                cny_reference_amount=total_cny_reference,
                exchange_rate_as_of=exchange_rate_as_of,
                source_status=source_status,
            ),
        })
        known_cny = _cny_amount(total_amount, currency, total_cny_reference)
        if known_cny is not None:
            category_cny_totals[category] += known_cny
            traveler_cny_totals[traveler_type] += known_cny
        if traveler_type == "adult":
            traveler_statuses[traveler_type] = "standard_price"
        elif official_price is not None:
            if traveler_statuses[traveler_type] != "adult_price_assumed":
                traveler_statuses[traveler_type] = "official_discount_verified"
        else:
            traveler_statuses[traveler_type] = "adult_price_assumed"
            message = f"{('儿童' if traveler_type == 'child' else '老人')}优惠未获官方确认，已按成人价估算"
            if message not in warnings:
                warnings.append(message)


def calculate_budget_summary(intent: Any, activities: Iterable[Any]) -> Dict[str, Any]:
    """Build a V2-compatible payload without fabricating exchange rates or discounts."""

    counts = _traveler_counts(intent)
    category_cny_totals = {key: 0.0 for key in ("transport", "lodging", "food", "activities", "shopping", "other")}
    category_entries: List[Dict[str, Any]] = []
    traveler_cny_totals = {key: 0.0 for key in _TRAVELER_TYPES}
    traveler_statuses = {
        key: ("not_applicable" if counts[key] == 0 else "not_applicable")
        for key in _TRAVELER_TYPES
    }
    unknown_items: List[str] = []
    warnings: List[str] = []
    unconverted_foreign_count = 0

    for activity in activities:
        category = _category(activity)
        title = _text(getattr(activity, "title", "")) or "未命名费用项"
        amount = _number(getattr(activity, "estimated_cost", None))
        is_explicit = amount is not None
        if amount is None:
            amount = _DEFAULT_PER_TRAVELER_CNY.get(category)
            if amount is None:
                unknown_items.append(title)
                continue
            currency = "CNY"
            cny_reference_amount = None
            exchange_rate_as_of = None
            source_status = "user_confirmation_required"
            per_traveler = True
        else:
            currency = _currency(getattr(activity, "estimated_cost_currency", None))
            cny_reference_amount = _number(getattr(activity, "estimated_cost_cny_reference_amount", None))
            exchange_rate_as_of = _text(getattr(activity, "estimated_cost_exchange_rate_as_of", None)) or None
            source_status = _SOURCE_STATUS.get(
                _text(getattr(activity, "data_type", "")),
                "user_confirmation_required",
            )
            per_traveler = getattr(activity, "estimated_cost_unit", "group") == "per_traveler"

        before_total = sum(category_cny_totals.values())
        if per_traveler:
            _add_per_traveler_cost(
                base_amount=amount,
                currency=currency,
                cny_reference_amount=cny_reference_amount,
                exchange_rate_as_of=exchange_rate_as_of,
                source_status=source_status,
                counts=counts,
                official_prices=_official_prices(activity),
                category=category,
                category_entries=category_entries,
                category_cny_totals=category_cny_totals,
                traveler_cny_totals=traveler_cny_totals,
                traveler_statuses=traveler_statuses,
                warnings=warnings,
            )
        else:
            category_entries.append({
                "category": category,
                "amount": _money_payload(
                    amount,
                    currency,
                    cny_reference_amount=cny_reference_amount,
                    exchange_rate_as_of=exchange_rate_as_of,
                    source_status=source_status,
                ),
            })
            known_cny = _cny_amount(amount, currency, cny_reference_amount)
            if known_cny is not None:
                category_cny_totals[category] += known_cny

        if is_explicit and currency != "CNY" and sum(category_cny_totals.values()) == before_total:
            unconverted_foreign_count += 1

    if unconverted_foreign_count:
        warnings.append(
            f"{unconverted_foreign_count} 项外币费用缺少人民币参考价，保留原币且不计入人民币预算比较"
        )

    category_entries = _aggregate_category_entries(category_entries)

    estimated_total = round(sum(category_cny_totals.values()), 2)
    target_total = _number(getattr(intent, "budget_total", None))
    people_count = sum(counts.values())
    if target_total is None:
        per_person_budget = _number(getattr(intent, "budget_per_person", None))
        target_total = round(per_person_budget * people_count, 2) if per_person_budget is not None else None
    overrun_amount = round(max(0.0, estimated_total - target_total), 2) if target_total is not None else 0.0

    traveler_costs = []
    for traveler_type in _TRAVELER_TYPES:
        estimated = traveler_cny_totals[traveler_type]
        traveler_cost = {
            "traveler_type": traveler_type,
            "count": counts[traveler_type],
            "pricing_status": traveler_statuses[traveler_type],
        }
        if estimated:
            traveler_cost["estimated_total"] = _money_payload(
                estimated,
                "CNY",
                cny_reference_amount=None,
                exchange_rate_as_of=None,
                source_status="user_confirmation_required",
            )
        traveler_costs.append(traveler_cost)

    return {
        "currency": "CNY",
        "budget_total": target_total if target_total is not None else (estimated_total or None),
        "budget_per_person": round(estimated_total / people_count, 2) if estimated_total else None,
        "people_count": people_count,
        "estimated_total": estimated_total if estimated_total else None,
        "known_total": estimated_total,
        "unknown_count": len(unknown_items),
        "unknown_items": unknown_items,
        "categories": {key: round(value, 2) for key, value in category_cny_totals.items()},
        "currency_breakdown": {
            "categories": category_entries,
            "traveler_costs": traveler_costs,
        },
        "over_budget": overrun_amount > 0,
        "overrun_amount": overrun_amount,
        "warnings": warnings,
        "source_label": "按已知价格、出行人数和官方优惠计算；未核验优惠按成人价，外币无人民币参考时不换算。",
        "updated_at": date.today().isoformat(),
        "data_type": "estimated_data",
    }
