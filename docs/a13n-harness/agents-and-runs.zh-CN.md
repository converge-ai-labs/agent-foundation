---
title: Agent 与执行
description: 一次构建可复用的执行对象，再用新的绑定运行或流式处理每次逻辑执行。
---

Agent Harness 通过代码在当前进程中构建 agent。它在 Pydantic AI 之外提供可复用的构建边界和统一的逻辑执行边界，不创建第二个 agent 循环或序列化的 agent 定义语言。

## 定义与构建

直接组合应用时，使用 `HarnessBuilder.build()`：

```python
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
)
from a13n_harness.models import SelfHealingModelCapability

executable = HarnessBuilder().build(
    AgentSpec(
        system_prompt="Answer concisely.",
        model_characteristics=HarnessModelCharacteristics(context_window_tokens=200_000),
    ),
    output_type=str,
    model=model,
    capabilities=(SelfHealingModelCapability(), *capabilities),
    plugins=plugins,
    subagents=subagents,
)
```

定义需要单独组装或保留时，使用显式 `AgentDefinition`：

```python
from a13n_harness import (
    AgentDefinition,
    HarnessBuilder,
)

agent_definition = AgentDefinition(
    agent=AgentSpec(),
    output_type=str,
    model=model,
    capabilities=capabilities,
)
executable = HarnessBuilder().build(agent_definition)
```

两个重载走相同的验证和构建路径。构建同步执行，不对模型、环境或外部 provider 进行 I/O。自修复可选，不会隐式启用；生产 agent 需要已知的一次性 provider 历史修复时，建议选择 `SelfHealingModelCapability()`。

### 构建时确定的值

`AgentDefinition` 固定以下内容：

- Harness `AgentSpec`，仍是原生 Pydantic AI spec，可添加有序静态系统提示、定义级用量限制和已解析模型特性；
- 一个输出契约；
- 一个字符串或具体模型选择；
- 定义选择的 Capabilities；
- 可信 Harness 中间件插件；
- 有限的内联子定义；
- 自修复与有界模型恢复策略；
- 一个构建时默认启用的模型成本策略。

输出契约不能逐次执行改变。通过 `output_type` 传入 Python 输出类型或 Pydantic AI `OutputSpec`，或使用 `AgentSpec.output_schema`；不要同时设置两者。

### 调整已加载的预设

使用 `with_updates()` 创建经过验证的本地变体，不修改加载的预设：

```python
preset = AgentSpec.from_file("research-agent.yaml")
local = preset.with_updates(
    model="anthropic:claude-sonnet-4-6",
    model_settings={"temperature": 0.1, "max_tokens": 8_000},
    toolset_instructions=False,
)
```

可选位置映射支持动态字段和 `model_characteristics`、`$schema` 等序列化别名。关键字覆盖使用普通 Python 字段名。未知字段、重复别名/名称更新和无效值会立即失败。更新替换完整顶层字段，不递归合并嵌套 provider 设置、元数据、schema 或 Capability 参数；需要合并时，显式构建合并后的字段。

这发生在 `HarnessBuilder.build()` 之前，返回独立深拷贝。它不是 Pydantic AI 已构建 agent 提供的临时执行范围上下文管理器。

### 冷启动保留

Harness `AgentSpec.cold_start_filter` 默认空闲间隔为一小时。最新模型响应至少已过去这么久时，过滤器缩短已消费工具结果中的过大字符串。待处理结果、用户输入和思考内容保留。它有意牺牲旧缓存前缀，减小冷请求；不会检测或适应 provider 缓存保留期。

```python
from a13n_harness.filters import ColdStartFilterConfiguration

spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
disabled = spec.with_updates(cold_start_filter=None)
```

对已发布 skill 目录中文件的成功直接 `view` 结果，会在读取时标记并免于冷启动裁剪，不受扩展名影响。标记在保存历史恢复后保留；不会绕过初始输出限制，不证明文件已完整读取，也不阻止压缩或交接替换历史。旧的未标记结果仍按普通规则裁剪，CodeAct 也不会把内部读取的豁免转移到合并输出。

普通 Pydantic AI spec 使用相同默认值。显式组合的 `ColdStartFilterCapability` 保留自身策略；`None` 禁用自动安装，不移除已编写的 Capability。

### 用量限制与重试

