---
title: 事件与处理器
description: 将 Harness 执行转换为 AG-UI 事件，读取快照并应用 Host 处理器。
---

为每个 `(thread_id, run_id)` 使用一个 `HarnessAguiObserver`，按顺序传入公开源条目。它转换观测，不读取私有状态。先运行[离线示例](getting-started.md)，再添加 Host 的传输和持久化。

## 观测 Harness 执行

为每次根执行或公开的子执行创建 observer，再按线程与执行关联标识路由每个公开源条目：

```python
from a13n_stream_protocol import HarnessAguiObserver

observers: dict[tuple[str, str], HarnessAguiObserver] = {}

async with executable.stream(input_value, bindings=bindings) as run_stream:
    async for item in run_stream:
        correlation = (item.thread_id, item.run_id)
        observer = observers.get(correlation)
        if observer is None:
            observer = HarnessAguiObserver()
            observers[correlation] = observer

        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)

snapshots = {
    correlation: observer.snapshot()
    for correlation, observer in observers.items()
}
```

Harness 父流可以转发内联子执行观测，同时保留子执行自身的 `thread_id` 和 `run_id`。先按关联标识路由，可以避免根 observer 拒绝子执行的观测。如果集成保证源只包含一次执行，可以直接使用一个 `HarnessAguiObserver`。

`observe()` 只返回该源条目产生的 AG-UI 事件。一个源条目可能产生多个事件，例如带初始内容的文本 part 会同时产生 `TEXT_MESSAGE_START` 和 `TEXT_MESSAGE_CONTENT`。

首次成功观测的条目绑定 `observer.thread_id` 和 `observer.run_id`。后续传给该 observer 的条目必须具有相同关联标识。Harness 启动另一次执行时，使用另一个 observer，即使两次执行推进的是同一线程。

### 事件映射

语义直接匹配时，observer 使用标准 AG-UI 事件：

| Harness 观测                       | AG-UI 输出                                                       |
| ---------------------------------- | ---------------------------------------------------------------- |
| 文本 part 开始、增量和结束         | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| 推理开始、内容、签名和结束         | 推理消息事件和 `REASONING_ENCRYPTED_VALUE`                       |
| 已完成工具调用                     | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`             |
| 成功的工具结果                     | `TOOL_CALL_RESULT`                                               |
| 已完成执行结果                     | `RUN_FINISHED`                                                   |
| 失败或取消的执行结果               | `RUN_ERROR`                                                      |
| 原生 Pydantic AI `CapabilityEvent` | 以原生 `kind` 命名的 `CUSTOM` 事件                               |
| 暂停结果或其他不匹配的公开观测     | 带命名空间的 `CUSTOM` 事件                                       |

未匹配的 Harness 扩展使用 `a13n.harness.lifecycle` 等名称。未匹配的 Pydantic AI 事件使用 `a13n.pydantic_ai.final_result` 等名称。原生 `CapabilityEvent` 保留具体 kind、Capability ID、可选工具调用关联和公开载荷。每个自定义值也保留公开线程、执行、序号、时间戳和源事件表示。

### 内容与大型自定义事件

Capability 事件保留原生名称和载荷，包括用户自定义 kind。文件编辑仍是前后对比数据，摘要仍是摘要数据，shell 状态仍是状态观测。协议不会把它们转换成助手回答或预渲染面板。展示方式由客户端决定。实际模型输入使用 user 角色文本事件，共享 `ContentMetadata`；常规显示省略标记为 `display: false` 的内容。

超过 48 KiB 的自定义事件使用通用 `a13n.stream.fragment` 帧。检查原事件前先重组：

```python
from a13n_stream_protocol import CustomEventAssembler

assembler = CustomEventAssembler()

# For each CUSTOM payload in one live subscription:
complete = assembler.accept(payload)
if complete is not None:
    render_custom(complete["name"], complete["value"])
```

消息帧保留完整 JSON 结构，不截断源内容。assembler 限制待处理内容，并拒绝不完整或不一致的序列；请检查 `assembler.gap`，重置订阅时替换 assembler。这些是尽力交付的观测，不是持久事件日志。限制和字段见[消息帧契约](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-stream-protocol/00-overview.md#large-custom-events)。

### 序列化事件

返回值是结构化的 `ag_ui.core.Event` 模型。传输或存储需要 JSON 兼容值时，使用上游 Pydantic 适配器：

```python
from ag_ui.core import Event
from pydantic import TypeAdapter

EVENT_ADAPTER = TypeAdapter(Event)

payloads = [
    EVENT_ADAPTER.dump_python(event, mode="json", by_alias=True)
    for event in new_events
]
```

Host 应在序列化事件外封装自己的持久 ID、顺序记录和交付元数据，不要改写协议关联标识。

## 读取累积快照

`snapshot()` 返回到目前为止保留的、经过处理器的全部事件的独立副本：

```python
events = observer.snapshot()
```

修改 `observe()` 或 `snapshot()` 返回的事件不会改变 observer。快照是便于读取的进程内状态，不是持久事件日志或 Harness 续接值。

observer 不压缩流式块，也不实施保留上限。长期运行的 Host 应持久化增量结果，并应用自己的有界保留或投影策略。

## 应用 Host 处理器

处理器可在事件累积并返回前，省略事件或替换允许修改的内容字段：

```python
from typing import Any

from ag_ui.core import Event
from ag_ui.core.events import CustomEvent, TextMessageContentEvent
from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver


def process_event(
    source: HarnessStreamEvent[Any],
    event: Event,
) -> Event | None:
    del source

    if (
        isinstance(event, CustomEvent)
        and event.name == "a13n.harness.diagnostic"
    ):
        return None

    if isinstance(event, TextMessageContentEvent):
        return event.model_copy(update={"delta": event.delta.strip("\x00")})

    return event


observer = HarnessAguiObserver(processor=process_event)
```

替换必须保留 AG-UI 事件类型和结构关联，包括消息、工具、线程、执行、生命周期和源字段。无效替换抛出 `AguiObservationError`，不会提交当前源条目。

配合 `resume()` 使用的处理器必须保持重放稳定：相同的有序源历史和稳定 Host 配置必须产生相同的保留事件序列。它不能保留可变处理状态，也不能执行持久化、发布、确认或其他外部可见副作用。
