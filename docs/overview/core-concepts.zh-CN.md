---
title: Agent 与对话
description: 了解在各产品中配置 agent、检查运行和保存对话状态的方式。
---

Service 和 Harness UI 都通过 Harness 运行 agent。配置和对话历史存在哪里、谁能访问，以及怎样恢复工作，取决于承载 Harness 的应用。

## 选择 agent

在 Service 中，选择[工作空间内的 agent](../a13n-service/core-concepts.md#choose-an-agent)。Harness UI 从[实例配置](../a13n-harness-ui/configuration.md)加载 Agent。嵌入 Harness 时，由应用[在 Python 进程中构建 Agent](../a13n-harness/agents-and-runs.md)。

## 开始对话

通过 [Console 或应用](../a13n-service/core-concepts.md#start-a-conversation)向 Service 发送消息。在 Harness UI 中，终端与 [WebUI](../a13n-harness-ui/webui.md)共用同一工作台。嵌入 Harness 时，应用负责传入输入并交付结果。

## 回答问题或审批工具调用

Service 会向调用方或 Console 用户展示[待响应的请求](../a13n-service/core-concepts.md#answer-a-question-or-approve-a-tool-call)。在应用中嵌入 Harness 时，需按宿主要求处理[交互请求](../a13n-harness/human-in-the-loop.md)并设置[工具审批策略](../a13n-harness/managed-tools.md)。

## 稍后继续

Service 将[线程历史](../a13n-service/core-concepts.md#continue-later)保存在部署中。Harness UI 将对话保存在实例中。嵌入 Harness 时，由应用决定怎样[存储状态并恢复工作](../a13n-harness/state-and-resume.md)。

## 需要时再了解执行细节

Service 将相关线程归入会话，并记录对话中的[运行和执行尝试](../a13n-service/core-concepts.md#understand-execution-when-you-need-it)。嵌入 Harness 的应用可以直接[观测执行过程](../a13n-harness/observation.md)。
