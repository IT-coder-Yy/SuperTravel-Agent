from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str
    message_id: str = None
    type: str = "normal"


class ChatRequest(BaseModel):
    type: str = "chat"
    messages: List[ChatMessage]
    use_deepthink: bool = True
    use_multi_agent: bool = True
    session_id: Optional[str] = None
    selected_mcp_servers: Optional[List[str]] = Field(default_factory=list)
    selected_skill_ids: Optional[List[str]] = Field(default_factory=list)


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
