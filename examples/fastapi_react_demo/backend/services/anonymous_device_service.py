import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Optional


DEVICE_COOKIE_NAME = "sta_device_token"
DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 5
LEGACY_SHARED_DEVICE_TOKEN = "supertravelagent-local-single-user-v1"
LOCAL_SHARED_DEVICE_ID = "local-single-user"
LOCAL_SHARED_DEVICE_TOKEN = LEGACY_SHARED_DEVICE_TOKEN
DEVICE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


@dataclass(frozen=True)
class AnonymousDeviceIdentity:
    device_id: str
    raw_token: str
    should_set_cookie: bool


def hash_device_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def resolve_anonymous_device(repository, raw_token: Optional[str]) -> AnonymousDeviceIdentity:
    supplied = (raw_token or "").strip()
    token_is_valid = bool(
        supplied
        and supplied != LEGACY_SHARED_DEVICE_TOKEN
        and DEVICE_TOKEN_PATTERN.fullmatch(supplied)
    )
    normalized = supplied if token_is_valid else secrets.token_urlsafe(32)
    should_set_cookie = not token_is_valid
    device_id = repository.get_or_create_device(hash_device_token(normalized))
    if supplied == LEGACY_SHARED_DEVICE_TOKEN:
        claim_history = getattr(repository, "claim_legacy_shared_history", None)
        if callable(claim_history):
            claim_history(device_id)
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
