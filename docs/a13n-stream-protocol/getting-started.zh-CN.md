---
title: Stream Protocol 快速入门
sidebarTitle: 快速入门
description: 将离线 Harness 执行转换为 AG-UI 事件。示例使用真实 Harness 流和转换器，无需 provider 凭据、浏览器、服务器或网络传输。
---

## 准备源码工作空间

使用 Python 3.13 和仓库锁文件：

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-stream-protocol
```

使用发布版时，安装 `a13n-stream-protocol` 并遵循对应版本的 API 文档。它要求匹配的 Harness 版本。

## 转换一次执行

保存为 `stream_example.py`，运行 `uv run python stream_example.py`：

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

每行打印一个 JSON 编码的 AG-UI 事件。输出包含文本事件、公开自定义观测和终结事件；ID 和时间戳会变化。这是 **JSON Lines，不是 SSE**。应用需要另行选择交付的消息帧格式。

1. Harness 负责 agent 执行和相应资源范围内的清理。
2. `observe(item)` 转换一个公开源条目，可能产生多个 AG-UI 事件。
3. Pydantic 适配器按线上协议字段别名序列化结构化事件。
4. 此示例打印增量事件；实际 Host 在此处负责持久化或发布。

## 添加子执行或多个并发执行

按 `(item.thread_id, item.run_id)` 路由源条目，为每个组合保留一个 observer。父流可以转发子条目，不改变其关联标识。全部送入同一个 observer 会产生关联错误。

启用委派前，先参考[多执行路由示例](events.md#observe-a-harness-run)。

## 明确添加传输层

转换器不提供 HTTP 路由、重放游标、持久事件 ID、保留策略或重连循环。Host 必须决定：

- 保留源观测、投影事件，还是两者都保留；
- 消费端可见的内容；
- 如何报告缺口和终结结果；
- 如何无重叠地从重放切换到实时交付；
- 在内存中保留多少 observer 状态。

observer 累积经过处理器的事件，不设保留上限。在 Host 中将其生命周期限定为一次执行，并考虑长流的资源开销。只需新事件时，不要反复发布 `snapshot()`。

## 下一步

[事件与处理器](events.md)介绍映射、自定义事件分片和过滤。[重放与恢复](replay.md)介绍重建，以及它与 agent 恢复的区别。
