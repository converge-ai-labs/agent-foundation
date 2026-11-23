---
title: 委派与 CodeAct
description: 在 Harness 中将边界清晰的任务委派给内联或宿主托管的子智能体，并通过 CodeAct 执行受限 Python 程序。
---

使用**子智能体**在干净的上下文窗口中隔离专项任务，使用 **CodeAct** 让智能体在受限 Python 中编排多步工具调用。

## 子智能体

<span id="asynchronous-children"></span>

通过 `HarnessBuilder().build(..., subagents=...)`（或 `SubagentCapability`）挂载命名子定义：

- **内联子智能体：** 在父运行流中执行，共享父级环境，并将结果直接返回给父智能体。
- **宿主托管子智能体：** 作为独立子运行由宿主（如 Service 或 Harness UI）管理，拥有独立检查点与并发上限。

### 宿主托管反馈

内置内联子智能体默认设置 `RunBindings.deferred_tools_supported=False`，因其无法独立暂停等待人工输入。支持子运行暂停与恢复的宿主在其自有子生命周期中处理延迟反馈。

## CodeAct

`CodeActCapability` 允许模型运行受限 Python 代码片段来调用智能体已有工具（受相同权限与审批约束），并通过 `store`、`load` 与 `forget` 在步骤间持久化 JSON 状态。
