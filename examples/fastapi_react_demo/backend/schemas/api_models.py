from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str
    message_id: str = None
    type: str = "normal"


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


class TripEditOperation(BaseModel):
    operation_id: str
    plan_id: str
    base_version: int
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class TripEditRequest(BaseModel):
    plan: Dict[str, Any]
    operation: TripEditOperation


class TripEditResponse(BaseModel):
    operation_id: Optional[str] = None
    plan: Dict[str, Any]
    previous_plan: Dict[str, Any]
    diff: Dict[str, Any]


class TripDocumentImportRequest(BaseModel):
    format: Literal["json", "markdown"]
    content: Any


class TripDocumentExportRequest(BaseModel):
    document: Dict[str, Any]


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
