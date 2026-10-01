---
layout: default
title: 架构指南
nav_order: 5
description: "理解Sage多智能体框架的架构和设计原则"
---

{: .note }
> 需要英文版本？请查看 [Architecture Guide](ARCHITECTURE.html)

## 目录
{: .no_toc .text-delta }

1. TOC
{:toc}

# 🏗️ 架构指南 (v2.0)

> 本文保留 Sage 通用框架 v2.0 的历史设计。当前 SuperTravelAgent 旅行规划产品的功能和开发入口，请参阅 [项目介绍](../README_CN.md) 与 [Web 应用指南](../examples/fastapi_react_demo/README.md)。

本文档提供了 Sage 多智能体框架增强架构、设计原则和生产就绪功能的内部工作流程的全面概述。

## 📋 目录

- [核心设计原则](#-核心设计原则)
- [系统概述](#-系统概述)
- [组件架构](#-组件架构)
- [智能体工作流程](#-智能体工作流程)
- [Token跟踪系统](#-token跟踪系统)
- [消息流程](#-消息流程)
- [工具系统](#-工具系统)
- [错误处理和恢复](#-错误处理和恢复)
- [配置系统](#-配置系统)
- [性能监控](#-性能监控)
- [扩展点](#-扩展点)

## 🎯 核心设计原则

### 1. **生产就绪**
- 企业级错误处理和恢复
- 全面监控和可观测性
- 性能优化和资源管理
- 成本跟踪和使用分析

### 2. **模块化和可维护性**
- 每个智能体都有单一、明确定义的职责
- 清晰的接口和依赖注入
- 组件和插件的热重载
- 全面的单元测试和集成测试

### 3. **可扩展性和灵活性**
- 基于插件的工具和智能体架构
- 可配置的执行流水线
- 支持多个LLM提供商和API格式
- 运行时配置更新

### 4. **可观测性和监控**
- 实时token使用跟踪和成本监控
- 结构化输出的全面日志
- 性能指标和瓶颈检测
- 流式可视化和进度跟踪

### 5. **可靠性和韧性**
- 自动恢复的优雅错误处理
- 指数退避重试机制
- 外部服务的熔断器模式
- 内存管理和资源清理

## 🌐 系统概述

```mermaid
graph TB
    subgraph "🎮 用户界面层"
        UI[网页界面<br/>📊 实时监控]
        CLI[命令行<br/>⚡ 高性能]
        API[Python API<br/>🔧 完全控制]
    end
    
    subgraph "🧠 控制层"
        AC[智能体控制器<br/>📈 增强协调]
        TT[Token跟踪器<br/>💰 成本监控]
        PM[性能监控器<br/>⏱️ 指标]
        EM[错误管理器<br/>🛡️ 恢复]
    end
    
    subgraph "🤖 智能体层 (v2.0)"
        TA[任务分析智能体<br/>🎯 上下文感知]
        PA[规划智能体<br/>🧩 依赖管理]
        EA[执行智能体<br/>🔧 工具集成]
        OA[观察智能体<br/>👁️ 进度跟踪]
        SA[总结智能体<br/>📄 结构化输出]
        DA[直接执行智能体<br/>⚡ 快速模式]
    end
    
    subgraph "🛠️ 增强工具层"
        TM[工具管理器<br/>🔍 自动发现]
        BT[内置工具<br/>📱 核心功能]
        MCP[MCP服务器<br/>🌐 外部API]
        CT[自定义工具<br/>🎨 用户定义]
        TO[工具编排器<br/>⚙️ 负载均衡]
    end
    
    subgraph "⚙️ 基础设施层"
        CFG[配置<br/>📋 热重载]
        LOG[日志<br/>📝 结构化]
        EXC[异常处理<br/>🔄 自动恢复]
        LLM[LLM提供商<br/>🤖 多API]
        CACHE[缓存层<br/>💾 性能]
    end
    
    UI --> AC
    CLI --> AC
    API --> AC
    
    AC <--> TT
    AC <--> PM
    AC <--> EM
    
    AC --> TA
    AC --> PA
    AC --> EA
    AC --> OA
    AC --> SA
    AC --> DA
    
    EA --> TM
    TM --> TO
    TO --> BT
    TO --> MCP
    TO --> CT
    
    AC --> CFG
    AC --> LOG
    AC --> EXC
    AC --> LLM
    AC --> CACHE
    
    TT -.-> TA
    TT -.-> PA
    TT -.-> EA
    TT -.-> OA
    TT -.-> SA
    
    style AC fill:#ff9999
    style TT fill:#ffcc99
    style TM fill:#99ccff
    style EM fill:#ff99cc
```

## 🔧 组件架构

### 智能体控制器（增强版v2.0）
具有企业级功能的中央编排器。

```python
class AgentController:
    """
    增强的多智能体工作流程编排器
    
    v2.0新功能:
    - 全面的token跟踪和成本监控
    - 性能指标和瓶颈检测
    - 带重试机制的高级错误恢复
    - 进度可视化的实时流式处理
    - 长时间运行任务的内存优化
    """
    
    def run(self, messages, tool_manager, **kwargs):
        """执行带监控的完整工作流程"""
        
    def run_stream(self, messages, tool_manager, **kwargs):
        """执行带实时流式处理和进度跟踪"""
        
    def get_comprehensive_token_stats(self):
        """获取详细的token使用和成本分析"""
        
    def enable_performance_monitoring(self):
        """启用详细性能跟踪"""
```

**增强功能:**
- **Token经济学**: 实时成本跟踪和预算警报
- **性能分析**: 执行时间分析和优化建议
- **内存管理**: 自动清理和资源优化
- **熔断器**: 自动故障检测和恢复
- **负载均衡**: 智能工具选择和请求分发

### 智能体层次结构（重构版v2.0）

```mermaid
classDiagram
    AgentBase <|-- TaskAnalysisAgent
    AgentBase <|-- PlanningAgent
    AgentBase <|-- ExecutorAgent
    AgentBase <|-- ObservationAgent
    AgentBase <|-- TaskSummaryAgent
    AgentBase <|-- DirectExecutorAgent
    
    class AgentBase {
        +token_stats: Dict
        +performance_metrics: Dict
        +run(messages, tool_manager)
        +run_stream(messages, tool_manager)
        +_track_token_usage(response, step_name)
        +_track_streaming_token_usage(chunks, step_name)
        +get_token_stats()
        +reset_token_stats()
        +_handle_error_generic(error, context)
    }
    
    class TaskAnalysisAgent {
        +analyze_requirements()
        +extract_objectives()
        +assess_complexity()
        +determine_execution_strategy()
    }
    
    class PlanningAgent {
        +decompose_tasks()
        +identify_dependencies()
        +create_execution_plan()
        +optimize_resource_allocation()
    }
    
    class ExecutorAgent {
        +execute_plan()
        +call_tools_with_retry()
        +handle_tool_results()
        +manage_concurrent_execution()
    }
    
    class ObservationAgent {
        +monitor_progress()
        +detect_completion()
        +identify_failures()
        +suggest_corrections()
    }
```

## 📊 Token跟踪系统

### 架构概述

```mermaid
graph LR
    subgraph "🔍 收集层"
        ST[流跟踪器]
        RT[响应跟踪器]
        UT[使用提取器]
    end
    
    subgraph "📊 处理层"
        AS[智能体聚合器]
        CS[成本计算器]
        PA[性能分析器]
    end
    
    subgraph "💾 存储层"
        TS[Token存储]
        MS[指标存储]
        ES[导出服务]
    end
    
    subgraph "📈 分析层"
        CA[成本分析]
        PA2[性能分析]
        RA[推荐引擎]
    end
    
    ST --> AS
    RT --> AS
    UT --> AS
    
    AS --> CS
    CS --> PA
    PA --> TS
    TS --> MS
    MS --> ES
    
    TS --> CA
    MS --> PA2
    CA --> RA
    PA2 --> RA
```

### Token使用流程

```python
# 带详细指标的增强token跟踪
class TokenTracker:
    def track_agent_usage(self, agent_name, usage_data):
        """按智能体跟踪token使用并计算成本"""
        
    def track_streaming_usage(self, chunks, agent_name):
        """跟踪带实时更新的流式响应"""
        
    def calculate_costs(self, model_name, usage_data):
        """基于模型定价计算成本"""
        
    def get_performance_insights(self):
        """分析性能模式和瓶颈"""
        
    def export_detailed_report(self, format='csv'):
        """导出全面使用报告"""
```

**跟踪的关键指标:**
- **输入Token**: 请求处理成本
- **输出Token**: 响应生成成本  
- **缓存Token**: 优化节省
- **推理Token**: 高级模型功能（o1等）
- **执行时间**: 性能跟踪
- **成功率**: 可靠性指标
- **每次操作成本**: 经济效率

### 工具系统架构（增强版）

```mermaid
graph TB
    subgraph "🔧 发现和注册"
        AD[自动发现<br/>📂 目录扫描]
        TR[工具注册表<br/>📋 中央目录]
        TV[工具验证<br/>✅ 模式检查]
        TH[工具健康检查<br/>🩺 状态监控]
    end
    
    subgraph "🛠️ 工具类别"
        LT[本地工具<br/>📱 内置功能]
        MT[MCP工具<br/>🌐 外部服务器]
        AT[智能体工具<br/>🤖 智能体包装器]
        CT[自定义工具<br/>🎨 用户扩展]
    end
    
    subgraph "⚡ 执行引擎"
        TE[工具执行器<br/>🔧 多线程]
        TQ[任务队列<br/>📬 负载均衡]
        CB[熔断器<br/>🛡️ 容错]
        RM[重试管理器<br/>🔄 错误恢复]
    end
    
    subgraph "📊 监控"
        PM[性能监控器<br/>⏱️ 指标]
        LB[负载均衡器<br/>⚖️ 分发]
        CH[缓存处理器<br/>💾 优化]
    end
    
    AD --> TR
    TV --> TR
    TH --> TR
    
    TR --> LT
    TR --> MT
    TR --> AT
    TR --> CT
    
    LT --> TQ
    MT --> TQ
    AT --> TQ
    CT --> TQ
    
    TQ --> TE
    TE --> CB
    TE --> RM
    
    TE --> PM
    PM --> LB
    LB --> CH
```

## 🛡️ 错误处理和恢复

### 多层错误管理

```mermaid
graph TD
    subgraph "🎯 检测层"
        ED[错误检测<br/>🔍 实时监控]
        TD[超时检测<br/>⏰ 资源管理]
        FD[故障检测<br/>💥 异常识别]
    end
    
    subgraph "🔄 恢复层"
        AR[自动重试<br/>🔁 指数退避]
        FB[回退策略<br/>🛤️ 替代路径]
        GD[优雅降级<br/>📉 功能减少]
    end
    
    subgraph "📝 日志层"
        SL[结构化日志<br/>📊 JSON格式]
        AT[警报触发<br/>🚨 通知]
        RM[恢复指标<br/>📈 成功跟踪]
    end
    
    ED --> AR
    TD --> FB
    FD --> GD
    
    AR --> SL
    FB --> AT
    GD --> RM
```

### 错误类别和策略

```python
class ErrorManager:
    """全面的错误处理和恢复系统"""
    
    ERROR_STRATEGIES = {
        'NetworkError': 'retry_with_backoff',
        'TokenLimitError': 'truncate_and_retry',
        'ToolTimeoutError': 'fallback_to_alternative',
        'ModelUnavailableError': 'switch_provider',
        'ValidationError': 'graceful_degradation'
    }
    
    def handle_error(self, error, context):
        """将错误路由到适当的恢复策略"""
        
    def retry_with_backoff(self, operation, max_attempts=3):
        """实现指数退避重试逻辑"""
        
    def circuit_breaker(self, service_name, failure_threshold=5):
        """为外部服务实现熔断器模式"""
```

## 📈 性能监控

### 实时指标收集

```mermaid
graph LR
    subgraph "📊 数据收集"
        ET[执行时间]
        MU[内存使用]
        TU[Token消耗]
        TR[工具响应时间]
    end
    
    subgraph "🔍 分析引擎"
        BA[瓶颈分析]
        PA[性能剖析]
        CA[成本分析]
        RA[资源分析]
    end
    
    subgraph "🎯 优化"
        RS[资源扩展]
        LO[负载优化]
        CC[缓存控制]
        PT[性能调优]
    end
    
    ET --> BA
    MU --> PA
    TU --> CA
    TR --> RA
    
    BA --> RS
    PA --> LO
    CA --> CC
    RA --> PT
```

### 性能分析

```python
class PerformanceMonitor:
    """高级性能监控和优化"""
    
    def collect_metrics(self):
        """收集全面的性能数据"""
        return {
            'execution_times': self._get_execution_times(),
            'memory_usage': self._get_memory_stats(),
            'token_efficiency': self._analyze_token_usage(),
            'tool_performance': self._get_tool_metrics(),
            'bottlenecks': self._identify_bottlenecks()
        }
    
    def generate_optimization_report(self):
        """生成可操作的优化建议"""
        
    def export_performance_data(self, format='json'):
        """导出详细的性能分析"""
```

## ⚙️ 增强配置系统

### 分层配置管理

```mermaid
graph TD
    subgraph "📁 配置源"
        ENV[环境变量<br/>🌍 系统级别]
        FILE[配置文件<br/>📄 YAML/JSON]
        CLI[命令行<br/>⌨️ 运行时参数]
        API[API参数<br/>🔧 程序化]
    end
    
    subgraph "🔄 处理层"
        VAL[验证引擎<br/>✅ 模式检查]
        MER[配置合并器<br/>🔀 优先级处理]
        HOT[热重载<br/>🔥 运行时更新]
    end
    
    subgraph "💾 存储和分发"
        CS[配置存储<br/>📚 集中化]
        CD[配置分发<br/>📡 组件更新]
        CB[配置备份<br/>💼 版本控制]
    end
    
    ENV --> VAL
    FILE --> VAL
    CLI --> VAL
    API --> VAL
    
    VAL --> MER
    MER --> HOT
    HOT --> CS
    
    CS --> CD
    CS --> CB
```

### 配置模式

```python
class ConfigurationManager:
    """企业级配置管理"""
    
    SCHEMA = {
        'agents': {
            'max_loop_count': {'type': 'int', 'default': 10, 'min': 1, 'max': 50},
            'tool_timeout': {'type': 'int', 'default': 30, 'min': 5, 'max': 300},
            'retry_attempts': {'type': 'int', 'default': 3, 'min': 1, 'max': 10}
        },
        'performance': {
            'enable_monitoring': {'type': 'bool', 'default': True},
            'memory_threshold': {'type': 'int', 'default': 1024, 'min': 256},
            'cache_ttl': {'type': 'int', 'default': 3600, 'min': 60}
        },
        'costs': {
            'budget_alert_threshold': {'type': 'float', 'default': 10.0, 'min': 0.1},
            'cost_tracking_enabled': {'type': 'bool', 'default': True}
        }
    }
    
    def validate_config(self, config):
        """根据模式验证配置"""
        
    def hot_reload(self, config_path):
        """不重启重新加载配置"""
```

## 🔌 扩展点

### 插件架构

```python
class PluginManager:
    """自定义功能的可扩展插件系统"""
    
    def register_agent_plugin(self, plugin_class):
        """注册自定义智能体实现"""
        
    def register_tool_plugin(self, plugin_class):
        """注册自定义工具实现"""
        
    def register_middleware(self, middleware_class):
        """注册请求/响应中间件"""
        
    def load_plugins_from_directory(self, directory):
        """自动发现和加载插件"""
```

### 自定义智能体开发

```python
class CustomAgent(AgentBase):
    """创建自定义智能体的模板"""
    
    def __init__(self, model, config):
        super().__init__(model, config, system_prefix="自定义智能体提示")
        self.agent_description = "用于特定任务的自定义智能体"
    
    def run_stream(self, messages, tool_manager, context):
        """实现自定义智能体逻辑"""
        # 您的自定义实现在这里
        yield from self._execute_streaming_with_token_tracking(
            prompt="您的自定义提示",
            step_name="custom_operation"
        )
```

## 🎯 消息流程和数据结构

### 增强消息格式

```python
# 带监控元数据的增强消息结构
MESSAGE_SCHEMA = {
    'role': str,              # 'user', 'assistant', 'tool'
    'content': str,           # 主要消息内容
    'type': str,              # 'normal', 'thinking', 'tool_call', 等
    'message_id': str,        # 唯一标识符
    'show_content': str,      # 显示友好内容
    'usage': {                # Token使用信息
        'prompt_tokens': int,
        'completion_tokens': int,
        'total_tokens': int,
        'cached_tokens': int,
        'reasoning_tokens': int
    },
    'metadata': {             # 性能和监控数据
        'execution_time': float,
        'agent_name': str,
        'step_name': str,
        'timestamp': float,
        'success': bool
    },
    'tool_calls': List,       # 工具调用数据
    'tool_call_id': str       # 工具响应链接
}
```

这种增强的架构提供了企业级可靠性、全面监控和生产就绪的性能优化，同时保持了使Sage在开发中强大的模块化和可扩展性。
