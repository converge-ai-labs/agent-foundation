---
title: Agent 与对话生命周期
description: 了解 agent、thread、run、人工审批与持久化状态在 Service、Harness 和 Harness UI 中的对应关系。
---

Service、Harness UI 以及嵌入 Harness 的 Python 应用共享同一套核心执行模型。由于三款产品底层均由 Harness 驱动，掌握一套对话生命周期概念即可在所有产品间无缝切换。

| 核心概念        | 在生命周期中的职责                                                             |
| --------------- | ------------------------------------------------------------------------------ |
| **Agent**       | 组合模型、系统指令、工具与能力（Capabilities），定义执行任务的方式与权限边界。 |
| **Thread**      | 保存单次对话的有序消息历史与可移植特性状态。                                   |
| **Run**         | 处理新输入或在人工决策后恢复执行的单次运行轮次，负责推进线程状态。             |
| **Environment** | 提供文件读写、Shell 命令、进程管理或桌面操作实际落地的执行环境。               |
| **Memory**      | 在不同线程与会话之间持久保存共享知识、用户偏好与项目交接笔记。                 |

## 选择或构建 Agent

每次对话都从选择或构建一个 agent 定义开始：

- **Service：** 在 Console 中选择工作空间内的 agent，或在调用 API 时传入 `agent_id`。每次修改配置都会生成不可变的 revision（修订版本），确保运行中的任务行为一致且可复现。详见 [Service 对话工作流](../a13n-service/core-concepts.md#choose-an-agent)。
- **Harness UI：** 从[实例配置](../a13n-harness-ui/configuration.md)中加载 Agent，可在终端使用 `/agent` 命令或在 WebUI 顶部选择器中随时切换。
- **Harness (Python SDK)：** 在 Python 代码中定义 `AgentSpec`（或 `AgentDefinition`），并通过 `HarnessBuilder` 构建可复用的 `ExecutableAgent`。详见 [Agents 与 Runs](../a13n-harness/agents-and-runs.md)。

## 发起对话

发送消息后，宿主会在新建或已有的线程上启动一次 run：

- **Service：** 通过 [Console](../a13n-service/core-concepts.md#start-a-conversation) 或 [Service SDK 与 HTTP API](../a13n-service/connect-application.md) 提交输入，支持实时订阅 SSE 事件流或轮询执行状态。
- **Harness UI：** 在 [TUI 终端](../a13n-harness-ui/everyday-use.md)或 [WebUI 浏览器](../a13n-harness-ui/webui.md)中发起任务。两端共享同一个本地会话库，可以在终端发起任务、在浏览器中查看文件树和 Git diff。
- **Harness (Python SDK)：** 调用 `await executable.run(...)` 等待最终输出，或使用 `async with executable.stream(...)` 实时消费模型生成文本与工具调用事件。

## 回答提问或审批工具调用

执行过程中，agent 可以主动挂起以向用户追问、在执行敏感工具前请求审批，或等待外部客户端工具返回结果：

- **Service：** Console 会展示交互式卡片以处理[待响应的提问与审批](../a13n-service/core-concepts.md#answer-a-question-or-approve-a-tool-call)；API 客户端则读取 run 上的 `pending` 请求列表，集齐响应后统一提交至 `/resume`。
- **Harness UI：** 审批与提问卡片会直接在 TUI 输入区或 WebUI 对话流中弹出，支持单次批准、附带原因拒绝，或在执行前修改工具 JSON 参数。
- **Harness (Python SDK)：** 实现宿主侧的[人机交互处理器](../a13n-harness/human-in-the-loop.md)与[托管工具审批策略](../a13n-harness/managed-tools.md)，或将挂起时的 `HarnessState` 落盘，待收到用户反馈后通过 `DeferredToolResume` 恢复运行。

## 稍后继续与跨重启恢复

对话状态与底层计算资源解耦，支持跨进程重启或断线后无缝续接：

- **Service：** 将[线程历史](../a13n-service/core-concepts.md#continue-later)持久保存在 PostgreSQL 中，将运行检查点保存在对象存储中，并支持挂载跨会话共享的 [Memory Spaces](../a13n-service/memory.md)。
- **Harness UI：** 自动在本地 SQLite 中保存对话与检查点；可在终端输入 `/resume` 搜索恢复，或在 WebUI 列表中点击继续。
- **Harness (Python SDK)：** 每次运行均返回可序列化的 `HarnessState`。在内存中可直接作为 `previous_state` 传给下一轮运行，也可序列化为 JSON 存入业务数据库，实现[跨进程重启恢复](../a13n-harness/state-and-resume.md)。

## 深入观测与排障

当你需要审查工具调用参数、统计 Token 成本或排查故障恢复过程时：

- **Service：** 将主对话线程与子 agent 委派线程统一归入一个 **session（会话）**，并记录每次 **run（运行）** 及其在 Worker 上的每次 **attempt（执行尝试）**。可在 Console 的 Debug 视图中审查，或参阅 [Service 执行模型](../a13n-service/core-concepts.md#understand-execution-when-you-need-it)。
- **Harness UI：** 在 TUI 中按 `Ctrl+O` 展开完整的工具参数、输出与 diff，使用 `/usage` 和 `/subagents` 查看用量成本与子任务详情。
- **Harness (Python SDK)：** 接入 [OpenTelemetry 与观测钩子](../a13n-harness/observation.md)，或使用 [Stream Protocol](../a13n-stream-protocol/index.md) 将运行流转换为前端可直接渲染的 AG-UI 事件。
