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
import re
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

    MAX_SUMMARY_TOOL_ROUNDS = 3
    
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
        
        conversation = list(clean_messages)
        all_tool_results = []
        tools_reserved_bytes = min(4096, tools_reserved_bytes)

        for round_index in range(self.MAX_SUMMARY_TOOL_ROUNDS):
            response = self.model.chat.completions.create(
                tools=tools_json,
                messages=self._prepare_messages_for_llm(
                    conversation,
                    extra_reserved_bytes=tools_reserved_bytes
                ),
                stream=True,
                stream_options={"include_usage": True},
                **self.model_request_config
            )
            text, tool_calls = self._collect_summary_model_response(
                response,
                step_name=f"task_summary_tool_round_{round_index + 1}"
            )

            if not tool_calls:
                yield from self._yield_safe_final_answer(
                    text=text,
                    tool_results=all_tool_results
                )
                return

            tool_results = self._execute_summary_tool_calls(
                tool_calls=tool_calls,
                tool_manager=tool_manager,
                session_id=session_id
            )
            all_tool_results.extend(tool_results)
            conversation = self._build_tool_followup_messages(
                base_messages=conversation,
                tool_calls=tool_calls,
                tool_results=tool_results
            )

        logger.warning(
            f"TaskSummaryAgent: 工具调用达到上限 {self.MAX_SUMMARY_TOOL_ROUNDS}，强制生成最终回答"
        )
        conversation.append({
            'role': 'user',
            'content': (
                "工具调用次数已达到上限。请仅基于已有结果立即生成最终用户答案，"
                "不要再请求工具，也不要输出工具名、内部字段、原始 JSON、调试信息或本地路径。"
            )
        })
        try:
            response = self.model.chat.completions.create(
                messages=self._prepare_messages_for_llm(conversation),
                stream=True,
                stream_options={"include_usage": True},
                **self.model_request_config
            )
            text, _ = self._collect_summary_model_response(
                response,
                step_name="task_summary_tool_limit_final"
            )
        except Exception as error:
            logger.error(f"TaskSummaryAgent: 强制生成最终回答失败: {error}")
            text = ""

        yield from self._yield_safe_final_answer(
            text=text,
            tool_results=all_tool_results
        )

    def _collect_summary_model_response(self,
                                        response: Any,
                                        step_name: str) -> tuple[str, Dict[str, Any]]:
        """收集一次模型响应中的文本和可能分片返回的工具调用。"""
        import time

        chunks = []
        text_parts = []
        tool_calls = {}
        call_ids_by_index = {}
        last_call_id = None
        start_time = time.time()

        for chunk in response:
            chunks.append(chunk)
            if not getattr(chunk, 'choices', None):
                continue

            delta = chunk.choices[0].delta
            for position, tool_call in enumerate(getattr(delta, 'tool_calls', None) or []):
                call_index = getattr(tool_call, 'index', position)
                call_id = getattr(tool_call, 'id', None)
                if call_id:
                    call_ids_by_index[call_index] = call_id
                    last_call_id = call_id
                else:
                    call_id = call_ids_by_index.get(call_index) or last_call_id
                if not call_id:
                    call_id = f"summary-call-{len(tool_calls) + 1}"
                    call_ids_by_index[call_index] = call_id
                    last_call_id = call_id

                function = getattr(tool_call, 'function', None)
                current = tool_calls.setdefault(call_id, {
                    'id': call_id,
                    'type': getattr(tool_call, 'type', None) or 'function',
                    'function': {'name': '', 'arguments': ''}
                })
                function_name = getattr(function, 'name', None)
                function_arguments = getattr(function, 'arguments', None)
                if function_name:
                    current['function']['name'] = function_name
                if function_arguments:
                    current['function']['arguments'] += function_arguments

            delta_content = getattr(delta, 'content', None)
            if delta_content:
                text_parts.append(delta_content)

        self._track_streaming_token_usage(chunks, step_name, start_time)
        return ''.join(text_parts).strip(), tool_calls

    def _execute_summary_tool_calls(self,
                                  tool_calls: Dict[str, Any],
                                  tool_manager: Any,
                                  session_id: str) -> List[Dict[str, Any]]:
        """
        执行任务总结中的工具调用，并将工具结果传回LLM生成最终回答
        
        Args:
            tool_calls: 工具调用字典
            tool_manager: 工具管理器
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 工具执行结果消息块
        """
        logger.info(f"TaskSummaryAgent: 开始执行 {len(tool_calls)} 个工具调用")
        all_results = []

        for tool_call_id, tool_call in tool_calls.items():
            function_name = str((tool_call.get('function') or {}).get('name') or '')
            try:
                function_args_str = tool_call['function'].get('arguments') or '{}'
                logger.info(f"TaskSummaryAgent: 执行工具 {function_name}")

                try:
                    function_args = json.loads(function_args_str)
                    if not isinstance(function_args, dict):
                        raise ValueError("工具参数必须是 JSON 对象")
                except (json.JSONDecodeError, ValueError) as error:
                    logger.error(f"TaskSummaryAgent: 解析工具参数失败: {error}")
                    function_args = {}

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

            except Exception as error:
                logger.error(f"TaskSummaryAgent: 工具 {function_name} 执行失败: {error}")
                all_results.append({
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "result": {"error": str(error)}
                })
        return all_results

    def _yield_safe_final_answer(self,
                                 text: str,
                                 tool_results: List[Dict[str, Any]]) -> Generator[List[Dict[str, Any]], None, None]:
        tool_names = [str(result.get('tool_name') or '') for result in tool_results]
        final_text = str(text or '').strip()
        if self._looks_like_raw_tool_output(final_text, tool_names):
            final_text = self._build_tool_results_fallback_answer(tool_results)

        message_id = str(uuid.uuid4())
        yield self._create_message_chunk(
            content=final_text,
            message_id=message_id,
            show_content=final_text,
            message_type='final_answer'
        )
        yield self._create_message_chunk(
            content='',
            message_id=message_id,
            show_content='\n',
            message_type='final_answer'
        )

    def _build_tool_followup_messages(self,
                                    base_messages: List[Dict[str, Any]],
                                    tool_calls: Dict[str, Any],
                                    tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized_tool_calls = []
        result_by_call_id = {
            str(result.get("tool_call_id") or ""): result
            for result in tool_results
        }

        for tool_call_id, tool_call in tool_calls.items():
            call_id = str(tool_call.get('id') or tool_call_id or uuid.uuid4())
            function = tool_call.get('function') or {}
            normalized_tool_calls.append({
                'id': call_id,
                'type': tool_call.get('type') or 'function',
                'function': {
                    'name': str(function.get('name') or ''),
                    'arguments': str(function.get('arguments') or '{}')
                }
            })

        followup_messages = list(base_messages)
        followup_messages.append({
            'role': 'assistant',
            'tool_calls': normalized_tool_calls
        })

        for tool_call in normalized_tool_calls:
            call_id = tool_call['id']
            result = result_by_call_id.get(call_id)
            if result is None:
                result = next(
                    (
                        item for item in tool_results
                        if item.get('tool_name') == tool_call['function']['name']
                    ),
                    {"result": ""}
                )
            followup_messages.append({
                'role': 'tool',
                'tool_call_id': call_id,
                'content': self._serialize_tool_result(result.get('result'))
            })

        followup_messages.append({
            'role': 'user',
            'content': (
                "请基于上面的工具结果继续完成任务。需要更多信息时可以继续调用可用工具；"
                "信息足够时生成最终用户可读答案。"
                "必须先给自然语言结论，再保留必要的关键事实；"
                "不要输出工具名、工具调用语法、内部字段、原始工具 JSON、调试信息或本地路径。"
            )
        })
        return followup_messages

    def _serialize_tool_result(self, result: Any) -> str:
        if isinstance(result, str):
            return result if result.strip() else "{}"
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            return str(result)

    def _looks_like_raw_tool_output(self,
                                    text: str,
                                    tool_names: Optional[List[str]] = None) -> bool:
        stripped = str(text or '').strip()
        if not stripped:
            return True

        raw_patterns = [
            r'^\[[^\]]+\]\s*结果\s*:',
            r'<invoke\b|</invoke>|<write_file\b|</write_file>|file_write\s*\(',
            r'\btool_call_id\b',
            r'"(?:formatted_address|location|latitude|longitude|lat|lng|error)"\s*:',
            r'Observation\s*:',
            r'Assistant:\s*Tool calls\s*:',
            r'(?:[A-Za-z]:\\|/(?:home|Users|tmp|var/tmp)/)[^\s`"\']+',
        ]
        if any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in raw_patterns):
            return True

        lowered = stripped.lower()
        if any(name and name.lower() in lowered for name in (tool_names or [])):
            return True

        if stripped.startswith(('{', '[')):
            try:
                parsed = json.loads(stripped)
                return isinstance(parsed, (dict, list))
            except Exception:
                return False

        return False

    def _build_tool_results_fallback_answer(self, tool_results: List[Dict[str, Any]]) -> str:
        if not tool_results:
            return "当前无法生成可靠的自然语言总结，请稍后重试。"

        lines = ["已完成必要的信息查询，关键信息如下："]
        for result in tool_results:
            brief = self._humanize_tool_result(result.get("result"))
            if brief:
                lines.append(f"- {brief}")
        if len(lines) == 1:
            return "已完成信息查询，但当前没有可供展示的可靠结果。"
        return "\n".join(lines)

    def _humanize_tool_result(self, result: Any) -> str:
        if isinstance(result, str):
            text = result.strip()
            try:
                parsed = json.loads(text)
                return self._humanize_tool_result(parsed)
            except Exception:
                return self._sanitize_fallback_text(text) if text else ""

        if isinstance(result, list):
            if not result:
                return "未查询到可用条目"
            first_item = self._humanize_tool_result(result[0])
            return f"共查询到 {len(result)} 条结果；首条为 {first_item}" if first_item else f"共查询到 {len(result)} 条结果"

        if isinstance(result, dict):
            candidates = []
            for key in ("name", "title", "formatted_address", "address", "message"):
                value = result.get(key)
                if value not in (None, "", [], {}):
                    candidates.append(self._sanitize_fallback_text(value))

            location = result.get("location")
            if isinstance(location, dict):
                latitude = location.get("lat", location.get("latitude"))
                longitude = location.get("lng", location.get("longitude"))
            else:
                latitude = result.get("lat", result.get("latitude"))
                longitude = result.get("lng", result.get("longitude"))
            if latitude is not None and longitude is not None:
                candidates.append(f"坐标为 {latitude}, {longitude}")

            if result.get("distance") not in (None, ""):
                candidates.append(f"距离为 {self._sanitize_fallback_text(result['distance'])}")
            if result.get("duration") not in (None, ""):
                candidates.append(f"预计耗时 {self._sanitize_fallback_text(result['duration'])}")
            if candidates:
                return "；".join(item for item in candidates if item)
            if "error" in result:
                return "本次信息查询未成功，请稍后重试"
            return "已取得结构化查询结果"

        return self._sanitize_fallback_text(result) if result is not None else ""

    def _sanitize_fallback_text(self, value: Any) -> str:
        text = self._truncate_text_with_head_tail(str(value), 300)
        text = re.sub(
            r'(?:[A-Za-z]:\\|/(?:home|Users|tmp|var/tmp)/)[^\s`"\']+',
            "[本地路径已隐藏]",
            text,
            flags=re.IGNORECASE
        )
        text = re.sub(r'\btool_call_id\b', "内部标识", text, flags=re.IGNORECASE)
        return text.strip()

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
        