Harness `AgentSpec.usage_limits` 是 Pydantic AI 原生 `UsageLimits`。默认允许一次逻辑执行最多 1,000 次模型请求，token、工具调用和成本限制均不设置：

```python
from a13n_harness import AgentSpec
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(
        request_limit=300,
        total_tokens_limit=500_000,
        cost_limit="25.00",
    ),
)
```

普通 Pydantic AI `AgentSpec` 也使用相同的 1,000 请求 Harness 默认值。显式移除请求数上限时，使用 `UsageLimits(request_limit=None)`；不给 `run()` 传 `usage_limits` 表示“使用定义值”，不是“禁用限制”。

单次执行可以完整替换定义值：

```python
result = await executable.run(
    "Complete the bounded analysis",
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
)
```

覆盖不逐字段合并。稳定工作负载预算放在 `AgentSpec`；调用参数用于更严格或有意不同的单次预算。内联子 agent 对自身定义和声明的委派边界逐字段取最严格值，并独立累积用量。父限制不施加整棵树的总上限。

`AgentSpec.retries` 仍是原生 Pydantic AI 设置：

```python
spec = AgentSpec(retries={"tools": 2, "output": 1})
```

省略时，Pydantic AI 允许一次函数工具重试和一次输出验证重试。整数同时设置两者；映射分别配置。它不配置 provider 传输重试，也不启用 Harness 模型中断恢复。`ModelRecoveryPolicy` 默认禁用，启用后有独立的有界连续失败预算。完整、已接受的主模型响应重置恢复计数和退避，包括工具调用响应；部分输出和压缩等辅助请求不会重置。恢复只重试已识别的临时模型请求失败。永久和未知失败立即停止，不消耗剩余重试次数。默认每轮连续失败最多五次尝试（含首次），不是整个执行最多五次。使用 `UsageLimits` 限制总体执行用量。

### 系统提示与 instructions

使用 Harness `AgentSpec.system_prompt` 保存定义管理的静态系统提示。字符串创建一个块，列表保留块顺序，`None` 或空列表不提供块：

```python
spec = AgentSpec(
    system_prompt=[
        "You are the support Agent.",
        "Answer with verified account information only.",
    ],
)
```

提示在一个已构建定义内固定。后续定义从非空 `HarnessState` 恢复时，Harness 移除历史 `SystemPromptPart`，把当前有序块放到首次请求开头。新定义移除提示时，相应历史部分也被移除。provider 暂停的响应仍是进行中的原生请求，不会改写；请用兼容定义恢复。

`AgentSpec.instructions` 仍是原生 Pydantic AI 指令通道。静态和动态指令保留逐请求生命周期，不会合并到 `system_prompt`，也不会被系统提示协调替换。Capability 和 Toolset 的指导也仍属于 instructions。

Toolset 只为当前开放的工具提供用法指导。Harness `AgentSpec.toolset_instructions` 默认为 `True`；设为 `False` 可在该定义全部执行中隐藏 Toolset 指令块：

```python
spec = AgentSpec(
    instructions="Follow the application policy.",
    toolset_instructions=False,
)
```

通过 `RunBindings.toolset_instructions` 为单次逻辑执行覆盖默认值：

```python
bindings = RunBindings.embedded(toolset_instructions=True)
result = await executable.run("Inspect the workspace", bindings=bindings)
```

`None` 继承 agent 默认值。该开关不隐藏显式 `AgentSpec.instructions`、Capability 功能指导、工具 schema 或工具可用性。

### 模型选择

