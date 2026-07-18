from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

from agents.tool.baidu_request_dispatcher import BaiduRequestDispatcher

if TYPE_CHECKING:
    from agents.agent.agent_controller import AgentController
    from agents.tool.tool_manager import ToolManager
else:
    AgentController = Any
    ToolManager = Any


@dataclass
class RuntimeState:
    """Container for mutable backend runtime dependencies."""

    tool_manager: Optional[ToolManager] = None
    controller: Optional[AgentController] = None
    active_sessions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    trip_repository: Optional[Any] = None
    baidu_request_dispatcher: BaiduRequestDispatcher = field(default_factory=BaiduRequestDispatcher)
    provider_gateway: Optional[Any] = None
    planning_orchestrator: Optional[Any] = None
    planning_run_manager: Optional[Any] = None


def create_runtime_state() -> RuntimeState:
    """Create a fresh runtime state instance for app process scope."""
    return RuntimeState()
