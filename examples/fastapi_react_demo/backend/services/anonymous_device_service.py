import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional


DEVICE_COOKIE_NAME = "sta_device_token"
DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5


@dataclass(frozen=True)
class AnonymousDeviceIdentity:
    device_id: str
    raw_token: str
    should_set_cookie: bool


def hash_device_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def resolve_anonymous_device(repository, raw_token: Optional[str]) -> AnonymousDeviceIdentity:
    normalized = (raw_token or "").strip()
    should_set_cookie = not normalized
    if not normalized:
        normalized = secrets.token_urlsafe(32)
    device_id = repository.get_or_create_device(hash_device_token(normalized))
    return AnonymousDeviceIdentity(
        device_id=device_id,
        raw_token=normalized,
        should_set_cookie=should_set_cookie,
    )


def set_anonymous_device_cookie(response, identity: AnonymousDeviceIdentity, *, secure: bool) -> None:
    if not identity.should_set_cookie:
        return
    response.set_cookie(
        key=DEVICE_COOKIE_NAME,
        value=identity.raw_token,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
