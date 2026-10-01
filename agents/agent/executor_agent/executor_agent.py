"""
ExecutorAgent 重构版本

执行智能体，负责使用工具或LLM直接执行子任务。
改进了代码结构、错误处理、日志记录和可维护性。

作者: Eric ZZ
版本: 2.0 (重构版)
"""

import json
import datetime
import traceback
import uuid
import time
from copy import deepcopy
from typing import List, Dict, Any, Optional, Generator

from agents.agent.agent_base import AgentBase
from agents.tool.tool_manager import ToolManager
from agents.tool.tool_base import AgentToolSpec
from agents.utils.logger import logger


class ExecutorAgent(AgentBase):
    """
    执行智能体
    
    负责执行子任务，可以使用工具调用或直接的LLM生成。
    支持流式输出，实时返回执行结果。
    """

    # 任务执行提示模板常量
    TASK_EXECUTION_PROMPT_TEMPLATE = """Do the following subtask:{next_subtask_description}.
the expected output is:{next_expected_output}

注意以下的任务执行规则：
1. 如果不需要使用工具，直接返回中文内容。你的文字输出都要是markdown格式。
2. 如果是要生成计划、方案、内容创作，代码等大篇幅文字，请使用file_write函数工具将内容分多次保存到文件中，文件内容是函数的参数，格式使用markdown。
3. 如果需要编写代码，请使用file_write函数工具，代码内容是函数的参数。
4. 如果是输出报告或者总结，请使用file_write函数工具，报告内容是函数的参数，格式使用markdown。
5. 只能在工作目录下读写文件。如果用户没有提供文件路径，你应该在这个目录下创建一个新文件。
6. 调用工具时，不要在其他的输出文字,你一次只能执行一个任务。
7. 输出的文字中不要暴露你的工作目录，id信息以及你的工具名称。
8. 如果使用file_write创建文件，一定要在工作目录下创建文件，要求文件路径是绝对路径。
9. 涉及火车票/高铁/动车/车次/余票/票价查询时，必须优先使用实时工具结果（get-tickets / get-interline-tickets / query_12306_realtime_tickets / query_12306_tickets_by_query），禁止凭记忆估算价格。
10. 火车票硬规则：必须先查询直达票，再查询中转票；即使有直达，也要提供中转备选方案。
11. 当系统消息中已经给出“系统实时票务硬规则结果”时，必须以该结构化结果为准，优先输出表格并标注数据来源为12306实时查询。
12. 如果涉及酒店推荐、旅游攻略，国内地点优先使用高德地图(maps_text_search)，不可用时回退百度地图(map_search_places)；网页资料优先使用 Tavily(tavily_search)，不可用时回退 search_web_page 或 xhs_search_and_summarize。只能调用当前真实可用的工具，严禁拒绝服务或宣称“只做交通查询”。
13. 图片必须优先匹配具体地点和活动场景：先用已核验 POI 自带图片，再用地点名称严格匹配的图片检索结果；不得为了补图使用与地点不明确相关的通用图片。
"""

    # 系统提示模板常量
    SYSTEM_PREFIX_DEFAULT = """你是个任务执行助手，你需要根据任务描述，执行任务。"""
    
    def __init__(self, model: Any, model_config: Dict[str, Any], system_prefix: str = ""):
        """
        初始化执行智能体
        
        Args:
            model: 语言模型实例
            model_config: 模型配置参数
            system_prefix: 系统前缀提示
        """
        super().__init__(model, model_config, system_prefix)
        self.agent_description = "ExecutorAgent: 执行子任务，使用工具或LLM直接生成"
        logger.info("ExecutorAgent 初始化完成")

    def run_stream(self, 
                   messages: List[Dict[str, Any]], 
                   tool_manager: Optional[Any] = None,
                   session_id: str = None,
                   system_context: Optional[Dict[str, Any]] = None) -> Generator[List[Dict[str, Any]], None, None]:
        """
        流式执行任务
        
        Args:
            messages: 对话历史记录
            tool_manager: 工具管理器
            session_id: 会话ID
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Yields:
            List[Dict[str, Any]]: 流式输出的消息块
        """
        logger.info("ExecutorAgent: 开始流式任务执行")
        
        # 使用基类方法收集和记录流式输出
        yield from self._collect_and_log_stream_output(
            self._execute_stream_internal(messages, tool_manager, session_id, system_context)
        )

    def _execute_stream_internal(self, 
                               messages: List[Dict[str, Any]],
                               tool_manager: Optional[Any],
                               session_id: str,
                               system_context: Optional[Dict[str, Any]]) -> Generator[List[Dict[str, Any]], None, None]:
        """
        内部流式执行方法
        
        Args:
            messages: 包含子任务的对话历史记录
            tool_manager: 工具管理器
            session_id: 会话ID
            system_context: 系统上下文
            
        Yields:
            List[Dict[str, Any]]: 流式输出的执行结果消息块
        """
        try:
            # 准备执行上下文
            execution_context = self._prepare_execution_context(
                messages=messages,
                session_id=session_id,
                system_context=system_context
            )
            
            # 解析子任务信息
            subtask_info = self._parse_subtask_info(messages)
            
            # 生成执行提示并准备消息
            execution_messages = self._prepare_execution_messages(
                messages=messages,
                subtask_info=subtask_info,
                execution_context=execution_context
            )
            
            # 发送任务执行提示
            yield from self._send_task_execution_prompt(subtask_info)
            
            # 执行任务
            yield from self._execute_task_with_tools(
                execution_messages=execution_messages,
                tool_manager=tool_manager,
                subtask_info=subtask_info,
                session_id=session_id
            )
            
        except Exception as e:
            logger.error(f"ExecutorAgent: 执行过程中发生异常: {str(e)}")
            logger.error(f"异常详情: {traceback.format_exc()}")
            yield from self._handle_execution_error(e)

    def _prepare_execution_context(self, 
                                 messages: List[Dict[str, Any]],
                                 session_id: str,
                                 system_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        准备执行所需的上下文信息
        
        Args:
            messages: 对话消息列表
            session_id: 会话ID
            system_context: 系统上下文
            
        Returns:
            Dict[str, Any]: 包含执行所需信息的上下文字典
        """
        logger.debug("ExecutorAgent: 准备执行上下文")
        
        # 提取相关消息
        task_description_messages = self._extract_task_description_messages(messages)
        completed_actions_messages = self._extract_completed_actions_messages(messages)
        
        # 获取上下文信息
        current_time = system_context.get('current_time', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')) if system_context else datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        file_workspace = system_context.get('file_workspace', '无') if system_context else '无'
        
        execution_context = {
            'task_description_messages': task_description_messages,
            'completed_actions_messages': completed_actions_messages,
            'current_time': current_time,
            'file_workspace': file_workspace,
            'session_id': session_id,
            'system_context': system_context
        }
        
        logger.info("ExecutorAgent: 执行上下文准备完成")
        return execution_context

    def _parse_subtask_info(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        解析子任务信息
        
        Args:
            messages: 消息列表
            
        Returns:
            Dict[str, Any]: 解析后的子任务信息
            
        Raises:
            json.JSONDecodeError: 当无法解析子任务消息时抛出
        """
        logger.debug("ExecutorAgent: 解析子任务信息")
        
        try:
            last_subtask_message = self._get_last_sub_task(messages)
            if not last_subtask_message:
                raise ValueError("未找到planning_result类型的消息")
            
            # 解析子任务内容
            content = last_subtask_message['content']
            logger.warning(f"ExecutorAgent: 📋 原始子任务content: {repr(content)[:200]}...")
            
            if content.startswith('Planning: '):
                content = content[len('Planning: '):]
                logger.warning(f"ExecutorAgent: 🔄 移除'Planning: '前缀后的content: {repr(content)[:200]}...")
            
            # 清理content内容
            cleaned_content = content.strip('```json\\n').strip('```')
            logger.warning(f"ExecutorAgent: 🧹 清理markdown标记后的content: {repr(cleaned_content)[:200]}...")
            
            # 尝试解析JSON
            logger.warning(f"ExecutorAgent: 🔍 准备解析JSON，内容长度: {len(cleaned_content)}")
            try:
                subtask_dict = json.loads(cleaned_content)
                logger.warning(f"ExecutorAgent: ✅ JSON解析成功，keys: {list(subtask_dict.keys())}")
            except json.JSONDecodeError as json_err:
                logger.error(f"ExecutorAgent: ❌ JSON解析失败!")
                logger.error(f"ExecutorAgent: 错误详情: {str(json_err)}")
                logger.error(f"ExecutorAgent: 错误位置: 第{json_err.lineno}行，第{json_err.colno}列")
                logger.error(f"ExecutorAgent: 完整content内容: {repr(cleaned_content)}")
                logger.error(f"ExecutorAgent: content字节长度: {len(cleaned_content.encode('utf-8'))}")
                logger.error(f"ExecutorAgent: content前50字符: {repr(cleaned_content[:50])}")
                logger.error(f"ExecutorAgent: content后50字符: {repr(cleaned_content[-50:])}")
                raise json_err
            
            # 确保 required_tools 是列表
            raw_tools = subtask_dict['next_step'].get('required_tools', [])
            if isinstance(raw_tools, str):
                try:
                    raw_tools = json.loads(raw_tools)
                except (json.JSONDecodeError, TypeError):
                    raw_tools = [t.strip().strip('"').strip("'") for t in raw_tools.strip('[]').split(',') if t.strip()]
            if not isinstance(raw_tools, list):
                raw_tools = []

            subtask_info = {
                'description': subtask_dict['next_step']['description'],
                'expected_output': subtask_dict['next_step']['expected_output'],
                'required_tools': raw_tools
            }
            
            logger.info(f"ExecutorAgent: 解析子任务成功 - {subtask_info['description']}")
            logger.debug(f"ExecutorAgent: 需要的工具: {subtask_info['required_tools']}")
            
            return subtask_info
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"ExecutorAgent: ❌ 解析子任务失败: {str(e)}")
            logger.error(f"ExecutorAgent: 异常类型: {type(e).__name__}")
            if hasattr(e, '__traceback__'):
                import traceback
                logger.error(f"ExecutorAgent: 完整堆栈跟踪:\n{traceback.format_exc()}")
            raise json.JSONDecodeError("Failed to parse subtask message as JSON", doc=str(e), pos=0)

    def _prepare_execution_messages(self, 
                                  messages: List[Dict[str, Any]],
                                  subtask_info: Dict[str, Any],
                                  execution_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        准备执行消息列表
        
        Args:
            messages: 原始消息列表
            subtask_info: 子任务信息
            execution_context: 执行上下文
            
        Returns:
            List[Dict[str, Any]]: 准备好的执行消息列表
        """
        logger.debug("ExecutorAgent: 准备执行消息")
        
        # 准备系统消息
        system_message = self.prepare_unified_system_message(
            session_id=execution_context.get('session_id'),
            system_context=execution_context.get('system_context')
        )
        
        # 深拷贝消息
        messages_input = deepcopy(messages)
        messages_input = [system_message] + messages_input
        
        # 添加任务执行提示
        task_prompt = self.TASK_EXECUTION_PROMPT_TEMPLATE.format(
            next_subtask_description=subtask_info['description'],
            next_expected_output=subtask_info['expected_output']
        )
        
        request_message = {
            'role': 'user',
            'content': task_prompt,
            'type': 'do_subtask',
            'message_id': str(uuid.uuid4()),
            'show_content': ""
        }
        
        messages_input.append(request_message)
        
        logger.debug(f"ExecutorAgent: 准备了 {len(messages_input)} 条执行消息")
        return messages_input

    def _send_task_execution_prompt(self, subtask_info: Dict[str, Any]) -> Generator[List[Dict[str, Any]], None, None]:
        """
        发送任务执行提示消息
        
        Args:
            subtask_info: 子任务信息
            
        Yields:
            List[Dict[str, Any]]: 任务执行提示消息块
        """
        logger.debug("ExecutorAgent: 发送任务执行提示")
        
        task_prompt = self.TASK_EXECUTION_PROMPT_TEMPLATE.format(
            next_subtask_description=subtask_info['description'],
            next_expected_output=subtask_info['expected_output']
        )
        
        request_message = {
            'role': 'assistant',
            'content': task_prompt,
            'type': 'do_subtask',
            'message_id': str(uuid.uuid4()),
            'show_content': ""
        }
        
        yield [request_message]

    def _execute_task_with_tools(self, 
                               execution_messages: List[Dict[str, Any]],
                               tool_manager: Optional[Any],
                               subtask_info: Dict[str, Any],
                               session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        使用工具执行任务
        
        Args:
            execution_messages: 执行消息列表
            tool_manager: 工具管理器
            subtask_info: 子任务信息
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 执行结果消息块
        """
        logger.info("ExecutorAgent: 开始使用工具执行任务")
        
        # 清理消息格式
        clean_messages = self.clean_messages(execution_messages)
        logger.debug(f"ExecutorAgent: 准备了 {len(clean_messages)} 条清理后的消息")
        
        # 准备工具
        tools_json = self._prepare_tools(tool_manager, subtask_info)
        
        # 调用LLM
        response = self._call_llm_with_tools(clean_messages, tools_json)
        
        # 处理流式响应
        yield from self._process_streaming_response(
            response=response,
            tool_manager=tool_manager,
            execution_messages=execution_messages,
            session_id=session_id
        )

    # 火车票查询场景中必须始终可用的关键工具
    _TRAIN_TICKET_ESSENTIAL_TOOLS = {
        'get-tickets', 'get-interline-tickets',
        'query_12306_realtime_tickets', 'query_12306_tickets_by_query',
        'complete_task',
    }
    _TRAIN_TICKET_KEYWORDS = (
        '火车', '高铁', '动车', '车票', '车次', '余票', '票价',
        '列车', '12306', '铁路',
    )

    def _prepare_tools(self, 
                      tool_manager: Optional[Any], 
                      subtask_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        准备工具列表
        
        Args:
            tool_manager: 工具管理器
            subtask_info: 子任务信息
            
        Returns:
            List[Dict[str, Any]]: 工具配置列表
        """
        logger.debug("ExecutorAgent: 准备工具列表")
        
        if not tool_manager:
            logger.warning("ExecutorAgent: 未提供工具管理器")
            return []
        
        # 获取所有工具
        all_tools_json = tool_manager.get_openai_tools()
        tools_json = all_tools_json
        
        # 根据建议的工具进行过滤
        suggested_tools = subtask_info.get('required_tools', [])
        if isinstance(suggested_tools, list) and suggested_tools:
            tools_suggest_json = [
                tool for tool in all_tools_json 
                if tool['function']['name'] in suggested_tools
            ]
            if tools_suggest_json:
                tools_json = tools_suggest_json

        # 火车票查询场景：确保关键查票工具始终可用
        description = subtask_info.get('description', '')
        if any(kw in description for kw in self._TRAIN_TICKET_KEYWORDS):
            current_tool_names = {t['function']['name'] for t in tools_json}
            missing_essential = self._TRAIN_TICKET_ESSENTIAL_TOOLS - current_tool_names
            if missing_essential:
                for tool in all_tools_json:
                    if tool['function']['name'] in missing_essential:
                        tools_json.append(tool)
                logger.info(f"ExecutorAgent: 火车票查询场景，补充关键工具: {missing_essential}")

        tool_names = [tool['function']['name'] for tool in tools_json]
        logger.info(f"ExecutorAgent: 准备了 {len(tools_json)} 个工具: {tool_names}")
        
        return tools_json

    def _call_llm_with_tools(self, 
                           messages: List[Dict[str, Any]], 
                           tools_json: List[Dict[str, Any]]):
        """
        调用LLM并支持工具调用
        
        Args:
            messages: 消息列表
            tools_json: 工具配置列表
            
        Returns:
            Generator: LLM流式响应
        """
        logger.debug("ExecutorAgent: 调用LLM进行工具辅助执行")

        tools_reserved_bytes = 0
        if tools_json:
            try:
                tools_reserved_bytes = len(json.dumps(tools_json, ensure_ascii=False).encode('utf-8'))
            except Exception:
                tools_reserved_bytes = 0

        # 为工具定义预留输入预算，避免 provider 侧 8192 参数错误
        prepared_messages = self._prepare_messages_for_llm(
            messages,
            extra_reserved_bytes=min(4096, tools_reserved_bytes)
        )
        
        return self.model.chat.completions.create(
            tools=tools_json if tools_json else None,
            messages=prepared_messages,
            stream=True,
            stream_options={"include_usage": True},
            **self.model_request_config
        )

    def _process_streaming_response(self, 
                                  response,
                                  tool_manager: Optional[Any],
                                  execution_messages: List[Dict[str, Any]],
                                  session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理流式响应
        
        Args:
            response: LLM流式响应
            tool_manager: 工具管理器
            execution_messages: 执行消息列表
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 处理后的响应消息块
        """
        logger.debug("ExecutorAgent: 处理流式响应")
        
        tool_calls = {}
        unused_tool_content_message_id = str(uuid.uuid4())
        last_tool_call_id = None
        
        # 收集所有chunks用于token跟踪
        start_time = time.time()
        chunks = []
        
        # 处理流式响应
        for chunk in response:
            chunks.append(chunk)
            if len(chunk.choices) ==0:
                continue
            if chunk.choices[0].delta.tool_calls:
                yield from self._handle_tool_calls_chunk(
                    chunk=chunk,
                    tool_calls=tool_calls,
                    last_tool_call_id=last_tool_call_id
                )
                # 更新last_tool_call_id
                for tool_call in chunk.choices[0].delta.tool_calls:
                    if tool_call.id and len(tool_call.id) > 0:
                        last_tool_call_id = tool_call.id
                        
            elif chunk.choices[0].delta.content:
                if tool_calls:
                    # 有工具调用时停止收集文本内容
                    logger.debug(f"ExecutorAgent: 检测到 {len(tool_calls)} 个工具调用，停止收集文本内容")
                    break
                
                # 使用基类的消息创建函数
                yield self._create_message_chunk(
                    content=chunk.choices[0].delta.content,
                    message_id=unused_tool_content_message_id,
                    show_content=chunk.choices[0].delta.content,
                    message_type='do_subtask_result'
                )
        
        # 跟踪token使用
        self._track_streaming_token_usage(chunks, "tool_execution", start_time)
        
        # 处理工具调用或发送结束消息
        if tool_calls:
            yield from self._execute_tool_calls(
                tool_calls=tool_calls,
                tool_manager=tool_manager,
                execution_messages=execution_messages,
                session_id=session_id
            )
        else:
            # 发送结束消息（使用基类函数）
            yield self._create_message_chunk(
                content='',
                message_id=unused_tool_content_message_id,
                show_content='\n',
                message_type='do_subtask_result'
            )

    def _handle_tool_calls_chunk(self, 
                               chunk,
                               tool_calls: Dict[str, Any],
                               last_tool_call_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理工具调用数据块
        
        Args:
            chunk: LLM响应块
            tool_calls: 工具调用字典
            last_tool_call_id: 最后的工具调用ID
            
        Yields:
            List[Dict[str, Any]]: 处理结果（通常为空）
        """
        for tool_call in chunk.choices[0].delta.tool_calls:
            if tool_call.id and len(tool_call.id) > 0:
                last_tool_call_id = tool_call.id                            
                
            if last_tool_call_id not in tool_calls:
                logger.debug(f"ExecutorAgent: 检测到新工具调用: {last_tool_call_id}, 工具名称: {tool_call.function.name}")
                tool_calls[last_tool_call_id] = {
                    'id': last_tool_call_id,
                                'type': tool_call.type,
                                'function': {
                                    'name': tool_call.function.name or '',
                                    'arguments': tool_call.function.arguments or ''
                                }
                            }
            else:
                if tool_call.function.name:
                    tool_calls[last_tool_call_id]['function']['name'] = tool_call.function.name
                if tool_call.function.arguments:
                    current_arguments = tool_calls[last_tool_call_id]['function'].get('arguments') or ''
                    tool_calls[last_tool_call_id]['function']['arguments'] = current_arguments + tool_call.function.arguments
        
        # 通常不需要yield任何内容
        return
        yield []

    def _execute_tool_calls(self, 
                          tool_calls: Dict[str, Any],
                          tool_manager: Optional[Any],
                          execution_messages: List[Dict[str, Any]],
                          session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行工具调用
        
        Args:
            tool_calls: 工具调用字典
            tool_manager: 工具管理器
            execution_messages: 执行消息列表
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 工具执行结果消息块
        """
        logger.info(f"ExecutorAgent: 执行 {len(tool_calls)} 个工具调用")
        
        for tool_call_id, tool_call in tool_calls.items():
            tool_name = tool_call['function']['name']
            logger.info(f"ExecutorAgent: 执行工具 {tool_name}")
            
            try:
                # 检查工具是否存在
                tool = tool_manager.get_tool(tool_name) if tool_manager else None
                if not tool:
                    logger.error(f"ExecutorAgent: 工具 {tool_name} 未找到")
                    continue
                
                # 发送一致的工具调用消息（始终包含tool_calls字段以符合API要求）
                tool_msg = {
                    'role': 'assistant',
                    'tool_calls': [{
                        'id': tool_call['id'],
                        'type': tool_call['type'],
                        'function': {
                            'name': tool_call['function']['name'],
                            'arguments': tool_call['function']['arguments']
                        }
                    }],
                    'type': 'tool_call',
                    'message_id': str(uuid.uuid4())
                }
                
                if isinstance(tool, AgentToolSpec):
                    tool_msg['content'] = f"该任务交接给了{tool.name}，进行执行"
                    tool_msg['show_content'] = f"该任务交接给了{tool.name}，进行执行\n\n"
                else:
                    tool_msg['show_content'] = f"调用工具：{tool_name}\n\n"
                    
                yield [tool_msg]
                
                # 解析并执行工具
                arguments = json.loads(tool_call['function']['arguments'])
                tool_response = tool_manager.run_tool(
                    tool_name,
                    messages=execution_messages,
                    session_id=session_id,
                    **arguments
                )
                    
                    # 处理工具响应
                logger.debug("ExecutorAgent: 收到工具响应，正在处理")
                logger.info(f"ExecutorAgent: 工具响应 {tool_response}")
                
                processed_response = self.process_tool_response(tool_response, tool_call_id)
                yield processed_response
                
            except Exception as e:
                logger.error(f"ExecutorAgent: 执行工具 {tool_name} 时发生错误: {str(e)}")
                yield from self._handle_tool_error(tool_call_id, tool_name, e)

    def _handle_execution_error(self, error: Exception) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理执行过程中的错误
        
        Args:
            error: 发生的异常
            
        Yields:
            List[Dict[str, Any]]: 错误消息块
        """
        yield from self._handle_error_generic(
            error=error,
            error_context="任务执行",
            message_type='do_subtask_result'
        )

    def _handle_tool_error(self, 
                          tool_call_id: str, 
                          tool_name: str, 
                          error: Exception) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理工具执行错误
        
        Args:
            tool_call_id: 工具调用ID
            tool_name: 工具名称
            error: 发生的异常
            
        Yields:
            List[Dict[str, Any]]: 工具错误消息块
        """
        logger.error(f"ExecutorAgent: 工具 {tool_name} 执行错误: {str(error)}")
        
        error_message = f"工具 {tool_name} 执行失败: {str(error)}"
        
        yield [{
            'role': 'tool',
            'content': error_message,
            'tool_call_id': tool_call_id,
            'message_id': str(uuid.uuid4()),
            'type': 'tool_call_result',
            'show_content': f"工具调用失败\n\n"
        }]

    def process_tool_response(self, tool_response: str, tool_call_id: str) -> List[Dict[str, Any]]:
        """
        处理工具执行响应
        
        Args:
            tool_response: 工具执行响应
            tool_call_id: 工具调用ID
            
        Returns:
            List[Dict[str, Any]]: 处理后的结果消息
        """
        logger.debug(f"ExecutorAgent: 处理工具响应，工具调用ID: {tool_call_id}")
        
        try:
            tool_response_dict = json.loads(tool_response)
            
            if "content" in tool_response_dict:
                result = [{
                    'role': 'tool',
                    'content': tool_response,
                    'tool_call_id': tool_call_id,
                    'message_id': str(uuid.uuid4()),
                    'type': 'tool_call_result',
                    'show_content': '\n```json\n' + json.dumps(tool_response_dict['content'], ensure_ascii=False, indent=2) + '\n```\n'
                }]
            elif 'messages' in tool_response_dict:
                raw_messages = tool_response_dict.get('messages')
                result = []
                if isinstance(raw_messages, list):
                    for msg in raw_messages:
                        if not isinstance(msg, dict):
                            continue
                        normalized_msg = msg.copy()

                        if normalized_msg.get('role') == 'tool' and not normalized_msg.get('tool_call_id'):
                            normalized_msg['tool_call_id'] = tool_call_id
                        if 'message_id' not in normalized_msg:
                            normalized_msg['message_id'] = str(uuid.uuid4())
                        if normalized_msg.get('role') == 'tool' and 'type' not in normalized_msg:
                            normalized_msg['type'] = 'tool_call_result'

                        result.append(normalized_msg)

                if not result:
                    result = [{
                        'role': 'tool',
                        'content': tool_response,
                        'tool_call_id': tool_call_id,
                        'message_id': str(uuid.uuid4()),
                        'type': 'tool_call_result',
                        'show_content': '\n' + tool_response + '\n'
                    }]
            else:
                # 默认处理
                result = [{
                    'role': 'tool',
                    'content': tool_response,
                    'tool_call_id': tool_call_id,
                    'message_id': str(uuid.uuid4()),
                    'type': 'tool_call_result',
                    'show_content': '\n' + tool_response + '\n'
                }]
            
            logger.debug("ExecutorAgent: 工具响应处理成功")
            return result
            
        except json.JSONDecodeError:
            logger.warning("ExecutorAgent: 处理工具响应时JSON解码错误")
            return [{
                'role': 'tool',
                'content': '\n' + tool_response + '\n',
                'tool_call_id': tool_call_id,
                'message_id': str(uuid.uuid4()),
                'type': 'tool_call_result',
                'show_content': "工具调用失败\n\n"
            }]

    def _get_last_sub_task(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        获取最后一个子任务消息
        
        Args:
            messages: 消息列表
            
        Returns:
            Optional[Dict[str, Any]]: 最后一个子任务消息，如果未找到则返回None
        """
        logger.debug(f"ExecutorAgent: 从 {len(messages)} 条消息中查找最后一个子任务")
        
        for i in range(len(messages) - 1, -1, -1):
            if (messages[i]['role'] == 'assistant' and 
                messages[i].get('type') == 'planning_result'):
                logger.debug(f"ExecutorAgent: 在索引 {i} 处找到最后一个子任务")
                return messages[i]
        
        logger.warning("ExecutorAgent: 未找到planning_result类型的消息")
        return None

    def run(self, 
            messages: List[Dict[str, Any]], 
            tool_manager: Optional[ToolManager] = None,
            session_id: str = None,
            system_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        执行子任务（非流式版本）
        
        Args:
            messages: 对话历史记录
            tool_manager: 可选的工具管理器
            session_id: 会话ID
            system_context: 系统上下文
            
        Returns:
            List[Dict[str, Any]]: 执行结果消息列表
        """
        logger.info("ExecutorAgent: 执行非流式子任务")
        
        # 调用父类的默认实现，将流式结果合并
        return super().run(
            messages=messages,
            tool_manager=tool_manager,
            session_id=session_id,
            system_context=system_context
        )
