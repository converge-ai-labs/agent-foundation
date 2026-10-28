---
title: Agent 与执行
description: 构建一次 Agent，执行一轮、检查结果，再流式执行下一轮。
---

为 Agent 的行为和输出契约构建一个 executable。只需要最终结果时调用 `run()`，需要实时事件时调用 `stream()`。每次调用都有新的 Run 上下文；传入上次结果的状态即可继续同一个 Thread。

## 定义与构建

使用[快速开始](getting-started.md)准备的仓库和环境。在仓库根目录创建 `runs.py`，保存以下离线程序：

```python title="runs.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    RunBindings,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turn}"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(system_prompt="Answer concisely."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("Draft a plan")
    print(first.output_or_raise())
    if first.state is None:
        raise RuntimeError("Completed turn has no continuation state")

    async with executable.stream(
        "Review the plan",
        previous_state=first.state,
        bindings=RunBindings.embedded(metadata={"stage": "review"}),
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                print(f"event {item.sequence}")
            elif isinstance(item, HarnessRunResultEvent):
                second = item.result
                print(second.output_or_raise())
                assert second.thread_id == first.thread_id
                assert second.run_id != first.run_id


if __name__ == "__main__":
    asyncio.run(main())
```

运行 `uv run python runs.py`。预期先输出 `turn-1`，接着是事件序号，最后是 `turn-2`。确定性的 Model 统计已有响应；断言检查两次 Run 是否属于同一个 Thread。

构建器将可信 Python 对象组合为可复用 executable。Harness 在原生 Agent 执行外管理状态、资源和事件。构建 executable 本身不会发起 Model 请求。

### 构建时确定的值

每个 executable 构建时确定以下内容：

| 输入                                                 | 放置位置                                 |
| ---------------------------------------------------- | ---------------------------------------- |
| instructions、静态系统提示、Model ID、设置、默认预算 | `AgentSpec`                              |
| Python 输出类型或原生 `OutputSpec`                   | `output_type`                            |
| 代替字符串 ID 的具体 Model                           | `model`，同时保持 `AgentSpec.model=None` |
| 工具和功能行为                                       | `capabilities`                           |
| 可信 Harness 中间件                                  | `plugins`                                |
| 具名子定义                                           | `subagents`                              |
| 可选的 Model 中断重试策略                            | `model_recovery`                         |

输出契约在构建时固定。可以选择 `output_type`，也可以设置 `AgentSpec.output_schema` 并保留 `output_type=None`；同时提供两者会被拒绝。字符串 Model 选择放在 `AgentSpec.model`，具体 Model 放在构建器的 `model` 参数中。这两个来源也只能选一个。

应用需要单独组装定义时，将 `runs.py` 中的构建调用替换为以下等价形式，并添加导入：

```python
from a13n_harness import AgentDefinition

agent_definition = AgentDefinition(
    agent=AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=FunctionModel(stream_function=respond),
)
executable = HarnessBuilder().build(agent_definition)
```

