---
title: 委派与 CodeAct
description: 将任务交给已声明的子 Agent、续接其保存的工作，或用受限 Python 组合允许调用的工具。
---

任务需要另一个拥有独立指令、Model 和工具的 Agent 时，使用委派。当前 Agent 需要用 Python 控制流组合工具结果时，使用 CodeAct。下面的离线示例都经过真实的 Harness 工具调用路径，无需提供方账号。

## 内联委派

内联委派等待已声明的子 Agent 完成，再将结果返回父 Model。Harness 负责这段嵌套执行，无需调度器。任务需要在父 Run 关闭后继续时，使用[异步子级](#asynchronous-children)。

### 声明子级结构

使用[快速开始](getting-started.md)的 Python 环境，将下面的完整程序保存为 `delegation_demo.py`。确定性父 Model 先调用 `delegate`，保存 State，再在新 Run 中调用 `resume_subagent`。子 Model 计算自身对话轮数，便于观察续接效果。

```python title="delegation_demo.py"
import asyncio
import json
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentDefinition, AgentSpec, HarnessBuilder, HarnessState, SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel


async def child_reply(
    messages: list[ModelMessage], info: AgentInfo,
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"reviewer-turn-{turn}"


def coordinator_model(resume: bool) -> FunctionModel:
    sent = False

    async def respond(
        messages: list[ModelMessage], info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal sent
        del info
        returns = [
            part for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, ToolReturnPart)
            and part.tool_name in {"delegate", "resume_subagent"}
        ]
        if not sent:
            sent = True
            if resume:
                previous = returns[-1].content
                assert isinstance(previous, dict)
                name = "resume_subagent"
                arguments = {
                    "execution_id": previous["execution_id"],
                    "prompt": "Check the revised plan.",
                }
            else:
                name = "delegate"
                arguments = {"subagent": "reviewer", "prompt": "Review this plan."}
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments))}
        else:
            result = returns[-1].content
            assert isinstance(result, dict)
            yield result["output"]

    return FunctionModel(stream_function=respond)


def build_parent(resume: bool):
    child = AgentDefinition(
        agent=AgentSpec(), output_type=str,
        definition_id="tutorial-reviewer-v1",
        model=FunctionModel(stream_function=child_reply),
    )
    return HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(), output_type=str,
            definition_id="tutorial-coordinator-v1",
            model=coordinator_model(resume),
            capabilities=(SubagentCapability(),),
            subagents=(SubagentDefinition(
                name="reviewer", description="Review one bounded plan.", agent=child,
            ),),
        )
    )


async def main() -> None:
    first = await build_parent(False).run("Review the plan")
    assert first.output_or_raise() == "reviewer-turn-1"
    assert first.state is not None
    print(first.output_or_raise())
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second = await build_parent(True).run("Review the revision", previous_state=restored)
    assert second.output_or_raise() == "reviewer-turn-2"
    assert first.thread_id == second.thread_id and first.run_id != second.run_id
    print(second.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
uv run python delegation_demo.py
```

预期输出：

```text
reviewer-turn-1
reviewer-turn-2
```

从子级向外理解这段构建代码：

1. `child` 是完整的 `AgentDefinition`，包含自身 Model 和业务 `output_type`。
2. `SubagentDefinition` 为子级指定父级名单中的名称和描述。
3. `SubagentCapability()` 提供内联 `delegate` 和 `resume_subagent` 工具。
4. 父 Model 选择子级并给出范围明确的任务。示例用脚本固定选择；实际提供方 Model 会根据指令和可用工具决定。

重建兼容的已保存对话时，保持定义 ID 稳定。构建会检查子级图有限且无环，同级名称唯一。每个构建后的子级都是不可变、可复用的 `ExecutableAgent`，包含在父级中。

### 内联执行与续接

`delegate(subagent, prompt)` 开始新的嵌套对话，返回包含 `execution_id`、`subagent` 和 `output` 的有限结果。用该确切 ID 调用 `resume_subagent(execution_id, prompt)` 继续同一个子级。再次调用 `delegate` 会创建另一段子级对话。

示例把父级完整 State 序列化为 JSON，重建执行对象后再续接。State 中包含嵌套子级的续接数据。需要跨进程保留时，按[状态与恢复](state-and-resume.md)中的应用检查点流程保存 JSON。

内联调用时，Harness 派生新的子实例关联，应用 `SubagentDefinition.context`，并通过 `ExecutableAgent.stream()` 执行子级。子事件保留其 Run/Thread 归属。Harness 先保存完整子检查点，再向父级返回成功。

子级借用父级已经进入的 Environment 映射，不重新进入或关闭 adapter，不单独发布其状态，也不能修改挂载或默认值。继承的调用策略继续生效。子声明可提供可信的 `run_bindings_factory` 来补充新绑定，同时保留必需的继承身份、Environment、配置和策略。资源归属见 [Environments](environments.md)。

为子级单独设置限制。每个子 Run 使用自身定义限制与 `SubagentDefinition.usage_limits` 逐字段的最严格交集。子级拥有独立用量累加器，不受父级预算约束。异步 Host 可进一步收紧上限。展示用的用量汇总与强制限制分别处理。

Harness `0.6` 改变了保留内联子级的状态格式。含有内联子级的旧版状态在续接或分叉时会以 `capability_state_version_unsupported` 失败；为这些对话新建 Thread。不含内联子级的状态不受影响，包括仅使用异步子级的 Host 状态。

### 异步子级

子任务需要由 Host 管理生命周期时，选择异步模式。提供 `SubagentOperator` 实现；下面的 helper 只负责把已有 operator 接入 Capability：

```python
from a13n_harness.capabilities import SubagentCapability, SubagentOperator


def async_children(operator: SubagentOperator) -> SubagentCapability:
    return SubagentCapability(async_enabled=True, operator=operator)
```

在父定义中用返回的 Capability 替换 `SubagentCapability()`。Agent 使用此模式前，operator 必须实现以下六项操作：

| 模型工具                                | 返回结果的含义                         |
| --------------------------------------- | -------------------------------------- |
| `delegate(subagent_name, prompt)`       | Host 已受理，不代表子级完成            |
| `subagent_info(...)`                    | Host 执行记录的当前有限视图            |
| `wait_subagent(...)`                    | 一次有限等待或汇总查询；超时不取消任务 |
| `steer_subagent(execution_id, message)` | 引导请求的确认回执                     |
| `cancel_subagent(execution_id)`         | 取消请求的确认回执，不证明已清理       |
| `resume_subagent(execution_id, prompt)` | 为兼容且可续接的子级受理一次关联续接   |

受理前，Harness 解析已声明子级、派生 Identity、上下文策略、用量上限和独立的父级关联。存在原工具调用 ID 和组装名称时，也会一并提供。Host 可利用这些关联自行定义幂等身份和重放范围。

`subagent_info` 和 `wait_subagent` 每次都查询 operator。父 Run 或 Environment 关闭不会取消已受理的异步工作，也不会关闭 operator。要判断结果，查询 Host 当前执行记录。

### 明确的职责边界

Host 在 operator 后实现异步生命周期：

1. 创建子 Thread 并构造新的 `RunBindings`，包括子级的 Environment 关联。
2. 调用确切的子执行对象，保留检查点和观测活动。
3. 路由反馈、引导、取消、完成通知和续接。
4. 处理执行丢失、保留期限、清理和应用关闭。

持久化可重建的定义引用和独立关联信息。在选定 worker 上构造新的子级权限，不把活动父 `AgentContext` 或已进入的 Environment 当作持久执行状态。内联执行由 Harness 内置执行器负责；异步调度和存储由应用负责。

### 子 Run 的延迟工具

根据子级的反馈生命周期选择延迟支持。根级和 Host 管理的子级使用新绑定中的 `RunBindings.deferred_tools_supported`，默认 `True`。内置内联子级始终关闭该功能。

没有子级反馈生命周期的 Host 应将此标志设为 `False`。声明式延迟工具及其第一方指引会被省略。运行时 `CallDeferred` 或 `ApprovalRequired` 在当前 Model 循环中转为 `ToolDenied("Deferred tool interaction is unavailable for this Run.")`。自定义 handler 不能覆盖该设置；意外的终态延迟会以 `deferred_tools_unsupported` 失败。Harness UI 为异步子级选择不支持模式，同时保留普通提示续接。

#### Host 管理的反馈

内联 `resume_subagent` 用新提示继续对话，不能提交延迟反馈，也不代子级挂起父级；子绑定工厂不能开启这项支持。

支持反馈的 Host 管理子级采用与根级相同的[结构化挂起流程](state-and-resume.md#structured-suspension)：

1. 将确切的 `HarnessRunResult.deferred` 请求与 `result.state` 一起保存。
2. 按目标调用 ID 收集经过身份验证的反馈。
3. 用新绑定、`previous_state` 和 `DeferredToolResume` 直接调用子级的 `run()` 或 `stream()`。
4. 按 Host 生命周期发布选定检查点并交付结果。

这次续接无需父 Model 调用。观测事件用于了解进展，实际续接使用检查点和待处理请求。只声明子级的业务 `output_type`，可使用结构化模型。Harness 自行加入延迟支持，并拒绝把保留类型 `DeferredToolRequests` 显式声明为业务输出。挂起结果含 `deferred`，没有业务输出；成功完成后，`output_or_raise()` 返回声明的类型。

## CodeAct

CodeAct 让当前 Agent 用受限 Python 组合允许调用的工具。`run_code` 接收内联源码；`run_program` 通过当前 Environment 读取可复用的 `*.codeact.py` 文件。两者都使用 Monty 受限 Python 运行时，支持系统时间 API，默认时区为 UTC。文件系统、网络、进程、环境变量和凭据访问必须经过注入的工具。

### 声明允许使用的工具

工具所有者通过 `CodeActPolicyToolset` 显式允许 CodeAct 调用。将下面的第二个完整离线示例保存为 `codeact_demo.py`。它计算并保存一个值，序列化 State，再在新构建的执行对象中读取：

```python title="codeact_demo.py"
import asyncio
import json
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    """Return twice the supplied value."""
    return value * 2


math_tools = Capability(
    id="math-tools",
    toolsets=[CodeActPolicyToolset(
        wrapped=FunctionToolset([double], id="math-functions"),
        policy=CodeActToolPolicy(tools={"double": True}),
        reject_unknown_tools=True,
    )],
)


def build_calculator(code: str, expected: int):
    sent = False

    async def respond(
        messages: list[ModelMessage], info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal sent
        if not sent:
            sent = True
            assert "run_code" in {tool.name for tool in info.function_tools}
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}))}
        else:
            returned = [
                part for message in messages if isinstance(message, ModelRequest)
                for part in message.parts if isinstance(part, ToolReturnPart)
                and part.tool_name == "run_code"
            ]
            assert returned[-1].content == expected
            yield str(expected)

    return HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=respond), output_type=str,
        capabilities=(math_tools, CodeActCapability()),
    )


async def main() -> None:
    first_code = '''value = await double(value=4)
await store(key="math.result", value=value)
value'''
    first = await build_calculator(first_code, 8).run("Calculate and save")
    assert first.output_or_raise() == "8" and first.state is not None
    print(first.output_or_raise())

    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second_code = '''saved = await load(key="math.result")
await double(value=saved)'''
    second = await build_calculator(second_code, 16).run(
        "Use the saved result", previous_state=restored,
    )
    assert second.output_or_raise() == "16"
    assert first.thread_id == second.thread_id and first.run_id != second.run_id
    print(second.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

运行 `uv run python codeact_demo.py`，先打印 `8`，再打印 `16`。第一个 runner 实际调用 `double` 和 `store`；新 Run 调用 `load`，将保存的值再乘以二。Model 在返回答案前断言每次原生工具结果。

`CodeActToolPolicy(tools={"double": True})` 表示工具所有者允许受限 Python 调用此函数。`CodeActCapability()` 安装 runner 和显式状态工具。模型可见性、元数据或看似只读的名称都不授予调用资格。当前准备好的工具仍须可用，并通过普通权限与授权检查。

对于已经允许 CodeAct 使用且设置了 `defer_loading=True` 的工具，先在 runner 外发现工具或加载其延迟 Capability。只有相应可用性记录仍在当前上下文中，schema 才会进入 runner 目录；记录移除后需要重新发现或加载。下文的代理 ToolSearch 路径支持在 runner 内发现工具。

### 嵌套派发

在 runner 内，使用关键字参数调用注入工具，并 await 每一次调用。`run_code` 返回最后一个表达式；程序返回 `main` 的返回值。只返回 Agent 需要的结果。一个 Model 响应中，runner 必须是唯一可执行工具调用；多个调用放到它的代码内协调。

嵌套调用经过当前最终 `ToolManager`，保留参数验证、Capability 钩子、受管理策略、凭据、提供方检查、事件和用量。根级 deferred handler 如果能在调用返回前解决请求，可提供嵌套结果。子级拒绝或根请求未解决会使当前 runner 失败，不会留下可续接的解释器栈帧。

源码验证错误、不可用函数、沙箱错误和资源超限返回失败工具结果，让 Agent 修改代码。这些失败不消耗 runner 的 Model 重试预算。外层参数 schema 验证和 runner 隔离违规仍采用各自的正常重试策略。

程序失败时，部分工作可能已经完成。Monty 在执行时解析名称，因此后面的不可用调用可能在前一个工具成功后才失败。嵌套调用开始后，失败元数据会记录副作用不确定性。完成的效果和显式存储写入不会回滚或自动重放；重新运行前先核对结果。

### 通过 ToolSearch 使用大型工具集合

预先列出所有 schema 会让 runner 目录过大时，使用 [ToolSearch 分组](tool-search.md#use-with-codeact)。代理模式下，CodeAct 获得搜索、描述和调用函数。程序可先发现工具，读取目标完整 schema 和指令，再通过已有 manager 调用确切目标。

先在来源工具上发布类型化 CodeAct 策略。代理分组改变发现方式，不改变调用资格或授权。两种 runner 都支持此路径。代理调用即使放在 `asyncio.gather` 中仍顺序执行；普通且相互独立的合资格调用可在配置限制内并发。

### 运行时状态

根据任务选择状态生命周期：

| 状态                       | 生命周期                                                    |
| -------------------------- | ----------------------------------------------------------- |
| 普通 `run_code` 变量和函数 | 当前逻辑 Harness Run；成功执行后可供后续调用复用            |
| `run_program` 变量         | 每次调用的新解释器                                          |
| 用 `store` 保存的值        | 显式 Capability 状态；Host 保存并恢复 State 后可跨 Run 使用 |

`run_code(restart=True)` 在求值前清空内联解释器。执行失败也会清空该解释器。两者都不会删除已经成功的显式 `store` 写入。普通解释器值不进入 `HarnessState`。

要使用可复用程序，将下面的 UTF-8 文件保存到可读的 Environment 挂载中。保留示例中允许调用的 `double` 工具和 CodeAct Capability：

```python title="double.codeact.py"
async def main(inputs):
    result = await double(value=inputs["value"])
    await store(key="math.result", value=result)
    return result
```

文件位于 `/workspace/double.codeact.py` 时，Model 可用以下参数调用 `run_program`：

```json
{"path": "/workspace/double.codeact.py", "inputs": {"value": 6}}
```

结果为 `12`，同时保存到 `math.result`。Host 必须绑定可读取该文件的 Environment source；设置方式见 [Environments](environments.md)。读取程序使用 Host 的 Environment 接口，无需向 Model 暴露文件工具。

每次调用都会重新读取源码，并启动新解释器。文件名必须以 `.codeact.py` 结尾，且恰好定义一个不带装饰器的 `async def main(inputs)`。模块顶层允许支持的导入、声明、文档字符串和无副作用常量。执行代码放在 `main` 内。输入是 JSON 数据，文件路径不增加工具调用资格。

要收紧执行预算，用配置后的实例替换 builder 中的 `CodeActCapability()`：

```python
from a13n_harness.capabilities import CodeActCapability, CodeActConfig

codeact = CodeActCapability(CodeActConfig(
    max_tool_calls=16,
    max_concurrency=4,
    timeout_seconds=60,
    max_state_entries=64,
    max_state_bytes=1024 * 1024,
))
```

每次 runner 的默认限制为 128 次嵌套工具尝试、16 路并发和 300 秒截止时间。父级工具调用限制及提供方上限仍生效。Host 要保留 runner 执行，须持有对应 runtime 和显式状态 owner，见 [Host 嵌入](hosting.md)。回执交付只结束前台等待，不结束 runner 或其中的副作用。

### 显式保存数据，不保存解释器

执行片段或 Run 结束后仍需使用的 JSON 数据，用 `store` 保存。下面是 runner 内的代码，不是普通独立 Python 模块：

```python
await store(key="search.results", value={"ids": [12, 34], "next_page": 3})
```

在后续 runner 中读取：

```python
results = await load(key="search.results")
results["ids"]
```

`load()` 列出全部键；`forget(key="search.results")` 删除一个键并返回它是否存在。没有 message 或 description 参数。缺失键会失败，保存的 null 则加载为 `None`。读取结果是独立副本，修改后需要再次 `store` 才会保存。

上面的完整示例展示了跨 Run 的要求：导出并恢复 `HarnessState`。成功写入可在后续沙箱失败或重置后保留，但 Host 持久化之前发生进程崩溃仍可能丢失。新建子级使用独立状态；分叉 State 是独立副本，不是共享映射。恢复值不恢复工具权限。

Model 收到的只是键目录，最多 32 个键、4 KiB。用 `load()` 列出省略的键。`CodeActConfig.max_state_entries` 默认 256；`max_state_bytes` 默认 10 MiB，限制包含键在内的完整紧凑 JSON 命名空间。键长为 1–256 字符，值必须是有限 JSON。被拒绝的写入保留原值，恢复的状态也必须满足当前限制。

## 如何选择

| 任务                                 | 使用方式                            |
| ------------------------------------ | ----------------------------------- |
| 将明确任务交给另一个 Agent，等待答案 | 内联委派                            |
| 用 Python 循环、条件或并发组合工具   | CodeAct                             |
| 让子工作在父 Run 或进程结束后继续    | Host 管理的异步子级或任务服务       |
| 用常规库执行可信应用 Python          | 普通应用代码                        |
| 运行不可信 shell 命令或原生程序      | 具备隔离能力的 Environment provider |

执行方式确有需要时再加入这些功能。单次操作直接调用工具更简单。
