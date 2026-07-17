# SuperTravelAgent 重构阶段 0 基线

> 日期：2026-07-17
> 分支：`codex/travel-product-refactor`
> 重构前基线提交：`a2ed55a`
> 状态：已完成，等待用户验收

## 1. 已确认实施参数

- 本地单实例部署，不使用 Docker；持久化采用 SQLite WAL。
- PDF 采用 HTML/CSS 与 Playwright/Chromium 生成。
- 外部 Provider 先使用契约测试、Mock 和录制数据建立稳定闭环，再接真实配置验收。
- 侧栏主导航保留旅行规划和旅行照片；次级区域按“用户画像、知识库、MCP、工具、设置”排序，并保留全部现有路由。
- 旅行照片工作台保留 PowerPaint 功能，本轮只统一外层导航、色彩、控件和状态样式。
- 视觉依据为“湖山青绿”方案 A；没有原参考图片时，以已确认色值、信息层级和 Ant Design 标准控件为准。

## 2. 重构前代码事实

- `backend/services/chat_service.py`：6531 行。
- `frontend/src/components/ChatInterface.tsx`：5003 行。
- 后端收集到 325 项测试。
- 前端共有 13 个测试文件、52 项测试。
- TypeScript 类型检查通过。
- 原前端测试脚本使用 Vitest `threads` 池时在当前 Windows 环境卡住；改用 `forks` 后 52 项测试通过。
- Demo 依赖文件仍固定在 FastAPI 0.100.0 与 Pydantic 1.10.12，但当前代码已使用 Pydantic V2 API。

## 3. 阶段 0 完成条件

- 新分支与重构前基线提交存在，`main` 不包含重构改动。
- Demo 的 FastAPI、Pydantic、OpenAI、HTTP 与 WebSocket 依赖使用已验证的精确版本。
- 前端默认测试命令不再使用会卡住的线程池。
- 一条命令可以执行后端测试、前端类型检查、前端单元测试和生产构建。
- 阶段 0 验证结果记录在本文档中，失败项不会被误写为通过。

## 4. 验证结果

2026-07-17 使用以下命令完成整套验证：

```powershell
.\scripts\verify_refactor_baseline.ps1 -Python "D:\Anaconda\anaconda3\python.exe"
```

结果：

- 后端：325 项全部通过，耗时 247.86 秒。
- TypeScript：`tsc --noEmit` 通过。
- 前端：13 个测试文件、52 项测试全部通过，耗时 101.53 秒。
- 生产构建：Vite 构建通过，耗时 23.72 秒。
- 总验证命令退出码：0。

非阻塞警告：

- Vite 仍提示 CJS Node API 即将弃用。
- React Router 测试提示两个 v7 future flag。
- 主入口构建产物约 2.09 MB、gzip 后约 688 KB，超过 500 KB 警告线；后续在页面拆分和质量阶段处理代码分包。

以上警告没有被当作失败隐藏，也不影响阶段 0 的依赖、测试和构建护栏目标。

## 5. 用户复验方式

在项目根目录执行：

```powershell
.\scripts\verify_refactor_baseline.ps1 -Python "D:\Anaconda\anaconda3\python.exe"
```

成功标准：命令最后显示 `Stage 0 baseline verification passed.`，且退出码为 0。只想快速检查前端时可以执行：

```powershell
.\scripts\verify_refactor_baseline.ps1 -Python "D:\Anaconda\anaconda3\python.exe" -SkipBackend
```
