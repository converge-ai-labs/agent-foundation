---
title: 上下文与工作状态
description: 区分模型当前可见的内容、历史压缩，以及结构化的工作状态。
---

agent 既需要当前任务相关的输入，也需要足够的状态来继续后续工作。Harness 将这几项职责分开：上下文投影准备当前模型请求，压缩修改历史，工作状态保存结构化的任务事实。

## 选择合适的机制

| 需求                           | 机制                        | 不提供的能力               |
| ------------------------------ | --------------------------- | -------------------------- |
| 当前时间、用量、工作空间元数据 | 运行时上下文与工作空间概览  | 扫描所有文件内容           |
| 当前指令或选定文件             | 文件上下文                  | 不受限制地递归读取整个仓库 |
| 长对话                         | Handoff 与压缩              | 恢复未保存的操作副作用     |
| 跨轮次的任务和笔记             | `HarnessState` 中的工作状态 | 跨 worker 调度或加锁       |
| 多个对话共享的持久文件         | [文件记忆](memory.md)       | 语义搜索或静默合并         |
| 按相似度召回事实               | [记录记忆](memory.md)       | 记录版本或冲突检测         |
| 空闲一段时间后缩减请求         | 冷启动过滤器                | 检测 provider 的缓存有效期 |

设置模型上下文预算不会自动启用工具。先选择相应的 Capability，再配置阈值。继续执行时的序列化和人工决策，参阅[状态与恢复](state-and-resume.md)。

## 组合上下文

Harness 的上下文功能共用一个模型上下文协调器。每个功能只贡献一个大小受限的内容块，不直接改写其他功能的消息。

下面是一种实用的通用组合：

```python
from a13n_harness.capabilities import (
    FileContextCapability,
    RuntimeContextCapability,
    WorkspaceOutlineCapability,
)

capabilities = (
    RuntimeContextCapability(),
    WorkspaceOutlineCapability(),
    FileContextCapability(),
)
```

- 运行时上下文在每次请求时刷新，只暴露显式选定的元数据键。
- 工作空间概览只读取元数据，不读取文件内容，且仅出现在输入请求中。
- 文件上下文在一次逻辑 Run 中只加载一次选定文件，并固定加载时使用的 Environment 路由。

这三种功能都有明确的字节数、条目数、深度或行数限制。应根据实际 Environment 和目标模型配置，不要把默认值当作通用标准。

对于上下文生命周期功能，调用方通过 `AgentSpec.model_characteristics` 构建参数提供由 Harness 管理的策略。显式设置的 Harness 上下文窗口会应用到实际使用的原生 `ModelProfile` 上。`HandoffCapability()` 在构建时据此计算 65% 的提醒阈值。未另行配置的 `CompactionCapability()` 则在每次请求时计算 90% 的阈值，优先使用原生 `RunContext` 的上下文窗口和用量，再回退到 Harness 模型特征及已记录的 provider 用量。显式 token 设置会覆盖这些值；这些 Capabilities 仍需主动选择才会启用。

## 工作状态

`WorkingStateCapability` 可以在自身可移植的 Capability 命名空间中保存任务和笔记：

```python
from a13n_harness.capabilities import WorkingStateCapability

capabilities = (WorkingStateCapability(),)
```

这种嵌入式模式适合在单个进程内运行、或从状态恢复的 Agent。Provider 模式通过 `RunBindings.task_state` 中新建的 `TaskStateBinding` 替换任务存储：provider 仍是权威来源，Harness 事件只报告大小受限、已经提交的变更。

Notes 工具具有明确的修改语义：

- `note_write(key, value)` 创建或更新笔记，返回 `created` 或 `updated`；
- `note_delete(key)` 是幂等操作，返回 `deleted` 或 `already_absent`；
- `note_get(key=None)` 读取一条完整的值，或列出排序后的键及数量。

Notes 只在用户输入边界投影；活跃 Tasks 则在用户输入和工具结果两个边界都会投影。它们作为大小受限的请求尾部内容，Notes 在前，Tasks 在后。已有历史中的投影保持不变，因此工具结果轮次不会刷新旧 Notes。能完整放入的笔记以 `<note>` 条目展示；`<note-ref>` 表示可通过 `note_get` 读取其值，`<notes-omitted>` 则提示未纳入投影的条目。笔记内容不会被部分截断；没有笔记时也不会生成 Notes 块。`WorkingStateConfiguration` 默认最多投影 256 条笔记和 128 个任务，共用 64 KiB 的上下文预算。

Notes 保存结构化的会话事实，Tasks 保存执行状态，`summarize` 保留叙述上的连续性和下一步安排。Handoff 前应核对并更新过时的笔记和任务状态，不要将所有笔记或任务复制进摘要。自动压缩同样只替换历史，随后会重新投影当前 Notes 和 Tasks。压缩内部发起的摘要请求会看到完整且未改动的历史，包括之前的叠加内容和 thinking，不会裁剪或只选择尾部内容。它保留 `tool_choice`，并要求模型不要调用工具。压缩和 `summarize` 都会按顺序重放当前逻辑 Run 的初始输入及已送达的用户引导，保留多模态内容；尚未送达的引导和内部通知不会重放。

工作状态不是分布式工作流引擎。跨 worker 的所有权、持久租约、调度和交付，由 Host 或任务 provider 负责。

## 过滤器

`MessageIntegrityFilterCapability` 是必需组件，由 builder 管理。`ContentFilterCapability` 可选。冷启动过滤通过 `AgentSpec.cold_start_filter` 默认启用，空闲间隔为一小时：

```python
from a13n_harness import AgentSpec
from a13n_harness.filters import (
    ColdStartFilterConfiguration,
    ContentFilterCapability,
    ContentFilterConfiguration,
)

capabilities = (
    ContentFilterCapability(
        ContentFilterConfiguration(
            accepted_media=frozenset({"image", "document"}),
            max_media_items=16,
        )
    ),
)
spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
without_cold_compression = spec.with_updates(cold_start_filter=None)
```

内容过滤只用于适配 provider 或模型的多模态兼容性。距离最近一次模型响应达到配置间隔后，冷启动过滤会缩短旧的、已经消费的工具结果字符串；用户输入、thinking、原生媒体和待处理工具结果保持不变。一小时是明确的保留策略，并不代表 provider 的缓存到期时间。如果显式组合了 `ColdStartFilterCapability`，它会使用自己的策略，并阻止自动实例启用。这两种过滤器都不负责传输重试或语义恢复。

## Handoff 与自动压缩

Handoff 和压缩都会要求 Agent 保留实际使用且仍相关的 skill 的 `SKILL.md` 路径、简要用途，以及接下来立即需要的支持文件路径。恢复后，会提醒 Agent 在依赖这些指引开展工作之前重新读取；如果完整内容已经在上下文中，则可以复用。这只是指引，不会自动重新加载，也不是工具调用的准入检查。仅查看过的 skill 不会自动成为正在使用的工作流程，仍完整保留在上下文中的读取结果可以继续使用。

`HandoffCapability` 提供显式的 `summarize` 工具，`CompactionCapability` 根据请求的上下文用量触发。两者都可选。通常通过[模型特征](models.md#model-characteristics)配置阈值；如果 Agent 需要固定策略，也可以显式设置 token 阈值。

摘要负责延续叙述，不能代替任务和笔记状态。摘要生成和压缩都不会提交应用存储。只有符合 Host 的结果接受策略时，才保存产生的安全 `HarnessState`。
