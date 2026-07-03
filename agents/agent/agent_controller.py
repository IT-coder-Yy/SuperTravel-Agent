"""
AgentController 重构版本

智能体控制器，负责协调多个智能体协同工作。
改进了代码结构、错误处理、日志记录和可维护性。

作者: Eric ZZ
版本: 2.0 (重构版)
"""

import json
import uuid
import re
import os
import sys
import datetime
import traceback
import time
from typing import List, Dict, Any, Optional, Generator

from .agent_base import AgentBase
from .task_analysis_agent.task_analysis_agent import TaskAnalysisAgent
from .executor_agent.executor_agent import ExecutorAgent
from .task_summary_agent.task_summary_agent import TaskSummaryAgent
from .planning_agent.planning_agent import PlanningAgent
from .observation_agent.observation_agent import ObservationAgent
from .direct_executor_agent.direct_executor_agent import DirectExecutorAgent
from .task_decompose_agent.task_decompose_agent import TaskDecomposeAgent
from agents.utils.logger import logger
from agents.config import get_settings


class AgentController:
    """
    智能体控制器
    
    负责协调多个智能体协同工作，管理任务执行流程，
    包括任务分析、规划、执行、观察和总结等阶段。
    """

    # 默认配置常量
    DEFAULT_MAX_LOOP_COUNT = 10
    DEFAULT_MESSAGE_LIMIT = 8000
    DUPLICATE_TOOL_CALL_LIMIT = 2
    STAGNANT_OBSERVATION_LIMIT = 2
    
    def __init__(self, model: Any, model_config: Dict[str, Any], system_prefix: str = ""):
        """
        初始化智能体控制器
        
        Args:
            model: 语言模型实例
            model_config: 模型配置参数
            system_prefix: 系统前缀提示
        """
        self.model = model
        self.model_config = model_config
        self.system_prefix = system_prefix
        self._init_agents()
        
        # 总体token统计
        self.overall_token_stats = {
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cached_tokens': 0,
            'total_reasoning_tokens': 0,
            'total_calls': 0,
            'total_execution_time': 0,
            'workflow_start_time': None,
            'workflow_end_time': None
        }
        
        logger.info("AgentController: 智能体控制器初始化完成")

    def _init_agents(self) -> None:
        """
        初始化所有必需的智能体
        
        使用共享的模型实例为所有智能体进行初始化。
        """
        logger.debug("AgentController: 初始化各类智能体")
        
        self.task_analysis_agent = TaskAnalysisAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        self.executor_agent = ExecutorAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        self.task_summary_agent = TaskSummaryAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        self.planning_agent = PlanningAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        self.observation_agent = ObservationAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        self.direct_executor_agent = DirectExecutorAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        self.task_decompose_agent = TaskDecomposeAgent(
            self.model, self.model_config, system_prefix=self.system_prefix
        )
        
        logger.info("AgentController: 所有智能体初始化完成")

    def run_stream(self, 
                   input_messages: List[Dict[str, Any]], 
                   tool_manager: Optional[Any] = None, 
                   session_id: Optional[str] = None, 
                   deep_thinking: bool = True, 
                   summary: bool = True,
                   max_loop_count: int = DEFAULT_MAX_LOOP_COUNT,
                   deep_research: bool = True,
                   system_context: Optional[Dict[str, Any]] = None) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行智能体工作流并流式输出结果
        
        Args:
            input_messages: 输入消息字典列表
            tool_manager: 工具管理器实例
            session_id: 会话ID
            deep_thinking: 是否进行任务分析
            summary: 是否生成任务总结
            max_loop_count: 最大循环次数
            deep_research: 是否进行深度研究（完整流程）
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Yields:
            List[Dict[str, Any]]: 自上次yield以来的新消息字典列表，每个消息包含：
            - message_id: 消息的唯一标识符
            - 其他标准消息字段（role、content、type等）
        """
        # 重置所有agent的token统计
        logger.info("AgentController: 重置所有Agent的Token统计")
        self.reset_all_token_stats()
        
        # 记录工作流开始时间
        self.overall_token_stats['workflow_start_time'] = time.time()
        logger.info(f"AgentController: 开始流式工作流，会话ID: {session_id}")
        
        if system_context:
            logger.info(f"AgentController: 设置了system_context参数: {list(system_context.keys())}")
        
        try:
            # 准备会话和消息
            session_id = self._prepare_session_id(session_id)
            all_messages = self._prepare_initial_messages(input_messages)
            
            # 设置执行上下文
            system_context = self._setup_system_context(session_id, system_context)
            
            # 执行工作流
            if deep_research:
                # 多智能体协作模式：执行完整工作流（分解->规划->执行->观察->总结）
                # deep_thinking 独立控制是否执行任务分析
                yield from self._execute_multi_agent_workflow(
                    all_messages=all_messages,
                    tool_manager=tool_manager,
                    system_context=system_context,
                    session_id=session_id,
                    deep_thinking=deep_thinking,
                    summary=summary,
                    max_loop_count=max_loop_count
                )
            else:
                # 直接执行模式：可选的任务分析 + 直接执行
                yield from self._execute_simplified_workflow(
                    all_messages=all_messages,
                    tool_manager=tool_manager,
                    system_context=system_context,
                    session_id=session_id,
                    deep_thinking=deep_thinking
                )
            
            logger.info(f"AgentController: 流式工作流完成，会话ID: {session_id}")
            
        except Exception as e:
            logger.error(f"AgentController: 流式工作流执行过程中发生异常: {str(e)}")
            logger.error(f"异常详情: {traceback.format_exc()}")
            yield from self._handle_workflow_error(e)
        finally:
            # 记录工作流结束时间并打印统计
            self.overall_token_stats['workflow_end_time'] = time.time()
            self.print_comprehensive_token_stats()

    def _prepare_session_id(self, session_id: Optional[str]) -> str:
        """
        准备会话ID
        
        Args:
            session_id: 可选的会话ID
            
        Returns:
            str: 准备好的会话ID
        """
        if session_id is None:
            session_id = str(uuid.uuid1())
            logger.info(f"AgentController: 生成新会话ID: {session_id}")
        return session_id

    def _prepare_initial_messages(self, input_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        准备初始消息
        
        Args:
            input_messages: 输入消息列表
            
        Returns:
            List[Dict[str, Any]]: 准备好的消息列表
        """
        logger.debug("AgentController: 准备初始消息")
        
        # 为消息添加message_id（如果没有的话）
        all_messages = []
        for msg in input_messages.copy():
            if 'message_id' not in msg:
                msg = {**msg, 'message_id': str(uuid.uuid4())} 
            all_messages.append(msg)
        
        # 清理过长的消息历史
        all_messages = self._trim_message_history(all_messages)
        
        logger.info(f"AgentController: 初始化消息数量: {len(all_messages)}")
        return all_messages

    def _trim_message_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        修剪消息历史，防止内容过长
        
        Args:
            messages: 原始消息列表
            
        Returns:
            List[Dict[str, Any]]: 修剪后的消息列表
        """
        logger.debug("AgentController: 检查并修剪消息历史")
        
        # 如果消息内容过长，删除非关键消息
        start_index = 0
        while len(json.dumps(messages)) > self.DEFAULT_MESSAGE_LIMIT and start_index < len(messages):
            if messages[start_index]['role'] == 'user' or messages[start_index].get('type') == 'final_answer':
                start_index += 1
                continue
            else:
                del messages[start_index]
                continue
        
        logger.debug(f"AgentController: 修剪后消息数量: {len(messages)}")
        return messages

    def _setup_system_context(self, session_id: str, user_system_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        设置系统上下文（合并基础信息和用户自定义信息）
        
        Args:
            session_id: 会话ID
            user_system_context: 用户提供的系统上下文
            
        Returns:
            Dict[str, Any]: 完整的系统上下文字典
        """
        logger.debug("AgentController: 设置系统上下文")
        
        current_time_str = datetime.datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')
        file_workspace = get_settings().get_workspace_path(session_id)
        
        # 创建工作目录
        if os.path.exists(file_workspace):
            logger.debug("AgentController: 使用现有工作目录")
        else:
            os.makedirs(file_workspace, exist_ok=True)
            logger.debug(f"AgentController: 创建工作目录: {file_workspace}")
        
        # 构建完整的系统上下文，基础信息在前
        system_context = {
            'session_id': session_id,
            'current_time': current_time_str, 
            'file_workspace': file_workspace
        }
        
        # 如果用户提供了自定义上下文，则合并
        if user_system_context:
            system_context.update(user_system_context)
            logger.info(f"AgentController: 合并用户系统上下文: {list(user_system_context.keys())}")
        
        logger.info(f"AgentController: 系统上下文设置完成，包含 {len(system_context)} 个字段")
        return system_context

    def _execute_multi_agent_workflow(self, 
                                     all_messages: List[Dict[str, Any]],
                                     tool_manager: Optional[Any],
                                     system_context: Dict[str, Any],
                                     session_id: str,
                                     deep_thinking: bool,
                                     summary: bool,
                                     max_loop_count: int) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行完整的工作流
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            deep_thinking: 是否进行深度思考
            summary: 是否生成总结
            max_loop_count: 最大循环次数
            
        Yields:
            List[Dict[str, Any]]: 工作流输出的消息块
        """
        logger.info("AgentController: 开始执行完整工作流")
        
        # 1. 任务分析阶段
        if deep_thinking:
            all_messages = yield from self._execute_task_analysis_phase(
                all_messages, tool_manager, system_context, session_id
            )
        
        # 2. 任务分解阶段
        all_messages = yield from self._execute_task_decomposition_phase(
            all_messages, tool_manager, system_context, session_id
        )
        
        # 3. 规划-执行-观察循环
        all_messages = yield from self._execute_main_loop(
            all_messages, tool_manager, system_context, session_id, max_loop_count
        )
        
        # 4. 任务总结阶段
        if summary:
            if self._has_recent_unrecoverable_failure(all_messages):
                logger.warning("AgentController: 检测到不可恢复失败，跳过任务总结阶段")
            else:
                all_messages = yield from self._execute_task_summary_phase(
                    all_messages, tool_manager, system_context, session_id
                )

    def _execute_task_analysis_phase(self, 
                                   all_messages: List[Dict[str, Any]],
                                   tool_manager: Optional[Any],
                                   system_context: Dict[str, Any],
                                   session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行任务分析阶段
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 任务分析输出的消息块
            
        Returns:
            List[Dict[str, Any]]: 更新后的消息列表
        """
        logger.info("AgentController: 开始任务分析阶段")
        
        analysis_chunks = []
        for chunk in self.task_analysis_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            analysis_chunks.append(chunk)
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info(f"AgentController: 任务分析阶段完成，生成 {len(analysis_chunks)} 个块")
        return all_messages

    def _execute_task_decomposition_phase(self, 
                                        all_messages: List[Dict[str, Any]],
                                        tool_manager: Optional[Any],
                                        system_context: Dict[str, Any],
                                        session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行任务分解阶段
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 任务分解输出的消息块
            
        Returns:
            List[Dict[str, Any]]: 更新后的消息列表
        """
        logger.info("AgentController: 开始任务分解阶段")
        
        decompose_chunks = []
        for chunk in self.task_decompose_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            decompose_chunks.append(chunk)
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info(f"AgentController: 任务分解阶段完成，生成 {len(decompose_chunks)} 个块")
        return all_messages

    def _execute_main_loop(self, 
                         all_messages: List[Dict[str, Any]],
                         tool_manager: Optional[Any],
                         system_context: Dict[str, Any],
                         session_id: str,
                         max_loop_count: int) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行主要的规划-执行-观察循环
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            max_loop_count: 最大循环次数
            
        Yields:
            List[Dict[str, Any]]: 循环输出的消息块
            
        Returns:
            List[Dict[str, Any]]: 更新后的消息列表
        """
        logger.info("AgentController: 开始规划-执行-观察循环")
        
        loop_count = 0
        loop_guard_state: Dict[str, Any] = {}
        while True:
            loop_count += 1
            logger.info(f"AgentController: 开始第 {loop_count} 轮循环")
            
            if loop_count > max_loop_count:
                logger.warning(f"AgentController: 达到最大循环次数 {max_loop_count}，停止工作流")
                break

            # 规划阶段
            all_messages = yield from self._execute_planning_phase(
                all_messages, tool_manager, system_context, session_id
            )

            if self._is_phase_failure(all_messages, 'planning_result'):
                logger.warning("AgentController: 规划阶段失败，终止后续执行以避免级联失败")
                yield from self._emit_failure_final_answer(all_messages, "规划")
                break
            
            # 执行阶段
            all_messages = yield from self._execute_execution_phase(
                all_messages, tool_manager, system_context, session_id
            )

            if self._is_phase_failure(all_messages, 'do_subtask_result'):
                logger.warning("AgentController: 执行阶段失败，终止后续观察以避免级联失败")
                yield from self._emit_failure_final_answer(all_messages, "执行")
                break

            repeated_tool_call = self._find_repeated_tool_call(all_messages)
            if repeated_tool_call:
                reason = f"检测到相同工具和参数重复调用: {repeated_tool_call}"
                logger.warning(f"AgentController: {reason}")
                self._append_loop_guard_context(all_messages, reason)
                break
            
            # 观察阶段
            all_messages, should_break = yield from self._execute_observation_phase(
                all_messages, tool_manager, system_context, session_id
            )

            if self._is_phase_failure(all_messages, 'observation_result'):
                logger.warning("AgentController: 观察阶段失败，终止当前工作流")
                yield from self._emit_failure_final_answer(all_messages, "观察")
                break
            
            if should_break:
                break

            stagnation_reason = self._update_stagnation_guard(all_messages, loop_guard_state)
            if stagnation_reason:
                logger.warning(f"AgentController: {stagnation_reason}")
                self._append_loop_guard_context(all_messages, stagnation_reason)
                break
        
        logger.info("AgentController: 规划-执行-观察循环完成")
        return all_messages

    def _is_failure_message(self, message: Optional[Dict[str, Any]]) -> bool:
        """
        判断消息是否表示阶段失败。

        Args:
            message: 消息字典

        Returns:
            bool: 是否为失败消息
        """
        if not message:
            return False

        content = str(message.get('content', '') or '')
        if not content.strip():
            return False

        lowered = content.lower()
        if any(keyword in lowered for keyword in ['internalerror', 'invalidparameter', 'range of input length']):
            return True

        # 对于 planning_result 类型，不使用通用的 '失败' 关键字检测，
        # 因为规划内容中经常出现 "若失败则..." 这类 fallback 描述。
        msg_type = message.get('type', '')
        if msg_type == 'planning_result':
            return False

        return '失败' in content

    def _extract_observation_payload(self, message: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        从 observation_result 中提取结构化 JSON 负载。

        Args:
            message: 消息字典

        Returns:
            Optional[Dict[str, Any]]: 解析出的 observation JSON；无法解析时返回 None
        """
        if not isinstance(message, dict):
            return None

        content = str(message.get('content', '') or '').strip()
        if not content:
            return None

        if content.startswith('Observation:'):
            content = content[len('Observation:'):].strip()

        if not content or not content.startswith('{'):
            return None

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None

        return payload if isinstance(payload, dict) else None

    def _is_unrecoverable_observation_message(self, message: Optional[Dict[str, Any]]) -> bool:
        """
        判断 observation_result 是否为不可恢复失败。

        规则：
        - 对于结构化 Observation JSON，只在出现明确的链路/配额/协议/索引类硬错误时判失败。
        - 对于非结构化 observation_result，回退到通用失败判断。
        """
        payload = self._extract_observation_payload(message)
        if payload is None:
            return self._is_failure_message(message)

        analysis = str(payload.get('analysis', '') or '')
        lowered = analysis.lower()

        hard_error_markers = [
            'allocationquota',
            'free tier',
            'exceeded your current quota',
            'internalerror',
            'invalidparameter',
            'messages with role "tool"',
            'tool_calls',
            'list index out of range',
            'jsondecodeerror',
            'traceback',
            'apierror',
            '任务执行失败',
            '规划失败',
            '观察分析失败',
            '阶段执行出现异常',
            '阶段解析结果异常',
        ]

        return any(marker in lowered for marker in hard_error_markers)

    def _is_unrecoverable_failure_message(self, message: Optional[Dict[str, Any]]) -> bool:
        """
        判断消息是否属于不可恢复失败。

        Args:
            message: 消息字典

        Returns:
            bool: 是否为不可恢复失败
        """
        if not message:
            return False

        if message.get('type') == 'observation_result':
            return self._is_unrecoverable_observation_message(message)

        return self._is_failure_message(message)

    def _is_phase_failure(self, all_messages: List[Dict[str, Any]], phase_type: str) -> bool:
        """
        判断指定阶段是否失败。

        Args:
            all_messages: 全量消息
            phase_type: 阶段消息类型

        Returns:
            bool: 是否失败
        """
        if not all_messages:
            return False

        last_msg = all_messages[-1]
        if last_msg.get('type') != phase_type:
            return False

        if phase_type == 'observation_result':
            return self._is_unrecoverable_observation_message(last_msg)

        if phase_type == 'planning_result':
            return self._is_planning_failure(last_msg)

        return self._is_unrecoverable_failure_message(last_msg)

    def _is_planning_failure(self, message: Optional[Dict[str, Any]]) -> bool:
        """
        专门判断 planning_result 是否为真正的规划失败。

        规则：
        - 如果 content 以 'Planning: ' 开头，说明规划已成功完成XML解析并
          生成了结构化JSON，此时不应判为失败（即使内容中有"失败"字样，
          那只是对 fallback 策略的描述）。
        - 只有 content 中包含明确的系统级错误关键字时，才判为规划失败。

        Args:
            message: 消息字典

        Returns:
            bool: 是否为规划失败
        """
        if not message:
            return False

        content = str(message.get('content', '') or '').strip()
        if not content:
            return False

        # 规划成功完成了结构化输出，不是失败
        if content.startswith('Planning: '):
            return False

        lowered = content.lower()

        # 只有明确的系统/模型级硬错误才判为规划失败
        hard_error_markers = [
            'internalerror',
            'invalidparameter',
            'range of input length',
            'allocationquota',
            'exceeded your current quota',
            'traceback',
            'apierror',
            '任务规划失败',
        ]

        return any(marker in lowered for marker in hard_error_markers)

    def _build_user_safe_failure_message(self, phase_name: str, raw_content: str) -> str:
        """
        将阶段失败内容转换为用户可读且不泄漏内部实现的提示。

        Args:
            phase_name: 阶段名称
            raw_content: 原始失败内容

        Returns:
            str: 安全的用户提示
        """
        content = str(raw_content or "")
        lowered = content.lower()

        if any(key in lowered for key in ["allocationquota", "free tier", "exceeded your current quota", "quota"]):
            return "当前模型额度已耗尽，暂时无法完成多阶段推理。请更换有额度的模型配置后重试。"

        if any(key in lowered for key in ["invalidparameter", "messages with role \"tool\"", "tool_calls"]):
            return "当前工具调用链路出现异常，已停止深度推理流程。请重试一次，若仍失败可先关闭深度思考后再试。"

        if any(key in lowered for key in ["list index out of range", "jsondecodeerror", "解析", "xml"]):
            return f"{phase_name}阶段解析结果异常，已中止后续链路并返回可用结果。请稍后重试。"

        return f"{phase_name}阶段执行出现异常，已停止后续链路。请稍后重试。"

    def _emit_failure_final_answer(self,
                                 all_messages: List[Dict[str, Any]],
                                 phase_name: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        将阶段失败信息提升为最终回答，确保前端可见且避免重复链路失败。

        Args:
            all_messages: 全量消息
            phase_name: 阶段名称

        Yields:
            List[Dict[str, Any]]: 最终回答消息块
        """
        if not all_messages:
            return

        last_msg = all_messages[-1]
        if not self._is_unrecoverable_failure_message(last_msg):
            return

        raw_content = str(last_msg.get('show_content') or last_msg.get('content') or '').strip()
        content = self._build_user_safe_failure_message(phase_name, raw_content)

        final_chunk = [{
            'role': 'assistant',
            'content': content,
            'type': 'final_answer',
            'message_id': str(uuid.uuid4()),
            'show_content': content
        }]

        merged_messages = self.task_analysis_agent._merge_messages(all_messages, final_chunk)
        all_messages.clear()
        all_messages.extend(merged_messages)
        yield final_chunk

    def _has_recent_unrecoverable_failure(self, all_messages: List[Dict[str, Any]]) -> bool:
        """
        检查最近消息中是否存在不可恢复失败。

        Args:
            all_messages: 全量消息

        Returns:
            bool: 是否存在不可恢复失败
        """
        if not all_messages:
            return False

        watched_types = {
            'task_analysis_result',
            'task_decomposition',
            'planning_result',
            'do_subtask_result',
            'observation_result',
            'final_answer'
        }

        recent_messages = all_messages[-6:]
        for msg in reversed(recent_messages):
            if msg.get('type') not in watched_types:
                continue
            if self._is_unrecoverable_failure_message(msg):
                return True

        return False

    def _execute_planning_phase(self, 
                              all_messages: List[Dict[str, Any]],
                              tool_manager: Optional[Any],
                              system_context: Dict[str, Any],
                              session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行规划阶段
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 规划输出的消息块
            
        Returns:
            List[Dict[str, Any]]: 更新后的消息列表
        """
        logger.info("AgentController: 开始规划阶段")
        
        plan_chunks = []
        for chunk in self.planning_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            plan_chunks.append(chunk)
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info(f"AgentController: 规划阶段完成，生成 {len(plan_chunks)} 个块")
        return all_messages

    def _execute_execution_phase(self, 
                               all_messages: List[Dict[str, Any]],
                               tool_manager: Optional[Any],
                               system_context: Dict[str, Any],
                               session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行执行阶段
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 执行输出的消息块
            
        Returns:
            List[Dict[str, Any]]: 更新后的消息列表
        """
        logger.info("AgentController: 开始执行阶段")
        
        exec_chunks = []
        for chunk in self.executor_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            exec_chunks.append(chunk)
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info(f"AgentController: 执行阶段完成，生成 {len(exec_chunks)} 个块")
        return all_messages

    def _execute_observation_phase(self, 
                                 all_messages: List[Dict[str, Any]],
                                 tool_manager: Optional[Any],
                                 system_context: Dict[str, Any],
                                 session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行观察阶段
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 观察输出的消息块
            
        Returns:
            Tuple[List[Dict[str, Any]], bool]: 更新后的消息列表和是否应该中断循环
        """
        logger.info("AgentController: 开始观察阶段")
        
        obs_chunks = []
        for chunk in self.observation_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            obs_chunks.append(chunk)
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info(f"AgentController: 观察阶段完成，生成 {len(obs_chunks)} 个块")
        
        # 检查是否应该继续循环
        should_break = self._check_loop_completion(all_messages)
        
        return all_messages, should_break

    def _execute_task_summary_phase(self, 
                                  all_messages: List[Dict[str, Any]],
                                  tool_manager: Optional[Any],
                                  system_context: Dict[str, Any],
                                  session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行任务总结阶段
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 总结输出的消息块
            
        Returns:
            List[Dict[str, Any]]: 更新后的消息列表
        """
        logger.info("AgentController: 开始任务总结阶段")
        
        summary_chunks = []
        for chunk in self.task_summary_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            summary_chunks.append(chunk)
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info(f"AgentController: 任务总结阶段完成，生成 {len(summary_chunks)} 个块")
        return all_messages

    def _execute_simplified_workflow(self, 
                                    all_messages: List[Dict[str, Any]],
                                    tool_manager: Optional[Any],
                                    system_context: Dict[str, Any],
                                    session_id: str,
                                    deep_thinking: bool) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行简化工作流（可选的任务分析 + 直接执行）
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            deep_thinking: 是否进行任务分析
            
        Yields:
            List[Dict[str, Any]]: 工作流输出的消息块
        """
        logger.info("AgentController: 开始简化工作流")
        
        # 1. 任务分析阶段
        if deep_thinking:
            all_messages = yield from self._execute_task_analysis_phase(
                all_messages, tool_manager, system_context, session_id
            )
        
        # 2. 直接执行
        yield from self._execute_direct_workflow(
            all_messages=all_messages,
            tool_manager=tool_manager,
            system_context=system_context,
            session_id=session_id
        )

    def _execute_direct_workflow(self, 
                               all_messages: List[Dict[str, Any]],
                               tool_manager: Optional[Any],
                               system_context: Dict[str, Any],
                               session_id: str) -> Generator[List[Dict[str, Any]], None, None]:
        """
        执行直接工作流（使用直接执行智能体）
        
        Args:
            all_messages: 所有消息列表
            tool_manager: 工具管理器
            system_context: 执行上下文
            session_id: 会话ID
            
        Yields:
            List[Dict[str, Any]]: 直接执行输出的消息块
        """
        logger.info("AgentController: 使用直接执行智能体")
        
        for chunk in self.direct_executor_agent.run_stream(
            messages=all_messages, 
            tool_manager=tool_manager, 
            system_context=system_context, 
            session_id=session_id
        ):
            all_messages = self.task_analysis_agent._merge_messages(all_messages, chunk)
            yield chunk
        
        logger.info("AgentController: 直接执行智能体完成")

    @staticmethod
    def _normalize_tool_arguments(arguments: Any) -> str:
        if isinstance(arguments, str):
            text = arguments.strip()
            try:
                parsed = json.loads(text)
            except Exception:
                return text
        else:
            parsed = arguments

        try:
            return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        except Exception:
            return str(parsed)

    def _find_repeated_tool_call(self, all_messages: List[Dict[str, Any]]) -> str:
        last_user_index = -1
        for index in range(len(all_messages) - 1, -1, -1):
            if all_messages[index].get('role') == 'user':
                last_user_index = index
                break

        counts: Dict[str, int] = {}
        labels: Dict[str, str] = {}
        for message in all_messages[last_user_index + 1:]:
            tool_calls = message.get('tool_calls')
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get('function')
                if not isinstance(function, dict):
                    continue
                tool_name = str(function.get('name') or '').strip()
                if not tool_name:
                    continue
                normalized_arguments = self._normalize_tool_arguments(function.get('arguments', ''))
                signature = f"{tool_name}:{normalized_arguments}"
                counts[signature] = counts.get(signature, 0) + 1
                labels[signature] = f"{tool_name}({normalized_arguments})"
                if counts[signature] >= self.DUPLICATE_TOOL_CALL_LIMIT:
                    return labels[signature]
        return ""

    @staticmethod
    def _latest_observation_result(all_messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for message in reversed(all_messages):
            if message.get('type') != 'observation_result':
                continue
            content = str(message.get('content') or '').replace('Observation: ', '', 1)
            try:
                payload = json.loads(content)
            except Exception:
                return None
            return payload if isinstance(payload, dict) else None
        return None

    def _update_stagnation_guard(
        self,
        all_messages: List[Dict[str, Any]],
        guard_state: Dict[str, Any],
    ) -> str:
        observation = self._latest_observation_result(all_messages)
        if not observation:
            return ""

        try:
            finish_percent = int(observation.get('finish_percent', 0))
        except (TypeError, ValueError):
            finish_percent = 0

        last_finish_percent = guard_state.get('last_finish_percent')
        if last_finish_percent is None or finish_percent > last_finish_percent:
            guard_state['last_finish_percent'] = finish_percent
            guard_state['stagnant_observations'] = 0
            return ""

        guard_state['stagnant_observations'] = int(guard_state.get('stagnant_observations', 0)) + 1
        if guard_state['stagnant_observations'] >= self.STAGNANT_OBSERVATION_LIMIT:
            return f"任务完成度连续未增长，当前完成度为 {finish_percent}%"
        return ""

    @staticmethod
    def _append_loop_guard_context(all_messages: List[Dict[str, Any]], reason: str) -> None:
        all_messages.append(
            {
                'role': 'system',
                'content': f"【循环保护】{reason}。已停止重复执行，请基于已有工具结果完成回答。",
                'type': 'system_loop_guard_context',
                'message_id': str(uuid.uuid4()),
            }
        )

    def _check_loop_completion(self, all_messages: List[Dict[str, Any]]) -> bool:
        """
        检查循环是否应该完成
        
        Args:
            all_messages: 所有消息列表
            
        Returns:
            bool: 是否应该中断循环
        """
        logger.debug("AgentController: 检查循环完成条件")
        
        try:
            obs_content = all_messages[-1]['content'].replace('Observation: ', '')
            obs_result = json.loads(obs_content)
            
            if obs_result.get('is_completed', False):
                logger.info("AgentController: 观察阶段指示任务已完成")
                return True
                
            if obs_result.get('needs_more_input', False):
                logger.info("AgentController: 任务需要用户提供更多输入")
                clarify_msg = {
                    'role': 'assistant',
                    'content': obs_result.get('user_query', ''),
                    'type': 'final_answer',
                    'message_id': str(uuid.uuid4()),
                    'show_content': obs_result.get('user_query', '') + '\n'
                }
                all_messages.append(clarify_msg)
                return True
                
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning(f"AgentController: 解析观察结果失败: {str(e)}，继续循环")
            
        return False

    def _handle_workflow_error(self, error: Exception) -> Generator[List[Dict[str, Any]], None, None]:
        """
        处理工作流执行错误
        
        Args:
            error: 发生的异常
            
        Yields:
            List[Dict[str, Any]]: 错误消息块
        """
        logger.error(f"AgentController: 处理工作流错误: {str(error)}")
        
        error_message = f"工作流执行失败: {str(error)}"
        message_id = str(uuid.uuid4())
        
        yield [{
            'role': 'assistant',
            'content': error_message,
            'type': 'final_answer',
            'message_id': message_id,
            'show_content': error_message
        }]

    def run(self, 
            input_messages: List[Dict[str, Any]], 
            tool_manager: Optional[Any] = None, 
            session_id: Optional[str] = None, 
            deep_thinking: bool = True,
            summary: bool = True,
            max_loop_count: int = DEFAULT_MAX_LOOP_COUNT,
            deep_research: bool = True,
            system_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        执行智能体工作流（非流式版本）
        
        Args:
            input_messages: 输入消息字典列表
            tool_manager: 工具管理器实例
            session_id: 会话ID
            deep_thinking: 是否进行任务分析
            summary: 是否生成任务总结
            max_loop_count: 最大循环次数
            deep_research: 是否进行深度研究（完整流程）
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Returns:
            Dict[str, Any]: 包含all_messages、new_messages、final_output和session_id的结果字典
        """
        logger.info(f"AgentController: 开始非流式工作流，会话ID: {session_id}")
        
        if system_context:
            logger.info(f"AgentController: 设置了system_context参数: {list(system_context.keys())}")
        
        # 重置所有agent的token统计
        logger.info("AgentController: 重置所有Agent的Token统计")
        self.reset_all_token_stats()
        
        # 记录工作流开始时间
        self.overall_token_stats['workflow_start_time'] = time.time()
        
        try:
            # 准备会话和消息
            session_id = self._prepare_session_id(session_id)
            
            # 初始化消息和状态
            all_messages = input_messages.copy()
            new_messages = []
            
            logger.info(f"AgentController: 初始化 {len(all_messages)} 条输入消息")
            
            # 根据deep_research参数选择执行路径
            if deep_research:
                # 多智能体协作模式：执行完整流程（分解->规划->执行->观察->总结）
                # deep_thinking 独立控制是否执行任务分析
                if deep_thinking:
                    all_messages, new_messages = self._execute_task_analysis_non_stream(
                        all_messages, new_messages, tool_manager, system_context
                    )
                    if self._is_phase_failure(all_messages, 'task_analysis_result'):
                        logger.warning("AgentController: 任务分析阶段失败，终止后续流程")
                        self._append_non_stream_failure_final_answer(all_messages, new_messages, "任务分析")
                        final_output = new_messages[-1] if new_messages else None
                        return {
                            'all_messages': all_messages,
                            'new_messages': new_messages,
                            'final_output': final_output,
                            'session_id': session_id,
                        }
                
                # 任务分解阶段
                all_messages, new_messages = self._execute_task_decompose_non_stream(
                    all_messages, new_messages, tool_manager, system_context
                )
                if self._is_phase_failure(all_messages, 'task_decomposition'):
                    logger.warning("AgentController: 任务分解阶段失败，终止后续流程")
                    self._append_non_stream_failure_final_answer(all_messages, new_messages, "任务分解")
                    final_output = new_messages[-1] if new_messages else None
                    return {
                        'all_messages': all_messages,
                        'new_messages': new_messages,
                        'final_output': final_output,
                        'session_id': session_id,
                    }
                
                # 主循环
                all_messages, new_messages = self._execute_main_loop_non_stream(
                    all_messages, new_messages, tool_manager, session_id, max_loop_count, system_context
                )
                
                # 总结阶段
                if summary:
                    if self._has_recent_unrecoverable_failure(all_messages):
                        logger.warning("AgentController: 检测到不可恢复失败，跳过任务总结阶段")
                        final_output = next(
                            (m for m in reversed(new_messages) if m.get('type') == 'final_answer'),
                            new_messages[-1] if new_messages else None
                        )
                    else:
                        all_messages, new_messages, final_output = self._execute_task_summary_non_stream(
                            all_messages, new_messages, tool_manager, system_context
                        )
                else:
                    final_output = new_messages[-1] if new_messages else None
            else:
                # 简化模式：可选的任务分析 + 直接执行
                if deep_thinking:
                    all_messages, new_messages = self._execute_task_analysis_non_stream(
                        all_messages, new_messages, tool_manager, system_context
                    )
                
                # 直接执行
                direct_messages = self.direct_executor_agent.run(
                    all_messages, tool_manager, session_id=session_id, system_context=system_context
                )
                all_messages.extend(direct_messages)
                new_messages.extend(direct_messages)
                final_output = new_messages[-1] if new_messages else None
            
            logger.info(f"AgentController: 非流式工作流完成，会话ID: {session_id}")
            
            return {
                'all_messages': all_messages,
                'new_messages': new_messages,
                'final_output': final_output,
                'session_id': session_id,
            }
            
        except Exception as e:
            logger.error(f"AgentController: 非流式工作流执行过程中发生异常: {str(e)}")
            logger.error(f"异常详情: {traceback.format_exc()}")
            
            error_message = {
                'role': 'assistant',
                'content': f"工作流执行失败: {str(e)}",
                'type': 'final_answer'
            }
            
            return {
                'all_messages': input_messages + [error_message],
                'new_messages': [error_message],
                'final_output': error_message,
                'session_id': session_id or str(uuid.uuid1()),
            }
        finally:
            # 记录工作流结束时间并打印统计
            self.overall_token_stats['workflow_end_time'] = time.time()
            self.print_comprehensive_token_stats()

    def _execute_task_analysis_non_stream(self, 
                                        all_messages: List[Dict[str, Any]], 
                                        new_messages: List[Dict[str, Any]], 
                                        tool_manager: Optional[Any],
                                        system_context: Optional[Dict[str, Any]]) -> tuple:
        """
        执行任务分析（非流式版本）
        
        Args:
            all_messages: 所有消息列表
            new_messages: 新消息列表
            tool_manager: 工具管理器
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Returns:
            tuple: 更新后的(all_messages, new_messages)
        """
        logger.info("AgentController: 开始初始任务分析")
        
        analysis_messages = self.task_analysis_agent.run(all_messages, tool_manager, system_context=system_context)
        logger.info(f"AgentController: 任务分析完成，生成 {len(analysis_messages)} 条消息")
        
        all_messages.extend(analysis_messages)
        new_messages.extend(analysis_messages)
        
        return all_messages, new_messages

    def _execute_task_decompose_non_stream(self, 
                                         all_messages: List[Dict[str, Any]], 
                                         new_messages: List[Dict[str, Any]], 
                                         tool_manager: Optional[Any],
                                         system_context: Optional[Dict[str, Any]]) -> tuple:
        """
        执行任务分解（非流式版本）
        
        Args:
            all_messages: 所有消息列表
            new_messages: 新消息列表
            tool_manager: 工具管理器
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Returns:
            tuple: 更新后的(all_messages, new_messages)
        """
        logger.info("AgentController: 开始任务分解")
        
        decompose_messages = self.task_decompose_agent.run(all_messages, tool_manager, system_context=system_context)
        logger.info(f"AgentController: 任务分解完成，生成 {len(decompose_messages)} 条消息")
        
        all_messages.extend(decompose_messages)
        new_messages.extend(decompose_messages)
        
        return all_messages, new_messages

    def _execute_main_loop_non_stream(self, 
                                    all_messages: List[Dict[str, Any]], 
                                    new_messages: List[Dict[str, Any]], 
                                    tool_manager: Optional[Any], 
                                    session_id: str,
                                    max_loop_count: int,
                                    system_context: Optional[Dict[str, Any]]) -> tuple:
        """
        执行主循环（非流式版本）
        
        Args:
            all_messages: 所有消息列表
            new_messages: 新消息列表
            tool_manager: 工具管理器
            session_id: 会话ID
            max_loop_count: 最大循环次数
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Returns:
            tuple: 更新后的(all_messages, new_messages)
        """
        loop_count = 0
        loop_guard_state: Dict[str, Any] = {}
        
        while loop_count < max_loop_count:
            loop_count += 1
            logger.info(f"AgentController: 开始第 {loop_count} 轮规划-执行-观察循环")
            
            # 规划阶段
            plan_messages = self.planning_agent.run(all_messages, tool_manager, system_context=system_context)
            logger.info(f"AgentController: 规划阶段完成，生成 {len(plan_messages)} 条消息")
            all_messages.extend(plan_messages)
            new_messages.extend(plan_messages)

            if self._is_phase_failure(all_messages, 'planning_result'):
                logger.warning("AgentController: 规划阶段失败，终止后续执行以避免级联失败")
                self._append_non_stream_failure_final_answer(all_messages, new_messages, "规划")
                break
            
            # 执行阶段
            exec_messages = self.executor_agent.run(all_messages, tool_manager, session_id=session_id, system_context=system_context)
            logger.info(f"AgentController: 执行阶段完成，生成 {len(exec_messages)} 条消息")
            all_messages.extend(exec_messages)
            new_messages.extend(exec_messages)

            if self._is_phase_failure(all_messages, 'do_subtask_result'):
                logger.warning("AgentController: 执行阶段失败，终止后续观察以避免级联失败")
                self._append_non_stream_failure_final_answer(all_messages, new_messages, "执行")
                break

            repeated_tool_call = self._find_repeated_tool_call(all_messages)
            if repeated_tool_call:
                reason = f"检测到相同工具和参数重复调用: {repeated_tool_call}"
                logger.warning(f"AgentController: {reason}")
                self._append_loop_guard_context(all_messages, reason)
                break
            
            # 观察阶段
            obs_messages = self.observation_agent.run(all_messages, system_context=system_context)
            logger.info(f"AgentController: 观察阶段完成，生成 {len(obs_messages)} 条消息")
            all_messages.extend(obs_messages)
            new_messages.extend(obs_messages)

            if self._is_phase_failure(all_messages, 'observation_result'):
                logger.warning("AgentController: 观察阶段失败，终止当前工作流")
                self._append_non_stream_failure_final_answer(all_messages, new_messages, "观察")
                break
            
            # 检查任务是否完成
            should_break = self._check_task_completion(obs_messages, all_messages, new_messages)
            if should_break:
                break

            stagnation_reason = self._update_stagnation_guard(all_messages, loop_guard_state)
            if stagnation_reason:
                logger.warning(f"AgentController: {stagnation_reason}")
                self._append_loop_guard_context(all_messages, stagnation_reason)
                break
        
        if loop_count >= max_loop_count:
            logger.warning(f"AgentController: 达到最大循环次数 {max_loop_count}，强制结束")
        
        return all_messages, new_messages

    def _append_non_stream_failure_final_answer(self,
                                              all_messages: List[Dict[str, Any]],
                                              new_messages: List[Dict[str, Any]],
                                              phase_name: str) -> None:
        """
        在非流式链路中补发最终失败回答，避免前端只看到中间失败阶段消息。

        Args:
            all_messages: 全量消息
            new_messages: 本次新增消息
            phase_name: 阶段名称
        """
        if not all_messages:
            return

        last_msg = all_messages[-1]
        if not self._is_unrecoverable_failure_message(last_msg):
            return

        if last_msg.get('type') == 'final_answer':
            return

        raw_content = str(last_msg.get('show_content') or last_msg.get('content') or '').strip()
        content = self._build_user_safe_failure_message(phase_name, raw_content)

        final_message = {
            'role': 'assistant',
            'content': content,
            'type': 'final_answer',
            'message_id': str(uuid.uuid4()),
            'show_content': content,
        }

        all_messages.append(final_message)
        new_messages.append(final_message)

    def _check_task_completion(self, 
                             obs_messages: List[Dict[str, Any]], 
                             all_messages: List[Dict[str, Any]], 
                             new_messages: List[Dict[str, Any]]) -> bool:
        """
        检查任务是否完成
        
        Args:
            obs_messages: 观察消息列表
            all_messages: 所有消息列表
            new_messages: 新消息列表
            
        Returns:
            bool: 是否应该中断循环
        """
        try:
            obs_result_content = obs_messages[-1]['content'].replace('Observation: ', '')
            obs_result_json = json.loads(obs_result_content)
            
            if obs_result_json.get('is_completed', False):
                logger.info("AgentController: 观察阶段指示任务已完成")
                return True
                
            if obs_result_json.get('needs_more_input', False):
                logger.info("AgentController: 任务需要用户提供更多输入")
                clarify_message = {
                    'role': 'assistant',
                    'content': obs_result_json.get('user_query', ''),
                    'type': 'final_answer'
                }
                all_messages.append(clarify_message)
                new_messages.append(clarify_message)
                return True
                
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning(f"AgentController: 观察结果解析失败: {str(e)}，继续执行")
            
        return False

    def _execute_task_summary_non_stream(self, 
                                       all_messages: List[Dict[str, Any]], 
                                       new_messages: List[Dict[str, Any]], 
                                       tool_manager: Optional[Any],
                                       system_context: Optional[Dict[str, Any]]) -> tuple:
        """
        执行任务总结（非流式版本）
        
        Args:
            all_messages: 所有消息列表
            new_messages: 新消息列表
            tool_manager: 工具管理器
            system_context: 运行时系统上下文字典，用于自定义推理时的变化信息
            
        Returns:
            tuple: 更新后的(all_messages, new_messages, final_output)
        """
        logger.info("AgentController: 开始任务总结阶段")
        
        summary_result = self.task_summary_agent.run(all_messages, tool_manager, system_context=system_context)
        logger.info(f"AgentController: 任务总结完成，生成 {len(summary_result)} 条消息")
        
        all_messages.extend(summary_result)
        new_messages.extend(summary_result)
        
        # 获取最终输出（最后一条正常消息）
        final_output = next(
            (m for m in reversed(summary_result) if m.get('type') == 'final_answer'),
            summary_result[-1] if summary_result else None
        )
        
        return all_messages, new_messages, final_output

    def _is_task_complete(self, messages: List[Dict[str, Any]]) -> bool:
        """
        基于评估输出检查任务是否完成
        
        Args:
            messages: 消息列表
            
        Returns:
            bool: 任务是否完成
        """
        logger.debug("AgentController: 检查任务完成状态")
        
        # 查找工具响应消息
        tool_response = next(
            (msg for msg in messages 
             if msg.get('role') == 'tool' and 
                msg.get('tool_call_id', '').startswith('decision_')),
            None
        )
        
        if not tool_response or not tool_response.get('content'):
            return False
            
        content = tool_response['content']
        
        try:
            # 尝试直接解析为JSON
            result = json.loads(content)
        except json.JSONDecodeError:
            # 尝试从markdown代码块中提取JSON
            code_block_pattern = r'```(?:json)?\n([\s\S]*?)\n```'
            match = re.search(code_block_pattern, content)
            if match:
                try:
                    result = json.loads(match.group(1))
                except json.JSONDecodeError:
                    return False
            else:
                return False
                
        is_complete = result.get('task_status', '') == 'completed'
        logger.debug(f"AgentController: 任务完成状态: {is_complete}")
        return is_complete

    def _collect_agent_stats(self) -> Dict[str, Any]:
        """
        收集所有agent的token统计信息
        
        Returns:
            Dict[str, Any]: 汇总的统计信息
        """
        all_stats = {}
        total_stats = {
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cached_tokens': 0,
            'total_reasoning_tokens': 0,
            'total_calls': 0,
            'agents': {}
        }
        
        # 收集各个agent的统计
        agents = [
            self.task_analysis_agent,
            self.executor_agent,
            self.task_summary_agent,
            self.planning_agent,
            self.observation_agent,
            self.direct_executor_agent,
            self.task_decompose_agent
        ]
        
        for agent in agents:
            if hasattr(agent, 'get_token_stats'):
                stats = agent.get_token_stats()
                all_stats[stats['agent_name']] = stats
                
                # 累加到总统计
                total_stats['total_input_tokens'] += stats['total_input_tokens']
                total_stats['total_output_tokens'] += stats['total_output_tokens']
                total_stats['total_cached_tokens'] += stats['total_cached_tokens']
                total_stats['total_reasoning_tokens'] += stats['total_reasoning_tokens']
                total_stats['total_calls'] += stats['total_calls']
                total_stats['agents'][stats['agent_name']] = stats
        
        return {
            'individual_stats': all_stats,
            'total_stats': total_stats
        }
    
    def print_comprehensive_token_stats(self):
        """
        打印综合的token使用统计
        """
        stats = self._collect_agent_stats()
        total = stats['total_stats']
        
        print("\n" + "="*80)
        print("🚀 AgentController 综合Token使用统计")
        print("="*80)
        
        # 总体统计
        print(f"\n📊 总体统计:")
        print(f"  📞 总调用次数: {total['total_calls']}")
        print(f"  📥 总输入tokens: {total['total_input_tokens']:,}")
        print(f"  📤 总输出tokens: {total['total_output_tokens']:,}")
        print(f"  🏃 总缓存tokens: {total['total_cached_tokens']:,}")
        print(f"  🧠 总推理tokens: {total['total_reasoning_tokens']:,}")
        print(f"  🔢 总计tokens: {total['total_input_tokens'] + total['total_output_tokens']:,}")
        
        if self.overall_token_stats['workflow_start_time'] and self.overall_token_stats['workflow_end_time']:
            workflow_time = self.overall_token_stats['workflow_end_time'] - self.overall_token_stats['workflow_start_time']
            print(f"  ⏱️  工作流总耗时: {workflow_time:.2f}秒")
        
        # 各agent详细统计
        print(f"\n🤖 各Agent详细统计:")
        for agent_name, agent_stats in total['agents'].items():
            if agent_stats['total_calls'] > 0:  # 只显示有调用的agent
                print(f"\n  🔹 {agent_name}:")
                print(f"    📞 调用: {agent_stats['total_calls']} 次")
                print(f"    📥 输入: {agent_stats['total_input_tokens']:,} tokens")
                print(f"    📤 输出: {agent_stats['total_output_tokens']:,} tokens")
                if agent_stats['total_cached_tokens'] > 0:
                    print(f"    🏃 缓存: {agent_stats['total_cached_tokens']:,} tokens")
                if agent_stats['total_reasoning_tokens'] > 0:
                    print(f"    🧠 推理: {agent_stats['total_reasoning_tokens']:,} tokens")
                print(f"    🔢 小计: {agent_stats['total_input_tokens'] + agent_stats['total_output_tokens']:,} tokens")
                
                # 显示步骤详情
                if agent_stats.get('step_details'):
                    print(f"    📋 步骤详情:")
                    for detail in agent_stats['step_details']:
                        print(f"      • {detail['step']}: 输入{detail['input_tokens']}, 输出{detail['output_tokens']}, 耗时{detail['execution_time']}s")
        
        print("\n" + "="*80)
        
    def reset_all_token_stats(self):
        """
        重置所有agent的token统计
        """
        agents = [
            self.task_analysis_agent,
            self.executor_agent,
            self.task_summary_agent,
            self.planning_agent,
            self.observation_agent,
            self.direct_executor_agent,
            self.task_decompose_agent
        ]
        
        for agent in agents:
            if hasattr(agent, 'reset_token_stats'):
                agent.reset_token_stats()
        
        # 重置总体统计
        self.overall_token_stats = {
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cached_tokens': 0,
            'total_reasoning_tokens': 0,
            'total_calls': 0,
            'total_execution_time': 0,
            'workflow_start_time': None,
            'workflow_end_time': None
        }
        
        logger.info("AgentController: 所有Token统计已重置")
