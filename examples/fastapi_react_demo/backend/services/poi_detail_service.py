import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DATA_TYPE_PRIORITY = {
    "estimated_data": 1,
    "reference_data": 2,
    "confirmed_live_data": 3,
}

POI_FIELDS = (
    "name",
    "address",
    "city",
    "coordinates",
    "category",
    "rating",
    "images",
    "summary",
    "suggested_duration_minutes",
    "opening_hours",
    "reservation",
    "price",
    "suitable_for",
    "unsuitable_for",
)

FIELD_ALIASES = {
    "name": ("name", "poi_name", "title"),
    "address": ("address", "formatted_address", "addr"),
    "city": ("city", "region", "area", "district"),
    "category": ("category", "poi_category"),
    "rating": ("rating", "score", "overall_rating"),
    "images": ("images", "image_urls", "image"),
    "summary": ("summary", "description", "introduction", "intro"),
    "suggested_duration_minutes": (
        "suggested_duration_minutes",
        "recommended_duration_minutes",
        "stay_duration_minutes",
        "duration_minutes",
    ),
    "opening_hours": ("opening_hours", "business_hours", "opening_time"),
    "reservation": ("reservation", "reservation_info", "booking_info"),
    "price": ("price", "price_reference", "ticket_price", "estimated_price"),
    "suitable_for": ("suitable_for", "good_for", "recommended_for"),
    "unsuitable_for": ("unsuitable_for", "not_suitable_for", "avoid_for"),
}


