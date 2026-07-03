# 🧠 Sage Multi-Agent Web应用

现代化的多智能体协作Web应用，采用FastAPI后端 + React前端架构，提供智能体协作、深度思考等功能。

## ✨ 功能特性

- 🤖 **多智能体协作** - 支持分解、规划、执行、观察、总结等多智能体流程
- 🧠 **深度思考模式** - 智能体任务分析和思考过程可视化
- 🚀 **FastAPI后端** - 高性能异步API服务器，支持流式响应
- ⚛️ **React前端** - 现代化响应式用户界面，豆包风格设计
- 📡 **实时通信** - WebSocket + SSE双重支持
- 🎨 **美观界面** - Ant Design组件库，支持深度思考可折叠气泡
- 🔧 **工具管理** - 自动发现和管理工具
- 🗺️ **百度地图集成** - 支持地理编码、路线规划、周边搜索等地图功能
- 🌐 **MCP服务器支持** - 模型上下文协议，支持外部工具集成
- 📱 **响应式设计** - 适配各种屏幕尺寸
- 🔧 **TypeScript支持** - 完整的类型安全

## 🏗️ 项目结构

```
fastapi_react_demo/
├── backend/                    # FastAPI后端
│   ├── main.py                # 主服务器文件
│   ├── config_example.yaml    # 配置文件模板
│   ├── config_loader.py       # 配置加载器
│   └── logs/                  # 日志目录
├── frontend/                  # React前端
│   ├── src/
│   │   ├── components/        # React组件
│   │   ├── context/           # 状态管理
│   │   ├── hooks/             # 自定义Hook
│   │   └── main.tsx           # 应用入口
│   ├── package.json           # 前端依赖
│   ├── vite.config.ts         # Vite配置
│   └── tsconfig.json          # TypeScript配置
├── start_backend.py           # 后端启动脚本
└── README.md                  # 项目说明
```

## 🚀 快速开始

### 前置要求

- Python 3.8+ 
- Node.js 18+
- npm 或 yarn

### 1. 配置文件设置

**首先复制配置模板：**
```bash
cd examples/fastapi_react_demo/backend
cp config_example.yaml config.yaml
```

**编辑 `config.yaml` 文件，填入您的配置：**
```yaml
# 模型配置 (必填)
model:
  api_key: "sk-your-api-key-here"     # 您的API密钥
  model_name: "deepseek-chat"         # 模型名称
  base_url: "https://api.deepseek.com/v1"  # API地址
  max_tokens: 4096
  temperature: 0.7

# 服务器配置
server:
  host: "0.0.0.0"
  port: 8001      # 后端端口
  reload: true
  log_level: "info"
```

> **重要：** `config.yaml` 文件包含敏感信息，不会提交到git。请从 `config_example.yaml` 复制并修改。

### 1.1 可选：使用 .env 管理敏感配置（推荐）

```bash
cd examples/fastapi_react_demo
cp .env.example .env
```

然后编辑 `.env`，至少填写以下变量：

```env
SAGE_API_KEY=YOUR_MODEL_API_KEY
SAGE_MODEL_NAME=deepseek-chat
SAGE_BASE_URL=https://api.deepseek.com/v1
SAGE_OUTPUT_ROOT=D:/vscode/project/SuperTravelAgent/outputs
```

说明：
- `start_backend.py` 会自动加载 `examples/fastapi_react_demo/.env`。
- 直接运行 `backend/main.py` 时，配置加载器也会自动读取 `.env`。
- 环境变量优先级高于配置文件同名项。

### 2. 后端启动

**方法1: 使用启动脚本（推荐）**
```bash
cd examples/fastapi_react_demo
python start_backend.py
```

说明（防止运行旧前端代码）：
- `start_backend.py` 会在启动后端前自动检查前端源码和 `backend/static` 的时间戳。
- 如果检测到前端源码更新，脚本会自动执行 `npm run build`，确保后端始终提供最新静态页面。
- 如果缺少静态产物（首次运行），也会自动触发构建。
- 可通过环境变量控制：
  - `SAGE_AUTO_BUILD_FRONTEND=0`：关闭自动检查和自动构建
  - `SAGE_FORCE_FRONTEND_BUILD=1`：每次启动后端都强制重新构建前端

**方法2: 直接启动**
```bash
cd examples/fastapi_react_demo/backend
python main.py
```

后端服务将在 `http://localhost:8001` 启动。

### 3. 前端启动

