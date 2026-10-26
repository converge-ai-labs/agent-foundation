---
title: 上下文与工作状态
description: 区分模型当前可见的内容、历史压缩，以及结构化的工作状态，并设置允许的主机等 Run 级配置。
---

上下文投影准备当前请求，压缩处理较长历史，工作状态保存任务事实。

## 选择合适的机制

| 需求                       | 机制                        |
| -------------------------- | --------------------------- |
| 当前时间、用量、文件元数据 | 运行时上下文与工作空间概览  |
| 当前指令或选定文件         | 文件上下文                  |
| 长 Thread 历史             | Handoff 与压缩              |
| 跨轮次的任务和笔记         | `HarnessState` 中的工作状态 |
| 多个 Thread 共享的持久文件 | [文件记忆](memory.md)       |
| 按相似度召回事实           | [记录记忆](memory.md)       |
| 空闲一段时间后缩减请求     | 冷启动过滤器                |

按任务选择 Capability。Thread 的保存与续接见[状态与恢复](state-and-resume.md)。

## Run 配置

通过 `RunConfiguration` 传入一次 Run 共享的配置：

```python
from a13n_harness import RunBindings, RunConfiguration

configuration = RunConfiguration(
    allowed_hosts={"api.example.com", "docs.example.com"},
    extensions={"example.reader": {"images": True}},
)
bindings = RunBindings.embedded(configuration=configuration)
# Pass bindings to executable.run(..., bindings=bindings).
```

插件和工具通过 `AgentContext.configuration` 读取配置（工具内使用 `ctx.deps.configuration`）。各集成验证自身命名空间下的 `extensions` 值。

`allowed_hosts=None` 放行全部目标，空集合拒绝全部。条目匹配规范化的主机名/IP 或显式 `regex:` 规则。第一方 HTTP 集成执行此允许列表；自定义集成应在请求和重定向前调用 `configuration.authorize_url(url)`。限制目标时，使用本地 MCP 和 Host Web 工具，将已授权媒体物化为 `BinaryContent`，不直接转发 URL。Shell 与第三方插件的网络流量需要 Environment 或部署隔离。

### 主机规则与正则表达式

主机规则通过 `re.fullmatch` 匹配完整的规范化主机名，不匹配 URL 路径或端口。域名会转为小写并做 IDNA 规范化；使用小写 ASCII 表达式或 `(?i)`。精确规则与正则规则可混用。

```python
configuration = RunConfiguration(
    allowed_hosts={
        "api.vendor.example",                         # Exact host only.
        r"regex:(api|docs)\.example\.com",            # Two named subdomains.
        r"regex:(?:[a-z0-9-]+\.)*assets\.example\.com", # Base and all subdomain levels.
    },
)
```

| 规则                                  | 放行                                | 不放行                                    |
| ------------------------------------- | ----------------------------------- | ----------------------------------------- |
| `example.com`                         | `example.com`                       | `api.example.com`                         |
| `regex:[a-z0-9-]+\.example\.com`      | `api.example.com`                   | `example.com`、`eu.api.example.com`       |
| `regex:(?:[a-z0-9-]+\.)*example\.com` | `example.com`、`eu.api.example.com` | `notexample.com`、`example.com.evil.test` |

字面点使用 `\.` 转义。`regex:(?:[a-z0-9-]+\.)*example\.com` 匹配基础域名及其子域名；`*.example.com` 不是 glob。每个重定向目标都必须匹配规则。

YAML 中使用单引号保留反斜杠，例如 `'regex:(api|docs)\.example\.com'`。JSON 需要双反斜杠：`"regex:(api|docs)\\.example\\.com"`。上面的 Python 原始字符串可避免额外转义。

## 组合上下文

组合以下 Capabilities，提供当前元数据与选定文件：

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
- 工作空间概览只读取元数据，不读取文件内容，且仅出现在用户输入请求中。
- 文件上下文在一次逻辑 Run 中只加载一次选定文件，并固定加载时使用的 Environment 路由。

