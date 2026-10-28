---
title: Stream Protocol 快速入门
sidebarTitle: 快速入门
description: 执行离线 Agent、转换输出，并检查生成的 AG-UI 事件。
---

本示例使用测试模型运行 Harness，每个事件打印为一个 JSON 对象。你将看到执行在哪里结束，以及应用的存储或传输从哪里接入。需要 Python 3.13 和 `uv`，无需模型凭据。

## 准备源码检出

克隆仓库并安装锁定的 Stream Protocol 依赖：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-stream-protocol
```

在该目录中完成后续步骤。在现有应用中，安装发布的 `a13n-stream-protocol` 包并使用对应版本的文档；它会固定依赖匹配的 Harness 版本。

## 转换一次执行

将以下内容保存为 `stream_example.py`：

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder, HarnessRunResultEvent
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event
from pydantic import TypeAdapter
from pydantic_ai.models.test import TestModel


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=TestModel(custom_output_text="Hello from the stream"),
    )
    observer = HarnessAguiObserver()
    adapter = TypeAdapter(Event)
    terminal = None

    # This example has no children: every item belongs to the same Run.
    async with executable.stream("Say hello") as stream:
        async for item in stream:
            for event in observer.observe(item):
                print(adapter.dump_json(event, by_alias=True).decode())
            if isinstance(item, HarnessRunResultEvent):
                terminal = item.result

    assert terminal is not None
    assert terminal.output_or_raise() == "Hello from the stream"
    assert observer.snapshot()


if __name__ == "__main__":
    asyncio.run(main())
```

运行程序：

```console
uv run python stream_example.py
```

断言通过后程序退出。在输出中找到 `RUN_STARTED`、包含 `Hello from the stream` 的文本和 `RUN_FINISHED`。公开 CUSTOM 观测也会出现在这些事件之间。每次执行的 ID 和时间戳会变化。

按三步理解循环：

1. `executable.stream()` 运行 Agent 并产出公开 Harness 条目，其上下文管理器负责相应资源范围内的清理。
2. `observer.observe(item)` 将一个条目转换为零个或多个结构化 AG-UI 事件。
3. `dump_json(..., by_alias=True)` 使用 `messageId` 等线上字段名。在示例打印事件的位置，Host 可以存储或发送该事件。

输出采用 JSON Lines。SSE 端点需要在事件外添加自己的消息帧格式。

## 添加子执行或多个并发执行

根流转发内联子执行观测时，改用 `HarnessAguiStreamObserver`。它在根流内跟踪每个子执行的输出。每次独立运行的根执行或异步子执行都使用独立 observer，包括同一线程中的另一次执行。

添加委派时，参考 [stream observer 示例](events.md#observe-a-harness-run)。快速入门的单次执行 observer 会明确拒绝其他执行的观测。

## 明确添加传输层

接入实时应用时，用 Host 的持久化和交付函数替换打印。每个新批次发布一次，再决定消费端重连后能恢复哪些内容：

| 消费端需求             | Host 设置                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------- |
| 实时事件已存到其他位置 | 设置 `retain_events=False`，持久化 `observe()` 返回的批次                             |
| 在进程内检查事件       | 保留默认日志，读取 `snapshot()`                                                       |
| 恢复消息与工具调用     | 使用 `DisplayFold` 并保存[展示检查点](replay.md#restore-a-compact-display-checkpoint) |

为传输定义持久 ID、顺序、保留策略，以及重放转实时的分界。明确客户端如何得知交付缺口。发布前应用可见性策略。这些决策属于应用；Stream Protocol 提供事件转换和展示状态。

## 下一步

通过[事件与处理器](events.md)连接实时流并过滤内容。通过[重放与恢复](replay.md)恢复尚未结束的消息，并验证其最终内容与不中断交付一致。
