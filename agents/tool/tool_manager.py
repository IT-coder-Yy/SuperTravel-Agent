from typing import Dict, Any, List, Type, Optional, Union
from agents.tool.tool_base import ToolBase, ToolSpec, McpToolSpec,SseServerParameters,AgentToolSpec
from agents.utils.logger import logger
import importlib
import pkgutil
from pathlib import Path
import inspect
import json
import asyncio
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp import ClientSession, Tool
from mcp.types import CallToolResult
import traceback
import time
import os,sys
import threading

from agents.tool.map_request_governor import MapRequestGovernor, MapRequestLimitExceeded


def _verbose_tool_logging_enabled() -> bool:
    return os.getenv("SAGE_VERBOSE_TOOL_LOGS", "0").strip().lower() in {"1", "true", "yes", "on"}

class ToolManager:
    def __init__(
        self,
        is_auto_discover=True,
        map_request_governor=None,
        baidu_request_dispatcher=None,
        provider_gateway=None,
    ):
        """初始化工具管理器"""
        logger.info("Initializing ToolManager")
        
        # 工具执行统计
        self.execution_stats = {
            'total_executions': 0,
            'successful_executions': 0,
            'failed_executions': 0,
            'tools_called': {},
            'error_types': {}
        }
        
        self.tools: Dict[str, Union[ToolSpec, McpToolSpec, AgentToolSpec]] = {}
        self._mcp_sessions: Dict[str, Dict[str, Union[ClientSession]]] = {}  # {session_id: {server_name: session}}
        # stdio MCP 进程必须与它的 ClientSession 运行在同一事件循环中。这个循环按需
        # 创建并在应用关闭时回收，使 Fetch 等 stdio MCP 在整个后端生命周期内保持连接。
        self._mcp_runtime_loop = None
        self._mcp_runtime_thread = None
        self._mcp_runtime_lock = threading.Lock()
        self._persistent_stdio_servers: Dict[str, Dict[str, Any]] = {}
        self.map_request_governor = map_request_governor or MapRequestGovernor(
            dispatcher=baidu_request_dispatcher,
        )
        self.baidu_request_dispatcher = getattr(
            self.map_request_governor,
            "dispatcher",
            baidu_request_dispatcher,
        )
        self.provider_gateway = provider_gateway
        self.baidu_network_timeout_seconds = max(
            35.0,
            float(os.getenv("BAIDU_MAP_NETWORK_TIMEOUT_SECONDS", "35")),
        )
        
        if is_auto_discover:
            self._auto_discover_tools()
            self._mcp_setting_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'mcp_servers', 'mcp_setting.json')
            # 在测试环境中，我们不希望自动发现MCP工具
            if not os.environ.get('TESTING'):
                logger.debug("Not in testing environment, discovering MCP tools")
                asyncio.run(self._discover_mcp_tools(mcp_setting_path=self._mcp_setting_path))
            else:
                logger.debug("In testing environment, skipping MCP tool discovery")

    def discover_tools_from_path(self, path: str):
        """Discover and register tools from a custom path
        
        Args:
            path: Path to scan for tools
        """
        return self._auto_discover_tools(path=path)
    async def initialize(self):
        """异步初始化，用于测试环境"""
        logger.info("Asynchronously initializing ToolManager")
        await self._discover_mcp_tools(mcp_setting_path=self._mcp_setting_path)
    async def cleanup_session(self, session_id: str):
        """Clean up all sessions for a given session_id"""
        logger.info(f"Cleaning up sessions for session_id: {session_id}")
        if session_id in self._mcp_sessions:
            for server_name, session in self._mcp_sessions[session_id].items():
                try:
                    logger.debug(f"Closing session for server: {server_name}")
                    await session.close()
                except Exception as e:
                    logger.error(f"Error closing session for server {server_name}: {e}")
            del self._mcp_sessions[session_id]
            logger.info(f"Successfully cleaned up sessions for session_id: {session_id}")
        else:
            logger.debug(f"No sessions found for session_id: {session_id}")

    async def register_mcp_server(self, server_name: str, config: dict):
        """Register an MCP server directly with configuration
        
        Args:
            server_name: Name of the server
            config: Dictionary containing server configuration:
                - For stdio server:
                    - command: Command to start server
                    - args: List of arguments (optional)
                    - env: Environment variables (optional)
                - For SSE server:
                    - sse_url: SSE server URL
        """
        logger.info(f"Registering MCP server: {server_name}")
        if config.get('disabled', False):
            logger.debug(f"Server {server_name} is disabled, skipping")
            return False

        if 'sse_url' in config:
            logger.debug(f"Registering SSE server {server_name} with URL: {config['sse_url']}")
            server_params = SseServerParameters(url=config['sse_url'])
            await self._register_mcp_tools_sse(server_name, server_params)
        else:
            logger.debug(f"Registering stdio server {server_name} with command: {config['command']}")
            server_params = StdioServerParameters(
                command=config['command'],
                args=config.get('args', []),
                env=config.get('env', None)
            )
            await self._register_mcp_tools_stdio(server_name, server_params)
        logger.info(f"Successfully registered MCP server: {server_name}")
        return True

    def _get_mcp_runtime_loop(self):
        """Return the dedicated event loop used by persistent stdio MCP sessions."""
        with self._mcp_runtime_lock:
            if self._mcp_runtime_loop and self._mcp_runtime_loop.is_running():
                return self._mcp_runtime_loop

            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_mcp_runtime_loop,
                args=(loop,),
                name="persistent-mcp-runtime",
                daemon=True,
            )
            thread.start()
            self._mcp_runtime_loop = loop
            self._mcp_runtime_thread = thread
            return loop

    @staticmethod
    def _run_mcp_runtime_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()
        loop.close()

    async def _run_on_mcp_runtime_loop(self, coroutine):
        loop = self._get_mcp_runtime_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return await asyncio.wrap_future(future)

    async def _connect_persistent_stdio_server(
        self,
        server_name: str,
        server_params: StdioServerParameters,
    ) -> List[Dict[str, Any]]:
        """Start a stdio MCP server and keep its transport/session open."""
        await self._close_persistent_stdio_server(server_name)
        ready = asyncio.get_running_loop().create_future()
        shutdown_event = asyncio.Event()
        connection: Dict[str, Any] = {
            "shutdown_event": shutdown_event,
            "lock": asyncio.Lock(),
        }
        task = asyncio.create_task(
            self._persistent_stdio_server_task(server_name, server_params, ready, shutdown_event, connection)
        )
        tools = await ready
        connection["task"] = task
        self._persistent_stdio_servers[server_name] = connection
        return tools

    async def _persistent_stdio_server_task(
        self,
        server_name: str,
        server_params: StdioServerParameters,
        ready: asyncio.Future,
        shutdown_event: asyncio.Event,
        connection: Dict[str, Any],
    ) -> None:
        """Own a stdio MCP context for its complete lifetime.

        AnyIO requires a context manager to be closed by the task that entered it,
        so this task remains alive until FastAPI shuts down.
        """
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    tools = [tool.model_dump() if isinstance(tool, Tool) else tool for tool in response.tools]
                    connection["session"] = session
                    ready.set_result(tools)
                    await shutdown_event.wait()
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            else:
                logger.error(f"Persistent stdio MCP server stopped unexpectedly: {server_name}: {error}")

    async def _close_persistent_stdio_server(self, server_name: str) -> None:
        connection = self._persistent_stdio_servers.pop(server_name, None)
        if not connection:
            return
        connection["shutdown_event"].set()
        await connection["task"]

    async def _call_persistent_stdio_tool(self, server_name: str, tool_name: str, kwargs: Dict[str, Any]) -> Any:
        connection = self._persistent_stdio_servers.get(server_name)
        if connection is None:
            raise RuntimeError(f"Persistent stdio MCP server is not connected: {server_name}")
        async with connection["lock"]:
            result = await connection["session"].call_tool(tool_name, kwargs)
            return result.model_dump()

    async def _close_persistent_stdio_servers(self) -> None:
        for server_name in list(self._persistent_stdio_servers):
            await self._close_persistent_stdio_server(server_name)

    async def close_mcp_connections(self) -> None:
        """Close persistent stdio MCP sessions and their dedicated event loop."""
        loop = self._mcp_runtime_loop
        thread = self._mcp_runtime_thread
        if loop is None:
            return

        try:
            future = asyncio.run_coroutine_threadsafe(self._close_persistent_stdio_servers(), loop)
            await asyncio.wrap_future(future)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread and thread.is_alive():
                await asyncio.to_thread(thread.join, 5)
            self._mcp_runtime_loop = None
            self._mcp_runtime_thread = None

    def _auto_discover_tools(self, path: str = None):
        """Auto-discover and register all tools in the tools package
        
        Args:
            path: Optional custom path to scan for tools. If None, uses package directory.
        """
        logger.info("Auto-discovering tools")
        package_path = Path(path) if path else Path(__file__).parent
        sys_package_path = package_path.parent
        package_name = package_path.name
        logger.info(f"Auto-discovery package name: {package_name}")
        logger.info(f"Scanning path: {package_path}")
        # 需要将package_path 加入sys.path
        if str(sys_package_path) not in sys.path:
            sys.path.append(str(sys_package_path))
            logger.info(f"Added path to sys.path: {sys_package_path}")
        for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
            if module_name == 'tool_base' or module_name.endswith('_base'):
                logger.debug(f"Skipping base module: {module_name}")
                continue
            try:
                logger.info(f"Attempting to import module: {module_name}")
                module = importlib.import_module(f'.{module_name}',package_name)
                for _, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, ToolBase) and obj is not ToolBase:
                        logger.info(f"Found tool class: {obj.__name__}")
                        self.register_tool_class(obj)
            except ImportError as e:
                logger.debug(traceback.format_exc())
                logger.error(f"Error importing module {module_name}: {e}")
                continue
        logger.info(f"Auto-discovery completed with {len(self.tools)} total tools")
        # 将package_path 从sys.path 中移除
        if str(sys_package_path) in sys.path:
            sys.path.remove(str(sys_package_path))
            logger.info(f"Removed package path from sys.path: {sys_package_path}")
    def register_tool_class(self, tool_class: Type[ToolBase]):
        """Register all tools from a ToolBase subclass"""
        logger.info(f"Registering tools from class: {tool_class.__name__}")
        tool_instance = tool_class()
        instance_tools = tool_instance.tools
        
        if not instance_tools:
            logger.warning(f"No tools found in {tool_class.__name__}")
            return False
        
        if _verbose_tool_logging_enabled():
            print(f"\nRegistering tools to manager from {tool_class.__name__}:")
        registered = False
        for tool_name, tool_spec in instance_tools.items():
            if self.register_tool(tool_spec):
                registered = True
        logger.info(f"Completed registering tools from {tool_class.__name__}, success: {registered}")
        return registered

    def register_tool(self, tool_spec: Union[ToolSpec, McpToolSpec, AgentToolSpec]):
        """Register a tool specification"""
        logger.debug(f"Registering tool: {tool_spec.name}")
        if tool_spec.name in self.tools:
            logger.warning(f"Tool already registered: {tool_spec.name}")
            if _verbose_tool_logging_enabled():
                print(f"Tool already registered: {tool_spec.name}")
            return False
        
        self.tools[tool_spec.name] = tool_spec
        logger.info(f"Successfully registered tool: {tool_spec.name}")
        if _verbose_tool_logging_enabled():
            print(f"Registered tool to manager: {tool_spec.name}")
        return True

    async def _discover_mcp_tools(self,mcp_setting_path: str = None):
        """Discover and register tools from MCP servers"""
        logger.info(f"Discovering MCP tools from settings file: {mcp_setting_path}")
        if os.path.exists(mcp_setting_path)==False:
            logger.warning(f"MCP setting file not found: {mcp_setting_path}")
            return
        try:
            with open(mcp_setting_path, encoding='utf-8') as f:
                mcp_config = json.load(f)
                logger.debug(f"Loaded MCP config with {len(mcp_config.get('mcpServers', {}))} servers")
            
            for server_name, config in mcp_config.get('mcpServers', {}).items():
                logger.debug(f"Processing MCP server config for {server_name}")
                if config.get('disabled', False):
                    logger.debug(f"Skipping disabled MCP server: {server_name}")
                    continue
                
                if 'sse_url' in config:
                    logger.debug(f"Setting up SSE server: {server_name} at URL: {config['sse_url']}")
                    server_params = SseServerParameters(url=config['sse_url'])
                    await self._register_mcp_tools_sse(server_name, server_params)
                else:
                    logger.debug(f"Setting up stdio server: {server_name} with command: {config['command']}")
                    server_params = StdioServerParameters(
                        command=config['command'],
                        args=config.get('args', []),
                        env=config.get('env', None)
                    )
                    await self._register_mcp_tools_stdio(server_name, server_params)
        except Exception as e:
            logger.error(f"Error loading MCP config: {str(e)}")

    async def _register_mcp_tools_stdio(self, server_name: str, server_params: StdioServerParameters):
        """Register tools from a stdio MCP server and preserve its connection."""
        logger.info(f"Registering tools from stdio MCP server: {server_name}")
        try:
            start_time = time.time()
            tools = await self._run_on_mcp_runtime_loop(
                self._connect_persistent_stdio_server(server_name, server_params)
            )
            elapsed = time.time() - start_time
            logger.debug(f"Initialized persistent stdio MCP server {server_name} in {elapsed:.2f} seconds")
            logger.info(f"Received {len(tools)} tools from stdio MCP server {server_name}")
            for tool in tools:
                await self._register_mcp_tool(server_name, tool, server_params)
        except Exception as e:
            logger.error(f"Failed to connect to stdio MCP server {server_name}: {str(e)}")
            logger.error(traceback.format_exc())

    async def _register_mcp_tools_sse(self, server_name: str, server_params: SseServerParameters):
        """Register tools from SSE MCP server"""
        logger.info(f"Registering tools from SSE MCP server: {server_name} at {server_params.url}")
        try:
            async with sse_client(server_params.url) as (read, write):
                async with ClientSession(read, write) as session:
                    logger.debug(f"Initializing session for SSE MCP server {server_name}")
                    start_time = time.time()
                    await session.initialize()
                    elapsed = time.time() - start_time
                    logger.debug(f"Session initialized in {elapsed:.2f} seconds")
                    response = await session.list_tools()
                    tools = response.tools
                    logger.info(f"Received {len(tools)} tools from SSE MCP server {server_name}")
                    for tool in tools:
                        await self._register_mcp_tool(server_name, tool, server_params)
        except Exception as e:
            logger.error(f"Failed to connect to SSE MCP server {server_name}: {str(e)}")

    async def _register_mcp_tool(self, server_name: str, tool_info:Union[Tool, dict], 
                               server_params: Union[StdioServerParameters, SseServerParameters]):
        
        if isinstance(tool_info, Tool):
            tool_info = tool_info.model_dump()
        if not isinstance(tool_info, dict):
            logger.warning(f"Invalid tool info type: {type(tool_info)}")
            return
        logger.debug(f"Registering MCP tool: {tool_info['name']} from server: {server_name}")
        """Register a tool from MCP server"""
        if 'input_schema' in tool_info:
            input_schema = tool_info.get('input_schema', {})
        else:
            input_schema = tool_info.get('inputSchema', {})
        tool_spec = McpToolSpec(
            name=tool_info['name'],
            description=tool_info.get('description', ''),
            func=None,
            parameters=input_schema.get('properties', {}),
            required=input_schema.get('required', []),
            server_name=server_name,
            server_params=server_params
        )
        registered = self.register_tool(tool_spec)
        logger.debug(f"MCP tool {tool_info['name']} registration result: {registered}")
    
    def register_tools_from_directory(self, dir_path: str):
        """Register all tools from a directory containing tool modules"""
        logger.info(f"Registering tools from directory: {dir_path}")
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            logger.warning(f"Directory not found: {dir_path}")
            if _verbose_tool_logging_enabled():
                print(f"Directory not found: {dir_path}")
            return False
            
        if _verbose_tool_logging_enabled():
            print(f"\nScanning directory for tools: {dir_path}")
        tool_count = 0
            
        for py_file in dir_path.glob('*.py'):
            if py_file.stem == '__init__' or py_file.stem.endswith('_base'):
                logger.debug(f"Skipping file: {py_file.name}")
                continue
                
            module_name = py_file.stem
            logger.debug(f"Found tool module: {module_name}")
            if _verbose_tool_logging_enabled():
                print(f"\nFound tool module: {module_name}")
            
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, ToolBase) and obj is not ToolBase:
                        logger.debug(f"Registering tool class: {name}")
                        if _verbose_tool_logging_enabled():
                            print(f"  Registering tool class: {name}")
                        if self.register_tool_class(obj):
                            tool_count = len(self.tools)
            except Exception as e:
                logger.error(f"Error loading tool from {py_file}: {str(e)}")
                if _verbose_tool_logging_enabled():
                    print(f"Error loading tool from {py_file}: {e}")
                continue
                
        logger.info(f"Successfully registered {tool_count} tools from directory")
        if _verbose_tool_logging_enabled():
            print(f"\nSuccessfully registered {tool_count} tools from directory")
        return tool_count > 0

    def get_tool(self, name: str) -> Optional[Union[ToolSpec, McpToolSpec]]:
        """Get a tool by name"""
        logger.debug(f"Getting tool by name: {name}")
        return self.tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all available tools with metadata"""
        logger.debug(f"Listing all {len(self.tools)} tools with metadata")
        return [{
            'name': tool.name,
            'description': tool.description,
            'parameters': tool.parameters,
            'required': tool.required
        } for tool in self.tools.values()]

    def list_tools_simplified(self) -> List[Dict[str, Any]]:
        """List all available tools with simplified metadata"""
        logger.debug(f"Listing all {len(self.tools)} tools with simplified metadata")
        return [{
            'name': tool.name,
            'description': tool.description
        } for tool in self.tools.values()]

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Get tool specifications in OpenAI-compatible format"""
        logger.debug(f"Getting OpenAI tool specifications for {len(self.tools)} tools")
        return [{
            'type': 'function',
            'function': {
                'name': tool.name,
                'description': tool.description,
                'parameters': {
                    'type': 'object',
                    'properties': tool.parameters,
                    'required': tool.required
                }
            }
        } for tool in self.tools.values()]

    def run_tool(self, tool_name: str, messages: list, session_id: str, **kwargs) -> Any:
        """Execute a tool by name with provided arguments"""
        execution_start = time.time()
        logger.info(f"Executing tool: {tool_name} (session: {session_id})")
        
        # Remove duplicate session_id from kwargs if present
        session_id = kwargs.pop('session_id', session_id)
        baidu_request_priority = kwargs.pop('_baidu_priority', None)
        
        # Step 1: Tool Lookup
        tool = self.get_tool(tool_name)
        if not tool:
            error_msg = f"Tool '{tool_name}' not found. Available: {list(self.tools.keys())}"
            logger.error(error_msg)
            self._log_execution(tool_name, False, "TOOL_NOT_FOUND")
            return self._format_error_response(error_msg, tool_name, "TOOL_NOT_FOUND")
        
        logger.debug(f"Found tool: {tool_name} (type: {type(tool).__name__})")
        
        try:
            # Step 2: Execute based on tool type
            if isinstance(tool, McpToolSpec):
                # For MCP tools, we need to handle async execution properly
                try:
                    def execute_mcp_call():
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None

                        if loop is not None:
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(asyncio.run, self._run_mcp_tool_async(tool, session_id, **kwargs))
                                result = future.result()
                        else:
                            result = asyncio.run(self._run_mcp_tool_async(tool, session_id, **kwargs))
                        return self._format_mcp_result(result)

                    if str(getattr(tool, "server_name", "")).lower() == "baidu-map":
                        try:
                            final_result = self.map_request_governor.execute(
                                tool_name=tool_name,
                                session_id=session_id,
                                kwargs=kwargs,
                                callback=execute_mcp_call,
                                priority=baidu_request_priority,
                            )
                        except MapRequestLimitExceeded:
                            return self._format_error_response(
                                "本次规划的地图实时查询次数已达上限，请使用已核验结果或稍后重试",
                                tool_name,
                                "MAP_REQUEST_LIMIT",
                            )
                    elif self.provider_gateway is not None:
                        server_name = str(getattr(tool, "server_name", "") or "mcp")
                        normalized = f"{server_name} {tool_name}".lower()
                        if any(marker in normalized for marker in ("12306", "ticket", "flight", "bus")):
                            timeout_class = "realtime_transaction"
                        elif any(marker in normalized for marker in ("search", "fetch", "xhs", "web")):
                            timeout_class = "web_research"
                        else:
                            timeout_class = "quick_tool"
                        final_result = self.provider_gateway.execute(
                            execute_mcp_call,
                            scope_id=session_id,
                            provider=server_name,
                            operation=tool_name,
                            arguments=kwargs,
                            timeout_class=timeout_class,
                        )
                    else:
                        final_result = execute_mcp_call()
                except RuntimeError as re:
                    if "cannot be called from a running event loop" in str(re):
                        # Fallback: create new event loop in thread
                        import concurrent.futures
                        import threading
                        def run_in_new_loop():
                            new_loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(new_loop)
                            try:
                                return new_loop.run_until_complete(self._run_mcp_tool_async(tool, session_id, **kwargs))
                            finally:
                                new_loop.close()
                        
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(run_in_new_loop)
                            result = future.result()
                        final_result = self._format_mcp_result(result)
                    else:
                        raise
            elif isinstance(tool, ToolSpec):
                final_result = self._execute_standard_tool(tool, **kwargs)
            elif isinstance(tool, AgentToolSpec):
                final_result = self._execute_agent_tool(tool, messages, session_id)
            else:
                error_msg = f"Unknown tool type: {type(tool).__name__}"
                logger.error(error_msg)
                self._log_execution(tool_name, False, "UNKNOWN_TOOL_TYPE")
                return self._format_error_response(error_msg, tool_name, "UNKNOWN_TOOL_TYPE")
            
            # Step 3: Validate Result
            execution_time = time.time() - execution_start
            logger.info(f"Tool '{tool_name}' completed successfully in {execution_time:.2f}s")
            
            # Validate JSON format
            is_valid, validation_msg = self._validate_json_response(final_result, tool_name)
            if not is_valid:
                logger.error(f"Tool '{tool_name}' returned invalid JSON: {validation_msg}")
                self._log_execution(tool_name, False, "INVALID_JSON")
                return self._format_error_response(f"Invalid JSON response: {validation_msg}", 
                                                 tool_name, "INVALID_JSON")
            
            self._log_execution(tool_name, True, execution_time=execution_time)
            return final_result
            
        except Exception as e:
            execution_time = time.time() - execution_start
            error_msg = f"Tool '{tool_name}' failed after {execution_time:.2f}s: {str(e)}"
            logger.error(error_msg)
            logger.error(f"Exception details: {type(e).__name__}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            
            self._log_execution(tool_name, False, "EXECUTION_ERROR")
            return self._format_error_response(error_msg, tool_name, "EXECUTION_ERROR", str(e))

    def begin_map_request_scope(self, session_id: str, scope_id: str = None) -> str:
        """Start a fresh per-planning request budget while retaining the shared result cache."""
        return self.map_request_governor.begin_scope(session_id, scope_id)

    def get_map_request_metrics(self) -> Dict[str, float | int]:
        """Return a point-in-time view of Baidu queue and provider execution metrics."""
        return self.map_request_governor.metrics_snapshot()

    def get_provider_metrics(self) -> Dict[str, int]:
        return self.provider_gateway.metrics_snapshot() if self.provider_gateway is not None else {}

    def _format_mcp_result(self, result) -> str:
        """Format MCP tool result to JSON string"""
        try:
            # Process MCP result
            if isinstance(result, dict) and result.get('content'):
                content = result['content']
                if isinstance(content, list) and len(content) > 0:
                    # Handle list content (e.g., from text/plain results)
                    formatted_content = '\n'.join([item.get('text', str(item)) for item in content])
                else:
                    formatted_content = str(content)
                
                # 清理错误的下载链接
                formatted_content = self._clean_download_links(formatted_content)
                return json.dumps({"content": formatted_content}, ensure_ascii=False, indent=2)
            else:
                # 也需要清理其他格式的结果
                result_str = json.dumps(result, ensure_ascii=False, indent=2)
                cleaned_result_str = self._clean_download_links(result_str)
                # 重新解析和返回
                if cleaned_result_str != result_str:
                    try:
                        cleaned_result = json.loads(cleaned_result_str)
                        return json.dumps(cleaned_result, ensure_ascii=False, indent=2)
                    except:
                        pass
                return result_str
        except Exception as e:
            logger.error(f"MCP result formatting failed: {str(e)}")
            return json.dumps({"error": f"Result formatting failed: {str(e)}"}, ensure_ascii=False, indent=2)
    
    def _clean_download_links(self, content: str) -> str:
        """清理内容中的错误下载链接"""
        import re
        
        # 移除所有包含localhost:8081的链接
        content = re.sub(r'http://localhost:8081[^\s\n\]]*', '', content)
        
        # 移除包含完整文件路径的错误链接格式
        content = re.sub(r'http://localhost:8081/home/[^\s\n\]]*', '', content)
        
        # 清理可能留下的空链接标记
        content = re.sub(r'\[\]\([^\)]*\)', '', content)
        content = re.sub(r'\[([^\]]*)\]\(\s*\)', r'\1', content)
        
        # 清理多余的空行
        content = re.sub(r'\n\n\n+', '\n\n', content)
        
        return content.strip()

    def _execute_standard_tool(self, tool: ToolSpec, **kwargs) -> str:
        """Execute standard tool and format result"""
        logger.debug(f"Executing standard tool: {tool.name}")
        
        try:
            # Execute the tool function
            if hasattr(tool.func, '__self__'):
                # Bound method
                result = tool.func(**kwargs)
            else:
                # Unbound method - need to create instance
                tool_class = getattr(tool.func, '__objclass__', None)
                if tool_class:
                    instance = tool_class()
                    result = tool.func.__get__(instance)(**kwargs)
                else:
                    result = tool.func(**kwargs)
            
            # Format result
            if isinstance(result, (dict, list)):
                content = json.dumps(result, ensure_ascii=False, indent=2)
                return json.dumps({"content": content}, ensure_ascii=False, indent=2)
            else:
                return json.dumps({"content": str(result)}, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"Standard tool execution failed: {tool.name} - {str(e)}")
            raise

    def _execute_agent_tool(self, tool: AgentToolSpec, messages: list, session_id: str) -> str:
        """Execute agent tool and format result"""
        logger.debug(f"Executing agent tool: {tool.name}")
        
        try:
            result = tool.func(messages=messages, session_id=session_id)
            return json.dumps({"messages": result}, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Agent tool execution failed: {tool.name} - {str(e)}")
            raise

    def _format_error_response(self, error_msg: str, tool_name: str, error_type: str, 
                              exception_detail: str = None) -> str:
        """Format a consistent error response"""
        error_response = {
            "error": True,
            "error_type": error_type,
            "message": error_msg,
            "tool_name": tool_name,
            "timestamp": time.time()
        }
        
        if exception_detail:
            error_response["exception_detail"] = exception_detail
            
        return json.dumps(error_response, ensure_ascii=False, indent=2)

    async def _run_mcp_tool_async(self, tool: McpToolSpec, session_id: str = None, **kwargs) -> Any:
        """Run an MCP tool asynchronously"""
        if not session_id:
            session_id = "default"
        
        if session_id not in self._mcp_sessions:
            self._mcp_sessions[session_id] = {}
        
        server_name = tool.server_name
        logger.debug(f"MCP tool execution: {tool.name} on {server_name}")
        
        try:
            if isinstance(tool.server_params, SseServerParameters):
                execution = self._execute_sse_mcp_tool(tool, **kwargs)
            else:
                execution = self._execute_stdio_mcp_tool(tool, **kwargs)
            if str(server_name).lower() == "baidu-map":
                return await asyncio.wait_for(
                    execution,
                    timeout=self.baidu_network_timeout_seconds,
                )
            return await execution
        except Exception as e:
            logger.error(f"MCP tool '{tool.name}' failed on server '{server_name}': {str(e)}")
            logger.debug(f"MCP error details - Tool: {tool.name}, Server: {server_name}, Args: {kwargs}")
            raise

    async def _execute_sse_mcp_tool(self, tool: McpToolSpec, **kwargs) -> Any:
        """Execute SSE MCP tool"""
        async with sse_client(tool.server_params.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool.name, kwargs)
                return result.model_dump()

    async def _execute_stdio_mcp_tool(self, tool: McpToolSpec, **kwargs) -> Any:
        """Execute through the stdio session kept alive since application startup."""
        server_name = tool.server_name
        if server_name not in self._persistent_stdio_servers:
            await self._run_on_mcp_runtime_loop(
                self._connect_persistent_stdio_server(server_name, tool.server_params)
            )
        return await self._run_on_mcp_runtime_loop(
            self._call_persistent_stdio_tool(server_name, tool.name, kwargs)
        )

    def _validate_json_response(self, response_text: str, tool_name: str) -> tuple[bool, str]:
        """Validate if response is proper JSON and return validation result"""
        if not response_text:
            return False, "Empty response"
        
        try:
            parsed = json.loads(response_text)
            
            # Check for common issues
            if isinstance(parsed, str) and len(parsed) > 10000:
                logger.warning(f"Tool '{tool_name}' returned very large response ({len(parsed)} chars)")
                
            return True, "Valid JSON"
            
        except json.JSONDecodeError as e:
            error_pos = getattr(e, 'pos', 'unknown')
            if hasattr(e, 'pos') and e.pos < len(response_text):
                start = max(0, e.pos - 50)
                end = min(len(response_text), e.pos + 50)
                context = response_text[start:end]
                logger.error(f"JSON parse error at position {error_pos}: {context}")
            
            return False, f"JSON decode error at position {error_pos}: {e}"
        except Exception as e:
            logger.error(f"Unexpected JSON validation error for '{tool_name}': {e}")
            return False, f"Validation error: {e}"

    def get_execution_stats(self) -> dict:
        """获取工具执行统计信息"""
        total = max(1, self.execution_stats['total_executions'])
        return {
            **self.execution_stats,
            'success_rate': (self.execution_stats['successful_executions'] / total) * 100,
            'total_tools_registered': len(self.tools)
        }

    def _log_execution(self, tool_name: str, success: bool, error_type: str = None, execution_time: float = None):
        """记录工具执行统计"""
        self.execution_stats['total_executions'] += 1
        
        if tool_name not in self.execution_stats['tools_called']:
            self.execution_stats['tools_called'][tool_name] = {'success': 0, 'failed': 0, 'avg_time': 0}
            
        if success:
            self.execution_stats['successful_executions'] += 1
            self.execution_stats['tools_called'][tool_name]['success'] += 1
            if execution_time:
                prev_avg = self.execution_stats['tools_called'][tool_name].get('avg_time', 0)
                count = self.execution_stats['tools_called'][tool_name]['success']
                self.execution_stats['tools_called'][tool_name]['avg_time'] = (prev_avg * (count - 1) + execution_time) / count
        else:
            self.execution_stats['failed_executions'] += 1
            self.execution_stats['tools_called'][tool_name]['failed'] += 1
            
            if error_type:
                self.execution_stats['error_types'][error_type] = self.execution_stats['error_types'].get(error_type, 0) + 1

    def print_execution_summary(self):
        """Print a summary of tool execution statistics"""
        stats = self.get_execution_stats()
        print("\n" + "="*50)
        print("🔧 TOOL EXECUTION SUMMARY")
        print("="*50)
        print(f"Total executions: {stats['total_executions']}")
        print(f"Success rate: {stats['success_rate']:.1f}%")
        print(f"Total tools registered: {stats['total_tools_registered']}")
        
        if stats['error_types']:
            print("\nError breakdown:")
            for error_type, count in stats['error_types'].items():
                print(f"  {error_type}: {count}")
        
        if stats['tools_called']:
            print("\nMost used tools:")
            sorted_tools = sorted(stats['tools_called'].items(), 
                                key=lambda x: x[1]['success'] + x[1]['failed'], reverse=True)
            for tool_name, tool_stats in sorted_tools[:5]:
                total_calls = tool_stats['success'] + tool_stats['failed']
                success_rate = (tool_stats['success'] / total_calls) * 100 if total_calls > 0 else 0
                avg_time = tool_stats.get('avg_time', 0)
                print(f"  {tool_name}: {total_calls} calls, {success_rate:.1f}% success, {avg_time:.2f}s avg")
        print("="*50)

    async def _run_mcp_tool(self, tool: McpToolSpec, session_id: str = None, **kwargs) -> CallToolResult:
        """Run an MCP tool through its server connection (legacy compatibility)"""
        return await self._run_mcp_tool_async(tool, session_id, **kwargs)