通过各 Capability 的配置设置适合工作空间的字节数、条目数、深度和行数限制。

文件上下文默认读取工作目录的 `AGENTS.md`，还会提示直属子目录中的 `AGENTS.md` 路径，以及工作目录和直属子目录中的 `.agents/skills` 目录。这些提示不载入子目录文件正文或 Skill 描述。Agent 按任务需要，通过普通文件工具读取相关项目的指导和 Skill。发现入口不会扩大已选择的 Skill 目录，也不会改变权限。

入口扫描最多检查工作目录的 64 个非隐藏条目，提示最多占用独立于文件正文预算的 8 KiB。不完整结果标记为 `incomplete`；更深层仍可能有指导文件。提示在下一个逻辑 Run 刷新，根目录没有 `AGENTS.md` 时也会发现子项目入口。设置 `FileContextConfiguration(include_project_guidance=False)` 可保留文件正文而关闭入口提示。

## 压缩较长的 Thread

添加 `CompactionCapability()` 以启用自动历史压缩。默认根据报告的 token 用量，在有效 Model 上下文窗口的 90% 处触发。Model 缺少窗口信息时，设置 `AgentSpec.model_characteristics`。固定 token 阈值可使用以下策略：

```python
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy

compaction = CompactionCapability(CompactionPolicy(trigger_tokens=100_000))
# Include compaction in HarnessBuilder.build(..., capabilities=(compaction,)).
```

```mermaid
flowchart TD
    Usage["报告的请求用量"] --> Threshold{"达到阈值？"}
    Threshold -->|否| Request["继续模型请求"]
    Threshold -->|是| Summary["同一 Agent 总结历史"]
    Summary --> Restore["替换历史并重放当前用户输入"]
    Restore --> Project["投影当前笔记与任务"]
    Project --> Request
    class Usage,Threshold,Summary,Restore,Project,Request a13n
```

总结请求使用同一 Agent 和完整历史。总结替换旧历史后，按顺序重放当前用户输入与已交付的 steering，包括媒体。压缩发出 `CompactionSummaryEvent`，返回状态中保存替换后的历史。压缩会调用模型，应将该请求纳入预算。

需要显式交接时，添加 `HandoffCapability()` 以启用 `summarize` 工具；默认在上下文窗口的 65% 处提醒。两种 Capability 均需显式选择，仅配置阈值不会启用。

### 后台压缩

Host 可以独立于前台 Run 执行总结生成：

```python
from a13n_harness.compaction import CompactionOperator


def background_compaction(operator: CompactionOperator) -> CompactionCapability:
    return CompactionCapability(background=True, operator=operator)
```

在 operator 中实现 `submit(input)`、`inspect(thread_id)` 和 `wait(thread_id, operation_id)`。提交总结的 Run 关闭后，后台执行仍应继续。每个 Thread 同时只接纳一个未确认的操作，先保存就绪结果再通知，读取结果不消费结果。使用 `generate_compaction(input, request)` 执行总结协议与有界瞬态重试。回调接收 `CompactionInput`，通过 `request_messages`、`settings` 和 `parameters` 获取新的可变副本，使用新的 Host 模型调用授权，并统计每次尝试；不要捕获父 Run 的 Agent、资源或可变用量对象。

Harness 冻结当前待发送请求之前的完整历史前缀。总结就绪后，Harness 替换该前缀，保留之后的全部原生消息，包括工具结果和 steering，并在下一个适用的模型边界应用。前台继续并行推进需要原生 token 计数、已知上下文窗口和显式 `max_tokens` 输出预留。容量未知或不足时，Harness 等待同一个操作。若合并后的请求仍被确认超出窗口，则报错，而不是裁剪最近的工作。

结果在两个 Run 之间就绪时，将其与最新选定的检查点一起传给普通 Run：

