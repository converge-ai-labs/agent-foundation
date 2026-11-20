---
title: Harness 快速入门
sidebarTitle: 快速入门
description: 在本地离线构建您的首个类型安全 Python Agent，流式消费 Token 事件，维系多轮会话线程状态，并平滑接入主流云端大模型。
---

本篇快速入门将指引您使用 **Harness** 迅速构建并运行您的首个 Agent。您将从完全离线、确定性且无需任何 API 密钥或外部服务的 Python 脚本起步，逐步掌握多轮对话续接、实时流式推送以及真实云端大模型的接入流程。

## 环境要求

确保本地已安装 Python 3.13 或更高版本、Git 以及现代 Python 依赖管理器 [`uv`](https://docs.astral.sh/uv/)。

克隆代码仓库并通过锁文件精准同步工程环境：

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

`uv sync` 会高速安装 Harness、底层 Pydantic AI 以及测试套件。离线示例的运行过程完全无需网络连接。

## 运行离线 Agent

在工程根目录下创建名为 `app.py` 的脚本：

```python title="app.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


# 1. 定义确定性的离线响应函数
async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "Hello from the Harness!"


# 2. 构建 Agent 可执行对象并执行提示词
async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="你是一个严谨且乐于助人的智能助手。"),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    result = await executable.run("打个招呼")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

使用 `uv` 运行此程序：

```bash
uv run python app.py
```

控制台将输出：

```text
Hello from the Harness!
```

虽然响应函数返回的是确定性文本，但 Harness 已经完整走完了原生生命周期——包括参数校验、执行状态管理以及结构化结果打包。

## 理解执行流程

核心架构将 Agent 的静态配置定义与单次动态执行解耦：

| 核心组件                      | 架构职责                                                          |
| :---------------------------- | :---------------------------------------------------------------- |
| `AgentSpec`                   | 声明式系统指令、模型选型标识符与静态运行策略                      |
| `HarnessBuilder().build(...)` | 将指令、模型与能力组装为线程安全的可执行对象（`ExecutableAgent`） |
| `output_type=str`             | 终态输出结果的类型契约（支持 Python 基础类型或 Pydantic 模型）    |
| `executable.run(...)`         | 启动单次逻辑运行（Run）并异步等待终态执行结果                     |
| `result.output_or_raise()`    | 安全提取经过校验的输出对象，执行失败时抛出具体异常                |
| `result.state`                | 包含历史消息与能力状态的轻量级序列化检查点快照                    |

## 构建一次，继续同一 Thread

在多轮业务会话中，必须将上一次对话的上下文无缝传递给下一步。您只需将前一次返回的 `state` 作为 `previous_state` 传入后续调用即可。

更新 `app.py` 中的 `main()` 函数：

```python
async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )

    # 第一轮交互
    first = await executable.run("第一次问候")
    print(first.output_or_raise())

    # 第二轮交互：传入 previous_state 延续同一会话线程
    second = await executable.run("第二次问候", previous_state=first.state)
    print(second.output_or_raise())

    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
    print("会话连续性校验通过：相同的 Thread，独立的 Run 审计记录。")
```

再次运行 `uv run python app.py`。断言将验证两次运行具有相同的 `thread_id`，同时记录了相互独立的 `run_id` 审计痕迹。

如需跨进程重启持久化保存会话，只需将 `result.state` 序列化为 JSON 并存入数据库即可。详情请参阅[状态与恢复](state-and-resume.md)。

## 流式接收公开事件

对于交互式聊天窗口与命令行实时打字效果，可通过 `executable.stream()` 消费类型化的事件流：

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )

    async with executable.stream("流式打个招呼") as stream:
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

`async with` 上下文管理器确保了在流消费完毕或中途退出时，底层网络连接和工具资源都能被安全释放。

## 接入真实模型

在离线流程验证通过后，即可接入 OpenAI、Anthropic 或 Gemini 等主流大模型：

1. 在 `app.py` 顶部添加 `import os`。
2. 在 `AgentSpec` 中配置模型标识符：

```python
    executable = HarnessBuilder().build(
        AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="你是一位专业的资深软件工程师。请给出精确、可落地的回答。",
        ),
        output_type=str,
    )
```

3. 在命令行中导出对应的环境变量：

```bash
export OPENAI_API_KEY="sk-..."
export HARNESS_MODEL="openai-responses:gpt-4o"
uv run python app.py
```

Harness 会解析模型前缀、建立带认证的 HTTP 传输连接，并与远端模型 API 交互。如需配置自定义代理与端点，请参考[模型认证指南](model-authentication.md)。

## 添加能力

各种可插拔功能——如运行时上下文、备忘录与任务管理、工具检索等——均通过 **Capabilities** 机制挂载：

```python
from a13n_harness.capabilities import RuntimeContextCapability, WorkingStateCapability


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(
            RuntimeContextCapability(),
            WorkingStateCapability(),
        ),
    )
    result = await executable.run("打个招呼")
    print(result.output_or_raise())
```

阅读 [Capabilities](capabilities.md) 了解内置的记忆管理、环境沙箱及工具发现扩展。

## 排查首次运行问题

常见初始化问题排查指南：

| 现象                                                  | 排查方向                                                                  |
| :---------------------------------------------------- | :------------------------------------------------------------------------ |
| `ModuleNotFoundError: No module named 'a13n_harness'` | 确保在当前目录下使用 `uv run python app.py` 启动，依赖已正确同步          |
| Python 版本不兼容报错                                 | Harness 依赖 Python 3.13+，请通过 `python3 --version` 确认解释器版本      |
| 模型配置冲突                                          | 仅在构建器的 `model=` 或 `AgentSpec.model` 中二选一指定模型，避免重复定义 |
| Provider 鉴权失败                                     | 检查 API Key 环境变量是否正确导出，以及模型账号权限是否已开通             |
| 缺少导入项报错                                        | 确保已在最新工程根目录完整执行 `uv sync`                                  |

## 接下来

在基础执行环境就绪后，建议继续探索：

1. [工具与依赖](tools-and-dependencies.md)：注册自定义 Python 原生函数并处理依赖注入。
2. [状态与恢复](state-and-resume.md)：在生产级数据库中序列化持久化会话检查点。
3. [自动化测试](testing.md)：为复杂的智能体逻辑编写高速确定的单元测试。
4. [执行环境 Environments](../environments/index.md)：为 Agent 赋予受控安全的文件读写与终端命令执行权限。
