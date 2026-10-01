"""Official ECB reference exchange rates for international budget estimates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
import threading
import time
from typing import Dict, Optional
from xml.etree import ElementTree

import requests


ECB_DAILY_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_cache_lock = threading.Lock()
_cached_at = 0.0
_cached_rates: Optional[tuple[date, Dict[str, float]]] = None


@dataclass(frozen=True)
class ExchangeRateQuote:
    currency: str
    cny_per_unit: float
    as_of: date
    provider: str = "European Central Bank"
    source_url: str = ECB_DAILY_RATES_URL


def clear_exchange_rate_cache() -> None:
    global _cached_at, _cached_rates
    with _cache_lock:
        _cached_at = 0.0
        _cached_rates = None


def _parse_ecb_rates(content: bytes) -> tuple[date, Dict[str, float]]:
    root = ElementTree.fromstring(content)
    rate_date: Optional[date] = None
    rates: Dict[str, float] = {"EUR": 1.0}
    for element in root.iter():
        if "time" in element.attrib:
            rate_date = date.fromisoformat(element.attrib["time"])
        currency = str(element.attrib.get("currency") or "").upper()
        raw_rate = element.attrib.get("rate")
        if currency and raw_rate is not None:
            value = float(raw_rate)
            if value > 0:
                rates[currency] = value
    if rate_date is None or "CNY" not in rates:
        raise ValueError("ECB 汇率响应缺少日期或 CNY")
    return rate_date, rates


def _latest_ecb_rates() -> Optional[tuple[date, Dict[str, float]]]:
    global _cached_at, _cached_rates
    now = time.monotonic()
    with _cache_lock:
        if _cached_rates is not None and now - _cached_at < _CACHE_TTL_SECONDS:
            return _cached_rates[0], dict(_cached_rates[1])
        if os.getenv("ECB_EXCHANGE_RATE_ENABLED", "true").strip().lower() in {"0", "false", "off", "no"}:
            return None
        endpoint = os.getenv("ECB_EXCHANGE_RATE_URL", ECB_DAILY_RATES_URL).strip() or ECB_DAILY_RATES_URL
        try:
            response = requests.get(
                endpoint,
                headers={"User-Agent": "SuperTravelAgent/1.0 (reference budget conversion)"},
                timeout=max(5.0, min(float(os.getenv("ECB_EXCHANGE_RATE_TIMEOUT_SECONDS", "15")), 30.0)),
            )
            response.raise_for_status()
            parsed = _parse_ecb_rates(response.content)
        except (requests.RequestException, ElementTree.ParseError, TypeError, ValueError):
            return None
        _cached_rates = (parsed[0], dict(parsed[1]))
        _cached_at = now
        return parsed[0], dict(parsed[1])


def quote_currency_to_cny(currency: str) -> Optional[ExchangeRateQuote]:
    """Return CNY for one unit of ``currency`` using the same-day EUR cross rate."""

    normalized = str(currency or "").strip().upper()
    if normalized == "CNY":
        return ExchangeRateQuote(currency="CNY", cny_per_unit=1.0, as_of=date.today())
    latest = _latest_ecb_rates()
    if latest is None:
        return None
    as_of, rates = latest
    foreign_per_eur = rates.get(normalized)
    cny_per_eur = rates.get("CNY")
    if foreign_per_eur is None or cny_per_eur is None or foreign_per_eur <= 0:
        return None
    return ExchangeRateQuote(
        currency=normalized,
        cny_per_unit=cny_per_eur / foreign_per_eur,
        as_of=as_of,
    )
