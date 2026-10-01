"""
Sage Multi-Agent Demo

智能多智能体协作演示应用
主要优化：代码结构、错误处理、用户体验、性能
"""

import os
import sys
import uuid
import argparse
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional

import streamlit as st
from openai import OpenAI

# 设置页面配置 - 必须在任何其他streamlit调用之前
st.set_page_config(
    page_title="Sage Multi-Agent Framework",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 项目路径配置
project_root = Path(os.path.realpath(__file__)).parent.parent
sys.path.append(str(project_root))

from agents.agent.agent_controller import AgentController
from agents.professional_agents.code_agents import CodeAgent
from agents.tool.tool_manager import ToolManager
from agents.utils import logger
from agents.config import get_settings, update_settings, Settings
from agents.utils import (
    SageException, 
    ToolExecutionError, 
    AgentTimeoutError,
    with_retry,
    exponential_backoff,
    handle_exception
)


class ComponentManager:
    """组件管理器 - 负责初始化和管理核心组件"""
    
    def __init__(self, api_key: str, model_name: str = None, base_url: str = None, 
                 tools_folders: List[str] = None, max_tokens: int = None, temperature: float = None):
        # 获取已更新的全局配置
        self.settings = get_settings()
        
        logger.debug(f"使用配置 - 模型: {self.settings.model.model_name}, 温度: {self.settings.model.temperature}")
        
        # 设置工具文件夹
        self.tools_folders = tools_folders or []
        
        # 初始化组件变量
        self._tool_manager: Optional[ToolManager] = None
        self._controller: Optional[AgentController] = None
        self._model: Optional[OpenAI] = None
        
    def initialize(self) -> tuple[ToolManager, AgentController]:
        """初始化所有组件"""
        try:
            logger.info(f"初始化组件，模型: {self.settings.model.model_name}")
            
            # 初始化工具管理器
            self._tool_manager = self._init_tool_manager()
            
            # 初始化模型和控制器
            self._model = self._init_model()
            self._controller = self._init_controller()
            
            logger.info("所有组件初始化成功")
            return self._tool_manager, self._controller
            
        except Exception as e:
            logger.error(f"组件初始化失败: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    def _init_tool_manager(self) -> ToolManager:
        """初始化工具管理器"""
        logger.debug("初始化工具管理器")
        tool_manager = ToolManager()
        
        # 注册工具目录
        for folder in self.tools_folders:
            if Path(folder).exists():
                logger.debug(f"注册工具目录: {folder}")
                tool_manager.register_tools_from_directory(folder)
            else:
                logger.warning(f"工具目录不存在: {folder}")
        
        return tool_manager
    
    @with_retry(exponential_backoff(max_attempts=3, base_delay=1.0, max_delay=5.0))
    def _init_model(self) -> OpenAI:
        """初始化模型"""
        logger.debug(f"初始化模型，base_url: {self.settings.model.base_url}")
        try:
            return OpenAI(
                api_key=self.settings.model.api_key,
                base_url=self.settings.model.base_url
            )
        except Exception as e:
            logger.error(f"模型初始化失败: {str(e)}")
            raise SageException(f"无法连接到 OpenAI API: {str(e)}")
    
    @with_retry(exponential_backoff(max_attempts=2, base_delay=0.5, max_delay=2.0))
    def _init_controller(self) -> AgentController:
        """初始化控制器"""
        try:
            model_config = {
                "model": self.settings.model.model_name,
                "temperature": self.settings.model.temperature,
                "max_tokens": self.settings.model.max_tokens
            }
            
            controller = AgentController(self._model, model_config)
            
            # 注册代码智能体
            try:
                code_agent = CodeAgent(self._model, model_config)
                self._tool_manager.register_tool(code_agent.to_tool())
                logger.debug("代码智能体注册成功")
            except Exception as e:
                logger.warning(f"代码智能体注册失败: {str(e)}")
                # 不中断整个初始化过程，代码智能体是可选的
            
            return controller
            
        except Exception as e:
            logger.error(f"控制器初始化失败: {str(e)}")
            raise SageException(f"无法初始化智能体控制器: {str(e)}")


def convert_messages_for_show(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换消息格式用于显示"""
    logger.debug(f"转换 {len(messages)} 条消息用于显示")
    new_messages = []
    
    for message in messages:
        if not message.get('show_content'):
            continue
            
        new_message = {
            'message_id': message.get('message_id', str(uuid.uuid4())),
            'role': 'assistant' if message['role'] != 'user' else 'user',
            'content': message.get('show_content')
        }
        new_messages.append(new_message)
        
    return new_messages


def create_user_message(content: str) -> Dict[str, Any]:
    """创建用户消息"""
    return {
        "role": "user",
        "content": content,
        "type": "normal",
        "message_id": str(uuid.uuid4())
    }


class StreamingHandler:
    """流式处理器 - 处理实时消息流"""
    
    def __init__(self, controller: AgentController):
        self.controller = controller
        self._current_stream = None
        self._current_stream_id = None
    
    def process_stream(self, 
                      messages: List[Dict[str, Any]], 
                      tool_manager: ToolManager,
                      session_id: Optional[str] = None,
                      use_deepthink: bool = True,
                      use_multi_agent: bool = True) -> List[Dict[str, Any]]:
        """处理消息流"""
        logger.debug("开始处理流式响应")
        
        new_messages = []
        
        try:
            for chunk in self.controller.run_stream(
                messages,
                tool_manager,
                session_id=session_id,
                deep_thinking=use_deepthink,
                summary=True,
                deep_research=use_multi_agent
            ):
                new_messages.extend(chunk)
                self._update_display(messages, new_messages)
                
        except Exception as e:
            error_info = handle_exception(e, {
                'method': 'process_stream',
                'session_id': session_id,
                'use_deepthink': use_deepthink,
                'use_multi_agent': use_multi_agent,
                'message_count': len(messages)
            })
            
            logger.error(f"流式处理出错: {str(e)}")
            
            # 根据异常类型提供不同的错误消息
            if isinstance(e, ToolExecutionError):
                error_message = f"工具执行失败: {str(e)}"
            elif isinstance(e, AgentTimeoutError):
                error_message = f"智能体响应超时: {str(e)}"
            elif isinstance(e, SageException):
                error_message = f"系统错误: {str(e)}"
            else:
                error_message = f"抱歉，处理过程中出现意外错误: {str(e)}"
            
            error_response = {
                "role": "assistant",
                "content": error_message,
                "message_id": str(uuid.uuid4()),
                "error_info": error_info
            }
            new_messages.append(error_response)
        
        return new_messages
    
    def _update_display(self, base_messages: List[Dict], new_messages: List[Dict]):
        """更新显示内容"""
        merged_messages = self.controller.task_analysis_agent._merge_messages(base_messages.copy(), new_messages)
        display_messages = convert_messages_for_show(merged_messages)
        
        # 找到最新的助手消息
        latest_assistant_msg = None
        for msg in reversed(display_messages):
            if msg['role'] in ['assistant', 'tool']:
                latest_assistant_msg = msg
                break
        
        if latest_assistant_msg:
            msg_id = latest_assistant_msg.get('message_id')
            
            # 处理新的消息流
            if msg_id != self._current_stream_id:
                logger.debug(f"检测到新消息流: {msg_id}")
                self._current_stream_id = msg_id
                self._current_stream = st.chat_message('assistant').empty()
            
            # 更新显示内容
            if self._current_stream:
                self._current_stream.write(latest_assistant_msg['content'])


def setup_ui():
    """设置用户界面"""
    st.title("🧠 Sage Multi-Agent Framework")
    st.markdown("**智能多智能体协作平台**")
    
    # 获取全局配置
    settings = get_settings()
    
    # 侧边栏设置
    with st.sidebar:
        st.header("⚙️ 设置")
        
        # 多智能体选项
        use_multi_agent = st.toggle('🤖 启用多智能体推理', 
                                   value=True)
        use_deepthink = st.toggle('🧠 启用深度思考', 
                                 value=settings.agent.enable_deep_thinking)
        
        # 系统信息
        st.subheader("📊 系统信息")
        st.info(f"**模型**: {settings.model.model_name}")
        st.info(f"**温度**: {settings.model.temperature}")
        st.info(f"**最大标记**: {settings.model.max_tokens}")
        st.info(f"**环境**: {settings.environment}")
        
        # 工具列表
        if st.session_state.get('tool_manager'):
            display_tools(st.session_state.tool_manager)
        
        # 清除历史按钮
        if st.button("🗑️ 清除对话历史", type="secondary"):
            clear_history()
    
    return use_multi_agent, use_deepthink


def display_tools(tool_manager: ToolManager):
    """显示可用工具"""
    st.subheader("🛠️ 可用工具")
    tools = tool_manager.list_tools_simplified()
    
    if tools:
        for tool_info in tools:
            with st.expander(f"🔧 {tool_info['name']}", expanded=False):
                st.write(tool_info['description'])
    else:
        st.info("暂无可用工具")


def clear_history():
    """清除对话历史"""
    logger.info("用户清除对话历史")
    st.session_state.conversation = []
    st.session_state.inference_conversation = []
    st.rerun()


def init_session_state():
    """初始化会话状态"""
    if 'conversation' not in st.session_state:
        st.session_state.conversation = []
    if 'inference_conversation' not in st.session_state:
        st.session_state.inference_conversation = []
    if 'components_initialized' not in st.session_state:
        st.session_state.components_initialized = False


def display_conversation_history():
    """显示对话历史"""
    for msg in st.session_state.conversation:
        if msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                st.write(msg['content'])


def process_user_input(user_input: str, tool_manager: ToolManager, controller: AgentController):
    """处理用户输入"""
    logger.info(f"处理用户输入: {user_input[:50]}{'...' if len(user_input) > 50 else ''}")
    
    # 创建用户消息
    user_msg = create_user_message(user_input)
    
    # 添加到对话历史
    st.session_state.conversation.append(user_msg)
    st.session_state.inference_conversation.append(user_msg)
    
    # 显示用户消息
    with st.chat_message("user"):
        st.write(user_input)
    
    # 处理响应
    with st.spinner("🤔 正在思考..."):
        try:
            generate_response(tool_manager, controller)
        except Exception as e:
            logger.error(f"生成响应时出错: {str(e)}")
            with st.chat_message("assistant"):
                st.error(f"抱歉，处理您的请求时出现了错误: {str(e)}")


def generate_response(tool_manager: ToolManager, controller: AgentController):
    """生成智能体响应"""
    streaming_handler = StreamingHandler(controller)
    
    # 处理流式响应
    new_messages = streaming_handler.process_stream(
        st.session_state.inference_conversation.copy(),
        tool_manager,
        session_id=None,
        use_deepthink=st.session_state.get('use_deepthink', True),
        use_multi_agent=st.session_state.get('use_multi_agent', True)
    )
    
    # 合并消息
    if new_messages:
        merged_messages = controller.task_analysis_agent._merge_messages(
            st.session_state.inference_conversation, new_messages
        )
        st.session_state.inference_conversation = merged_messages
        
        # 更新显示对话
        display_messages = convert_messages_for_show(merged_messages)
        st.session_state.conversation = display_messages
        
        logger.info("响应生成完成")


def update_global_settings(api_key: str, model_name: str = None, base_url: str = None, 
                          max_tokens: int = None, temperature: float = None):
    """提前更新全局设置，确保UI能显示正确的配置信息"""
    settings = get_settings()
    
    # 直接更新全局配置
    if api_key:
        settings.model.api_key = api_key
    if model_name:
        settings.model.model_name = model_name
    if base_url:
        settings.model.base_url = base_url
    if max_tokens:
        settings.model.max_tokens = max_tokens
    if temperature is not None:
        settings.model.temperature = temperature
    
    logger.debug(f"全局配置已更新 - 模型: {settings.model.model_name}, 温度: {settings.model.temperature}")


def run_web_demo(api_key: str, model_name: str = None, base_url: str = None, 
                 tools_folders: List[str] = None, max_tokens: int = None, temperature: float = None):
    """运行 Streamlit web 界面"""
    logger.info("启动 Streamlit web 演示")
    
    # 提前更新全局设置，确保UI显示正确的配置
    update_global_settings(api_key, model_name, base_url, max_tokens, temperature)
    
    # 初始化会话状态
    init_session_state()
    
    # 设置界面（此时能获取到正确的配置）
    use_multi_agent, use_deepthink = setup_ui()
    
    # 存储设置到会话状态
    st.session_state.use_multi_agent = use_multi_agent
    st.session_state.use_deepthink = use_deepthink
    
    # 初始化组件（只执行一次）
    if not st.session_state.components_initialized:
        try:
            with st.spinner("正在初始化系统组件..."):
                component_manager = ComponentManager(
                    api_key=api_key,
                    model_name=model_name,
                    base_url=base_url,
                    tools_folders=tools_folders,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                tool_manager, controller = component_manager.initialize()
                st.session_state.tool_manager = tool_manager
                st.session_state.controller = controller
                st.session_state.components_initialized = True
                st.session_state.config_updated = True  # 标记配置已更新
            st.success("系统初始化完成！")
            # 初始化完成后重新运行，确保UI显示更新后的配置
            st.rerun()
        except SageException as e:
            # 系统级异常，提供详细的错误信息和建议
            st.error(f"系统初始化失败: {str(e)}")
            error_info = handle_exception(e, {'component': 'system_initialization'})
            
            st.warning("**建议解决方案:**")
            for suggestion in error_info.get('recovery_suggestions', []):
                st.write(f"• {suggestion}")
            
            if 'API' in str(e):
                st.info("💡 **提示**: 请检查您的 API key 是否正确，网络连接是否正常")
            
            st.stop()
        except Exception as e:
            # 其他异常
            st.error(f"系统初始化失败: {str(e)}")
            error_info = handle_exception(e, {'component': 'system_initialization'})
            
            st.warning("**技术详情:**")
            st.code(traceback.format_exc())
            
            st.stop()
    
    # 显示历史对话
    display_conversation_history()
    
    # 用户输入
    user_input = st.chat_input("💬 请输入您的问题...")
    
    if user_input and user_input.strip():
        process_user_input(
            user_input.strip(), 
            st.session_state.tool_manager, 
            st.session_state.controller
        )


def parse_arguments() -> Dict[str, Any]:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='Sage Multi-Agent Interactive Chat',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python sage_demo.py --api_key YOUR_API_KEY
  python sage_demo.py --api_key YOUR_API_KEY --model gpt-4 --tools_folders ./tools
        """
    )
    
    parser.add_argument('--api_key', required=True, 
                       help='OpenRouter API key（必需）')
    parser.add_argument('--model', 
                       default='mistralai/mistral-small-3.1-24b-instruct:free',
                       help='模型名称')
    parser.add_argument('--base_url', 
                       default='https://openrouter.ai/api/v1',
                       help='API base URL')
    parser.add_argument('--tools_folders', nargs='+', default=[],
                       help='工具目录路径（多个路径用空格分隔）')
    parser.add_argument('--max_tokens', type=int, default=4096,
                       help='最大令牌数')
    parser.add_argument('--temperature', type=float, default=0.2,
                       help='温度参数')
    
    args = parser.parse_args()
    
    return {
        'api_key': args.api_key,
        'model_name': args.model,
        'base_url': args.base_url,
        'tools_folders': args.tools_folders,
        'max_tokens': args.max_tokens,
        'temperature': args.temperature
    }


def main():
    """主函数"""
    try:
        # 解析配置
        config = parse_arguments()
        logger.info(f"启动应用，模型: {config['model_name']}")
        
        # 运行 Web 演示
        run_web_demo(
            config['api_key'],
            config['model_name'],
            config['base_url'],
            config['tools_folders'],
            config['max_tokens'],
            config['temperature']
        )
        
    except SageException as e:
        logger.error(f"应用启动失败: {str(e)}")
        st.error(f"系统错误: {str(e)}")
        error_info = handle_exception(e, {'component': 'main_application'})
        
        st.warning("**恢复建议:**")
        for suggestion in error_info.get('recovery_suggestions', []):
            st.write(f"• {suggestion}")
            
    except Exception as e:
        logger.error(f"应用启动失败: {str(e)}")
        logger.error(traceback.format_exc())
        
        st.error(f"应用启动失败: {str(e)}")
        error_info = handle_exception(e, {'component': 'main_application'})
        
        with st.expander("🔍 查看技术详情", expanded=False):
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