```python
from a13n_harness.compaction import compaction_disposition

continued = await executable.run(
    "Continue", previous_state=latest_state, compaction_result=result, bindings=bindings
)
# Commit continued.state through your Host's normal fenced checkpoint path.
# Only the actually selected, committed state permits acknowledgement:
disposition = await compaction_disposition(
    committed_state, basis=result.basis, state_store=state_store
)
if disposition is not None:
    await retire_operation(result.basis.operation_id)  # Your Host's operation store.
```

Run 不修改传入的检查点。结果自带 `CompactionBasis`，包含操作和 Thread 身份、前缀长度及摘要指纹、保留的语义输入。Harness 在恢复逻辑改写历史之前解析并验证规范历史，仅替换匹配的前缀，保留完整后缀。检查点内容已外置时，通过 `RunBindings(state_store=...)` 提供存储。当前系统提示由 executable 决定；即使中间的 Run 更换、重排或删除系统提示，也不会使结果失效。其他历史修改会得到终态 `skipped`。匹配的历史若仍处于未闭合工具或挂起边界，则推迟应用，在后续 Run 再次传入结果。

`a13n.compaction` 命名空间记录最近处理的操作、应用依据指纹、applied/skipped 处理结果和独立的待恢复标记。同一结果再次交付时，任一已保存的处理结果都使其成为空操作。通过 `compaction_disposition` 查询，不要解析该私有命名空间。下一次 Run 首次投影上下文前，Harness 按当前定义恢复工具发现和 CodeAct 描述，并通过恢复事件重置记忆交付游标。待发送请求遵循当前图片、视频规则及容量检查。恢复过程不要求 executable 配置 `CompactionCapability` 或 operator。监听器失败或取消时保留待恢复标记，供后续重试。恢复事件本身不是保存确认。

`CompactionInput` 保存投影后的总结请求及捕获的模型配置。`CompactionResult` 仅保存应用依据和总结，应用时无需生成输入或模型配置。规范原始内容保留在 State 中。输入和结果都应按机密执行数据保存。凭据和连接策略保留在新的 Host 请求回调中。`apply_compaction` 是共享的纯历史变换；活动 Run 和下一次 Run 的联合恢复共享判断与恢复规则。普通检查点提交确认之前，同一操作持续占用未确认位置。后台完成后保存独立结果，空闲时等待下一次 Run，不改写检查点。Service 和 Harness UI 需要各自接入 operator 与持久化流程后才能启用此模式。

## Handoff 与自动压缩

续接总结保留仍需使用的 Skill 路径与作用。恢复时，若完整内容已不可用，应重新读取。

