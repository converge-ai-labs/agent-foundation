---
title: 人机协作
description: 暂停 Harness 运行以收集结构化用户问题、工具审批或外部工具结果，并从已保存状态安全恢复。
---

当智能体需要人工决策——结构化提问（`UserInteractionCapability`）、工具审批（`ask` 权限）或客户端执行工具结果——运行时会返回 `result.status == "suspended"`。

## 暂停与恢复流程

1. 执行或流式运行智能体，检查 `result.status == "suspended"`。
2. 持久化 `result.state`，并从 `result.deferred` 读取待处理的问题、审批或外部工具调用。
3. 在应用界面或 API 中收集用户回答或审批结果。
4. 在下一次 `run()` 或 `stream()` 调用中传入 `previous_state=result.state` 与延迟响应即可恢复运行。

## 宿主边界

- 当运行不支持暂停等待人工输入时（例如[内置内联子智能体](delegation-and-codeact.md#host-managed-feedback)），设置 `RunBindings.deferred_tools_supported=False`。
- 带 `Idempotency-Key` 的 Service HTTP 恢复流程见 [Service 等待中的运行](../a13n-service/agents-and-runs.md#resume-a-waiting-run)与 [SDK 指南](../a13n-service/sdks.md)。
