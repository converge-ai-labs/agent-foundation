---
title: 委派与 CodeAct
description: 在 Harness 中通过子智能体编排多角色协作，或使用 CodeAct 以异步 Python 控制流并发调用外部工具。
---

\*\*委派（Delegation）\*\*将专项任务隔离在独立的提示词上下文与模型中；**CodeAct** 则让智能体在受限 Python 环境内直接通过循环、条件过滤与 `asyncio.gather` 并发调用工具，减少多轮模型往返。

| 编排需求                           | 机制                                             |
| ---------------------------------- | ------------------------------------------------ |
| 角色隔离或专用审查/领域专家        | 内联 `SubagentCapability`（`delegate`）          |
| 跨越父轮次存活的后台子任务         | [异步子智能体](#asynchronous-children)           |
| 循环批处理、结果过滤或并发工具扇出 | `CodeActCapability`（`run_code`、`run_program`） |

## 内联委派

声明子 `AgentDefinition`，并通过 `SubagentCapability` 与 `SubagentDefinition` 注册到父智能体：

```python title="delegation_demo.py"
import os
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability

reviewer = AgentDefinition(
    agent=AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Analyze the plan and report concrete architectural risks.",
    ),
    output_type=str,
    definition_id="plan-reviewer-v1",
)

parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="Delegate deep reviews to 'reviewer', then synthesize.",
        ),
        output_type=str,
        definition_id="coordinator-v1",
        capabilities=(SubagentCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Reviews plans for architectural and security risks.",
                agent=reviewer,
            ),
        ),
    )
)
```

父模型调用 `delegate(subagent="reviewer", prompt=...)` 启动子线程，并通过 `resume_subagent(execution_id=..., prompt=...)` 在同一子线程上继续追问。子线程谱系随父级 `HarnessState` 一并序列化保存。

## CodeAct

启用 `CodeActCapability` 并用 `CodeActPolicyToolset` 包装允许在 Python 中调用的工具集：

```python title="codeact_demo.py"
import os
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability, CodeActConfig
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    return value * 2


math_tools = Capability(
    id="math-tools",
    toolsets=[
        CodeActPolicyToolset(
            wrapped=FunctionToolset([double], id="math-functions"),
            policy=CodeActToolPolicy(tools={"double": True}),
            reject_unknown_tools=True,
        )
    ],
)

calculator = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=str,
    capabilities=(
        math_tools,
        CodeActCapability(CodeActConfig(max_tool_calls=32, max_concurrency=8, timeout_seconds=60)),
    ),
)
```

在 `run_code` 内，模型可使用 `await asyncio.gather(double(value=10), double(value=20))` 并发调用工具，通过 `run_program` 执行工作区中的 `.codeact.py` 脚本，并使用 `await store(key, value)`、`await load(key)` 与 `await forget(key)` 在 `HarnessState` 中跨轮次持久化 JSON 状态。

### 直接提交结构化输出

设置 `CodeActConfig(allow_final_output=True)`，即可在同一轮模型调用中通过 CodeAct 提交结构化结果。下面的示例为 Agent 配置了一个 Pydantic 输出模型：

```python
import os

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability, CodeActConfig
from pydantic import BaseModel


class Answer(BaseModel):
    total: int


calculator = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=Answer,
    capabilities=(CodeActCapability(CodeActConfig(allow_final_output=True)),),
)
```

对于采用结构化输出的非实时 Run，CodeAct 目录会列出 `run_code` 和 `run_program` 均可调用的输出函数。在上面的 Agent 中，模型可用 `final_result(total=6 * 7)` 结束代码片段。调用输出函数时，代码片段在调用处停止，参数随即提交校验。如果参数未通过 Schema 校验，或结果被输出验证器拒绝，Pydantic AI 会通过原生的输出重试机制将反馈传给模型。普通文本（`output_type=str`）和实时 Run 的行为不变。

## 异步子智能体

当子任务需要跨越父轮次在后台持续运行时，由宿主提供 `SubagentOperator`：

```python
capability = SubagentCapability(async_enabled=True, operator=host_operator)
```

该模式向模型开放 `delegate`、`subagent_info`、`wait_subagent`、`steer_subagent`、`cancel_subagent` 与 `resume_subagent`。

### 宿主托管反馈

内置内联子智能体默认设置 `RunBindings.deferred_tools_supported=False`，因其无法独立挂起。配合异步 `SubagentOperator` 时，子智能体遇到提问或工具审批会进入 `suspended` 并保存自身 `HarnessState`，待宿主收集反馈后通过 `DeferredToolResume` 恢复。