```bash
# 进入前端目录
cd examples/fastapi_react_demo/frontend

# 安装依赖（首次运行）
npm install

# 启动开发服务器
npm run dev
```

前端开发服务器将在 `http://localhost:8080` 启动。

### 4. 访问应用

打开浏览器访问：`http://localhost:8080`

如果未配置API密钥，可在**系统配置**页面进行配置。

## 🌍 公网访问（外网任何人可访问）

如果你希望不在同一局域网的人也能通过一个网址访问你的旅游项目，推荐使用 Cloudflare Tunnel。

### 方案说明

- 推荐方式：前端先构建到后端静态目录，然后只对外暴露后端一个端口（`8001`）。
- 优点：只有一个公网网址，用户直接打开就能聊天和做旅游规划。

### 步骤 1：构建前端（仅首次或前端更新后）

```bash
cd examples/fastapi_react_demo/frontend
npm install
npm run build
```

> 你也可以直接运行 `python start_backend.py`，脚本会自动判断是否需要重新构建前端。

### 步骤 2：启动后端

```bash
cd examples/fastapi_react_demo
python start_backend.py
```

### 步骤 3：安装 Cloudflare Tunnel 客户端（Windows）

```bash
winget install Cloudflare.cloudflared
```

安装完成后，重开终端。

### 步骤 4：启动公网隧道

```bash
cd examples/fastapi_react_demo
python start_public_tunnel.py --port 8001
```

脚本会输出一个 `https://xxxx.trycloudflare.com` 的公网地址。
把这个地址发给别人，外网即可访问。

### 固定网址（可选，推荐）

`trycloudflare.com` 地址每次重启都会变。如果你想长期使用同一个域名：

1. 在 Cloudflare Zero Trust 创建 Tunnel，并绑定你自己的域名（如 `travel.yourdomain.com`）。
2. 获取 token。
3. 本地启动：

```bash
set CLOUDFLARE_TUNNEL_TOKEN=你的token
python start_public_tunnel.py --token %CLOUDFLARE_TUNNEL_TOKEN%
```

这样别人始终通过固定网址访问。

## 🎯 使用指南

### 智能体对话功能

1. **深度思考模式** - 开启后显示任务分析过程
2. **多智能体协作** - 开启后展示完整的智能体协作流程
3. **实时流式响应** - 查看智能体实时思考和执行过程
4. **可折叠思考气泡** - 点击展开/收起查看详细思考过程

### 界面功能

- **新对话** - 开始新的对话会话
- **对话历史** - 自动保存对话记录（localStorage）
- **工具管理** - 查看系统可用工具
- **MCP服务器** - 管理和监控MCP服务器状态（包括百度地图）
- **系统配置** - 配置API密钥和模型参数

### 🗺️ 百度地图功能

本应用已集成百度地图MCP服务器，提供丰富的地理位置服务：

- **地理编码** - 地址转坐标："帮我查找北京市朝阳区三里屯的坐标"
- **逆地理编码** - 坐标转地址："坐标116.404,39.915是什么地方？"
- **周边搜索** - 查找附近POI："天安门附近的餐厅有哪些？"
- **路线规划** - 导航路径："从北京站到北京大学的驾车路线"
- **行政区划** - 区域查询："北京市有哪些区？"

**配置百度地图：**
1. 获取百度地图API密钥：https://lbsyun.baidu.com/
2. 在 `config.yaml` 中设置 `BAIDU_MAP_API_KEY`
3. 详细说明请参考：[README_BAIDU_MCP.md](README_BAIDU_MCP.md)

## 🔧 配置说明

### config.yaml 配置项

```yaml
# 模型配置
model:
  api_key: ""          # API密钥（必填）
  model_name: ""       # 模型名称，如：deepseek-chat, gpt-4等
  base_url: ""         # API基础URL
  max_tokens: 4096     # 最大Token数
  temperature: 0.7     # 温度参数(0.0-2.0)

# 服务器配置  
server:
  host: "0.0.0.0"     # 服务器地址
  port: 8001          # 服务器端口
  reload: true        # 开发模式热重载
  log_level: "info"   # 日志级别

# 工具配置
tools:
  auto_discover: true      # 自动发现工具
  enabled_tools: []        # 启用的工具列表

# 智能体配置
agents:
  enable_deep_thinking: true   # 启用深度思考
  enable_multi_agent: true     # 启用多智能体协作
  max_agents: 7               # 最大智能体数量
```

### 端口配置

**后端端口配置：**
在 `backend/config.yaml` 中修改：
```yaml
server:
  port: 8001  # 修改为您想要的端口
```