参见[模型与身份验证](models.md#model-selection)。

### 模型编写别名

参见[模型与身份验证](models.md#model-authoring-aliases)。

### 模型特性

参见[模型与身份验证](models.md#model-characteristics)。

### 自动模型请求亲和性

参见[模型与身份验证](models.md#automatic-model-request-affinity)。

## 必需组合

每个执行对象都接收一份 Harness 管理的必需边界：

- 函数工具执行边界；
- 消息完整性过滤；
- 模型上下文协调；
- 模型 ID 解析；
- 从线程派生的模型请求亲和性；
- 模型请求生命周期事件；
- 用量归因与报告。

应用不能重复添加必需边界。可选请求/历史过滤器和功能 Capability 仍由定义显式选择。

## 每次执行的新绑定

`RunBindings` 携带当前可信执行输入：

```python
from a13n_harness import (
    HarnessObservationContext,
    RunBindings,
)

bindings = RunBindings.embedded(
    environment=environment_binding,
    model_resolver=model_resolver,
    model_context=model_context_binding,
    capabilities=run_capabilities,  # Invocation policy and/or MCP only.
    web=web_binding,  # WebBinding; requires a selected WebCapability.
    skill_selection=frozenset({"code-review"}),
    metadata={"request_kind": "interactive"},
    observation=HarnessObservationContext(
        name="interactive-agent-run",
        labels=("interactive",),
        metadata={"channel": "web"},
    ),
)
```

`RunBindings.embedded()` 提供嵌入式身份和可选高级集成。嵌入应用需要执行 Capability、模型解析器、模型上下文中间件、元数据或高级 `EnvironmentRuntime` 时使用。普通 `run()` 和 `stream()` 可省略 `bindings`；执行规范化会创建新嵌入式绑定，未提供环境输入时创建空环境运行时。Host 也可用精确 `AgentInstanceContext` 直接构建 `RunBindings`。

每次根执行、恢复执行或子执行都创建新绑定。不要将活跃绑定持久保存或复用为续接状态。可选功能 provider 和覆盖使用 `web`、`media_reader`、`document_converter`、`file_media_understanding`、`skill_selection`、`task_state` 和 `client_toolsets`；每个字段由对应功能 Capability 消费，不另设配套执行 Capability。选择字段保留 `None` 可使用默认值；显式空 skill 集合或客户端工具元组表示不选择任何项。Host 负责 provider 生命周期，包括有意共享的传输。

| 稳定定义输入            | 每次执行的新输入             |
| ----------------------- | ---------------------------- |
| `AgentSpec`             | 身份和 agent 实例上下文      |
| 输出契约                | 环境运行时                   |
| agent 行为 Capabilities | 模型解析器和模型上下文绑定   |
| 直接插件                | 策略与 provider 协作对象     |
| 子 agent 拓扑           | 带类型的功能覆盖和当前策略   |
| 恢复策略                | 有界非权威元数据和观测上下文 |

## 输入

直接传入一个原生输入值：

```python
result = await executable.run(
    "Summarize the change",
    bindings=bindings,
)
```

也可以在当前环境聚合进入后、模型执行前生成语义输入。Host 构建新适配器时提供权威 provider 状态；Harness 不会在进入后从可移植观测恢复 provider，目标准备仍可能延迟进行：

```python
async def make_input(preparation):
    return f"Run {preparation.run_id} in the current workspace"

result = await executable.run(
    input_factory=make_input,
    bindings=bindings,
    previous_state=state,
)
```

`input` 和 `input_factory` 互斥。工厂完成后，插件接收规范化的语义输入。

## 执行并获取结果

`run()` 消费统一事件流，返回唯一终结结果：

```python
result = await executable.run("Do the work", bindings=bindings)
output = result.output_or_raise()
```

结果只有一个状态：

| 状态        | 含义                                 | 主要字段                              |
| ----------- | ------------------------------------ | ------------------------------------- |
| `completed` | 已验证业务输出完成，且清理成功       | `output`, `state`, `usage`            |
| `suspended` | 支持的原生延后工具或批准需要后续输入 | `state`, `deferred`, `suspend_reason` |
| `failed`    | 逻辑执行以安全规范化失败结束         | `failure`、可选安全 `state` 候选      |
| `cancelled` | 取消停止了逻辑执行                   | 无业务输出                            |

只接受完成时，使用 `raise_for_status()`。要验证状态并返回带类型输出，使用 `output_or_raise()`。应用显式处理其他结果时，检查 `status`、`failure` 或 `deferred`。新 `RunBindings.deferred_tools_supported` 启用时（默认），根和子执行都可返回 `suspended`。禁用时，在同一模型循环中拒绝动态延后；意外终结延后变为 `failed`，代码为 `deferred_tools_unsupported`。

`all_messages()` 返回结果代表的完整独立消息历史。`new_messages()` 只返回该次逻辑执行新增的消息。

## 流式事件

`stream()` 延迟启动，只能进入一次，并只有一个消费者：

```python
from a13n_harness import (
    HarnessEvent,
    HarnessRunResultEvent,
)

async with executable.stream("Do the work", bindings=bindings) as stream:
    async for item in stream:
        if isinstance(item, HarnessEvent):
            consume_observation(item)
        elif isinstance(item, HarnessRunResultEvent):
            result = item.result
```

公开流联合类型包含：

- `HarnessEvent`，封装原生 Pydantic AI `AgentStreamEvent` 或有界 `HarnessExtensionEvent`；
- 一个终结 `HarnessRunResultEvent`，只在所属执行资源成功关闭后发出。

同一次逻辑执行的每个条目携带相同 `thread_id`、`run_id` 和单调递增 `sequence`。内联子观测也可能出现在父流中，保留子执行关联。

进入并退出流而不迭代，不会启动模型执行。迭代启动统一事件路径。

## 活跃流操作

进入流后：

- `stream.context` 向可信嵌入代码提供新的 `AgentContext`；
- `stream.usage` 返回当前本地 Context 用量的独立 `RunUsageSummary`，不是可变原生累加器；
- `await stream.export_state()` 返回最新安全可移植状态边界；
- `await stream.steer(input, input_id=None)` 通过 Pydantic AI 活跃执行的 `priority="asap"` 队列交付非空原生用户内容，并返回入队 ID；Host 选择的 `input_id` 记录在交付请求上，`a13n_harness.capabilities.steering` 中的 `steering_input_ids(state.message_history)` 列出导出状态中的 ID；
- `stream.cancel()` 请求语义取消；
- `stream.result` 仅在终结结果事件交付后可用。

`steer()` 只在内部 Pydantic 执行活跃时可用。配置自动压缩时，Harness 还保留已接受的初始和 steering 输入，供后续压缩重放，保留结构化和多模态内容。这优先保留用户上下文，不保证精确一次重放；不是持久命令或回执协议。

始终将流作为异步上下文管理器使用。消费者提前退出、异常、任务取消或显式取消请求仍会触发 Harness 清理。

## 恢复层次

恢复由各自明确的负责方处理：

| 失败类别                         | 负责方                                                  |
| -------------------------------- | ------------------------------------------------------- |
| provider 传输重试                | 模型 provider/客户端及原生 Pydantic AI 重试配置         |
| 精确的 provider 历史不兼容       | 所选 `SelfHealingModelCapability` 和 `SelfHealingModel` |
| 同一活跃逻辑执行内的模型尝试中断 | `ModelRecoveryPolicy` 和 `HarnessRunStream`             |
| worker/进程丢失、持久重放或交付  | 嵌入 Host                                               |

自修复通过 `SelfHealingModelCapability` 主动启用，只围绕最终生效模型执行支持的一次性历史修复，包括具体模型、执行时解析模型或原生推断模型。它不重试任意模型或工具异常。语义模型恢复默认禁用；应用允许继续已中断模型尝试时，在定义上选择有界 `ModelRecoveryPolicy`。

中断尝试保留已发出文本，即使后续工具调用中流才停止。下一次尝试将该部分响应作为中断历史接收，不视为已完成输出。未完成思考和工具参数会排除；无效 provider 原生调用/返回组会移除，不抹掉周围可恢复文本。失败或取消的执行导出相同过滤历史，供 Host 后续选择续接。

恢复不会让结果不确定的外部副作用变为精确一次。工具或 provider 修改可能已分派但缺少权威结果时，先核对当前 provider 状态再重试。

## 用量

原生用量、provider 归因、目录更新和自定义估值见[用量、限制与定价](usage-and-limits.md)。

## 关联标识

- `thread_id` 标识一段独立推进的历史。恢复保留它；`HarnessState.fork()` 创建另一段。
- `run_id` 标识一次进程内逻辑执行。每个新根执行、恢复或替代执行都获取新值。
- 内部模型尝试和内联子执行具有更窄的独立关联，不替代线程或根执行身份。

标识符用于观测和路由，不授予权限。

## 清理

`ExecutableAgent` 是不可变、可复用的构建结果，不管理任何已进入的模型、插件、Capability、客户端或子资源，因此没有 `close()` 或异步上下文生命周期。`run()` 在内部为一个 `HarnessRunStream` 限定范围并关闭。使用 `stream()` 的调用者必须通过 `async with` 进入；提前退出时就会确定地关闭执行临时资源。

正常 `HarnessRunResultEvent` 表示 Harness 管理的执行清理已完成。它仍只是进程内候选结果；Host 决定何时持久保存检查点、提交持久完成状态或交付外部输出。
