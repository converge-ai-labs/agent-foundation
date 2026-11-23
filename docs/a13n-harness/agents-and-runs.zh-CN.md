---
title: 智能体与运行
description: 在 Python 中构建可复用的 ExecutableAgent，使用全新 RunBindings 执行或流式输出轮次，实时引导运行，并配置限额与恢复策略。
---

基于 `AgentSpec` 构建可复用的 `ExecutableAgent`，调用 `run()` 获取最终结果，或调用 `stream()` 消费实时事件。每次调用都会创建全新的 Run；传入 `previous_state=first.state` 即可在同一 Thread 上继续对话。

## 构建并运行智能体

将以下离线示例保存为 `runs.py` 并运行 `uv run python runs.py`：

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

    async with executable.stream(
        "Review the plan",
        previous_state=first.state,
        bindings=RunBindings.embedded(metadata={"stage": "review"}),
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                print(f"event {item.sequence}")
            elif isinstance(item, HarnessRunResultEvent):
                print(item.result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

### 构建期输入

| 输入项                      | 配置位置                                      |
| --------------------------- | --------------------------------------------- |
| 提示词、模型 ID、参数与预算 | `AgentSpec`                                   |
| 类型化输出契约              | `output_type`（或 `AgentSpec.output_schema`） |
| 具体 Model 实例             | `model`（同时保持 `AgentSpec.model=None`）    |
| 工具与功能能力              | `capabilities`                                |
| 中间件与子智能体            | `plugins`、`subagents`                        |
| 模型中断恢复策略            | `model_recovery`                              |

## 执行并获取结果

常规成功路径直接调用 `result.output_or_raise()`；如需处理多种终态，按 `result.status` 分支：

| 状态        | 应用处理方式                          | 关键字段                              |
| ----------- | ------------------------------------- | ------------------------------------- |
| `completed` | 读取 `output` 并按需持久化 `state`    | `output`、`state`、`usage`            |
| `suspended` | 收集延迟工具结果或审批后恢复运行      | `state`、`deferred`、`suspend_reason` |
| `failed`    | 检查 `failure` 并决定是否保留 `state` | `failure`、可选 `state`               |
| `cancelled` | 按取消处理，不视为已完成输出          | 可选 `state`                          |

使用 `all_messages()` 获取完整历史，`new_messages()` 获取本次 Run 新增消息。检查点持久化见[状态与恢复](state-and-resume.md)，延迟工具流程见[客户端工具](client-tools.md)。

## 流式事件

`executable.stream(...)` 产出两类公开对象，均携带 `thread_id`、`run_id` 与单调递增的 `sequence`：

- `HarnessEvent` —— 原生智能体事件与 Harness 扩展事件。
- `HarnessRunResultEvent` —— 运行资源关闭后的最终结果事件。

请始终在 `async with` 块内消费事件流。可通过 [Stream Protocol](../a13n-stream-protocol/index.md) 将其投影为 AG-UI 事件。

### 活动流操作

| 操作                                       | 用途                               |
| ------------------------------------------ | ---------------------------------- |
| `stream.context` / `stream.usage`          | 读取当前 `AgentContext` 或实时用量 |
| `await stream.export_state()`              | 导出最新安全的可移植检查点         |
| `await stream.steer(input, input_id=None)` | 在运行期间入队实时引导指令         |
| `stream.cancel()`                          | 请求取消当前运行                   |
| `stream.result`                            | 在终止事件到达后读取最终结果       |

## 全新运行绑定

每次根运行、恢复运行或子运行都应创建新的 `RunBindings`，提供请求级协作者、身份、环境权限与元数据：

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

不要将活跃的绑定对象存入持久化的 `HarnessState`。若构造输入需要依赖已进入的环境，请改用 `input_factory=make_input`。

## 配置定义

### 加载并微调预设

从 YAML 加载预设，并通过 `with_updates()` 生成不可变变体：

```python
preset = AgentSpec.from_file("research-agent.yaml")
local = preset.with_updates(
    model_settings={"temperature": 0.1, "max_tokens": 8_000},
    toolset_instructions=False,
)
```

- `system_prompt` 定义静态系统提示块（在后续基于历史恢复的 Run 中会自动替换为最新定义）。
- `instructions` 提供按请求生效的指令；设置 `toolset_instructions=False` 可关闭工具集自动注入的说明。

### 用量限额与重试

```python
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

- Harness 默认允许每次运行最多 `1,000` 次模型请求（传入 `UsageLimits(request_limit=None)` 可取消上限）。在 `run()` 中传入 `usage_limits=` 会整体替换定义中的预算。
- `AgentSpec.retries` 默认工具重试 `5` 次、输出校验重试 `3` 次。
- 如需处理模型流中断重试，在 `HarnessBuilder().build(...)` 中配置 `model_recovery=ModelRecoveryPolicy(...)`。

### 冷启动历史裁剪

距离上次模型响应超过 1 小时后，Harness 默认会裁剪已消费的旧工具结果中的超长字符串（已读取的 `SKILL.md` 文件不受影响）。可在 `AgentSpec` 中自定义或关闭：

```python
from a13n_harness.filters import ColdStartFilterConfiguration

spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
disabled = spec.with_updates(cold_start_filter=None)
```

## 相关指南

- [模型](models.md) —— 模型选择、别名、特性与请求亲和性
- [用量与限额](usage-and-limits.md) —— Token 统计与费用追踪
- [可观测性](observation.md) —— 关联 ID 与 OpenTelemetry 追踪
- [托管集成](hosting.md) —— 生产环境组合与资源清理
