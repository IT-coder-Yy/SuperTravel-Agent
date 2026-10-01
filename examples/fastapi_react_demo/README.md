# SuperTravelAgent Web 应用

FastAPI 提供规划、SSE、历史旅程、草稿和导出 API；React 提供对话、行程工作台和地图。产品介绍与预览见 [中文 README](../../README_CN.md) / [English README](../../README.md)。

## 启动

需要 Python 3.10+、Node.js 20+ 和 npm。从仓库根目录开始：

```bash
python -m pip install -r requirements.txt -r examples/fastapi_react_demo/requirements.txt
cd examples/fastapi_react_demo/frontend
npm ci
cd ..
```

复制 `.env.example` 为 `.env`（PowerShell：`Copy-Item .env.example .env`；macOS/Linux：`cp .env.example .env`）。使用真实规划时填写：

```env
SAGE_API_KEY=your-model-api-key
SAGE_MODEL_NAME=your-model-name
SAGE_BASE_URL=https://your-provider.example/v1
SAGE_HOST=127.0.0.1
SAGE_PORT=8001
SAGE_RELOAD=false
```

`SAGE_BASE_URL` 是 OpenAI 兼容模型服务地址。`.env` 已被 Git 忽略，保留在本机。内置案例回放不需要模型密钥。

```bash
python start_backend.py
```

访问 [localhost:8001](http://localhost:8001)。启动脚本检查前端源码与 `backend/static` 的时间戳，必要时自动构建。可用 `SAGE_FORCE_FRONTEND_BUILD=1` 强制构建，或 `SAGE_AUTO_BUILD_FRONTEND=0` 关闭自动构建。

## 外部服务

完整选项见 [.env.example](.env.example)。根据需要配置百度/高德地图、Serper/Tavily 检索、Unsplash 图片等服务；MCP 工具配置见 [配置模板](../../mcp_servers/mcp_setting.example.json)。不要把真实密钥写入模板、源码或浏览器测试。

国际地点与餐厅可使用 Nominatim，人民币参考换算可使用欧洲央行汇率；端点和开关可通过环境变量调整。外部信息无法确认时，界面展示待确认或不可用状态。

默认 SQLite 数据库位于 `backend/data/supertravelagent.sqlite3`，可通过 `SUPERTRAVEL_DB_PATH` 指定可写路径。旅程默认保存在本机；清除浏览器数据、切换部署环境或设备可能影响历史访问。

## 使用流程

1. 点击 **新旅程**，填写表单，或在对话中输入需求；也可选择内置案例 **一键回放**。
2. 查看五阶段规划过程，等待校验通过的正式方案进入工作台。
3. 按天查看活动和地图；修改保存在草稿中，检查后点击 **应用修改**。
4. 下载已应用的正式方案，或恢复上一正式版本。回放案例可转为新建表单模板。

案例回放不调用模型或实时规划服务，也不写入用户旅程历史。案例信息不代表实时价格和库存。产品不下单、不支付。

## 前端开发

保持后端在 8001 端口运行，在 `frontend/` 下执行：

```bash
npm run dev
```

访问 [localhost:8080](http://localhost:8080)。`/api` 和 `/ws` 代理至后端；设置 `E2E_BACKEND_URL` 可修改代理目标。生产构建使用 `npm run build`，输出到 `backend/static/`。

## 检查

在 `examples/fastapi_react_demo/` 下执行：

```bash
python -m pip install pytest
python -m pytest backend/tests -q -p no:cacheprovider
cd frontend
npm run type-check
npm test
npm run build
```

后端启动后，可运行真实案例回放的浏览器回归：

```bash
npx playwright install chromium
npm run test:e2e -- e2e/demo-replay-formal-plan.spec.ts
```

## 排障

| 情况 | 检查方式 |
| --- | --- |
| 页面仍显示旧版本 | 执行 `npm run build`，重启后端并刷新浏览器。 |
| 无法启动规划 | 检查模型配置和服务连通性，查看页面设置与后端日志。 |
| 地图或路线不可用 | 检查服务密钥、配额与网络，保留未核验提示。 |
| 数据库无法写入 | 将 `SUPERTRAVEL_DB_PATH` 设置为当前用户有写权限的本地路径。 |
| 端口占用 | 修改 `SAGE_PORT`；开发前端同时更新 `E2E_BACKEND_URL`。 |
