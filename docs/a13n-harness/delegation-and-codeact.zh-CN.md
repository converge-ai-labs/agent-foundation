---
title: 委派与 CodeAct
description: 把任务交给另一个 Agent，或用受限 Python 在当前 Agent 中组合工具。
---

委派把任务交给另一个拥有独立指令、Model 和工具的 Agent。CodeAct 让当前 Agent 用 Python 循环、条件和并发组合工具调用。

## 如何选择

| 任务                                          | 使用方式                                  |
| --------------------------------------------- | ----------------------------------------- |
| 执行单次操作                                  | 直接调用工具                              |
| 请另一个 Agent 审查、研究或解决范围明确的任务 | [内联委派](#inline-delegation)            |
| 用 Python 控制流组合工具结果                  | [CodeAct](#codeact)                       |
| 让子任务在父 Run 关闭后继续执行               | [异步子级](#asynchronous-children)        |
| 用已安装的库运行应用 Python，或执行原生命令   | 应用代码或 [Environment](environments.md) |

下面的示例沿用[快速开始中的 Model 设置](getting-started.md#use-a-real-model)。运行前设置 `HARNESS_MODEL` 和提供方凭据。

## 内联委派

内联委派等待已声明的子 Agent 完成，再把结果返回父 Model。Harness 负责执行子级，应用无需实现调度器。

### 声明子级

为子级提供完整的 `AgentDefinition`，再用一个名称将它注册到父级。`SubagentCapability()` 向父 Model 提供委派工具。

```python title="delegation_demo.py"
import asyncio
import os

from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability

reviewer = AgentDefinition(
    agent=AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Review the supplied plan. Identify concrete risks and missing steps.",
    ),
    output_type=str,
    definition_id="plan-reviewer-v1",
)
parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="Delegate plan reviews to reviewer, then summarize its findings.",
        ),
        output_type=str,
        definition_id="coordinator-v1",
        capabilities=(SubagentCapability(),),
        subagents=(SubagentDefinition(
            name="reviewer", description="Review a plan for concrete risks.", agent=reviewer,
        ),),
    )
)


async def main() -> None:
    result = await parent.run("Review this plan: deploy the update, then run the tests.")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

运行 `uv run python delegation_demo.py`。父级可调用 `delegate(subagent="reviewer", prompt=...)` 并使用子级的答案。具体措辞取决于所用 Model。

### 继续子级对话

`delegate` 随子级输出返回 `execution_id`。父 Model 可调用 `resume_subagent(execution_id=..., prompt=...)` 继续该对话。再次调用 `delegate` 会开始新的对话。

保存并恢复父级完整的 `HarnessState`，即可跨 Run 保留内联子级。重建兼容定义时，保持定义 ID 稳定。持久化方式见[状态与恢复](state-and-resume.md)。

内联子级共享父级已进入的 Environment，并继承调用策略。为每个子级明确设置预算：子级用量独立于父级预算，`SubagentDefinition.usage_limits` 可进一步收紧子级定义的限制。

内联子级不能挂起等待延迟工具反馈。需要这类交互时，使用 Host 管理的子级。

## CodeAct

CodeAct 提供两个工具：`run_code` 执行内联 Python，`run_program` 执行可复用源码文件。两者都运行受限 Python。文件、网络、进程、环境变量和凭据访问通过允许调用的工具完成。需要常规 Python 包或原生程序时，使用 Environment shell。

### 为工具启用 CodeAct

添加 `CodeActCapability()`，并显式允许 Python 调用所需工具。下面的示例开放一个 `double` 函数：

```python title="codeact_demo.py"
import asyncio
import os

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
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
calculator = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=str,
    capabilities=(math_tools, CodeActCapability()),
)


async def main() -> None:
    result = await calculator.run(
        "Use run_code to double each of 2, 4, and 6, then return their sum."
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

运行 `uv run python codeact_demo.py`，预期总和为 `24`。`CodeActToolPolicy` 赋予调用资格；每次调用仍须通过正常的可用性、参数验证、权限和审批检查。

### 组合工具调用

Model 向 `run_code` 提供如下代码。这些片段是 runner 的输入，不是独立 Python 模块：

```python
values = []
for value in [2, 4, 6]:
    values.append(await double(value=value))
sum(values)
```

使用关键字参数，并 await 每次工具调用。`run_code` 返回最后一个表达式。相互独立的调用可以并发执行：

```python
import asyncio

values = await asyncio.gather(double(value=2), double(value=4), double(value=6))
sum(values)
```

一个 Model 响应中只放一个 runner 工具调用。在代码内部组合所需调用，并只返回 Agent 需要的数据。

对于设置了 `defer_loading=True` 的工具，先发现工具或加载其 Capability，再调用 runner。大型工具集合可使用 [ToolSearch 分组](tool-search.md#use-with-codeact)，在 runner 内发现工具。ToolSearch 代理调用始终顺序执行，包括放在 `asyncio.gather` 中时。

### 复用程序

将 UTF-8 文件保存到可读的 Environment 挂载。定义一个不带装饰器的 `async def main(inputs)`，返回 JSON 兼容结果：

```python title="double.codeact.py"
async def main(inputs):
    result = await double(value=inputs["value"])
    await store(key="math.result", value=result)
    return result
```

文件位于 `/workspace/double.codeact.py` 时，Model 用以下参数调用 `run_program`：

```json
{"path": "/workspace/double.codeact.py", "inputs": {"value": 6}}
```

结果为 `12`，同时保存为 `math.result`。将示例路径替换为已绑定 [Environment](environments.md) 中的路径。Harness 通过该 Environment 读取文件，Model 无需具备文件读取工具。

每次调用都会重新读取源码，并启动新的解释器。`main` 之外可包含受支持的导入、辅助函数、dataclass 和模块初始化。主要流程放在 `main` 中；模块初始化也会在每次调用时执行，遵循相同的工具策略和限制。

### 跨调用保留数据

| 状态                  | 生命周期                                       |
| --------------------- | ---------------------------------------------- |
| `run_code` 变量和函数 | 当前 Run 内的成功调用之间                      |
| `run_program` 变量    | 单次调用                                       |
| 用 `store` 保存的值   | Host 保存并恢复 `HarnessState` 后可跨 Run 使用 |

用 `store` 和 `load` 显式保存、读取 JSON 数据：

```python
await store(key="search.results", value={"ids": [12, 34], "next_page": 3})
results = await load(key="search.results")
results["ids"]
```

`load()` 列出键。`forget(key="search.results")` 删除一个值。读取结果是独立副本，修改后需要再次调用 `store` 保存。缺失键会失败，保存的 JSON null 则读取为 `None`。

`run_code(restart=True)` 在求值前清空临时变量。求值失败也会清空临时变量。成功的 `store` 写入在这两种情况下都会保留。要在进程退出后保留写入，按[状态与恢复](state-and-resume.md)保存返回的 State。

### 处理失败并设置限制

失败结果说明错误，并在可用时提供源码位置。修正代码后再重试。runner 失败前可能已经完成部分工具调用：重复写入或其他外部操作前，先核对执行结果。已完成的效果和已存储的值不会回滚。

用 `CodeActConfig` 收紧执行和存储限制：

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

在 builder 中用此实例替换 `CodeActCapability()`。默认每次 runner 允许 128 次嵌套工具尝试、16 路并发和 300 秒执行时间。存储默认最多 256 项、10 MiB。父级工具调用限制和提供方上限仍然生效。

## 异步子级

子任务需要在父 Run 结束后继续时，使用异步子级。Host 实现 `SubagentOperator`，负责调度子级、保留检查点并交付结果。将 operator 传给 `SubagentCapability(async_enabled=True, operator=operator)`，替换内联 Capability。

| Model 工具                              | 结果                   |
| --------------------------------------- | ---------------------- |
| `delegate(subagent_name, prompt)`       | 受理回执，任务独立继续 |
| `subagent_info(...)`                    | 当前执行状态           |
| `wait_subagent(...)`                    | 有限等待一个或多个执行 |
| `steer_subagent(execution_id, message)` | 引导请求的确认回执     |
| `cancel_subagent(execution_id)`         | 取消请求的确认回执     |
| `resume_subagent(execution_id, prompt)` | 关联续接的受理回执     |

等待超时不会取消任务。取消回执不代表清理已经完成。查询当前执行记录以确认结果。关闭父 Run 不会取消已受理的异步任务。

Host 负责新的子级绑定、存储、关闭和恢复。生命周期接入见 [Host 嵌入](hosting.md)。

### Host 管理的反馈

支持子级反馈的 Host 使用[结构化挂起流程](state-and-resume.md#structured-suspension)：保存子级 State 和延迟请求，收集反馈，再通过 `DeferredToolResume` 直接续接子级。否则，设置 `RunBindings.deferred_tools_supported=False`；Harness 会省略延迟工具，并拒绝运行时延迟请求。Harness UI 对异步子级采用此设置。
