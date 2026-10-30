---
title: Harness UI
sidebarTitle: 概览
description: 在终端或浏览器中启动 Agent，再按工作需要调整配置。
---

Harness UI 让你在自己的电脑或连接的 Environment 中与 Agent 一起工作：理解代码库、修改文件、运行检查，或调查问题。你提供模型订阅或 API 密钥，Harness UI 提供对话、工具和配置管理。

## 安装并启动

第一次使用时，按顺序完成这两篇指南：

1. [安装 Harness UI](installation.md)。安装包同时包含终端界面和 WebUI。
2. [设置第一个 Agent](setup.md)。连接 Model、选择执行权限，并发送测试消息。

开始前不需要编写 YAML，也不需要创建 Project。第一条消息成功后，按[终端指南](everyday-use.md)或 [WebUI 指南](webui.md)操作文件、继续任务。

## 选择工作方式

**终端：** 在要处理的代码库中运行 `a13n-harness-ui`。终端界面使用这个工作目录。[学习对话操作](everyday-use.md)。

**浏览器：** 运行 `a13n-harness-ui webui`，打开打印的登录链接。WebUI 提供对话、文件、Git 变更和终端。[开始浏览器会话](webui.md)。

**语音：** 在 WebUI 中启用 [Live Mode](live-mode.md)，通过语音讨论工作。Live 需要受支持的 Codex 订阅 Model，以及浏览器麦克风权限。

## 配置工作台

理解三个资源，就能处理大多数配置任务：

- **Model** 定义提供方连接和请求设置。
- **Agent** 选择 Model，并添加指令和工具。
- **Project** 组织工作目录和创建默认值。

要修改配置时，先看[常见配置用法](configuration-recipes.md)。查字段和优先级时，再看[配置参考](configuration.md)。

## 配置工作流

第一轮对话成功后，每次添加一项能力：

- **调整 Agent 使用的模型：** [连接另一个 Model](models-and-authentication.md)，再[配置 Agent](agents-and-subagents.md)。
- **选择工作位置：** [选择 Environment 并组织 Project](environments-and-projects.md)。
- **教授可重复执行的任务：** [添加 Skill](skills-and-content-plugins.md)。
- **连接其他工具服务：** [添加 MCP 服务器](mcp.md)；需要交互式结果时使用 [MCP Apps](mcp-apps.md)。
- **使用提供方工具或媒体输入：** [配置原生工具与 Web 工具](native-and-web-tools.md)。

知道要完成哪项操作后，可在[命令参考](command-reference.md)中查找终端命令。

## 共享并继续工作

Agent 工作时保持 Harness UI 运行。关闭浏览器标签页后，活动 Run 会继续；停止应用则会结束当前执行。应用重启后，按照[恢复指南](automation-and-troubleshooting.md)从最后保存的检查点继续。

只与可信协作者共享 WebUI 实例。他们使用同一组凭据、配置和文件。Host 原生文件访问和终端默认开启；`--no-share-computer` 可关闭这些共享功能，与 Agent 执行权限分别设置。

需要受管理的用户、角色，以及跨 worker 的持久执行时，使用 [Service](../a13n-service/index.md)。

## 基于 Harness UI 构建

要在相同配置和对话存储之上构建其他界面，使用 [Python App](embedding.md) 或 [HTTP API](http-api.md)。要在自己的应用中嵌入 Agent 执行，并自行管理存储和访问策略，从 [Harness](../a13n-harness/getting-started.md) 开始。