**前端端口配置：**
在 `frontend/vite.config.ts` 中修改：
```typescript
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8080,  // 修改前端端口
    proxy: {
      '/api': 'http://localhost:8001',  // 确保指向正确的后端端口
      '/ws': {
        target: 'ws://localhost:8001',  // WebSocket代理地址
        ws: true,
      }
    }
  }
})
```

> **重要：** 如果修改了后端端口，需要同时更新前端的代理配置中的端口号，确保前后端通信正常。

**端口配置示例：**
- 后端使用 9000 端口：
  - `config.yaml`: `port: 9000`
  - `vite.config.ts`: 代理地址改为 `http://localhost:9000`
- 前端使用 3000 端口：
  - `vite.config.ts`: `port: 3000`

### API端点

- `GET /` - 主页面（如果配置了静态文件）
- `GET /api/status` - 系统状态
- `POST /api/configure` - 动态配置系统
- `GET /api/tools` - 获取工具列表
- `POST /api/chat-stream` - 流式聊天API
- `GET /docs` - FastAPI自动文档

## 🛠️ 开发说明

### 前端开发

**添加新组件：**
1. 在 `frontend/src/components/` 创建组件
2. 在 `App.tsx` 中添加路由
3. 在 `Sidebar.tsx` 中添加菜单项

**自定义样式：**
- 全局样式：`src/App.css`
- Ant Design主题：`App.tsx` 中的 `themeConfig`

**状态管理：**
- 使用React Context: `src/context/SystemContext.tsx`
- 自定义Hook: `src/hooks/`

### 后端扩展

**添加API端点：**
在 `main.py` 中添加新的路由函数

**工具集成：**
工具会自动从 `agents/tool/` 目录发现

## 🐛 故障排除

### 常见问题

1. **配置文件未找到**
   ```
   ⚠️ 未找到配置文件，使用默认配置
   ```
   - 确保已从 `config_example.yaml` 复制为 `config.yaml`

2. **API密钥未配置**
   ```
   ⚠️ 未配置API密钥，需要通过Web界面配置
   ```
   - 在 `config.yaml` 中填入API密钥，或通过Web界面配置

3. **端口被占用**
   ```
   Address already in use
   ```
   - 修改 `config.yaml` 中的端口号，或关闭占用端口的进程
   - 检查端口占用：`lsof -i :8001` (macOS/Linux) 或 `netstat -ano | findstr :8001` (Windows)
   - 如果修改后端端口，记得同步更新前端的 `vite.config.ts` 中的代理设置

4. **前端依赖安装失败**
   - 删除 `node_modules` 重新安装：`rm -rf node_modules && npm install`
   - 检查Node.js版本：`node --version`（推荐18+）

5. **WebSocket连接失败**
   - 检查后端是否正常运行
   - 确认防火墙设置
   - 查看浏览器控制台错误

### 调试技巧

- **后端日志**：查看终端输出，或 `logs/` 目录下的日志文件
- **前端调试**：打开浏览器开发者工具(F12)，查看控制台和网络请求
- **API文档**：访问 `http://localhost:8001/docs` 查看交互式API文档

## 📦 生产部署

### 构建前端

```bash
cd frontend
npm run build
```

构建文件将输出到 `backend/static/` 目录。

### 生产配置

修改 `config.yaml`:
```yaml
server:
  reload: false      # 关闭热重载
  log_level: "warning"  # 降低日志级别
```

### Docker部署（可选）

```dockerfile
# Dockerfile示例
FROM node:18 AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.9
WORKDIR /app
COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist ./backend/static/
COPY config_example.yaml ./backend/config.yaml
RUN pip install fastapi uvicorn
EXPOSE 8001
CMD ["python", "backend/main.py"]
```

## 🔒 安全注意事项

- **API密钥安全**：生产环境使用环境变量或密钥管理服务
- **CORS配置**：生产环境限制允许的域名
- **HTTPS**：生产环境使用HTTPS协议
- **输入验证**：所有用户输入都应进行验证和清理

## 📈 性能优化

- **前端优化**：组件懒加载、虚拟滚动、React.memo
- **后端优化**：异步处理、连接池、缓存策略
- **网络优化**：gzip压缩、CDN加速

## 📄 许可证

本项目采用 MIT 许可证。详见项目根目录 LICENSE 文件。

## 🆘 获取帮助

如遇问题，请：
1. 查看本README的故障排除部分
2. 检查项目Issues
3. 查看Sage框架主文档 