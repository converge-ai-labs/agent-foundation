---
title: Harness 快速入门
sidebarTitle: 快速入门
description: 离线运行第一个 Agent，继续同一 Thread，接收流式回答，再连接 provider。
---

先用一个小型 Python 程序取得结果，再配置 provider。随后复用这个程序，继续同一 Thread 并显示流式输出。离线步骤无需账号、API 密钥或外部服务。

## 环境要求

安装 Python 3.13 或更高版本、Git 和 [uv](https://docs.astral.sh/uv/)。这些指南对应 `main` 分支的源码 API，请配套使用仓库和锁文件：

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

后续命令都在这个 checkout 中执行。`uv` 会安装 Harness 及其依赖，包括下文使用的确定性 `FunctionModel`。安装依赖需要网络，运行离线示例不需要。

## 运行离线 Agent

1. 在 checkout 根目录创建 `app.py`，写入以下完整程序：

   ```python title="app.py"
   import asyncio
   from collections.abc import AsyncIterator

   from a13n_harness import AgentSpec, HarnessBuilder
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

2. 在同一目录运行：

   ```bash
   uv run python app.py
   ```

3. 检查输出：

   ```text
   Hello from the Harness
   ```

模型回答由函数提供，Harness 则执行真实的 Run 生命周期。这个结果确认安装、Agent 构建和结果处理已连通。

## 理解执行流程

| 代码                          | 职责                                         |
| ----------------------------- | -------------------------------------------- |
| `AgentSpec()`                 | Agent 指令和声明式设置                       |
| `HarnessBuilder().build(...)` | 将定义、模型和输出约定组合成可复用的执行对象 |
| `output_type=str`             | 要求最终返回文本                             |
| `executable.run(...)`         | 启动 Run，等待终态结果                       |
| `result.output_or_raise()`    | 返回已完成的输出；结果未完成时抛出异常       |
| `result.state`                | 将消息和可移植状态带入后续 Run               |

这里通过 builder 参数传入具体模型，因此保持 `AgentSpec.model` 未设置。只选择一种模型来源。即使 Agent 只返回文本，也要明确填写 `output_type`。

## 构建一次，在线程中继续执行

保留第一个程序的导入、`respond()` 和最后的 `asyncio.run(main())`。只替换 `main()`：

```python
async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("Say hello")
    print(first.output_or_raise())
    second = await executable.run("Say hello again", previous_state=first.state)
    print(second.output_or_raise())
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
    print("Same Thread, new Run")
```

再次运行 `uv run python app.py`，应打印两次问候和 `Same Thread, new Run`。断言检查续接身份；固定的离线 responder 对每个提示词仍返回相同文本。

需要在程序重启后继续时，保存返回的 `HarnessState`。[状态指南](state-and-resume.md)介绍 JSON 往返和存储流程。应用另外重建当前凭据和活跃资源。

## 流式接收公开事件

要在文本到达时显示它，从最初的离线程序开始。添加以下导入，替换 `main()`，保留最后的 `asyncio.run(main())`：

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
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

再次运行文件，检查问候输出。文本事件用于显示，终态 `HarnessRunResultEvent` 用于确认执行是否完成。`async with` 作用域负责关闭 Run 资源，提前停止消费时也会清理。为流式应用添加持久化时，使用终态结果中的 `state`。

## 接入真实模型

离线程序运行成功后，再连接可用账号。以下示例使用 OpenAI Responses 集成；请选择该账号可用的模型 ID。

1. 回到最初的 `app.py`，添加 `import os`。

2. 将 `main()` 内的 builder 调用替换为：

   ```python
       executable = HarnessBuilder().build(
           AgentSpec(
               model=os.environ["HARNESS_MODEL"],
               instructions="Answer clearly and concisely.",
           ),
           output_type=str,
       )
   ```

3. 在进程环境中设置凭据和模型选择。以下是 POSIX shell 写法，请替换两个占位值：

   ```bash
   export OPENAI_API_KEY='YOUR_API_KEY'
   export HARNESS_MODEL='openai-responses:YOUR_MODEL_ID'
   uv run python app.py
   ```

回答现在来自 provider，内容可能变化。可以删除离线 `respond()` 函数及其模型导入。不要将密钥提交到版本控制。其他 provider、应用管理的客户端和短期凭据见[模型](models.md)及[认证与 HTTP 客户端](model-authentication.md)。

## 添加能力

根据 Agent 的需要添加功能。例如，下文为最初的离线程序添加运行上下文和工作笔记、任务功能。将导入放在文件顶部，并用下文替换 `main()`：

```python
from a13n_harness.capabilities import RuntimeContextCapability, WorkingStateCapability


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(RuntimeContextCapability(), WorkingStateCapability()),
    )
    result = await executable.run("Say hello")
    print(result.output_or_raise())
```

问候输出不变，因为确定性 responder 没有选择新增工具。接着阅读[工具与依赖](tools-and-dependencies.md)，运行真正调用函数的离线示例；或阅读 [Capabilities](capabilities.md)，选择内置功能。

## 排查首次运行问题

| 现象                            | 检查项                                                         |
| ------------------------------- | -------------------------------------------------------------- |
| Python 版本不受支持             | 使用 Python 3.13 或更高版本，重新执行锁定依赖同步              |
| `No module named a13n_harness`  | 在已同步依赖的 checkout 中通过 `uv run` 运行                   |
| 模型选择冲突                    | builder 的 `model=` 与 `AgentSpec.model` 二选一                |
| 缺少输出约定                    | 保留 `output_type=str`，或采用输入输出指南中的显式 schema 流程 |
| 离线问候成功，provider 执行失败 | 检查模型 ID、账号访问权、凭据和 provider 错误                  |
| 粘贴片段后出现未定义名称        | 按说明替换原程序的对应部分；片段复用其导入和 `respond()`       |

## 接下来

1. [调用 Python 工具](tools-and-dependencies.md)并检查结果。
2. [保存和恢复 Thread](state-and-resume.md)，跨程序调用继续工作。
3. 连接外部服务前先[离线测试](testing.md)。

应用结构见 [Agent 与执行](agents-and-runs.md)。文件和命令访问见[环境](environments.md)。[Agent 应用示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app)将多轮流式执行、持久化和重启恢复组合起来。
