import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ChatMessage(BaseModel):
    role: str
    content: str
    message_id: str = None
    type: str = "normal"


class StructuredTripCreateRequest(BaseModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    start_date: datetime.date
    end_date: datetime.date
    adults: int = Field(ge=0, le=20)
    children: int = Field(ge=0, le=20)
    seniors: int = Field(ge=0, le=20)
    budget: float = Field(gt=0, le=1_000_000)
    party_type: Optional[Literal["独自", "情侣", "朋友", "亲子", "家庭"]] = None
    preferences: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_complete_trip(self):
        self.origin = self.origin.strip()
        self.destination = self.destination.strip()
        if not self.origin or not self.destination:
            raise ValueError("出发地和目的地不能为空")
        if self.origin == self.destination:
            raise ValueError("出发地和目的地不能相同")
        if self.adults + self.children + self.seniors < 1:
            raise ValueError("至少需要一位出行人")
        days = (self.end_date - self.start_date).days + 1
        if days < 1 or days > 7:
            raise ValueError("行程只支持 1～7 天")
        if self.start_date < datetime.date.today():
            raise ValueError("出发日期不能早于今天")
        self.preferences = [item.strip() for item in self.preferences if item.strip()]
        return self


class ChatRequest(BaseModel):
    type: str = "chat"
    request_id: Optional[str] = None
    messages: List[ChatMessage]
    use_deepthink: bool = True
    use_multi_agent: bool = True
    session_id: Optional[str] = None
    trip_id: Optional[str] = None
    selected_mcp_servers: Optional[List[str]] = None
    selected_skill_ids: Optional[List[str]] = Field(default_factory=list)
    profile: Optional[Dict[str, Any]] = Field(default_factory=dict)
    planning_mode: Optional[str] = None
    allow_web_search: bool = True
    clarification_answers: Optional[Dict[str, Any]] = Field(default_factory=dict)
    clarification_question_id: Optional[str] = None
    selected_knowledge_context: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    input_source: Literal["natural_language", "structured_form"] = "natural_language"
    structured_trip_request: Optional[StructuredTripCreateRequest] = None

    @model_validator(mode="after")
    def require_structured_trip_request(self):
        if self.input_source == "structured_form" and self.structured_trip_request is None:
            raise ValueError("结构化表单请求缺少 structured_trip_request")
        return self


class ReverseGeocodeRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class TripEditOperation(BaseModel):
    operation_id: str
    plan_id: str
    base_version: int
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class TripEditRequest(BaseModel):
    plan: Optional[Dict[str, Any]] = None
    operation: TripEditOperation
    document: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def require_plan_or_document(self):
        if self.plan is None and self.document is None:
            raise ValueError("plan 与 document 至少需要提供一个")
        return self


class TripEditResponse(BaseModel):
    operation_id: Optional[str] = None
    plan: Dict[str, Any]
    previous_plan: Dict[str, Any]
    diff: Dict[str, Any]
    document: Optional[Dict[str, Any]] = None
    previous_document: Optional[Dict[str, Any]] = None
    draft_validation: Optional[Dict[str, Any]] = None


class TripDraftValidationRequest(BaseModel):
    document: Dict[str, Any]


class TripActivityDeleteImpactRequest(BaseModel):
    document: Dict[str, Any]
    activity_id: str


class TripDayRouteOptimizationPreviewRequest(BaseModel):
    document: Dict[str, Any]
    day: int = Field(ge=1, le=7)


class TripDocumentImportRequest(BaseModel):
    format: Literal["json", "markdown"]
    content: Any


class TripDocumentExportRequest(BaseModel):
    model_config = {"extra": "forbid"}
    trip_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)


class ShareCreateRequest(BaseModel):
    document: Dict[str, Any]
    scopes: List[Literal["itinerary", "budget", "sources", "checklist", "notes"]] = Field(
        default_factory=lambda: ["itinerary"]
    )


class DayRouteRequest(BaseModel):
    day: int = Field(ge=1)
    plan_version: int = Field(ge=1)
    scope: Literal["domestic", "international"] = "domestic"
    activities: List[Dict[str, Any]] = Field(default_factory=list)


class ConfigRequest(BaseModel):
    api_key: str
    model_name: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = 0.7


class ToolInfo(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str
    workflow_steps: List[str]
    allowed_mcp_servers: List[str]
    allowed_local_tools: List[str]
    answer_style: str
    fallback: str
    required_env: List[str]
    available: bool
    missing_mcp_servers: List[str]
    missing_local_tools: List[str]
    missing_env: List[str]


class SystemStatus(BaseModel):
    status: str
    agents_count: int
    tools_count: int
    active_sessions: int
    version: str = "0.8"
    model_name: Optional[str] = None
    base_url: Optional[str] = None
    api_key_masked: Optional[str] = None
    config_source: Optional[str] = None


class PowerPaintStatus(BaseModel):
    url: str
    reachable: bool
    message: str
