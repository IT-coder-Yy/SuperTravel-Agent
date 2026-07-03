"""
TaskSummaryAgent 重构版本

任务总结智能体，负责根据原始任务和执行历史生成清晰完整的回答。
改进了代码结构、错误处理、日志记录和可维护性。

作者: Eric ZZ
版本: 2.0 (重构版)
"""

import json
import uuid
import datetime
import traceback
from typing import List, Dict, Any, Optional, Generator

from agents.agent.agent_base import AgentBase
from agents.utils.logger import logger


class TaskSummaryAgent(AgentBase):
    """
    任务总结智能体
    
    负责根据原始任务和执行历史生成清晰完整的回答。
    支持流式输出，实时返回总结结果。
    """

    # 任务总结提示模板常量
    SUMMARY_PROMPT_TEMPLATE = """根据以下任务和执行历史，用自然语言提供清晰完整的回答。
可以使用markdown格式组织内容。

任务: 
{task_description}

执行历史:
{completed_actions}

工作目录: {file_workspace}

你的回答应该:
1. 直接回答原始任务。
2. 使用清晰详细的语言，但要保证回答的完整性和准确性，保留执行过程中的关键结果。
3. 图表直接使用markdown进行显示。
4. 不是为了总结执行过程，而是以执行过程的信息为基础，生成一个针对用户任务的完美回答。
5. **重要：最终回复必须是纯用户可读内容，严禁出现工具调用语法或系统标签。**
    - 禁止输出：`<invoke>...</invoke>`、`<write_file>`、`<path>`、`<content>`、`file_write(...)`
    - 禁止输出任何本地绝对路径（如 `/home/...`、`C:\\...`）
    - 不要在最终回复中展示工具参数、调用中间态或调试信息
6. **重要**：如果回答涉及旅行行程、地点推荐、路线规划等包含具体地理位置的内容：
   - 必须使用可用地图工具获取坐标，优先调用 `map_geocode`；需要路线耗时/距离时调用 `map_directions` 或 `map_distance_matrix`。
   - 正文必须使用合法 Markdown：用 `##`/`###` 标题、列表或完整 GFM 表格；不要输出 `|----|` 这类没有表头的伪表格；每个标题和表格前后都要留空行。
   - 按天输出行程时，每一天使用独立小节，写清“路线顺序”和“交通方式/耗时依据”。
   - 必须在回答末尾附加一个独立 JSON 代码块，供前端地图读取；该 JSON 不要混入正文说明，格式如下：
   ```json
   {{
     "map_locations": [
       {{
         "id": "day1_1",
         "name": "地点名称",
         "lat": 纬度,
         "lng": 经度,
         "description": "地点描述",
         "category": "景点|酒店|餐厅|交通|购物|娱乐|其他",
         "day": 1,
         "order": 1
       }}
     ]
   }}
   ```
   `day` 表示第几天，`order` 表示当天路线顺序。这样前端地图组件能按天显示地点标记和路线连线。
7. **重要**：如果执行历史包含12306实时票务结果，你必须先给出自然语言结论和推荐（如最省钱/最稳妥方案及理由），再附上实时票务表格；不得编造不存在的车次、票价或余票，无法确认的信息必须明确说明“当前无法确认”。
8. **重要**：车票信息展示时，出发/到达时间请使用“日期+时间”（例如 `2026-04-09 11:53`），历时请使用中文可读格式（例如 `5小时51分钟`）；若跨天到达要显式体现日期变化。
9. **重要**：当用户请求推荐酒店、餐厅或旅游攻略时，绝对不要说“你的功能主要集中在交通查询”或“无法访问预订平台”。必须综合已有的网页搜索和地图检索结果给出最优推荐和详细信息。
"""

    # 系统提示模板常量
    SYSTEM_PREFIX_DEFAULT = """你是一个任务总结者，你需要根据原始任务和执行历史，生成清晰完整的回答。最终回复必须是纯用户可读内容，禁止输出任何工具调用语法、标签和本地路径。"""
    
    def __init__(self, model: Any, model_config: Dict[str, Any], system_prefix: str = ""):
        """
        初始化任务总结智能体
        
        Args:
            model: 语言模型实例
            model_config: 模型配置参数
            system_prefix: 系统前缀提示
        """
        super().__init__(model, model_config, system_prefix)
        self.agent_description = "任务总结智能体，专门负责根据任务和执行历史生成完整回答"
        logger.info("TaskSummaryAgent 初始化完成")

    def run_stream(self, 
                   messages: List[Dict[str, Any]], 
                   tool_manager: Optional[Any] = None,
                   session_id: str = None,
                   system_context: Optional[Dict[str, Any]] = None) -> Generator[List[Dict[str, Any]], None, None]:
        """
        流式执行任务总结
        
        Args:
            messages: 对话历史记录，包含完整的任务执行过程
            tool_manager: 可选的工具管理器
            session_id: 可选的会话标识符
            system_context: 运行时系统上下文字典
            
        Yields:
            List[Dict[str, Any]]: 流式输出的任务总结消息块
        """
        logger.info("TaskSummaryAgent: 开始流式任务总结")
        
        # 使用基类方法收集和记录流式输出
        yield from self._collect_and_log_stream_output(
            self._execute_summary_stream_internal(messages, tool_manager, session_id, system_context)
        )

    def _execute_summary_stream_internal(self, 
                                       messages: List[Dict[str, Any]],
                                       tool_manager: Optional[Any],
                                       session_id: str,
                                       system_context: Optional[Dict[str, Any]]) -> Generator[List[Dict[str, Any]], None, None]:
        """
        内部任务总结流式执行方法
        
        Args:
            messages: 对话历史记录，包含整个任务流程
            tool_manager: 可选的工具管理器
            session_id: 会话ID
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Yields:
            List[Dict[str, Any]]: 流式输出的任务总结消息块
        """
        try:
            # 准备总结上下文
            summary_context = self._prepare_summary_context(
                messages=messages,
                session_id=session_id,
                system_context=system_context,
                tool_manager=tool_manager
            )
            
            # 生成总结提示
            prompt = self._generate_summary_prompt(summary_context)
            
            # 执行流式任务总结
            yield from self._execute_streaming_summary(prompt, summary_context)
            
        except Exception as e:
            logger.error(f"TaskSummaryAgent: 任务总结过程中发生异常: {str(e)}")
            logger.error(f"异常详情: {traceback.format_exc()}")
            yield from self._handle_summary_error(e)

    def _prepare_summary_context(self, 
                                messages: List[Dict[str, Any]],
                                session_id: str,
                                system_context: Optional[Dict[str, Any]],
                                tool_manager: Optional[Any] = None) -> Dict[str, Any]:
        """
        准备任务总结所需的上下文信息
        
        Args:
            messages: 对话消息列表
            session_id: 会话ID
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            tool_manager: 工具管理器，用于地理编码功能
            
        Returns:
            Dict[str, Any]: 包含总结所需信息的上下文字典
        """
        logger.debug("TaskSummaryAgent: 准备任务总结上下文")
        
        # 提取任务描述
        task_description = self._extract_task_description(messages)
        logger.debug(f"TaskSummaryAgent: 提取任务描述，长度: {len(task_description)}")
        
        # 提取完成的操作
        completed_actions = self._extract_completed_actions(messages)
        logger.debug(f"TaskSummaryAgent: 提取完成操作，长度: {len(completed_actions)}")
        
        # 获取上下文信息
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        file_workspace = system_context.get('file_workspace', '无') if system_context else '无'
        
        summary_context = {
            'task_description': task_description,
            'completed_actions': completed_actions,
            'current_time': current_time,
            'file_workspace': file_workspace,
            'session_id': session_id,
            'system_context': system_context,
            'tool_manager': tool_manager
        }
        
        logger.info("TaskSummaryAgent: 任务总结上下文准备完成")
        return summary_context

    def _generate_summary_prompt(self, context: Dict[str, Any]) -> str:
        """
        生成任务总结提示
        
        Args:
            context: 总结上下文信息
            
        Returns:
            str: 格式化后的总结提示
        """
        logger.debug("TaskSummaryAgent: 生成任务总结提示")
        
        prompt = self.SUMMARY_PROMPT_TEMPLATE.format(
            task_description=context['task_description'],
            completed_actions=context['completed_actions'],
            file_workspace=context['file_workspace'],
            session_id=context.get('session_id', 'session-id')
        )
        
        logger.debug("TaskSummaryAgent: 总结提示生成完成")
        return prompt

    def _execute_streaming_summary(self, 
                                 prompt: str, 
                                 summary_context: Dict[str, Any]) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行流式任务总结
        
        Args:
            prompt: 总结提示
            summary_context: 总结上下文
            
        Yields:
            List[Dict[str, Any]]: 流式输出的消息块
        """
        logger.info("TaskSummaryAgent: 开始执行流式任务总结")
        
        # 准备系统消息
        system_message = self.prepare_unified_system_message(
            session_id=summary_context.get('session_id'),
            system_context=summary_context.get('system_context')
        )
        
        # 获取tool_manager
        tool_manager = summary_context.get('tool_manager')
        
        if tool_manager:
            # 准备地理编码工具
            tools_json = []
            available_tools = tool_manager.get_openai_tools()
            
            # 只添加地理编码相关的工具
            for tool in available_tools:
                tool_name = tool['function']['name']
                if 'geocod' in tool_name.lower() or 'map' in tool_name.lower():
                    tools_json.append(tool)
            
            if tools_json:
                logger.info(f"TaskSummaryAgent: 找到 {len(tools_json)} 个地图相关工具")
                # 使用支持工具的流式处理
                yield from self._execute_summary_with_tools(
                    prompt=prompt,
                    system_message=system_message,
                    tools_json=tools_json,
                    tool_manager=tool_manager,
                    session_id=summary_context.get('session_id')
                )
            else:
                logger.info("TaskSummaryAgent: 未找到地图相关工具，使用普通流式处理")
                # 使用基类的流式处理和token跟踪
                yield from self._execute_streaming_with_token_tracking(
                    prompt=prompt,
                    step_name="task_summary",
                    system_message=system_message,
                    message_type='final_answer'
                )
        else:
            logger.info("TaskSummaryAgent: 未提供工具管理器，使用普通流式处理")
            # 使用基类的流式处理和token跟踪
            yield from self._execute_streaming_with_token_tracking(
                prompt=prompt,
                step_name="task_summary",
                system_message=system_message,
                message_type='final_answer'
            )

    def _execute_summary_with_tools(self,
                                  prompt: str,
                                  system_message: str,
                                  tools_json: List[Dict[str, Any]],
                                  tool_manager: Any,
                                  session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        使用工具执行任务总结
        
        Args:
            prompt: 总结提示
            system_message: 系统消息
            tools_json: 工具配置列表
            tool_manager: 工具管理器
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 执行结果消息块
        """
        logger.info("TaskSummaryAgent: 开始使用工具执行任务总结")
        
        # 准备消息，确保内容是字符串且不包含None值
        clean_system_message = str(system_message) if system_message else ""
        clean_prompt = str(prompt) if prompt else ""
        
        messages = [
            {'role': 'system', 'content': clean_system_message},
            {'role': 'user', 'content': clean_prompt}
        ]
        
        # 清理消息格式
        clean_messages = self.clean_messages(messages)

        tools_reserved_bytes = 0
        if tools_json:
            try:
                tools_reserved_bytes = len(json.dumps(tools_json, ensure_ascii=False).encode('utf-8'))
            except Exception:
                tools_reserved_bytes = 0

        clean_messages = self._prepare_messages_for_llm(
            clean_messages,
            extra_reserved_bytes=min(4096, tools_reserved_bytes)
        )
        
        logger.debug(f"TaskSummaryAgent: 准备了 {len(clean_messages)} 条清理后的消息")
        
        # 调用LLM
        response = self.model.chat.completions.create(
            tools=tools_json,
            messages=clean_messages,
            stream=True,
            stream_options={"include_usage": True},
            **self.model_request_config
        )
        
        # 处理流式响应
        yield from self._process_summary_streaming_response(
            response=response,
            tool_manager=tool_manager,
            session_id=session_id
        )

    def _process_summary_streaming_response(self,
                                          response,
                                          tool_manager: Any,
                                          session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理任务总结的流式响应
        
        Args:
            response: LLM流式响应
            tool_manager: 工具管理器
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 处理后的响应消息块
        """
        import uuid
        import time
        
        logger.debug("TaskSummaryAgent: 处理流式响应")
        
        tool_calls = {}
        message_id = str(uuid.uuid4())
        last_tool_call_id = None
        
        # 收集所有chunks用于token跟踪
        start_time = time.time()
        chunks = []
        
        # 处理流式响应
        for chunk in response:
            chunks.append(chunk)
            if len(chunk.choices) == 0:
                continue
                
            if chunk.choices[0].delta.tool_calls:
                # 处理工具调用
                for tool_call in chunk.choices[0].delta.tool_calls:
                    if tool_call.id and len(tool_call.id) > 0:
                        last_tool_call_id = tool_call.id
                        
                    if last_tool_call_id not in tool_calls:
                        logger.debug(f"TaskSummaryAgent: 检测到新工具调用: {last_tool_call_id}")
                        tool_calls[last_tool_call_id] = {
                            'id': last_tool_call_id,
                            'type': tool_call.type,
                            'function': {
                                'name': tool_call.function.name if tool_call.function.name else '',
                                'arguments': tool_call.function.arguments if tool_call.function.arguments else ''
                            }
                        }
                    else:
                        # 追加函数参数
                        if tool_call.function.arguments:
                            current_arguments = tool_calls[last_tool_call_id]['function'].get('arguments') or ''
                            tool_calls[last_tool_call_id]['function']['arguments'] = current_arguments + tool_call.function.arguments
                            
            elif chunk.choices[0].delta.content:
                if tool_calls:
                    # 有工具调用时停止收集文本内容
                    logger.debug(f"TaskSummaryAgent: 检测到 {len(tool_calls)} 个工具调用，停止收集文本内容")
                    break
                
                # 输出文本内容
                yield self._create_message_chunk(
                    content=chunk.choices[0].delta.content,
                    message_id=message_id,
                    show_content=chunk.choices[0].delta.content,
                    message_type='final_answer'
                )
        
        # 跟踪token使用
        self._track_streaming_token_usage(chunks, "task_summary", start_time)
        
        # 处理工具调用
        if tool_calls:
            yield from self._execute_summary_tool_calls(
                tool_calls=tool_calls,
                tool_manager=tool_manager,
                session_id=session_id
            )
        else:
            # 发送结束消息
            yield self._create_message_chunk(
                content='',
                message_id=message_id,
                show_content='\n',
                message_type='final_answer'
            )

    def _execute_summary_tool_calls(self,
                                  tool_calls: Dict[str, Any],
                                  tool_manager: Any,
                                  session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行任务总结中的工具调用，并将工具结果传回LLM生成最终回答
        
        Args:
            tool_calls: 工具调用字典
            tool_manager: 工具管理器
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 工具执行结果消息块
        """
        import uuid
        
        logger.info(f"TaskSummaryAgent: 开始执行 {len(tool_calls)} 个工具调用")
        
        all_results = []
        
        for tool_call_id, tool_call in tool_calls.items():
            try:
                function_name = tool_call['function']['name']
                function_args_str = tool_call['function']['arguments']
                
                logger.info(f"TaskSummaryAgent: 执行工具 {function_name}")
                
                # 解析参数
                try:
                    function_args = json.loads(function_args_str)
                except json.JSONDecodeError as e:
                    logger.error(f"TaskSummaryAgent: 解析工具参数失败: {e}")
                    function_args = {}
                
                # 执行工具 — 使用 ToolManager 的正确方法 run_tool
                tool_response = tool_manager.run_tool(
                    function_name,
                    messages=[],
                    session_id=session_id or "default",
                    **function_args
                )
                
                logger.info(f"TaskSummaryAgent: 工具 {function_name} 执行完成")
                all_results.append({
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "result": tool_response
                })
                
            except Exception as e:
                logger.error(f"TaskSummaryAgent: 工具 {function_name} 执行失败: {e}")
                all_results.append({
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "result": f"工具{function_name}执行失败: {str(e)}"
                })
        
        # 将工具结果合并为上下文文本，输出给用户
        if all_results:
            combined_parts = []
            for r in all_results:
                combined_parts.append(f"[{r['tool_name']}] 结果:\n{r['result']}")
            combined_result = "\n\n".join(combined_parts)
            
            yield self._create_message_chunk(
                content=combined_result,
                message_id=str(uuid.uuid4()),
                show_content=combined_result,
                message_type='final_answer'
            )

    def _handle_summary_error(self, error: Exception) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理总结过程中的错误
        
        Args:
            error: 发生的异常
            
        Yields:
            List[Dict[str, Any]]: 错误消息块
        """
        yield from self._handle_error_generic(
            error=error,
            error_context="任务总结",
            message_type='final_answer'
        )

    def _extract_task_description(self, messages: List[Dict[str, Any]]) -> str:
        """
        从消息中提取原始任务描述
        
        Args:
            messages: 消息列表
            
        Returns:
            str: 任务描述字符串
        """
        logger.debug(f"TaskSummaryAgent: 处理 {len(messages)} 条消息以提取任务描述")
        
        task_description_messages = self._extract_task_description_messages(messages)
        result = self.convert_messages_to_str(task_description_messages)
        
        logger.debug(f"TaskSummaryAgent: 生成任务描述，长度: {len(result)}")
        return result

    def _extract_completed_actions(self, messages: List[Dict[str, Any]]) -> str:
        """
        从消息中提取已完成的操作
        
        Args:
            messages: 消息列表
            
        Returns:
            str: 已完成操作的字符串
        """
        logger.debug(f"TaskSummaryAgent: 处理 {len(messages)} 条消息以提取完成操作")
        
        completed_actions_messages = self._extract_completed_actions_messages(messages)
        result = self.convert_messages_to_str(completed_actions_messages)
        
        logger.debug(f"TaskSummaryAgent: 生成完成操作，长度: {len(result)}")
        return result

    def run(self, 
            messages: List[Dict[str, Any]], 
            tool_manager: Optional[Any] = None,
            session_id: str = None,
            system_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        执行任务总结（非流式版本）
        
        Args:
            messages: 对话历史记录
            tool_manager: 可选的工具管理器
            session_id: 会话ID
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Returns:
            List[Dict[str, Any]]: 任务总结结果消息列表
        """
        logger.info("TaskSummaryAgent: 执行非流式任务总结")
        
        # 调用父类的默认实现，将流式结果合并
        return super().run(
            messages=messages,
            tool_manager=tool_manager,
            session_id=session_id,
            system_context=system_context
        )
        
