import os

docs_dir = 'docs'

doc_map = {
    'PROJECT_ARCHITECTURE_AND_IMPLEMENTATION_CN.md': '''---
layout: default
title: 项目架构设计与实现全景图
nav_exclude: true
---

# SuperTravelAgent 项目架构设计与技术实现全景图

## 1. 核心定位与业务价值

SuperTravelAgent 是一个面向旅行场景的**多智能体协作及工具编排系统 (Multi-Agent System)**。
系统的初衷不仅是处理基础的问答，而是针对结构复杂、步骤繁多的旅行规划需求，实现“任务拆解、自主行动、工具调用与结果总装”的端到端闭环。

## 2. 系统核心架构设计

我主导设计了前后台完全解耦的三层架构骨架，确保了极高的可扩展性和业务代码的纯净性。

### 2.1 后端：基于 FastAPI 的领域驱动分层架构
- **路由入口层 (Thin Routers)**: 极为轻薄的接入层 (`main.py`)，仅负责请求参数的校验转发与 HTTP 边界的错误转换。
- **服务编排层 (Service Layer)**: 位于 `services/` 目录，封装流式事件 (`chat_service`)、工具与 MCP 发现机制 (`mcp_service`)、系统生命周期及前端所需的统一 `RuntimeState` 装配过程。
- **智能体引擎层 (Agent Core)**: 位于 `agents/`，实现了自研的多智能体协作管线 (Pipeline)，将复杂意图切分为“分析 -> 分解 -> 规划 -> 执行 -> 观察 -> 总结”的执行序列。

### 2.2 前端：基于 React与统一状态机制的视图侧响应
- 移除了传统的散落式 API 拉取，设计了全局单例的 `apiClient.ts` 以集中处理鉴权、跨域与流回放。
- 高可用流式渲染：通过原生 SSE 收发 `chat_start -> chat_chunk -> error/complete` 等块事件，保障了 LLM 思考链路状态对于用户的毫秒级可见性。

## 3. 核心功能实现难点与解决

### 3.1 跨进程模型与本地工具/MCP (Model Context Protocol) 融合
- **痛点**：传统 Agent 调用工具能力受限，或仅支持内置 Python 函数，难以动态接入三方能力。
- **我的设计**：设计了注册总线 `ToolManager`。一方面反射加载本地 `ToolBase` 实现，另一方面基于 MCP 协议通过本地 Server 动态拉取系统外部的命令和方法，最后将其合并给 LLM 的 `tools` 调用槽位，打破了智能体的执行边界。

### 3.2 深度耦合解耦与安全防线
- **环境隔离注入**：引入强契约的 `.env` 管理所有核心秘钥与文件路径挂载。引擎和接口不再信任绝对路径，使用 `os.path.join` 和白名单检查杜绝可能的配置穿越与越权读取 (`services/file_service.py`)。
- **配置热加载**：所有基于 OpenAI 的 SDK 客户端都不被初始化并驻留于全局变量，而是在请求到达或系统配置更新时，由 `system_service.py` 动态构建执行控制器。

### 3.3 测试金字塔与契约防腐层
我为所有跨层调用的缝隙构建了“契约测试(Contract Tests)”。
- 测试用例无需等待实际的 LLM 响应网络开销，直接 `patch` 内核，从而覆盖包含 SSE 响应投递断联、协议降级、非预期 500 等异常边界语义。通过这种设计，保障了持续演进期间前端交互和后端基础能力的绝对稳定性。
''',

    'INTERVIEW_IMPLEMENTATION_CN.md': '''---
layout: default
title: 项目构建与技术实现讲稿
nav_exclude: true
---

# SuperTravelAgent 项目构建与技术实现核心讲稿

## 1. 黄金三分钟介绍流 (Elevator Pitch)

我独立设计并开发了一个用于旅行规划场景的多智能体协作系统。这个项目最大的不同点在于，它不仅仅是一个 RAG 或普通对话框，而是一套真实可落地的**复杂任务执行架构**。

在架构层面，我通过 React 构建前端流式界面，并采用 FastAPI 构建后端的服务管线；
在智能体内核上，我实现了一个包含了“任务分析、规划生成、行动执行与总结”的 Multi-Agent Controller，使庞大的行程计算能被拆解给不同的模型或工具节点去执行。

其中我最满意的一个技术亮点是**对外部工具的动态编排能力**。我落地了 Model Context Protocol (MCP) 标准，让系统不仅能调用本地 Python 函数（如预算计算），还能随时无缝挂载外部服务提供的能力（如搜索、天气订阅）。同时，由于这类执行极度耗时，我还深度定制了 SSE 流通讯协议，把执行过程切分成 `chat_chunk` 并实时打通到前端，极大抹平了用户的等待焦虑。

## 2. 核心技术能力深挖 (Top 3 追问)

### Q1：智能体协作链路，你是如何从零设计的？
**技术回答**：
业务中行程规划会包含订酒店、查机票、算预算多个异构环境。传统的 ReAct 链往往因为 Prompt 过长而导致上下文注意力崩溃。
所以我设计了分块流水线 (Pipeline)：
1.  首先是“理解层” (Task Analysis Agent) 将用户自然语言拆分为子任务树。
2.  然后进入“分拣调度” (Agent Controller) 挑选对应的特定执行智能体去挂载对应工具。
3.  系统最后设计了独立的“总结收口代理” (Task Summary Agent)，把前面对 JSON 与函数的繁杂编排收录并转化为用户友好的结果流。

### Q2：为什么你要特别强调系统的分层抽象（Service层与Router拦截）？
**技术回答**：
初期系统所有的流数据组装、模型参数甚至错误抛出都在入口文件堆积。我主动设计了标准的 Layered Architecture (领域驱动分层)：
1. 提取出 `apiClient` 给整个前端做单一的错误回退与重试。
2. 后端入口全面退化为 Thin Router，只接引流量。
3. 会话状态清理、静态资源回退、CORS 注册乃至工具目录获取，都有专门封装的 Service 包装器。并设计了契约测试对包装器边界 100% 覆盖。这让整个应用从一个玩具 Demo 变成了一个能支持协同开发的准企业级工程。

### Q3：SSE 流和前后端通讯交互，你遇到了哪些问题，如何解决？
**技术回答**：
难点在于如何在长程且容易中断的请求中告诉前端“刚才发生了什么”。
我在后端设计了一种受控的事件类型下推器，不是单纯返回字符串，而是使用结构化的 `{"type": "chat_chunk", "content": "..."}`。
一旦工具引擎报错，后端捕获异常后会向流中压入 `{"type": "error", "message": "调用失败"}`，而前端的 Reader 会监听到此类型并做对应的红色 UI 警示，保证了局部异常不会导致整页白屏崩溃，大幅提升了系统的容错鲁棒性。
''',

    'MODULE_WALKTHROUGH_CN.md': '''---
layout: default
title: 核心模块与架构设计走读
nav_exclude: true
---

# SuperTravelAgent 系统架构设计与模块串讲 

## 1. 前端请求生命周期基建
**目标文件**：`examples/fastapi_react_demo/frontend/src/services/apiClient.ts`
- **设计初衷**：打造前后端通讯防腐层。
- **架构价值**：系统内存在对话流、配置更新、工具轮询等多种通讯要求，全部收口在单例 Client 中。实现了对请求头、错误日志向控制台的统一输出，以及超时重试的可拔插控制。

## 2. 后端：网关级瘦路由接入
**目标文件**：`examples/fastapi_react_demo/backend/main.py`
- **设计初衷**：让主文件回归“接入”本质。
- **架构价值**：采用了严格的装饰器映射模式，利用应用生命周期 `lifespan` 挂接 `lifecycle_service` 中的资源装配。对任何业务异常、跨域设置 (OPTIONS 预检)、甚至是文件下载鉴权，路由层全部转发至具体 Service，展现了高度的高内聚与低耦合开发理念。

## 3. 动态配置与运行时状态分发
**目标文件**：`examples/fastapi_react_demo/backend/services/system_service.py`
- **设计初衷**：实现业务状态配置的热加载机制。
- **架构价值**：由于不同用户可能会切换 LLM 模型 (比如 DeepSeek/ChatGPT) 或更改 Temperature 设定，这里设计了 `RuntimeState` 容器。这个服务在检测到配置变化时，能安全地刷新内存中的 `AgentController`，同时不影响那些正在并行通讯的其余存活 Session。

## 4. MCP 与内部总线工具混编
**目标文件**：`examples/fastapi_react_demo/backend/services/tool_runtime_service.py` & `mcp_service.py`
- **设计初衷**：连接内部函数世界与外部大语言模型。
- **架构价值**：这里的逻辑像是一个插件池，它在初始化时扫描本地业务方法，同时从配置加载 `mcp_servers` 的注册。最终组装出的规范化 JSON Schema 结果直接喂给 LLM，这代表了我对“工具调用”范式的底层组装和调度能力的直接掌握。

## 5. 多智能体引擎核心调度器
**目标文件**：`agents/agent/agent_controller.py` 与及其基类实现
- **设计初衷**：大脑指挥官，控制 LLM 思考过程。
- **架构价值**：这不是一个普通的单文件脚本，它定义了一套多智能体执行协议（含环境与上下文透传）。系统通过此类把外部注入的工具 (`tool_manager`) 与用户状态绑定在一起，完成从输入拆解到输出合成的高阶语义调度。它向面试官证明了我不仅能调包，还能设计 AI 领域的复杂执行层。
''',

    'INTERVIEW_MOCK_QA_CN.md': '''---
layout: default
title: 架构设计与底层实现面经模拟
nav_exclude: true
---

# SuperTravelAgent 架构设计与底层实现 问答自测脚本

## 1. 核心架构设计

**Q1: 在设计之初，你是如何规划这个项目的前后端边界的？**
- 答法建议：前端只负责 UI 的渲染映射和用户的输入捕获事件 (`React` + 组件封装)；请求调度全部收缩在 `apiClient.ts`。后端我不将其视为单纯的 Restful API 集合，而是作为一个微型 Orchestrator。FastAPI 提供 HTTP 屏障，底层隐藏一套复杂的智能体工作流。这样哪怕未来需要接入终端，后部的 `services` 和 `agents` 模块不需要改写一行代码。

## 2. 工具集成能力 (Function Calling)

**Q2: 如何让你的 Multi-Agent 能够执行外部操作，甚至操作本地机器或者第三方服务？**
- 答法建议：我开发并接入了兼容 Model Context Protocol (MCP) 的网关，同时在本地维护了继承了 `ToolBase` 基类的 Python 本地沙盒脚本。执行发生时，后端相当于路由，把大模型推理出的 `call_function("search_weather", args)` 拦截下来，反射去对应的 MCP 服务器拿取真实数据并送还给模型，整个过程依靠我自己编写的 `Task Manager` 循环驱动。

## 3. 流式通讯与全链路异常治理

**Q3: 相比普通的等待 HTTP 响应，你们的架构怎么实现大语言模型逐字显示的体验？如果有错误怎么捕获？**
- 答法建议：采用了 Server-Sent Events (SSE) 通道。前端通过专门的 Reader 分片读取数据流。如果在大模型产生字词的半途中，底层工具突然崩溃（譬如网络超时），我在 `services/chat_service.py` 的生成器中使用 `try...except` 吞掉基础设施崩溃，主动向客户端下发一个 `{"type": "error", "content": "底层服务已断开"}` 的 JSON 块；前端接到后可以静默显示错误提示而不需要强行打断用户的其它上下文会话页面。

## 4. 健壮性与架构防腐

**Q4: 在复杂的开发进程中，你是怎么保证系统的长期可维护性的？**
- 答法建议：系统大量采用了门面模式和依赖反转。我的主文件（`main.py`）里不写任何的业务 `if...else`，全是声明式委托。更关键的是，我在核心网关外接了高密度的契约测试 (`unittest`)，并且使用了基于 `importlib` 的按需跨环境 `Skip` 策略。这种白盒与服务黑盒测试结合，让我不用每次修改底层方法都去页面上人工点一遍，保证了架构重组的底气和工程交付级的质量。
'''
}

for name, content in doc_map.items():
    with open(os.path.join(docs_dir, name), 'w', encoding='utf-8') as f:
        f.write(content)

# Delete old playbook
playbook_path = os.path.join(docs_dir, 'PROJECT_REFACTOR_PLAYBOOK_CN.md')
if os.path.exists(playbook_path):
    try:
        os.remove(playbook_path)
    except Exception:
        pass
