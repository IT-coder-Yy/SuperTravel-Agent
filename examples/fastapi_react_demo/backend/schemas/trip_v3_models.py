from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any, Dict, List, Literal, Mapping, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, model_validator


DateValue = date

SourceStatus = Literal[
    "realtime_verified",
    "official_reference",
    "map_reference",
    "guide_reference",
    "user_confirmation_required",
]
DocumentStatus = Literal["formal", "degraded"]
SectionStatus = Literal["ready", "degraded", "unavailable", "user_confirmation_required"]
ActivityKind = Literal[
    "attraction",
    "food",
    "transport",
    "hotel",
    "shopping",
    "free_time",
    "other",
]
PlanningLifecycleStatus = Literal[
    "idle",
    "planning",
    "completed",
    "completed_degraded",
    "cancelled",
    "error",
    "draft",
    "demo_preview",
    "demo_replaying",
    "demo_completed",
]
PlanningStage = Literal[
    "requirements_analysis",
    "research",
    "route_planning",
    "realtime_verification",
    "validation_completed",
]
PlanningEventType = Literal[
    "run_started",
    "agent_stage_started",
    "agent_stage_updated",
    "agent_stage_retrying",
    "agent_stage_completed",
    "provider_queue_waiting",
    "run_soft_timeout",
    "final_plan_section",
    "trip_plan_completed",
    "plan_revision_started",
    "plan_revision_section",
    "plan_revision_completed",
    "run_cancelled",
    "error",
]


USER_VISIBLE_STATUS_LABELS: Dict[str, str] = {
    "idle": "待开始",
    "planning": "规划中",
    "completed": "规划已完成",
    "completed_degraded": "已完成，部分信息待确认",
    "cancelled": "规划已停止",
    "error": "生成失败",
    "draft": "有未应用修改",
    "demo_preview": "案例预览",
    "demo_replaying": "示例回放中",
    "demo_completed": "示例回放完成",
}


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def is_valid_route_geometry(geometry: Any) -> bool:
    """Return true only for finite, renderable GeoJSON LineString route geometry."""
    if not isinstance(geometry, Mapping) or geometry.get("type") != "LineString":
        return False
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return False
    for point in coordinates:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return False
        lng, lat = point[0], point[1]
        if isinstance(lng, bool) or isinstance(lat, bool):
            return False
        try:
            longitude, latitude = float(lng), float(lat)
        except (TypeError, ValueError):
            return False
        if not (
            math.isfinite(longitude)
            and math.isfinite(latitude)
            and -180 <= longitude <= 180
            and -90 <= latitude <= 90
        ):
            return False
    return True


class TravelerCountV3(ContractModel):
    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    seniors: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.seniors

    @model_validator(mode="after")
    def require_at_least_one_traveler(self) -> "TravelerCountV3":
        if self.total < 1:
            raise ValueError("至少需要一位出行人")
        return self


class MoneyV3(ContractModel):
    amount: float = Field(ge=0)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    cny_reference_amount: Optional[float] = Field(default=None, ge=0)
    exchange_rate_as_of: Optional[date] = None
    source_status: SourceStatus = "user_confirmation_required"

    @model_validator(mode="after")
    def normalize_currency(self) -> "MoneyV3":
        self.currency = self.currency.upper()
        return self


class TripIntentV3(ContractModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    date_mode: Literal["fixed", "flexible"] = "fixed"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    days: int = Field(ge=1, le=7)
    travelers: TravelerCountV3
    budget: MoneyV3
    preferences: List[str] = Field(default_factory=list)
    dietary_preferences: List[str] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "intensive"] = "balanced"

    @model_validator(mode="after")
    def validate_date_range(self) -> "TripIntentV3":
        if self.date_mode == "flexible":
            if self.start_date is not None or self.end_date is not None:
                raise ValueError("日期待定行程不能携带虚构的起止日期")
            return self
        if self.start_date is None or self.end_date is None:
            raise ValueError("固定日期行程必须提供起止日期")
        expected_days = (self.end_date - self.start_date).days + 1
        if expected_days != self.days:
            raise ValueError("intent.days 必须与起止日期的自然日数量一致")
        return self


