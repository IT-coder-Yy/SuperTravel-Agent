import re
from typing import Any


REDACTED = "[已移除敏感信息]"

_SENSITIVE_KEY_PARTS = {
    "password",
    "passwd",
    "passcode",
    "passport",
    "id_card",
    "identity_number",
    "card_number",
    "bank_card",
    "credit_card",
    "cvv",
    "cvc",
    "payment",
    "verification_code",
    "otp",
    "order_number",
    "证件",
    "身份证",
    "护照",
    "密码",
    "银行卡",
    "信用卡",
    "验证码",
    "支付",
    "订单号",
}

_TEXT_PATTERNS = (
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    re.compile(
        r"(?i)(密码|验证码|护照号|身份证号|银行卡号|信用卡号|password|passport|id[ _-]?card|otp)"
        r"\s*[:：=]?\s*[A-Za-z0-9_-]{4,}"
    ),
)
_CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){14,18}\d(?!\d)")


def _passes_luhn(value: str) -> bool:
    checksum = 0
    parity = len(value) % 2
    for index, character in enumerate(value):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _redact_card_candidate(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return REDACTED if 15 <= len(digits) <= 19 and _passes_luhn(digits) else match.group(0)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def sanitize_persisted_text(value: str) -> str:
    sanitized = value
    for pattern in _TEXT_PATTERNS:
        sanitized = pattern.sub(REDACTED, sanitized)
    return _CARD_CANDIDATE_PATTERN.sub(_redact_card_candidate, sanitized)


def sanitize_for_persistence(value: Any) -> Any:
    """Remove high-risk secrets before a payload reaches SQLite."""
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_sensitive_key(key) else sanitize_for_persistence(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_persistence(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_persistence(item) for item in value]
    if isinstance(value, str):
        return sanitize_persisted_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_persisted_text(str(value))