显式交接使用 `summarize`，自动压缩根据用量触发。阈值见[压缩较长的 Thread](#compact-a-long-thread)。

直接调用 `summarize`，并确保它是该次模型响应中唯一的工具调用；不能通过 CodeAct 或 ToolSearch 代理组调用。保留的 CodeAct runner 仍在执行时，自动压缩会等待，Handoff 会拒绝调用。先等待 runner 结束并检查结果，再编写新的总结。其他保留的函数工具不会阻止上下文替换。

按 Host 的接受策略保存返回的 `HarnessState`；总结不会保存应用存储。

### 保留已调用的工具

`summarize` 和自动压缩都会保留数量和大小受限的已调用 [ToolSearch](tool-search.md) 工具。只有完成的目标工具调用才进入工作集，包括经 CodeAct 或 proxy 分发的调用。仅搜索不会加入工具，重复调用只刷新同一条记录。普通业务工具的结果由总结承载，不再重放。

优先保留最近调用的工具，最多 32 个，发现上下文的估算预算为 64 KiB。预算计入原生 schema 展开和两个 CodeAct runner 目录，不计入恢复用 metadata。这是 UTF-8 字节估算，不是 token 限制。每个入选工具都保留完整的当前 schema；已移除、不可用或来源身份改变的工具会被丢弃。通过 `load_capability` 激活延迟 Capability 的生命周期不在此保留策略内。

Harness 从当前定义重建一份精简的合成搜索结果，并明确提示这是重建记录、保留集合不完整、需要其他工具时可以重新搜索。此过程不会执行搜索，也不保留原始查询：原生 `queries` 和代理 `query` 都使用明确的占位符 `[query omitted during context replacement]`。搜到 A 和 B 后仅调用 A，则只保留 A；B 不占用保留预算。

例如，发现 `crm__customer_name` 并查询客户 42 后，替换上下文按以下顺序组织：

| 位置 | 显式 `summarize`                   | 自动压缩                                  |
| ---- | ---------------------------------- | ----------------------------------------- |
| 1    | 稳定系统前缀、交接总结与恢复提醒   | 稳定系统前缀与压缩指令                    |
| 2    | 重建提示与已调用工具的合成发现结果 | assistant 总结                            |
| 3    | 当前用户输入与已交付的 steering    | 重建提示与已调用工具的合成发现结果        |
| 4    |                                    | 恢复标记、当前用户输入与已交付的 steering |

当前笔记、任务和运行时上下文通过各自原有的 provider 重新投影。客户查询结果写入总结；保留的发现记录让 Agent 无需再次搜索即可调用 `crm__customer_name`。替换后的第一个请求中，两个 CodeAct 目录都会与保留的工具一致。

可移植的保留状态只按调用先后顺序保存工具名和来源身份，最多占用 128 KiB，不缓存搜索结果或 schema。CodeAct 内调用的目标工具与直接调用遵循同一策略。保存返回的 `HarnessState`，即可将这个工作集和替换后的历史带入下一个 Run。

## 工作状态

`WorkingStateCapability` 可以在自身可移植的 Capability 命名空间中保存任务和笔记：

```python
from a13n_harness.capabilities import WorkingStateCapability

capabilities = (WorkingStateCapability(),)
```

这种不使用 `TaskStateBinding` 的嵌入式模式，适合在单个进程内运行或从状态恢复的 Agent。Provider 模式通过 `RunBindings.task_state` 中新建的 `TaskStateBinding` 替换任务存储：provider 仍是权威来源，Harness 事件只报告大小受限、已经提交的变更。

笔记工具具有明确的修改语义：

- `note_write(key, value)` 创建或更新笔记，返回 `created` 或 `updated`；
- `note_delete(key)` 是幂等操作，返回 `deleted` 或 `already_absent`；
- `note_get(key=None)` 读取一条完整的值，或列出排序后的键及数量。

笔记在用户输入请求时投影；任务还会在工具结果请求时刷新。完整笔记以 `<note>` 显示；`<note-ref>` 和 `<notes-omitted>` 指向可通过 `note_get` 读取的值。`WorkingStateConfiguration` 默认投影最多 256 条笔记和 128 个任务，共享 64 KiB 预算。

笔记保存事实，任务记录执行进度，`summarize` 保留续接叙事。交接前清理过时笔记和任务状态，不必将全部内容复制到总结中。

工作状态不是分布式工作流引擎。跨 worker 的所有权、持久租约、调度和交付，由 Host 或任务 provider 负责。

## 过滤器

Harness 在模型请求前执行消息完整性与媒体兼容性过滤。媒体预处理不改变保存的历史或源文件。视频使用[媒体读取](multimedia-understanding.md)，图片限制见[图片输入策略](models.md#image-input-policy)。内容过滤可选，冷启动过滤默认在空闲一小时后启用：

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

内容过滤选择兼容媒体。冷启动过滤缩短旧工具结果文本，保留用户输入、思考、媒体和待处理结果。显式 `ColdStartFilterCapability` 替换默认策略。

图片预处理默认启用。可覆盖所选 Model 的策略或禁用：

```python
from a13n_harness import HarnessModelCharacteristics, ImageInputPolicy

characteristics = HarnessModelCharacteristics(
    image_input=ImageInputPolicy(max_images=10, split_large_images=False),
)
spec = AgentSpec(model_characteristics=characteristics)
without_image_preparation = spec.with_updates(
    model_characteristics=characteristics.model_copy(update={"image_input": None}),
)
```

省略 `image_input` 使用默认策略，`None` 禁用预处理。限制与自定义过滤器见[图片输入策略](models.md#image-input-policy)。
