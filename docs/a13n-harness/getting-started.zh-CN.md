---
title: Harness 快速入门
sidebarTitle: 快速入门
description: 构建并运行一个离线 Agent，然后接入模型 provider。
---

## 环境要求

- Python 3.13 或更高版本
- [uv](https://docs.astral.sh/uv/)

这些指南对应 `main` 分支的源码 API。要使用匹配的依赖运行示例，先克隆仓库，再按锁文件安装 Harness 包：

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

离线示例使用随 Harness 一起安装的 Pydantic AI `FunctionModel`，不需要 provider 密钥。

## 运行离线 Agent

创建 `app.py`：

```python title="app.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "Hello from the Harness"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )

    result = await executable.run("Say hello")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
uv run python app.py
```

输出如下：

```text
Hello from the Harness
```

`FunctionModel` 的行为是确定的，既无需联网，也适合用于测试。

## 理解执行流程

`HarnessBuilder` 将 `AgentSpec` 和离线 `FunctionModel` 组合成可复用的执行对象。每次调用 `run()` 都会开始一次有明确作用域的执行，并返回输出与继续执行所需的状态。这里直接向 builder 传入模型，因此没有设置 `AgentSpec.model`；构建 Agent 时只选择一种模型来源。

## 接入真实模型

在 `app.py` 中，将 `main()` 里的 `executable = ...` 替换为：

```python
    executable = HarnessBuilder().build(
        AgentSpec(
            model="openai-responses:gpt-5",
            instructions="Answer clearly and concisely.",
        ),
        output_type=str,
    )
```

设置 OpenAI provider 凭据，再运行**修改后的** 文件：

```bash
export OPENAI_API_KEY=your-api-key
uv run python app.py
```

离线示例中的 `respond` 函数和 `FunctionModel` 导入此时已不再需要。其他 provider 或短期凭据的用法见[模型](models.md)及[认证与 HTTP 客户端](model-authentication.md)。

## 构建一次，在线程中继续执行

执行对象可以重复调用。每次调用都会生成新的 `run_id`；传入 `previous_state` 则会沿用同一个 Harness 线程：

```python
first = await executable.run("Remember that the release is Friday.")
second = await executable.run(
    "When is the release?",
    previous_state=first.state,
)
```

持久化返回的 `HarnessState`，就能在进程重启后继续工作。当前凭据和环境资源需要另外重新构建。

## 流式接收公开事件

要在文本到达时立即打印，将离线 `app.py` 中的 `main()` 替换为以下版本，并补充示例中的导入：

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
    )
    async with executable.stream("Say hello") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                item.result.raise_for_status()
            elif isinstance(item, HarnessEvent):
                event = item.event
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    print(event.part.content, end="", flush=True)
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    print(event.delta.content_delta, end="", flush=True)
    print()
```

再次运行 `uv run python app.py`。即使调用方提前停止消费，`async with` 作用域也会关闭本次执行的资源。如果需要持久化状态、可重复调用的流式应用，可参考 [agent-app 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app)。

## 添加能力

Agent 需要相应上下文或工具时，可以加入可选的 Capabilities：

```python
from a13n_harness.capabilities import (
    RuntimeContextCapability,
    WorkingStateCapability,
)

executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(RuntimeContextCapability(), WorkingStateCapability()),
)
```

当前凭据和按请求创建的客户端应放在执行绑定中；其他能力选项见 [Capabilities](capabilities.md)。

## 接下来

- [离线测试 Agent](testing.md)。
- 阅读 [Agent 与执行](agents-and-runs.md)，了解模型路由、流式输出、结果、清理和用量。
- 阅读 [Capabilities](capabilities.md)，选择项目提供的可选能力。
- 通过[环境](../environments/index.md)接入文件、命令、进程和端口。
- 运行 [Agent 应用示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app)，了解多轮流式执行、状态持久化和重启恢复。
