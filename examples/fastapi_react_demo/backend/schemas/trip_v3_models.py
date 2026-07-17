from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


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


class TravelerCountV3(ContractModel):
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    seniors: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.seniors


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
    start_date: date
    end_date: date
    days: int = Field(ge=1, le=7)
    travelers: TravelerCountV3
    budget: MoneyV3
    preferences: List[str] = Field(default_factory=list)
    dietary_preferences: List[str] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "intensive"] = "balanced"

    @model_validator(mode="after")
    def validate_date_range(self) -> "TripIntentV3":
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
    coordinates: CoordinatesV3
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
        if self.local_iso is not None and not self.timezone:
            raise ValueError("local_iso 存在时必须提供 IANA timezone")
        return self


class TransportOptionV3(ContractModel):
    option_id: str = Field(min_length=1)
    mode: Literal["train", "intercity_bus", "flight"]
    service_number: Optional[str] = None
    departure_place: Optional[str] = None
    arrival_place: Optional[str] = None
    departure_time: Optional[TransportTimeV3] = None
    arrival_time: Optional[TransportTimeV3] = None
    duration_minutes: Optional[int] = Field(default=None, ge=0)
    transfers: Optional[int] = Field(default=None, ge=0)
    price: Optional[MoneyV3] = None
    availability: Literal["available", "limited", "unknown"] = "unknown"
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
        if self.status == "ready" and (not self.provider or self.geometry is None):
            raise ValueError("ready 路线必须包含真实 provider 与 geometry")
        return self


class ReservationInfoV3(ContractModel):
    required: bool = False
    status: Literal["not_required", "needs_confirmation", "recommended"] = "needs_confirmation"
    guidance: Optional[str] = None
    source_ref: Optional[str] = None


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
    place: Optional[TripPlaceV3] = None
    images: List[ImageAssetV3] = Field(default_factory=list, max_length=3)
    cover_image_id: Optional[str] = None
    estimated_cost: Optional[MoneyV3] = None
    reservation: Optional[ReservationInfoV3] = None
    note_id: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    route_to_next: Optional[RouteLegV3] = None

    @model_validator(mode="after")
    def validate_activity_state(self) -> "TripActivityV3":
        image_ids = [image.image_id for image in self.images]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("活动图片 image_id 不能重复")
        if self.cover_image_id and self.cover_image_id not in image_ids:
            raise ValueError("cover_image_id 必须指向当前活动图片")
        if self.place is not None and self.kind not in {"free_time", "other"}:
            if self.place.category != self.kind:
                raise ValueError("活动 kind 必须与地点 category 一致")
        if self.day is None:
            if any(value is not None for value in (self.order, self.start_at, self.end_at)) or self.fixed_time:
                raise ValueError("候选活动不能包含日期顺序、时间或固定状态")
        elif self.kind not in {"free_time", "other"}:
            if self.place is None:
                raise ValueError("正式地点型活动必须绑定真实地点")
        return self


class TripAnchorV3(ContractModel):
    anchor_id: str = Field(min_length=1)
    kind: Literal["lodging_departure", "lodging_return", "arrival_hub", "departure_hub"]
    day: int = Field(ge=1, le=7)
    place: TripPlaceV3
    display_label: str = Field(min_length=1)


class TripDayV3(ContractModel):
    day: int = Field(ge=1, le=7)
    date: date
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


class BudgetSummaryV3(ContractModel):
    total_budget: MoneyV3
    estimated_total: Optional[MoneyV3] = None
    categories: List[BudgetCategoryV3] = Field(default_factory=list)
    unknown_cost_count: int = Field(default=0, ge=0)
    over_budget: bool = False
    overrun_amount: Optional[MoneyV3] = None
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
    def validate_document_integrity(self) -> "TravelPlanDocumentV3":
        if not self.validation.valid:
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
        if [day.date for day in self.itinerary.days] != [
            date.fromordinal(self.intent.start_date.toordinal() + offset)
            for offset in range(self.intent.days)
        ]:
            raise ValueError("行程日期必须与 intent 日期区间一致")

        activities = [activity for day in self.itinerary.days for activity in day.activities]
        candidates = self.candidate_pool
        if any(candidate.day is not None for candidate in candidates):
            raise ValueError("candidate_pool 只能包含未排期活动")
        activity_ids = [activity.activity_id for activity in activities + candidates]
        if len(activity_ids) != len(set(activity_ids)):
            raise ValueError("活动 activity_id 在正式日程和候选池中必须全局唯一")

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
    stage: PlanningStage
    agent_name: str = Field(min_length=1)
    task: str = Field(min_length=1)
    data_sources: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    status: Literal["running", "retrying", "completed", "degraded", "failed"]


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


PlanningEventPayload = Union[
    RunStartedPayload,
    AgentStagePayload,
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
