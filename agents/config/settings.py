import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any
from agents.utils.logger import logger


def _default_output_root() -> str:
    """Resolve output root from env or repository-local outputs directory."""
    env_root = os.getenv('SAGE_OUTPUT_ROOT')
    if env_root:
        return str(Path(env_root).expanduser().resolve())

    repo_root = Path(__file__).resolve().parents[2]
    return str((repo_root / 'outputs').resolve())

@dataclass
class ModelConfig:
    model_name: str = "qwen-flash-character"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 60

@dataclass  
class AgentConfig:
    max_loop_count: int = 10
    enable_deep_thinking: bool = True
    enable_summary: bool = True
    task_timeout: int = 300

@dataclass
class ToolConfig:
    tool_timeout: int = 30
    max_concurrent_tools: int = 5

@dataclass
class Settings:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    tool: ToolConfig = field(default_factory=ToolConfig)
    debug: bool = False
    environment: str = "development"
    output_root: str = field(default_factory=_default_output_root)
    
    def __post_init__(self):
        # 从环境变量加载配置
        self.debug = os.getenv('SAGE_DEBUG', 'false').lower() == 'true'
        self.environment = os.getenv('SAGE_ENVIRONMENT', self.environment)

        if os.getenv('SAGE_MODEL_NAME'):
            self.model.model_name = os.getenv('SAGE_MODEL_NAME')
        if os.getenv('SAGE_API_KEY'):
            self.model.api_key = os.getenv('SAGE_API_KEY')
        elif os.getenv('DASHSCOPE_API_KEY'):
            # 兼容百炼示例环境变量，优先使用 SAGE_API_KEY。
            self.model.api_key = os.getenv('DASHSCOPE_API_KEY')
        elif os.getenv('OPENAI_API_KEY'):
            # 兼容旧环境变量，优先使用 SAGE_API_KEY。
            self.model.api_key = os.getenv('OPENAI_API_KEY')
        if os.getenv('SAGE_BASE_URL'):
            self.model.base_url = os.getenv('SAGE_BASE_URL')
        if os.getenv('SAGE_MAX_TOKENS'):
            self.model.max_tokens = int(os.getenv('SAGE_MAX_TOKENS'))
        if os.getenv('SAGE_TEMPERATURE'):
            self.model.temperature = float(os.getenv('SAGE_TEMPERATURE'))
        
        if os.getenv('SAGE_MAX_LOOP_COUNT'):
            self.agent.max_loop_count = int(os.getenv('SAGE_MAX_LOOP_COUNT'))
        if os.getenv('SAGE_TOOL_TIMEOUT'):
            self.tool.tool_timeout = int(os.getenv('SAGE_TOOL_TIMEOUT'))
    
    def get_model_config_dict(self) -> Dict[str, Any]:
        return {
            'model': self.model.model_name,
            'max_tokens': self.model.max_tokens,
            'temperature': self.model.temperature,
            'timeout': self.model.timeout
        }
    
    def get_workspace_path(self, session_id: str) -> str:
        return str(Path(self.output_root) / session_id)
    
    def export_config(self, format: str = "yaml") -> str:
        import json
        config_dict = {
            'debug': self.debug,
            'environment': self.environment,
            'output_root': self.output_root,
            'model': {
                'model_name': self.model.model_name,
                'base_url': self.model.base_url,
                'max_tokens': self.model.max_tokens,
                'temperature': self.model.temperature
            },
            'agent': {
                'max_loop_count': self.agent.max_loop_count,
                'enable_deep_thinking': self.agent.enable_deep_thinking,
                'enable_summary': self.agent.enable_summary
            },
            'tool': {
                'tool_timeout': self.tool.tool_timeout,
                'max_concurrent_tools': self.tool.max_concurrent_tools
            }
        }
        return json.dumps(config_dict, indent=2)

# 全局配置实例
_settings_instance = None

def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance

def update_settings(**kwargs):
    settings = get_settings()
    for key, value in kwargs.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
        else:
            logger.warning(f"Settings: 未知配置项: {key}")

def reset_settings():
    global _settings_instance
    _settings_instance = None 