`AgentDefinition` 保存进程内对象。存储 Agent 配置的 Host 在构建前重新创建这些对象。构建器还选择默认开启的成本策略和定价目录，见[用量](#usage)。

## 执行并获取结果

简单应用可使用 `result.output_or_raise()` 检查完成状态并取得带类型输出。支持其他结果的应用应按 `result.status` 分支处理：

| 状态        | 应用操作                       | 常用字段                              |
| ----------- | ------------------------------ | ------------------------------------- |
| `completed` | 接受输出并选择检查点           | `output`, `state`, `usage`            |
| `suspended` | 收集延后结果或审批，再恢复     | `state`, `deferred`, `suspend_reason` |
| `failed`    | 检查失败，决定是否保留候选状态 | `failure`、可选 `state`               |
| `cancelled` | 处理取消，不作为已完成输出     | 可选 `state`                          |

`raise_for_status()` 只检查完成状态，不返回输出。`all_messages()` 返回独立的完整历史，`new_messages()` 返回本次逻辑 Run 新增的消息。应用负责保存选定的检查点，具体流程见[状态与恢复](state-and-resume.md)。

`RunBindings.deferred_tools_supported=True` 时，根执行和 Host 管理的子执行支持延后工具；这是默认值。内置内联子执行关闭这项支持。关闭后，动态延后会在 Model 循环内被拒绝，意外的终结延后则变为 `deferred_tools_unsupported` 失败。暂停与恢复流程见[客户端工具](client-tools.md)。

## 流式事件

示例消费两种公开条目：

- `HarnessEvent` 携带原生 Agent 事件或 Harness 扩展事件。
- `HarnessRunResultEvent` 在所属 Run 资源成功关闭后携带唯一的终结结果。

每个条目都带有根 `thread_id`、`run_id` 和单调递增的 `sequence`。内联子级的观测也可以出现在这个流中，并保留子级关联。将这些观测投影为 AG-UI 事件的方式见 [Stream Protocol](../a13n-stream-protocol/index.md)。

一个流只能进入一次，也只有一个消费者。迭代才启动 Model 执行；只进入再退出不会启动。将整个消费过程放在 `async with` 内，包括可能抛出异常或提前停止的应用回调。

## 每次执行的新绑定

示例第一轮省略绑定，第二轮创建绑定。省略时会创建新的嵌入式绑定；如果也没有 Environment 输入，则使用空 Environment 运行时。

需要当前应用的协作对象或元数据时，使用 `RunBindings.embedded()`。例如，在 `main()` 构建 executable 之后添加：

```python
from a13n_harness import HarnessObservationContext

bindings = RunBindings.embedded(
    metadata={"request_kind": "interactive"},
    observation=HarnessObservationContext(
        name="interactive-agent-run",
        labels=("interactive",),
        metadata={"channel": "web"},
    ),
)
result = await executable.run("Summarize the change", bindings=bindings)
```

每次根执行、恢复执行和子执行都创建新绑定。Host 可使用自己的 `AgentInstanceContext` 直接构建 `RunBindings`。绑定提供当前身份、Environment 权限、Model 解析、调用策略和功能协作对象。元数据与观测标签描述 Run，不授予权限。

功能字段包括 `web`、`document_converter`、`file_media_understanding`、`skill_selection`、`task_state` 和 `client_toolsets`。先在定义中选择对应的功能 Capability，再在这里提供带类型的协作对象或覆盖值。`None` 保留选择默认值；空 Skill 集合或客户端工具元组表示不选择任何项。Run 的 `capabilities` 仅支持规定的调用策略和 MCP 组合，不用于任意功能激活。

Host 管理协作对象的生命周期，包括有意共享的传输。不要把活跃绑定写入续接状态。应用客户端和单次 Run 资源的归属见[工具与依赖](tools-and-dependencies.md)。

## 输入

将字符串或其他支持的原生输入直接传给 `run()` 或 `stream()`。如果生成输入需要当前已进入的 Environment，使用 `input_factory`。在 `runs.py` 中将第一次 `run()` 调用替换为：

```python
async def make_input(preparation):
    return f"Describe the task for Run {preparation.run_id}"

first = await executable.run(input_factory=make_input)
```

`input` 和 `input_factory` 互斥。工厂在初始 Environment 注册之后、Model 执行之前运行，随后插件收到规范化的语义输入。目标激活仍可能延迟进行。Host 通过新绑定提供权威 provider 状态，不在进入后根据保存的观测恢复 provider。

## 活跃流操作

可信应用代码可在已进入的流上使用以下操作：

| 操作                                       | 用途                                      |
| ------------------------------------------ | ----------------------------------------- |
| `stream.context`                           | 访问新的 `AgentContext`                   |
| `stream.usage`                             | 读取当前本地用量的独立摘要                |
| `await stream.export_state()`              | 获取最新安全可移植检查点候选              |
| `await stream.steer(input, input_id=None)` | 内部 Model Run 活跃时，将非空用户内容入队 |
| `stream.cancel()`                          | 请求语义取消                              |
| `stream.result`                            | 终结事件交付后读取结果                    |

`steer()` 返回入队 ID，使用原生活跃执行的 `priority="asap"` 队列。传入的 `input_id` 会在交付时记录；`a13n_harness.capabilities.steering` 中的 `steering_input_ids(state.message_history)` 可在导出历史中找到已交付 ID。入队回执本身不证明持久交付。

自动压缩保留已接受的初始输入和引导输入用于重放，包括结构化和多模态内容。这优先保留用户上下文，而非保证精确一次重放。处理中断的延后输入时，将检查点与 `stream.pending_deferred_input` 配对，见[状态与恢复](state-and-resume.md#recover-an-interrupted-deferred-resume)。

## 配置定义

以下片段在构建前调整定义。将其放到 `runs.py` 的构建部分，或应用自己的定义工厂中。

### 调整已加载的预设

在仓库根目录创建 `research-agent.yaml`：

```yaml title="research-agent.yaml"
instructions: Answer with concise findings.
toolset_instructions: true
```

加载预设，再创建本地变体：

```python
preset = AgentSpec.from_file("research-agent.yaml")
local = preset.with_updates(
    model_settings={"temperature": 0.1, "max_tokens": 8_000},
    toolset_instructions=False,
)
```

将构建器中的 `AgentSpec(...)` 替换为 `local`。`with_updates()` 验证独立深拷贝，并替换完整顶层字段。需要合并嵌套设置时，显式构造合并后的字段。未知字段、重复别名/名称更新和无效值会立即失败。可选位置映射支持 `model_characteristics`、`$schema` 等序列化别名，关键字参数使用 Python 字段名。

### 系统提示与 instructions

使用 `system_prompt` 保存定义管理的静态块。列表保留顺序；`None` 或空列表不提供块：

```python
spec = AgentSpec(
    system_prompt=[
        "You are the support Agent.",
        "Answer with verified account information only.",
    ],
    instructions="Follow the application policy.",
    toolset_instructions=False,
)
```

后续 Run 恢复保存历史时，Harness 用当前定义的块替换历史系统提示部分。移除提示也会移除旧块。provider 暂停的响应仍属于进行中的原生请求，应使用兼容定义恢复。

`instructions` 保留逐请求生命周期。Capability 指导也仍属于 instructions。Toolset 默认会为当前活跃工具提供指导；`toolset_instructions=False` 只隐藏 Toolset 管理的指令块。工具 schema、可用性、显式 Agent instructions 和 Capability 指导均保持不变。

通过 `RunBindings.embedded(toolset_instructions=True)` 为单次 Run 覆盖该开关。绑定默认值 `None` 继承定义设置。

### 用量限制与重试

在定义上设置常规工作负载预算：

```python
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

Run 的 `usage_limits=UsageLimits(...)` 替换整个定义值，省略则使用定义预算。Harness 默认每个逻辑 Run 最多发起 1,000 次 Model 请求，token、工具调用和成本限制不设置。使用 `UsageLimits(request_limit=None)` 可显式移除请求数上限。原生 Pydantic AI spec 也获得相同的 Harness 默认值。

内联子级将自身定义和父子边上的限制逐字段取最严格值，并独立累积用量。父级限制不是整棵树的总预算。

区分各类重试机制：

| 设置                               | 默认值     | 适用范围                    |
| ---------------------------------- | ---------- | --------------------------- |
| `AgentSpec.retries` 的 tools 类别  | 5 次重试   | 函数工具失败后的 Model 纠正 |
| `AgentSpec.retries` 的 output 类别 | 3 次重试   | 输出验证失败后的 Model 纠正 |
| `ModelRecoveryPolicy`              | 关闭       | 已识别的临时 Model 请求中断 |
| provider/客户端重试设置            | 由集成决定 | 传输请求                    |

`AgentSpec.retries` 为整数时同时设置两个类别，包括零。映射只替换给出的类别，省略的类别保留 Harness 默认值。显式 Tool 或 Toolset 重试限制优先。五次工具重试允许五次纠正，第六次失败耗尽预算。开启 Model 中断恢复前，先阅读[恢复层次](#recovery-layers)。

### 冷启动保留

距离最新 Model 响应至少一小时后，默认冷启动过滤器会缩短已消费工具结果中的过大字符串，保留待处理结果、用户输入和思考内容。这会减少旧历史，同时改变缓存前缀；它不检测 provider 缓存是否过期。

```python
from a13n_harness.filters import ColdStartFilterConfiguration

spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
disabled = spec.with_updates(cold_start_filter=None)
```

对已发布 Skill 目录内文件的成功直接 `view` 结果会获得标记，免于这种裁剪。标记在保存历史恢复后仍有效。初始输出限制、后续压缩或交接仍适用，带标记的结果也可能不完整。旧的未标记结果和 CodeAct 合并输出按普通规则裁剪。

原生 spec 使用相同默认过滤器。`None` 关闭自动安装，保留显式组合的 `ColdStartFilterCapability` 及其策略。

### 模型选择

通过[模型选择](models.md#model-selection)选择字符串 Model ID 或具体 provider Model。

### 模型编写别名

通过[模型编写别名](models.md#model-authoring-aliases)解析便于应用使用的名称。

### 模型特性

通过[模型特性](models.md#model-characteristics)提供上下文窗口和媒体能力信息。

### 自动模型请求亲和性

Thread 派生的 provider 关联方式见[模型请求亲和性](models.md#model-request-affinity)。

## 恢复层次

先实现正常状态处理，再为具体失败类型添加对应的自动恢复：

| 失败                            | 负责方                     |
| ------------------------------- | -------------------------- |
| provider 传输失败               | provider/客户端重试配置    |
| 已识别的 provider 历史不兼容    | 默认开启的自修复           |
| 活跃 Run 内的临时 Model 中断    | 可选 `ModelRecoveryPolicy` |
| worker 丢失、持久重放、输出交付 | 嵌入 Host                  |

自修复在重试前修复一次已识别的过期 provider item ID、无效思考历史和过大内联媒体。它作用于最终有效 Model，包括 Run 时解析的 Model、内联子级和同一 Agent 的压缩。可从 `a13n_harness.models` 选择 `SelfHealingModelCapability(rules=...)` 替换规则。`HarnessBuilder(self_healing_enabled=False)` 关闭自动安装，但保留显式选择的自修复 Capability。

要开启有界临时恢复，在 `runs.py` 的构建调用中加入以下导入和参数：

```python
from a13n_harness import ModelRecoveryPolicy

executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    model_recovery=ModelRecoveryPolicy(enabled=True, max_attempts=3),
)
```

这里每轮连续失败允许三次尝试，含首次。完整且已接受的主响应会重置计数和退避，包括工具调用响应。部分输出、压缩等辅助请求不会重置。永久或未知错误立即停止。用量限制用于约束整个 Run；开启策略但未另设尝试次数时，默认每轮连续失败最多尝试五次。

中断尝试将已发出的文本保留为中断历史，移除未完成思考、工具参数和无效 provider 原生调用/返回组，同时保留周围可恢复的文本。失败或取消的 Run 可导出这份过滤历史，由 Host 决定是否继续。重复结果不确定的外部修改前，先核对其结果；恢复不保证外部副作用精确一次。

## 必需组合

构建器安装工具执行边界、消息完整性过滤、Model 上下文协调、Model ID 解析、Thread 亲和性、请求生命周期事件和用量报告。应用添加可选 Capability 和过滤器，无需重复安装这些必需组件。

## 用量

用量记录、provider 归因、目录更新和自定义估值见[用量、限制与定价](usage-and-limits.md)。成本策略在构建时选择，也可以传入 `pricing_catalog` 参数。

## 关联标识

`thread_id` 关联一段独立推进的历史，`run_id` 标识一次逻辑执行。恢复保留 Thread ID 并创建新的 Run ID，`HarnessState.fork()` 则创建另一个 Thread。内部尝试和内联子级保留各自更窄的关联。ID 用于路由和观测，不授予权限。

## 清理

`ExecutableAgent` 是可复用的构建结果，不拥有已进入资源的生命周期，也没有 `close()` 方法。`run()` 消费并关闭内部事件流。使用 `stream()` 时，调用者管理 `async with` 范围；提前退出、异常和取消仍会触发清理。

正常终结事件确认 Harness 管理的 Run 清理已完成。Host 另行决定何时保存状态、提交持久完成状态和交付输出。应用侧边界见[嵌入 Host](hosting.md)。
