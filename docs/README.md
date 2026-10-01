---
layout: default
title: Home
nav_order: 1
description: "SuperTravelAgent documentation and archived agent framework guides"
permalink: /
---

# SuperTravelAgent 文档

当前旅行应用的功能、真实录屏和启动方式见 [中文介绍](../README_CN.md) / [English introduction](../README.md)，配置与开发说明见 [Web 应用指南](../examples/fastapi_react_demo/README.md)。

## 框架参考

以下文档保留早期 Sage 通用 Agent 框架的设计与使用说明，部分接口和演示方式早于当前旅行 Web 应用。

| 内容 | English | 简体中文 |
| --- | --- | --- |
| 快速开始 | [Quick start](QUICK_START.md) | [快速开始](QUICK_START_CN.md) |
| 架构 | [Architecture](ARCHITECTURE.md) | [架构](ARCHITECTURE_CN.md) |
| 工具开发 | [Tool development](TOOL_DEVELOPMENT.md) | [工具开发](TOOL_DEVELOPMENT_CN.md) |
| API | [API reference](API_REFERENCE.md) | [API 参考](API_REFERENCE_CN.md) |
| 配置 | [Configuration](CONFIGURATION.md) | [配置](CONFIGURATION_CN.md) |
| 示例 | [Examples](EXAMPLES.md) | [示例](EXAMPLES_CN.md) |

## 文档构建与发布

`Documentation CI` 在文档、根 README 或工作流修改时构建 Jekyll，并保留构建产物；普通提交和 PR 不依赖 GitHub Pages 是否启用。

需要发布文档网站时，先在仓库的 **Settings → Pages** 中选择 **GitHub Actions** 作为构建来源，再到 **Actions → Documentation CI → Run workflow** 选择 `main` 并勾选 `deploy`。默认的手动运行也只校验构建。
