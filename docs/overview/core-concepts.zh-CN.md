---
title: Agent 与对话
description: 深入理解 Agent 配置、运行时会话历史与持久化执行状态在 Harness、Harness UI 和 Service 中的统一运作机制。
---

Agent Foundation 生态中的所有 Agent 交互均由底层的 Harness 引擎统一驱动。根据宿主运行时的不同，Agent 的配置位置、调用权限、状态持久化方式以及中断恢复机制会有所适配，但核心概念保持高度一致。

## 选择 agent

Agent 配置将角色系统提示词、模型参数、工具访问权限与执行环境绑定在一起：

- **在 Service 中：** 直接选用[工作空间内受版本化保护的 Agent 定义](../a13n-service/core-concepts.md#choose-an-agent)。成员权限由组织与工作区管理员统一分配与约束。
- **在 Harness UI 中：** Agent 配置通过项目目录或用户根目录下的[声明式 YAML 配置文件](../a13n-harness-ui/configuration.md)进行加载与切换。
- **在内嵌 Harness 中：** 开发者在 [Python 业务代码](../a13n-harness/agents-and-runs.md)中直接构造和编排 `AgentSpec` 或 `AgentDefinition` 对象。

## 开始对话

会话将用户的输入提示、Agent 生成的回复以及工具执行期间产生的所有中间事件编排在一条有序的线程（Thread）中：

- **在 Service 中：** 通过 [Console 控制台或客户端 SDK](../a13n-service/core-concepts.md#start-a-conversation)发送消息。Service 将任务写入收件箱条目，分配 Run 实例，并通过 SSE 长连接持续推送实时流式事件。
- **在 Harness UI 中：** 借助交互式终端 TUI 或沉浸式 [WebUI 界面](../a13n-harness-ui/webui.md)发起任务。支持多行编辑、本地文件拖拽挂载，并能在 Agent 思考生成过程中实时引导（Steer）。
- **在内嵌 Harness 中：** 应用程序通过调用 `executable.run()` 获取终态响应，或使用 `async with executable.stream()` 消费类型化的实时事件流。

## 回答问题或审批工具调用

Agent 在执行高风险操作或需要用户补充关键输入时，会主动发起人机交互（Human-in-the-Loop）：

- **在 Service 中：** 待处理的交互会以交互卡片形式展现在 Console 中，或通过 API 返回挂起状态（Suspended）。在用户审批或回答后，批量提交即可[恢复后续执行](../a13n-service/core-concepts.md#answer-a-question-or-approve-a-tool-call)。
- **在 Harness UI 中：** 决策卡片会在 TUI 或 WebUI 中直观展示执行命令、参数差异及安全风险摘要，供用户一键确认或提供理由。
- **在内嵌 Harness 中：** 开发者可以注册自定义的[人机交互处理器](../a13n-harness/human-in-the-loop.md)，并灵活定制与宿主架构深度适配的[工具审批策略](../a13n-harness/managed-tools.md)。

## 稍后继续

完整的对话状态持久化机制确保 Agent 能够从中断处平滑恢复，并保持跨多轮甚至跨服务重启的长程上下文：

- **在 Service 中：** 完整的线程历史、工具执行输出与检查点（Checkpoint）统一由 PostgreSQL 与对象存储安全持久化。
- **在 Harness UI 中：** 对话记录与工作区上下文自动沉淀在本地工作台存储中，支持随时检索与无缝恢复。
- **在内嵌 Harness 中：** 每次运行结束时，Harness 会产出精简且可序列化的 `HarnessState`。宿主应用只需持久化该状态，并在下一次调用时传入 `run(..., previous_state=state)` 即可无缝接续。

## 需要时再了解执行细节

在排查 Agent 异常行为或优化运行延迟时，可从不同颗粒度观测系统状态：

- **Session（会话）与 Thread（线程）：** Session 负责归组一系列业务强相关的协作流程；Thread 记录具体的有序消息流。
- **Run（运行）与 Attempt（重试）：** Run 代表对单次用户输入的完整处理流程。在分布式架构中，如果计算节点因宿主维护发生调度切换，系统会自动发起新的 Attempt，从上一个持久化检查点继续推进。
- **全链路追踪与调试：** 通过 Console 内置的 Debug 视图或底层的 [Harness 观测接口](../a13n-harness/observation.md)，可精确审计模型原始请求报文、Token 开销、首字延迟以及各项工具调用的真实入参与出参。