class CoordinatesV3(ContractModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_system: Literal["WGS84", "BD09LL"]


class ReviewSummaryV3(ContractModel):
    text: str = Field(min_length=1)
    source_refs: List[str] = Field(default_factory=list)
    updated_at: Optional[AwareDatetime] = None


class TripPlaceV3(ContractModel):
    poi_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: ActivityKind
    city: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    opening_hours: Optional[str] = None
    timezone: Optional[str] = None
    coordinates: Optional[CoordinatesV3] = None
    summary: Optional[str] = None
    review_summary: Optional[ReviewSummaryV3] = None
    evidence_refs: List[str] = Field(default_factory=list)


class ImageAssetV3(ContractModel):
    image_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    alt: str = ""
    mime_type: Optional[str] = None
    width: Optional[int] = Field(default=None, gt=0)
    height: Optional[int] = Field(default=None, gt=0)
    display_allowed: bool = True
    export_allowed: bool = False
    attribution_required: bool = True
    attribution_text: Optional[str] = None
    attribution_url: Optional[str] = None
    provider_name: Optional[str] = None
    provider_url: Optional[str] = None
    download_location: Optional[str] = None
    source_ref: Optional[str] = None
    checked_at: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def validate_export_permission(self) -> "ImageAssetV3":
        if self.export_allowed and self.attribution_required:
            raise ValueError("需要署名的图片不能进入无署名 PDF 导出")
        return self


class TransportTimeV3(ContractModel):
    display_text: str = Field(min_length=1)
    utc: Optional[AwareDatetime] = None
    local_iso: Optional[AwareDatetime] = None
    timezone: Optional[str] = None
    beijing_iso: Optional[AwareDatetime] = None
    day_offset: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def require_timezone_for_local_time(self) -> "TransportTimeV3":
        temporal_values = [self.utc, self.local_iso, self.beijing_iso]
        has_temporal_value = any(value is not None for value in temporal_values)
        if not has_temporal_value:
            return self
        if not all(value is not None for value in temporal_values) or not self.timezone:
            raise ValueError("已规范化交通时刻必须同时保存 UTC、当地时间、北京时间和 IANA timezone")
        try:
            local_zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("交通时刻 timezone 必须是有效 IANA 标识") from exc

        localized = self.local_iso.astimezone(local_zone)
        if (
            self.local_iso.replace(tzinfo=None) != localized.replace(tzinfo=None)
            or self.local_iso.utcoffset() != localized.utcoffset()
        ):
            raise ValueError("local_iso 必须与 timezone 的当地日期、时间和偏移量一致")

        utc_value = self.utc.astimezone(timezone.utc)
        if localized.astimezone(timezone.utc) != utc_value:
            raise ValueError("UTC 与当地交通时刻必须表示同一时刻")

        beijing_zone = ZoneInfo("Asia/Shanghai")
        expected_beijing = utc_value.astimezone(beijing_zone)
        localized_beijing = self.beijing_iso.astimezone(beijing_zone)
        if (
            self.beijing_iso.replace(tzinfo=None) != localized_beijing.replace(tzinfo=None)
            or self.beijing_iso.utcoffset() != localized_beijing.utcoffset()
            or localized_beijing != expected_beijing
        ):
            raise ValueError("beijing_iso 必须与 UTC 表示同一北京时间")
        return self


class TransportSeatOptionV3(ContractModel):
    """One provider-returned cabin or seat class for a real-time transport option."""

    name: str = Field(min_length=1)
    availability: Literal["available", "limited", "unavailable", "unknown"] = "unknown"
    remaining_text: Optional[str] = None
    price: Optional[MoneyV3] = None

    @model_validator(mode="after")
    def protect_realtime_claims(self) -> "TransportSeatOptionV3":
        if self.availability in {"available", "limited", "unavailable"} and not self.remaining_text:
            raise ValueError("座席余量状态必须保留 Provider 返回的余量文本")
        return self


class TransportOptionV3(ContractModel):
    option_id: str = Field(min_length=1)
    mode: Literal["train", "intercity_bus", "flight"]
    service_number: Optional[str] = None
    departure_place: Optional[str] = None
    arrival_place: Optional[str] = None
    departure_hub: Optional[TripPlaceV3] = None
    arrival_hub: Optional[TripPlaceV3] = None
    departure_time: Optional[TransportTimeV3] = None
    arrival_time: Optional[TransportTimeV3] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    transfers: Optional[int] = Field(default=None, ge=0)
    price: Optional[MoneyV3] = None
    availability: Literal["available", "limited", "unknown"] = "unknown"
    seat_options: List[TransportSeatOptionV3] = Field(default_factory=list, max_length=12)
    booking_url: Optional[str] = None
    source_status: SourceStatus = "user_confirmation_required"
    source_refs: List[str] = Field(default_factory=list)
    verified_at: Optional[AwareDatetime] = None

    @model_validator(mode="after")
    def protect_realtime_claims(self) -> "TransportOptionV3":
        has_realtime_claim = self.availability in {"available", "limited"}
        if has_realtime_claim and self.source_status != "realtime_verified":
            raise ValueError("余票或库存结论必须来自实时已核验数据")
        has_specific_schedule = bool(self.service_number or self.departure_time or self.arrival_time)
        if has_specific_schedule and self.source_status != "realtime_verified":
            raise ValueError("具体班次和时刻必须来自实时已核验数据")
        departure = self.departure_time
        arrival = self.arrival_time
        has_complete_context = lambda value: bool(
            value
            and value.utc is not None
            and value.local_iso is not None
            and value.beijing_iso is not None
            and value.timezone
        )
        if has_complete_context(departure) and has_complete_context(arrival):
            if arrival.utc < departure.utc:
                raise ValueError("到达交通时刻不能早于出发交通时刻")
            expected_day_offset = max(
                (arrival.local_iso.date() - departure.local_iso.date()).days,
                0,
            )
            if arrival.day_offset != expected_day_offset:
                raise ValueError("到达 day_offset 必须与当地日期差一致")
        return self


class TransportSectionV3(ContractModel):
    direction: Literal["outbound", "return"]
    scope: Literal["domestic", "international"]
    status: SectionStatus
    status_reason: Optional[str] = None
    selected_option_id: Optional[str] = None
    options: List[TransportOptionV3] = Field(default_factory=list, max_length=3)
    official_query_url: Optional[str] = None

    @model_validator(mode="after")
    def validate_selected_option(self) -> "TransportSectionV3":
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("交通方案 option_id 不能重复")
        if self.selected_option_id and self.selected_option_id not in option_ids:
            raise ValueError("selected_option_id 必须指向当前交通方案")
        return self


class LodgingOptionV3(ContractModel):
    lodging_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    area: Optional[str] = None
    place: Optional[TripPlaceV3] = None
    nightly_price: Optional[MoneyV3] = None
    total_price: Optional[MoneyV3] = None
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    reasons: List[str] = Field(default_factory=list)
    booking_url: Optional[str] = None
    source_status: SourceStatus = "user_confirmation_required"
    source_refs: List[str] = Field(default_factory=list)


class LodgingPlanV3(ContractModel):
    status: SectionStatus
    status_reason: Optional[str] = None
    planning_lodging_id: Optional[str] = None
    options: List[LodgingOptionV3] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_planning_lodging(self) -> "LodgingPlanV3":
        option_ids = [option.lodging_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("住宿 lodging_id 不能重复")
        if self.planning_lodging_id and self.planning_lodging_id not in option_ids:
            raise ValueError("planning_lodging_id 必须指向当前住宿建议")
        return self


class RouteLegV3(ContractModel):
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    distance_meters: Optional[int] = Field(default=None, ge=0)
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    estimated_cost: Optional[MoneyV3] = None
    provider: Optional[str] = None
    coordinate_system: Optional[Literal["WGS84", "BD09LL"]] = None
    geometry: Optional[Dict[str, Any]] = None
    calculated_at: Optional[AwareDatetime] = None
    status: Literal["ready", "unavailable"] = "unavailable"

    @model_validator(mode="after")
    def validate_ready_route(self) -> "RouteLegV3":
        if self.status == "ready" and (
            not self.provider
            or self.coordinate_system not in {"WGS84", "BD09LL"}
            or not is_valid_route_geometry(self.geometry)
        ):
            raise ValueError("ready 路线必须包含真实 provider、坐标系与有效 LineString geometry")
        return self


class ReservationInfoV3(ContractModel):
    required: bool = False
    status: Literal["not_required", "needs_confirmation", "recommended"] = "needs_confirmation"
    guidance: Optional[str] = None
    source_ref: Optional[str] = None


class CandidateInsertionOptionV3(ContractModel):
    """One server-checked time slot that a candidate may occupy."""

    day: int = Field(ge=1, le=7)
    start_at: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_at: str = Field(pattern=r"^\d{2}:\d{2}$")
    position: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "CandidateInsertionOptionV3":
        if self.end_at <= self.start_at:
            raise ValueError("候选插入时段必须有正向时长")
        return self


class TripActivityV3(ContractModel):
    activity_id: str = Field(min_length=1)
    kind: ActivityKind
    title: str = Field(min_length=1)
    day: Optional[int] = Field(default=None, ge=1, le=7)
    order: Optional[int] = Field(default=None, ge=1)
    start_at: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end_at: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    fixed_time: bool = False
    meal_type: Optional[Literal["breakfast", "lunch", "dinner"]] = None
    place: Optional[TripPlaceV3] = None
    images: List[ImageAssetV3] = Field(default_factory=list, max_length=3)
    cover_image_id: Optional[str] = None
    estimated_cost: Optional[MoneyV3] = None
    reservation: Optional[ReservationInfoV3] = None
    estimated_cost_unit: Literal["per_traveler", "group"] = "per_traveler"
    official_traveler_prices: Dict[Literal["adult", "child", "senior"], MoneyV3] = Field(default_factory=dict)
    note_id: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    route_to_next: Optional[RouteLegV3] = None
    insertion_options: List[CandidateInsertionOptionV3] = Field(default_factory=list, max_length=7)
    insertion_unavailable_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_activity_state(self) -> "TripActivityV3":
        image_ids = [image.image_id for image in self.images]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("活动图片 image_id 不能重复")
        if self.cover_image_id and self.cover_image_id not in image_ids:
            raise ValueError("cover_image_id 必须指向当前活动图片")
        if self.meal_type and self.kind != "food":
            raise ValueError("meal_type 只能用于餐饮活动")
        if bool(self.start_at) != bool(self.end_at):
            raise ValueError("活动开始和结束时间必须同时提供")
        if self.start_at and self.end_at:
            start_hour, start_minute = (int(value) for value in self.start_at.split(":"))
            end_hour, end_minute = (int(value) for value in self.end_at.split(":"))
            start_minutes = start_hour * 60 + start_minute
            end_minutes = end_hour * 60 + end_minute
            if start_minutes >= 24 * 60 or end_minutes >= 24 * 60:
                raise ValueError("活动时间必须是当天有效的 HH:MM")
            if end_minutes <= start_minutes:
                raise ValueError("活动结束时间必须晚于开始时间")
            if self.duration_minutes is not None and self.duration_minutes <= 0:
                raise ValueError("已排期活动时长必须大于 0")
        if self.place is not None and self.kind not in {"free_time", "other"}:
            if self.place.category != self.kind:
                raise ValueError("活动 kind 必须与地点 category 一致")
        if self.day is None:
            if any(value is not None for value in (self.order, self.start_at, self.end_at)) or self.fixed_time:
                raise ValueError("候选活动不能包含日期顺序、时间或固定状态")
        elif self.kind not in {"free_time", "other"}:
            if self.place is None:
                raise ValueError("正式地点型活动必须绑定真实地点")
        if self.day is not None and (self.insertion_options or self.insertion_unavailable_reason):
            raise ValueError("已排期活动不能保留候选插入信息")
        return self


class TripAnchorV3(ContractModel):
    anchor_id: str = Field(min_length=1)
    kind: Literal["lodging_departure", "lodging_return", "arrival_hub", "departure_hub"]
    day: int = Field(ge=1, le=7)
    place: TripPlaceV3
    display_label: str = Field(min_length=1)


class TripDayV3(ContractModel):
    day: int = Field(ge=1, le=7)
    date: Optional[DateValue] = None
    timezone: str = Field(min_length=1)
    theme: Optional[str] = None
    note_id: Optional[str] = None
    activities: List[TripActivityV3] = Field(default_factory=list)
    anchors: List[TripAnchorV3] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_day_members(self) -> "TripDayV3":
        orders: List[int] = []
        for activity in self.activities:
            if activity.day != self.day:
                raise ValueError("正式活动 day 必须与所属日一致")
            if activity.order is None:
                raise ValueError("正式活动必须包含 order")
            orders.append(activity.order)
        if len(orders) != len(set(orders)):
            raise ValueError("同一天活动 order 不能重复")
        if any(anchor.day != self.day for anchor in self.anchors):
            raise ValueError("锚点 day 必须与所属日一致")
        return self


class ItineraryV3(ContractModel):
    days: List[TripDayV3] = Field(min_length=1, max_length=7)


class TripActionItemV3(ContractModel):
    action_id: str = Field(min_length=1)
    kind: Literal["checklist", "reservation_reminder"]
    scope: Literal["trip", "day", "activity"]
    target_id: Optional[str] = None
    text: str = Field(min_length=1)
    status: Literal["pending", "completed", "overdue"] = "pending"
    due_at: Optional[AwareDatetime] = None
    timezone: Optional[str] = None
    source_ref: Optional[str] = None

    @model_validator(mode="after")
    def validate_scope_and_due_time(self) -> "TripActionItemV3":
        if self.scope != "trip" and not self.target_id:
            raise ValueError("day/activity 行动项必须提供 target_id")
        if self.due_at is not None and not self.timezone:
            raise ValueError("行动项 due_at 存在时必须提供 timezone")
        return self


class TripNoteV3(ContractModel):
    note_id: str = Field(min_length=1)
    scope: Literal["trip", "day", "activity"]
    target_id: Optional[str] = None
    content: str = Field(min_length=1)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_note_scope(self) -> "TripNoteV3":
        if self.scope != "trip" and not self.target_id:
            raise ValueError("day/activity 备注必须提供 target_id")
        return self


class BudgetCategoryV3(ContractModel):
    category: Literal["transport", "lodging", "food", "activities", "shopping", "other"]
    amount: MoneyV3


class TravelerBudgetCostV3(ContractModel):
    traveler_type: Literal["adult", "child", "senior"]
    count: int = Field(ge=0)
    estimated_total: Optional[MoneyV3] = None
    pricing_status: Literal[
        "standard_price",
        "official_discount_verified",
        "adult_price_assumed",
        "not_applicable",
    ] = "not_applicable"


class BudgetSummaryV3(ContractModel):
    total_budget: MoneyV3
    estimated_total: Optional[MoneyV3] = None
    categories: List[BudgetCategoryV3] = Field(default_factory=list)
    unknown_cost_count: int = Field(default=0, ge=0)
    over_budget: bool = False
    overrun_amount: Optional[MoneyV3] = None
    traveler_costs: List[TravelerBudgetCostV3] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DayRouteV3(ContractModel):
    day: int = Field(ge=1, le=7)
    status: Literal["ready", "partial", "unavailable"]
    legs: List[RouteLegV3] = Field(default_factory=list)


class MapGuidanceV3(ContractModel):
    status: SectionStatus
    status_reason: Optional[str] = None
    formal_location_ids: List[str] = Field(default_factory=list)
    day_routes: List[DayRouteV3] = Field(default_factory=list)
    candidates_visible_by_default: Literal[False] = False


class TripSourceV3(ContractModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    status: SourceStatus
    url: Optional[str] = None
    updated_at: Optional[AwareDatetime] = None
    related_fields: List[str] = Field(default_factory=list)
    related_place_ids: List[str] = Field(default_factory=list)


class DeliveryV3(ContractModel):
    markdown_filename: str = Field(min_length=1)
    pdf_filename: str = Field(min_length=1)
    markdown_enabled: bool = True
    pdf_enabled: bool = True


class ValidationIssueV3(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Literal["info", "warning", "error"]
    target_id: Optional[str] = None


class ValidationSummaryV3(ContractModel):
    valid: bool = True
    degraded: bool = False
    issues: List[ValidationIssueV3] = Field(default_factory=list)


class DestinationOverviewV3(ContractModel):
    name_zh: str = Field(min_length=1)
    name_en: Optional[str] = None
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    timezone: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    summary: Optional[str] = None
    themes: List[str] = Field(default_factory=list)
    cover_image: Optional[ImageAssetV3] = None
    evidence_refs: List[str] = Field(default_factory=list)


class TravelPlanDocumentV3(ContractModel):
    schema_version: Literal["3.0"] = "3.0"
    plan_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: DocumentStatus
    title: str = Field(min_length=1)
    generated_at: AwareDatetime
    locale: str = "zh-CN"
    intent: TripIntentV3
    destination_overview: DestinationOverviewV3
    outbound_transport: TransportSectionV3
    lodging_plan: LodgingPlanV3
    itinerary: ItineraryV3
    candidate_pool: List[TripActivityV3] = Field(default_factory=list, max_length=15)
    action_items: List[TripActionItemV3] = Field(default_factory=list)
    notes: List[TripNoteV3] = Field(default_factory=list)
    budget: BudgetSummaryV3
    return_transport: TransportSectionV3
    map_guidance: MapGuidanceV3
    sources: List[TripSourceV3] = Field(default_factory=list)
    delivery: DeliveryV3
    validation: ValidationSummaryV3

    @model_validator(mode="after")
    def validate_document_integrity(self, info: ValidationInfo) -> "TravelPlanDocumentV3":
        # 草稿允许携带待修复的业务冲突；发布、导出默认仍严格要求正式校验通过。
        if not self.validation.valid and not (info.context or {}).get("draft"):
            raise ValueError("正式 V3 文档必须通过业务校验")
        if self.status == "degraded" and not self.validation.degraded:
            raise ValueError("degraded 文档必须在 validation 中明确降级")
        if self.status == "formal" and self.validation.degraded:
            raise ValueError("formal 文档不能携带降级标记")
        section_statuses = [
            self.outbound_transport.status,
            self.lodging_plan.status,
            self.return_transport.status,
            self.map_guidance.status,
        ]
        if self.status == "formal" and any(status != "ready" for status in section_statuses):
            raise ValueError("包含降级或待确认章节的文档必须标记为 degraded")

        expected_days = list(range(1, self.intent.days + 1))
        actual_days = [day.day for day in self.itinerary.days]
        if actual_days != expected_days:
            raise ValueError("itinerary.days 必须从 1 开始连续排列")
        if self.intent.date_mode == "fixed":
            assert self.intent.start_date is not None
            if [day.date for day in self.itinerary.days] != [
                date.fromordinal(self.intent.start_date.toordinal() + offset)
                for offset in range(self.intent.days)
            ]:
                raise ValueError("行程日期必须与 intent 日期区间一致")
        elif any(day.date is not None for day in self.itinerary.days):
            raise ValueError("日期待定行程必须按第 1 天至第 N 天表达，不能填入虚构日期")

        if self.intent.date_mode == "flexible":
            for section in (self.outbound_transport, self.return_transport):
                if section.options or section.selected_option_id:
                    raise ValueError("日期待定行程不能包含具体往返交通方案")

        activities = [activity for day in self.itinerary.days for activity in day.activities]
        candidates = self.candidate_pool
        if any(candidate.day is not None for candidate in candidates):
            raise ValueError("candidate_pool 只能包含未排期活动")
        activity_ids = [activity.activity_id for activity in activities + candidates]
        if len(activity_ids) != len(set(activity_ids)):
            raise ValueError("活动 activity_id 在正式日程和候选池中必须全局唯一")

        for day in self.itinerary.days:
            timed = [activity for activity in day.activities if activity.start_at and activity.end_at]
            timed.sort(key=lambda activity: (activity.start_at or "", activity.end_at or ""))
            for left, right in zip(timed, timed[1:]):
                if (left.end_at or "") > (right.start_at or ""):
                    raise ValueError(f"第 {day.day} 天活动时间不能重叠")

        def selected_option(section: TransportSectionV3) -> Optional[TransportOptionV3]:
            return next(
                (option for option in section.options if option.option_id == section.selected_option_id),
                None,
            )

        outbound = selected_option(self.outbound_transport)
        first_day = self.itinerary.days[0]
        if outbound and outbound.arrival_time and outbound.arrival_time.local_iso and first_day.date:
            arrival = outbound.arrival_time.local_iso
            if arrival.date() == first_day.date:
                earliest_start = min(
                    (activity.start_at for activity in first_day.activities if activity.start_at),
                    default=None,
                )
                if earliest_start and earliest_start < arrival.strftime("%H:%M"):
                    raise ValueError("首日活动不能早于所选去程到达时间")

        returning = selected_option(self.return_transport)
        last_day = self.itinerary.days[-1]
        if returning and returning.departure_time and returning.departure_time.local_iso and last_day.date:
            departure = returning.departure_time.local_iso
            if departure.date() == last_day.date:
                latest_end = max(
                    (activity.end_at for activity in last_day.activities if activity.end_at),
                    default=None,
                )
                if latest_end and latest_end > departure.strftime("%H:%M"):
                    raise ValueError("末日活动不能晚于所选返程出发时间")

        anchors = [anchor for day in self.itinerary.days for anchor in day.anchors]
        anchor_ids = [anchor.anchor_id for anchor in anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("锚点 anchor_id 必须全局唯一")

        note_ids = [note.note_id for note in self.notes]
        if len(note_ids) != len(set(note_ids)):
            raise ValueError("note_id 必须全局唯一")
        note_id_set = set(note_ids)
        referenced_note_ids = [
            note_id
            for note_id in [
                *(day.note_id for day in self.itinerary.days),
                *(activity.note_id for activity in activities + candidates),
            ]
            if note_id
        ]
        if any(note_id not in note_id_set for note_id in referenced_note_ids):
            raise ValueError("日程和活动的 note_id 必须指向 notes")

        day_target_ids = {str(day.day) for day in self.itinerary.days}
        activity_id_set = set(activity_ids)
        for item in self.action_items:
            if item.scope == "day" and item.target_id not in day_target_ids:
                raise ValueError("day 行动项必须指向当前行程日期序号")
            if item.scope == "activity" and item.target_id not in activity_id_set:
                raise ValueError("activity 行动项必须指向当前活动")

        expected_map_ids = {
            activity.activity_id for activity in activities if activity.place is not None
        } | set(anchor_ids)
        if set(self.map_guidance.formal_location_ids) != expected_map_ids:
            raise ValueError("地图正式地点必须与当前修订的正式活动和锚点一致")
        if any(route.day not in expected_days for route in self.map_guidance.day_routes):
            raise ValueError("地图路线 day 必须属于当前行程")
        for route in self.map_guidance.day_routes:
            for leg in route.legs:
                if leg.from_id not in expected_map_ids or leg.to_id not in expected_map_ids:
                    raise ValueError("地图路线端点必须属于当前正式地图地点")
        for activity in activities:
            if activity.route_to_next:
                if activity.route_to_next.from_id != activity.activity_id:
                    raise ValueError("活动 route_to_next.from_id 必须指向活动自身")
                if activity.route_to_next.to_id not in expected_map_ids:
                    raise ValueError("活动 route_to_next.to_id 必须属于当前正式地图地点")

        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id 必须全局唯一")
        source_id_set = set(source_ids)
        refs: List[str] = list(self.destination_overview.evidence_refs)
        if self.destination_overview.cover_image and self.destination_overview.cover_image.source_ref:
            refs.append(self.destination_overview.cover_image.source_ref)
        for activity in activities + candidates:
            refs.extend(activity.evidence_refs)
            if activity.place:
                refs.extend(activity.place.evidence_refs)
                if activity.place.review_summary:
                    refs.extend(activity.place.review_summary.source_refs)
            for image in activity.images:
                if image.source_ref:
                    refs.append(image.source_ref)
            if activity.reservation and activity.reservation.source_ref:
                refs.append(activity.reservation.source_ref)
        for section in (self.outbound_transport, self.return_transport):
            for option in section.options:
                refs.extend(option.source_refs)
        for option in self.lodging_plan.options:
            refs.extend(option.source_refs)
            if option.place:
                refs.extend(option.place.evidence_refs)
        refs.extend(item.source_ref for item in self.action_items if item.source_ref)
        if any(ref not in source_id_set for ref in refs):
            raise ValueError("文档中的来源引用必须指向 sources")
        return self


class RunStartedPayload(ContractModel):
    status: Literal["planning"] = "planning"
    message: str = Field(min_length=1)


class AgentStagePayload(ContractModel):
    task_id: Optional[str] = None
    attempt: Optional[int] = Field(default=None, ge=1)
    result: Optional[Dict[str, Any]] = None
    stage: PlanningStage
    agent_name: str = Field(min_length=1)
    task: str = Field(min_length=1)
    data_sources: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    status: Literal["running", "retrying", "completed", "degraded", "failed"]


class ProviderQueueWaitingPayload(ContractModel):
    stage: Literal["realtime_verification"] = "realtime_verification"
    provider: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    priority: Literal["formal", "scheduled_dining", "candidate", "supplemental"]
    queue_position: int = Field(ge=1)
    message: Literal["地点核验排队中"] = "地点核验排队中"


class SoftTimeoutPayload(ContractModel):
    elapsed_seconds: int = Field(ge=180)
    message: str = Field(min_length=1)


PlanSectionName = Literal[
    "destination_overview",
    "outbound_transport",
    "lodging_plan",
    "itinerary",
    "budget_and_reminders",
    "map_guidance",
    "delivery",
    "sources",
]


class PlanSectionPayload(ContractModel):
    plan_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    section: PlanSectionName
    content: Dict[str, Any]


class PlanCompletedPayload(ContractModel):
    plan_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: Literal["completed", "completed_degraded"]
    checksum: str = Field(min_length=1)


class PlanRevisionStartedPayload(ContractModel):
    plan_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    operation_id: str = Field(min_length=1)


class RunCancelledPayload(ContractModel):
    reason: str = Field(min_length=1)


class PlanningErrorPayload(ContractModel):
    code: str = Field(min_length=1)
    user_message: str = Field(min_length=1)
    retryable: bool = False
    actions: List[str] = Field(default_factory=list)
    details: List[Dict[str, str]] = Field(default_factory=list, max_length=8)


PlanningEventPayload = Union[
    RunStartedPayload,
    AgentStagePayload,
    ProviderQueueWaitingPayload,
    SoftTimeoutPayload,
    PlanSectionPayload,
    PlanCompletedPayload,
    PlanRevisionStartedPayload,
    RunCancelledPayload,
    PlanningErrorPayload,
]


class PlanningEventEnvelope(ContractModel):
    event_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime
    type: PlanningEventType
    payload: PlanningEventPayload

    @model_validator(mode="after")
    def validate_payload_for_type(self) -> "PlanningEventEnvelope":
        expected_payloads = {
            "run_started": RunStartedPayload,
            "agent_stage_started": AgentStagePayload,
            "agent_stage_updated": AgentStagePayload,
            "agent_stage_retrying": AgentStagePayload,
            "agent_stage_completed": AgentStagePayload,
            "provider_queue_waiting": ProviderQueueWaitingPayload,
            "run_soft_timeout": SoftTimeoutPayload,
            "final_plan_section": PlanSectionPayload,
            "trip_plan_completed": PlanCompletedPayload,
            "plan_revision_started": PlanRevisionStartedPayload,
            "plan_revision_section": PlanSectionPayload,
            "plan_revision_completed": PlanCompletedPayload,
            "run_cancelled": RunCancelledPayload,
            "error": PlanningErrorPayload,
        }
        if not isinstance(self.payload, expected_payloads[self.type]):
            raise ValueError(f"事件 {self.type} 的 payload 类型不匹配")
        return self