class PoiDetailError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _as_mapping(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise PoiDetailError("INVALID_POI", "POI detail must be a mapping or Pydantic model")
    return dict(value)


def _first_value(payload: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "" and value != [] and value != {}:
            return value
    return None


def _normalize_data_type(value: Any, default: str = "estimated_data") -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "confirmed": "confirmed_live_data",
        "live": "confirmed_live_data",
        "verified": "confirmed_live_data",
        "reference": "reference_data",
        "retrieved": "reference_data",
        "estimated": "estimated_data",
        "estimate": "estimated_data",
        "unknown": "estimated_data",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in DATA_TYPE_PRIORITY else default


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: Any) -> float:
    parsed = _parse_datetime(value)
    return parsed.timestamp() if parsed else float("-inf")


def _is_expired(
    payload: Mapping[str, Any],
    now: datetime,
    max_reference_age_days: Optional[int],
) -> bool:
    if payload.get("is_expired") is True or payload.get("expired") is True:
        return True
    expires_at = _parse_datetime(payload.get("expires_at"))
    if expires_at and expires_at <= now:
        return True
    if max_reference_age_days is not None:
        updated_at = _parse_datetime(payload.get("updated_at"))
        if updated_at and updated_at + timedelta(days=max_reference_age_days) <= now:
            return True
    return False


def _effective_data_type(
    declared: Any,
    parent: str,
    expired: bool,
    *,
    enforce_parent: bool = True,
) -> str:
    value = _normalize_data_type(declared, parent)
    if enforce_parent and DATA_TYPE_PRIORITY[value] > DATA_TYPE_PRIORITY[parent]:
        value = parent
    if expired and value == "confirmed_live_data":
        return "reference_data"
    return value


def _normalize_text_list(value: Any) -> List[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: List[str] = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_coordinates(payload: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    coordinates = payload.get("coordinates")
    if isinstance(coordinates, Mapping):
        lat = _number(coordinates.get("lat", coordinates.get("latitude")))
        lng = _number(
            coordinates.get("lng", coordinates.get("lon", coordinates.get("longitude")))
        )
    elif isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
        lng = _number(coordinates[0])
        lat = _number(coordinates[1])
    else:
        lat = _number(payload.get("lat", payload.get("latitude")))
        lng = _number(payload.get("lng", payload.get("lon", payload.get("longitude"))))
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"lat": lat, "lng": lng}


def _normalize_field_value(field: str, value: Any) -> Any:
    if field in {"images", "suitable_for", "unsuitable_for"}:
        return _normalize_text_list(value)
    if field == "suggested_duration_minutes":
        number = _number(value)
        return int(number) if number is not None and number >= 0 else None
    if field == "rating":
        number = _number(value)
        return round(number, 1) if number is not None and 0 <= number <= 5 else None
    if field in {"name", "address", "city", "category", "summary"}:
        return str(value or "").strip() or None
    return value


def _identity_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)


def build_stable_poi_id(poi: Any) -> str:
    payload = _as_mapping(poi)
    supplied_id = _first_value(payload, ("poi_id", "stable_id", "place_id", "id"))
    if supplied_id is not None:
        return str(supplied_id).strip()

    name = _first_value(payload, FIELD_ALIASES["name"])
    if not str(name or "").strip():
        raise PoiDetailError("MISSING_NAME", "POI name is required when no stable ID is supplied")
    coordinates = _normalize_coordinates(payload)
    if coordinates:
        location_key = f"{coordinates['lat']:.5f},{coordinates['lng']:.5f}"
    else:
        location_key = "|".join(
            filter(
                None,
                (
                    _identity_text(payload.get("city")),
                    _identity_text(payload.get("address")),
                    _identity_text(_first_value(payload, FIELD_ALIASES["category"])),
                ),
            )
        )
    identity = f"{_identity_text(name)}|{location_key}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"poi_{digest}"


def _normalize_source(
    source: Any,
    parent_type: str,
    parent_updated_at: Optional[str],
    now: datetime,
    max_reference_age_days: Optional[int],
    enforce_parent: bool,
) -> Optional[Dict[str, Any]]:
    if isinstance(source, str):
        payload: Dict[str, Any] = {"title": source}
    elif isinstance(source, Mapping):
        payload = dict(source)
    else:
        return None
    updated_at = payload.get("updated_at") or parent_updated_at
    expiry_payload = {**payload, "updated_at": updated_at}
    expired = _is_expired(expiry_payload, now, max_reference_age_days)
    data_type = _effective_data_type(
        payload.get("data_type"),
        parent_type,
        expired,
        enforce_parent=enforce_parent,
    )
    source_reference_id = _first_value(payload, ("source_reference_id", "source_id", "id"))
    if source_reference_id is None:
        identity = "|".join(
            str(payload.get(key) or "").strip() for key in ("url", "source", "title")
        )
        source_reference_id = f"source_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:12]}"
    return {
        "source_reference_id": str(source_reference_id),
        "title": str(payload.get("title") or payload.get("source") or "").strip(),
        "url": str(payload.get("url") or "").strip(),
        "data_type": data_type,
        "updated_at": str(updated_at).strip() if updated_at else None,
        "is_expired": expired,
    }


def _normalize_sources(
    payload: Mapping[str, Any],
    parent_type: str,
    updated_at: Optional[str],
    now: datetime,
    max_reference_age_days: Optional[int],
    enforce_parent: bool,
) -> List[Dict[str, Any]]:
    raw_sources = payload.get("sources", payload.get("source_references"))
    if raw_sources is None and payload.get("source") is not None:
        raw_sources = [payload.get("source")]
    elif raw_sources is None and payload.get("source_reference_id") is not None:
        raw_sources = [
            {
                "source_reference_id": payload.get("source_reference_id"),
                "title": payload.get("source_title", ""),
                "url": payload.get("source_url", ""),
            }
        ]
    if raw_sources is None:
        return []
    if not isinstance(raw_sources, (list, tuple)):
        raw_sources = [raw_sources]
    return [
        normalized
        for item in raw_sources
        if (
            normalized := _normalize_source(
                item,
                parent_type,
                updated_at,
                now,
                max_reference_age_days,
                enforce_parent,
            )
        )
        is not None
    ]


def normalize_poi_detail(
    poi: Any,
    *,
    now: Optional[datetime] = None,
    max_reference_age_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Normalize one POI into a JSON-serializable detail with field-level evidence."""

    payload = _as_mapping(poi)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    declared_parent_type = payload.get(
        "data_type", payload.get("data_confidence", payload.get("confidence"))
    )
    parent_type = _normalize_data_type(declared_parent_type)
    updated_at_value = payload.get("updated_at")
    updated_at = str(updated_at_value).strip() if updated_at_value else None
    expired = _is_expired(payload, current_time, max_reference_age_days)
    parent_type = _effective_data_type(parent_type, parent_type, expired)
    sources = _normalize_sources(
        payload,
        parent_type,
        updated_at,
        current_time,
        max_reference_age_days,
        enforce_parent=declared_parent_type is not None,
    )
    primary_source_id = sources[0]["source_reference_id"] if sources else None
    raw_evidence = payload.get("field_evidence", payload.get("field_sources", {}))
    raw_evidence = raw_evidence if isinstance(raw_evidence, Mapping) else {}

    result: Dict[str, Any] = {"poi_id": build_stable_poi_id(payload)}
    field_evidence: Dict[str, Dict[str, Any]] = {}
    for field in POI_FIELDS:
        if field == "coordinates":
            value = _normalize_coordinates(payload)
        else:
            value = _normalize_field_value(field, _first_value(payload, FIELD_ALIASES[field]))
        result[field] = value
        has_value = value is not None and value != [] and value != ""

        evidence = raw_evidence.get(field, {})
        evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
        evidence_updated_at = evidence.get("updated_at") or (updated_at if has_value else None)
        evidence_expired = _is_expired(
            {**evidence, "updated_at": evidence_updated_at},
            current_time,
            max_reference_age_days,
        ) or expired
        evidence_type = _effective_data_type(
            evidence.get("data_type"),
            parent_type,
            evidence_expired,
            enforce_parent=declared_parent_type is not None,
        )
        field_evidence[field] = {
            "source_reference_id": evidence.get("source_reference_id", primary_source_id if has_value else None),
            "data_type": evidence_type,
            "updated_at": str(evidence_updated_at).strip() if evidence_updated_at else None,
            "is_expired": evidence_expired,
        }

    if not result["name"]:
        raise PoiDetailError("MISSING_NAME", "POI name is required")
    result.update(
        {
            "sources": sources,
            "field_evidence": field_evidence,
            "updated_at": updated_at,
            "data_type": max(
                (item["data_type"] for item in field_evidence.values()),
                key=lambda item: DATA_TYPE_PRIORITY[item],
            ),
        }
    )
    return result


def _candidate_key(value: Any, evidence: Mapping[str, Any]) -> Tuple[int, float, str]:
    data_type = _normalize_data_type(evidence.get("data_type"))
    return (
        DATA_TYPE_PRIORITY[data_type],
        _timestamp(evidence.get("updated_at")),
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
    )


def _merge_source_lists(details: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for detail in details:
        for source in detail.get("sources", []):
            source_id = source["source_reference_id"]
            if source_id not in merged:
                merged[source_id] = dict(source)
                order.append(source_id)
                continue
            current = merged[source_id]
            if _candidate_key(source, source) > _candidate_key(current, current):
                merged[source_id] = dict(source)
    return [merged[source_id] for source_id in order]


def _merge_normalized_group(details: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    first = details[0]
    merged: Dict[str, Any] = {"poi_id": first["poi_id"]}
    merged_evidence: Dict[str, Dict[str, Any]] = {}
    for field in POI_FIELDS:
        candidates = [
            (detail.get(field), detail["field_evidence"][field])
            for detail in details
            if detail.get(field) is not None and detail.get(field) != []
        ]
        if candidates:
            value, evidence = max(candidates, key=lambda item: _candidate_key(*item))
            merged[field] = value
            merged_evidence[field] = dict(evidence)
        else:
            merged[field] = None if field not in {"images", "suitable_for", "unsuitable_for"} else []
            merged_evidence[field] = dict(first["field_evidence"][field])

    merged["sources"] = _merge_source_lists(details)
    merged["field_evidence"] = merged_evidence
    evidence_updates = [item.get("updated_at") for item in merged_evidence.values()]
    source_updates = [item.get("updated_at") for item in merged["sources"]]
    updates = [item for item in evidence_updates + source_updates if item]
    merged["updated_at"] = max(updates, key=_timestamp) if updates else None
    merged["data_type"] = max(
        (item["data_type"] for item in merged_evidence.values()),
        key=lambda item: DATA_TYPE_PRIORITY[item],
    )
    return merged


def merge_poi_details(
    pois: Iterable[Any],
    *,
    now: Optional[datetime] = None,
    max_reference_age_days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Normalize POIs and merge records sharing the same stable POI ID."""

    groups: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for poi in pois:
        normalized = normalize_poi_detail(
            poi, now=now, max_reference_age_days=max_reference_age_days
        )
        poi_id = normalized["poi_id"]
        if poi_id not in groups:
            groups[poi_id] = []
            order.append(poi_id)
        groups[poi_id].append(normalized)
    return [_merge_normalized_group(groups[poi_id]) for poi_id in order]


def merge_poi_detail(
    pois: Iterable[Any],
    *,
    now: Optional[datetime] = None,
    max_reference_age_days: Optional[int] = None,
) -> Dict[str, Any]:
    merged = merge_poi_details(
        pois, now=now, max_reference_age_days=max_reference_age_days
    )
    if len(merged) != 1:
        raise PoiDetailError("POI_ID_MISMATCH", "all POI details must share one stable ID")
    return merged[0]


normalize_and_merge_poi_details = merge_poi_details
