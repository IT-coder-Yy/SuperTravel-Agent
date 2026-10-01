import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


DataConfidence = Literal["confirmed_live_data", "reference_data", "estimated_data"]
CLARIFICATION_SKIP_SENTINEL = "__skip__"
ClarificationSkipValue = Literal["__skip__"]
ChatFinishReason = Literal["completed", "clarification_required", "cancelled", "failed"]


def _normalize_data_confidence(value: Any, default: DataConfidence) -> DataConfidence:
    if value in {"confirmed_live_data", "reference_data", "estimated_data"}:
        return value
    legacy_mapping = {
        "confirmed": "confirmed_live_data",
        "live": "confirmed_live_data",
        "verified": "confirmed_live_data",
        "reference": "reference_data",
        "retrieved": "reference_data",
        "estimated": "estimated_data",
        "estimate": "estimated_data",
        "unknown": "estimated_data",
    }
    return legacy_mapping.get(str(value or "").strip().lower(), default)


def _normalize_data_type_payload(value: Any, default: DataConfidence) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    normalized["data_type"] = _normalize_data_confidence(
        normalized.get(
            "data_type",
            normalized.get("data_confidence", normalized.get("confidence")),
        ),
        default,
    )
    return normalized


class TripIntent(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
    date_range: Optional[str] = None
    days: Optional[int] = None
    people_count: Optional[int] = None
    adult_count: Optional[int] = Field(default=None, ge=0)
    child_count: Optional[int] = Field(default=None, ge=0)
    senior_count: Optional[int] = Field(default=None, ge=0)
    people_type: Optional[str] = None
    budget_total: Optional[float] = None
    budget_per_person: Optional[float] = None
    travel_style: Optional[str] = None
    pace: Optional[Literal["relaxed", "balanced", "intensive"]] = None
    interests: List[str] = Field(default_factory=list)
    dietary_preferences: List[str] = Field(default_factory=list)
    hotel_preference: Optional[str] = None
    transport_preference: Optional[str] = None
    must_visit: List[str] = Field(default_factory=list)
    avoid: List[str] = Field(default_factory=list)
    output_preference: Optional[str] = None
    confidence: float = 0.0


class ClarificationQuestion(BaseModel):
    id: str
    field: str
    question: str
    reason: str
    options: List[str]
    allow_custom: bool = True
    allow_skip: bool = True
    skip_value: ClarificationSkipValue = "__skip__"
    profile_default_value: Optional[str] = None


class ClarificationRequest(BaseModel):
    session_id: str
    intent: TripIntent
    questions: List[ClarificationQuestion] = Field(min_length=1, max_length=1)


class ChatCompleteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat_complete"] = "chat_complete"
    request_id: str
    sequence: Optional[int] = Field(default=None, ge=1)
    message_id: str
    finish_reason: ChatFinishReason


class ChatStreamErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    request_id: str
    sequence: Optional[int] = Field(default=None, ge=1)
    code: str
    phase: str
    retryable: bool
    user_message: str
    actions: List[str] = Field(default_factory=list)


class UserTravelProfile(BaseModel):
    user_id: str = "local"
    preferred_budget_level: Optional[str] = None
    default_people_type: Optional[str] = None
    travel_style: List[str] = Field(default_factory=list)
    dietary_preferences: List[str] = Field(default_factory=list)
    pace: Optional[str] = None
    hotel_preference: Optional[str] = None
    transport_preference: Optional[str] = None
    disliked_items: List[str] = Field(default_factory=list)
    accessibility_needs: List[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


class TripPlace(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    category: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    source: Optional[str] = None
    data_type: DataConfidence = "estimated_data"
    poi_id: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    images: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    suggested_duration_minutes: Optional[int] = Field(default=None, ge=0)
    opening_hours: Optional[Any] = None
    reservation: Optional[Any] = None
    price: Optional[Any] = None
    suitable_for: List[str] = Field(default_factory=list)
    unsuitable_for: List[str] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    field_evidence: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    updated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_confidence(cls, value: Any) -> Any:
        return _normalize_data_type_payload(value, "estimated_data")


class RouteLeg(BaseModel):
    mode: str
    distance_meters: Optional[int] = Field(default=None, ge=0)
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    estimated_cost: Optional[float] = Field(default=None, ge=0)
    provider: Optional[str] = None
    data_type: DataConfidence = "estimated_data"
    calculated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_route(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"mode": value, "data_type": "estimated_data"}
        return _normalize_data_type_payload(value, "estimated_data")


class TripActivity(BaseModel):
    activity_id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex}")
    day: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    title: str
    activity_type: Literal["attraction", "food", "hotel", "transport", "free_time"] = "attraction"
    meal_type: Optional[Literal["breakfast", "lunch", "dinner"]] = None
    place: Optional[TripPlace] = None
    map_visible: bool = True
    transport_to_next: Optional[str] = None
    route_to_next: Optional[RouteLeg] = None
    estimated_cost: Optional[float] = None
    estimated_cost_currency: Optional[str] = None
    estimated_cost_cny_reference_amount: Optional[float] = Field(default=None, ge=0)
    estimated_cost_exchange_rate_as_of: Optional[str] = None
    estimated_cost_unit: Literal["group", "per_traveler"] = "group"
    official_traveler_prices: Dict[str, float] = Field(default_factory=dict)
    reservation: Optional[Dict[str, Any]] = None
    evidence_refs: List[Any] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    data_type: DataConfidence = "estimated_data"

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_confidence(cls, value: Any) -> Any:
        normalized = _normalize_data_type_payload(value, "estimated_data")
        if not isinstance(normalized, dict):
            return normalized
        if not normalized.get("activity_id") and normalized.get("id"):
            normalized["activity_id"] = normalized["id"]
        route = normalized.get("route_to_next")
        legacy_transport = normalized.get("transport_to_next")
        if route is None and legacy_transport:
            normalized["route_to_next"] = {
                "mode": legacy_transport,
                "data_type": "estimated_data",
            }
        elif not legacy_transport and isinstance(route, dict):
            normalized["transport_to_next"] = route.get("mode")
        return normalized

    @model_validator(mode="after")
    def validate_meal_type(self) -> "TripActivity":
        if self.meal_type and self.activity_type != "food":
            raise ValueError("meal_type 只能用于餐饮活动")
        return self


class TripDay(BaseModel):
    day: int = Field(ge=1)
    date: Optional[str] = None
    theme: Optional[str] = None
    activities: List[TripActivity] = Field(default_factory=list)
    budget_subtotal: Optional[float] = Field(default=None, ge=0)
    warnings: List[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    data_type: DataConfidence = "estimated_data"

    @model_validator(mode="after")
    def align_activity_days(self) -> "TripDay":
        for activity in self.activities:
            activity.day = self.day
        return self


class TripSourceReference(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "reference"
    title: str = ""
    source: str = ""
    url: str = ""
    snippet: str = ""
    data_type: DataConfidence = "reference_data"
    updated_at: Optional[str] = None
    confidence: Optional[Any] = None
    related_fields: List[str] = Field(default_factory=list)
    related_places: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_confidence(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        return _normalize_data_type_payload(value, "reference_data")


class TripPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex}")
    version: int = Field(default=1, ge=1)
    title: str
    intent: TripIntent
    days: int
    activities: List[TripActivity] = Field(default_factory=list)
    trip_days: List[TripDay] = Field(default_factory=list)
    budget_summary: Dict[str, Any] = Field(default_factory=dict)
    map_locations: List[Dict[str, Any]] = Field(default_factory=list)
    source_references: List[TripSourceReference] = Field(default_factory=list)
    data_confidence_summary: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_new_and_legacy_day_shapes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        public_days = normalized.get("days")
        if isinstance(public_days, list):
            normalized.setdefault("trip_days", public_days)
            normalized["days"] = normalized.get("day_count") or max(
                [item.get("day", 0) for item in public_days if isinstance(item, dict)] or [len(public_days), 1]
            )
        if not normalized.get("activities") and isinstance(normalized.get("trip_days"), list):
            normalized["activities"] = [
                activity
                for day in normalized["trip_days"]
                if isinstance(day, dict)
                for activity in day.get("activities", [])
                if isinstance(activity, dict)
            ]
        return normalized


SectionStatus = Literal["ready", "needs_date", "unavailable", "needs_confirmation"]


class CoverImage(BaseModel):
    url: str
    alt: str = ""
    photographer_name: str
    photographer_url: str
    unsplash_url: str
    download_location: Optional[str] = None
    source_reference_id: Optional[str] = None


class DestinationOverview(BaseModel):
    status: SectionStatus = "needs_confirmation"
    status_reason: Optional[str] = "目的地资料待核验"
    name_zh: str = "待确认"
    name_en: Optional[str] = None
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    timezone: Optional[str] = None
    currency: Optional[str] = None
    languages: List[str] = Field(default_factory=list)
    best_seasons: List[str] = Field(default_factory=list)
    area_overview: Optional[str] = None
    themes: List[str] = Field(default_factory=list)
    cover_image: Optional[CoverImage] = None
    source_reference_ids: List[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


class TransportOption(BaseModel):
    option_id: str = Field(default_factory=lambda: f"transport_{uuid.uuid4().hex}")
    mode: Literal["train", "intercity_bus", "flight"]
    service_number: Optional[str] = None
    departure_station: Optional[str] = None
    arrival_station: Optional[str] = None
    departure_hub: Optional[TripPlace] = None
    arrival_hub: Optional[TripPlace] = None
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    transfers: Optional[int] = Field(default=None, ge=0)
    price: Optional[float] = Field(default=None, ge=0)
    currency: str = "CNY"
    availability: Literal["available", "limited", "unknown"] = "unknown"
    seat_options: List[Dict[str, Any]] = Field(default_factory=list)
    booking_url: Optional[str] = None
    source_reference_id: Optional[str] = None
    data_type: DataConfidence = "reference_data"
    queried_at: Optional[str] = None


class TransportSection(BaseModel):
    direction: Literal["outbound", "return"]
    scope: Literal["domestic", "international"] = "domestic"
    travel_date: Optional[str] = None
    supported_modes: List[Literal["train", "intercity_bus", "flight"]] = Field(default_factory=list)
    recommended_option_id: Optional[str] = None
    options: List[TransportOption] = Field(default_factory=list)
    status: SectionStatus = "needs_confirmation"
    status_reason: Optional[str] = None
    official_query_url: Optional[str] = None


class HotelRecommendation(BaseModel):
    hotel_id: str = Field(default_factory=lambda: f"hotel_{uuid.uuid4().hex}")
    area: str = "待确认"
    name: str = "待确认"
    place: Optional[TripPlace] = None
    nightly_price: Optional[float] = Field(default=None, ge=0)
    total_price: Optional[float] = Field(default=None, ge=0)
    currency: str = "CNY"
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    commute_to_first_activity_minutes: Optional[int] = Field(default=None, ge=0)
    commute_from_last_activity_minutes: Optional[int] = Field(default=None, ge=0)
    reasons: List[str] = Field(default_factory=list)
    booking_url: Optional[str] = None
    source_reference_id: Optional[str] = None
    source_reference_ids: List[str] = Field(default_factory=list)
    data_type: DataConfidence = "reference_data"
    updated_at: Optional[str] = None


class HotelSection(BaseModel):
    status: SectionStatus = "needs_confirmation"
    status_reason: Optional[str] = "暂无可靠实时库存，酒店仅作参考"
    recommendations: List[HotelRecommendation] = Field(default_factory=list)


class ItinerarySection(BaseModel):
    status: SectionStatus = "ready"
    status_reason: Optional[str] = None
    days: List[TripDay] = Field(default_factory=list)


class FriendlyReminder(BaseModel):
    category: Literal[
        "documents", "weather", "reservation", "payment", "communication",
        "safety", "diet", "accessibility", "other"
    ] = "other"
    content: str
    source_reference_id: Optional[str] = None
    data_type: DataConfidence = "reference_data"
    valid_until: Optional[str] = None
    updated_at: Optional[str] = None


class FriendlyReminderSection(BaseModel):
    status: SectionStatus = "needs_confirmation"
    status_reason: Optional[str] = "动态旅行提醒待联网核验"
    items: List[FriendlyReminder] = Field(default_factory=list)


class RouteGeometryLeg(BaseModel):
    from_activity_id: str
    to_activity_id: str
    mode: str
    distance_meters: Optional[int] = Field(default=None, ge=0)
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    geometry: Optional[Dict[str, Any]] = None
    coordinate_system: Literal["BD09LL", "WGS84"]
    provider: str
    calculated_at: Optional[str] = None
    status: Literal["ready", "unavailable"] = "unavailable"


class DayRoute(BaseModel):
    day: int = Field(ge=1)
    plan_version: int = Field(ge=1)
    provider: str
    coordinate_system: Literal["BD09LL", "WGS84"]
    legs: List[RouteGeometryLeg] = Field(default_factory=list)
    bbox: Optional[List[float]] = None
    status: Literal["ready", "partial", "unavailable"] = "unavailable"


class MapGuidance(BaseModel):
    status: SectionStatus = "needs_confirmation"
    status_reason: Optional[str] = "真实路线待计算"
    location_ids: List[str] = Field(default_factory=list)
    day_routes: List[DayRoute] = Field(default_factory=list)
    reminders: List[str] = Field(default_factory=list)
    unavailable_segments: List[Dict[str, Any]] = Field(default_factory=list)


class DeliverySection(BaseModel):
    status: SectionStatus = "ready"
    markdown_filename: str = "旅行规划-v1.md"
    share_status: Literal["private", "shared", "closed"] = "private"
    share_scopes: List[str] = Field(default_factory=list)


class TravelPlanDocumentV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    plan_id: str
    version: int = Field(ge=1)
    title: str
    generated_at: str
    locale: str = "zh-CN"
    intent: TripIntent
    destination_overview: DestinationOverview
    outbound_transport: TransportSection
    hotel_recommendations: HotelSection
    itinerary: ItinerarySection
    return_transport: TransportSection
    friendly_reminders: FriendlyReminderSection
    map_guidance: MapGuidance
    delivery: DeliverySection
    budget: Dict[str, Any] = Field(default_factory=dict)
    sources: List[TripSourceReference] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    checklist: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_places: List[TripPlace] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def validate_directions_and_map_locations(self) -> "TravelPlanDocumentV2":
        if self.outbound_transport.direction != "outbound":
            raise ValueError("outbound_transport.direction 必须为 outbound")
        if self.return_transport.direction != "return":
            raise ValueError("return_transport.direction 必须为 return")
        visible_ids = []
        for day in self.itinerary.days:
            for activity in day.activities:
                place = activity.place
                if not place or place.lat is None or place.lng is None:
                    continue
                if getattr(activity, "map_visible", True) is False:
                    continue
                visible_ids.append(place.poi_id or activity.activity_id)
        if self.map_guidance.location_ids != visible_ids:
            raise ValueError("map_guidance.location_ids 必须与当前版本日程地点一致")
        return self


class TripValidationIssue(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    repair_hint: Optional[str] = None


class TripValidationResult(BaseModel):
    valid: bool
    issues: List[TripValidationIssue] = Field(default_factory=list)


class TripPlanRepairResult(BaseModel):
    repaired: bool
    plan: TripPlan
    attempted_issue_codes: List[str] = Field(default_factory=list)
    resolved_issue_codes: List[str] = Field(default_factory=list)
    remaining_issue_codes: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    remaining_validation: TripValidationResult
