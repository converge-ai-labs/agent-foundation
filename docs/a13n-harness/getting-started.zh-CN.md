---
title: Harness 快速开始
sidebarTitle: 快速开始
description: 离线构建首个类型安全的 Python 智能体，流式接收事件，维护多轮线程状态，并接入外部模型提供方。
---

无需任何 API 密钥即可离线构建并运行你的第一个 **Harness** 智能体，随后平滑扩展至多轮状态保持、实时流式输出与真实大模型提供方。

## 环境要求

安装 Python 3.13+、Git 与 [`uv`](https://docs.astral.sh/uv/)，克隆仓库并同步依赖：

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

## 离线运行并延续多轮对话

将以下代码保存为 `app.py` 并运行 `uv run python app.py`：

```python title="app.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "Hello from Harness!"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="You are a helpful assistant."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("First turn")
    second = await executable.run("Second turn", previous_state=first.state)
    assert second.thread_id == first.thread_id and second.run_id != first.run_id
    print(second.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

| 核心组件                      | 职责                                                            |
| ----------------------------- | --------------------------------------------------------------- |
| `AgentSpec`                   | 声明式指令、模型路由、预算上限与可序列化能力                    |
| `HarnessBuilder().build(...)` | 将 `AgentSpec`、`output_type` 与能力组装为可复用的可执行体      |
| `executable.run(...)`         | 执行一次逻辑 Run 并返回 `HarnessRunResult`                      |
| `result.state`                | 可移植的 `HarnessState` 检查点，通过 `previous_state=` 延续对话 |

## 流式消费实时事件

使用 `async with executable.stream(...) as stream:` 边生成边输出增量文本，并确保退出时自动清理资源：

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

async with executable.stream("Stream a greeting") as stream:
    async for item in stream:
        if isinstance(item, HarnessRunResultEvent):
            item.result.raise_for_status()
        elif isinstance(item, HarnessEvent):
            ev = item.event
            if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                print(ev.part.content, end="", flush=True)
            elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                print(ev.delta.content_delta, end="", flush=True)
```

## 切换真实模型并挂载能力

在 `AgentSpec` 上指定 `model`（省略构建器的 `model=`），并按需组合模块化[能力（Capabilities）](capabilities.md)：

```python
import os
from a13n_harness.capabilities import RuntimeContextCapability, WorkingStateCapability

executable = HarnessBuilder().build(
    AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Provide concise, actionable answers.",
    ),
    output_type=str,
    capabilities=(RuntimeContextCapability(), WorkingStateCapability()),
)
```

```bash
export OPENAI_API_KEY="sk-..."
export HARNESS_MODEL="openai-responses:gpt-5"
uv run python app.py
```

## 下一步

- [智能体与运行](agents-and-runs.md) —— `RunBindings`、实时引导、限额与重试
- [工具与依赖注入](tools-and-dependencies.md) —— 注册自定义 Python 工具与请求级依赖
- [状态与恢复](state-and-resume.md) —— JSON 检查点持久化、线程分叉与人机协作恢复
- [运行环境](environments.md) —— 沙箱化文件、Shell 与桌面工具
