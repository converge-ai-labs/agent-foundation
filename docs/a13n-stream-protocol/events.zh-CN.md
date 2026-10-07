---
title: 事件与处理器
description: 将 Harness 执行转换为 AG-UI 事件，读取快照并应用 Host 处理器。
---

为每个根流使用一个 `HarnessAguiStreamObserver`，按顺序传入公开源条目，包括转发的内联子执行。它转换观测，不读取私有状态。先运行[离线示例](getting-started.md)，再添加 Host 的传输和持久化。

## 观测 Harness 执行

为每次独立运行的根执行或异步子执行创建一个 stream observer：

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver()
async with executable.stream(input_value, bindings=bindings) as run_stream:
    async for item in run_stream:
        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)

snapshot = observer.snapshot()
```

stream observer 保留子执行的原生 ID，并为其输出添加 `subagentRunId`。展示键应组合根执行、子执行归属和原生 ID。子执行终态来自输出投影与状态保留之后的权威委派结果，不使用子执行私有的 Run 结果。若源只包含一次执行，仍可使用较小的 `HarnessAguiObserver` API。

`observe()` 只返回该源条目产生的 AG-UI 事件。一个源条目可能产生多个事件，例如带初始内容的文本 part 会同时产生 `TEXT_MESSAGE_START` 和 `TEXT_MESSAGE_CONTENT`。

首次成功观测的条目绑定 `observer.thread_id` 和 `observer.run_id`。后续根条目保持该关联；每个内联子执行独立验证关联并维护分片状态。Harness 启动另一次根执行或异步子执行时，使用另一个 observer，即使两次执行推进的是同一线程。

### 事件映射

语义直接匹配时，observer 使用标准 AG-UI 事件：

| Harness 观测                       | AG-UI 输出                                                       |
| ---------------------------------- | ---------------------------------------------------------------- |
| 文本 part 开始、增量和结束         | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| 推理开始、内容、签名和结束         | 推理消息事件和 `REASONING_ENCRYPTED_VALUE`                       |
| 已完成工具调用                     | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`             |
| 成功的工具结果                     | `TOOL_CALL_RESULT`                                               |
| 已完成执行结果                     | `RUN_FINISHED`                                                   |
| 逻辑执行开始                       | `RUN_STARTED`，包含 `protocolVersion: "1.0"`                     |
| 取消的执行结果                     | `RUN_FINISHED`，包含 cancelled outcome                           |
| 暂停的执行结果                     | `RUN_FINISHED`，包含 interrupt outcome                           |
| 失败的执行结果                     | `RUN_ERROR`                                                      |
| 内联委派                           | `SUBAGENT_STARTED`、`SUBAGENT_FINISHED`、`SUBAGENT_ERROR`        |
| 原生 Pydantic AI `CapabilityEvent` | 以原生 `kind` 命名的 `CUSTOM` 事件                               |
| 其他不匹配的公开观测               | 带命名空间的 `CUSTOM` 事件                                       |

未匹配的 Harness 扩展使用 `a13n.harness.lifecycle` 等名称。未匹配的 Pydantic AI 事件使用 `a13n.pydantic_ai.final_result` 等名称。原生 `CapabilityEvent` 保留具体 kind、Capability ID、可选工具调用关联和公开载荷。每个自定义值也保留公开线程、执行、序号、时间戳和源事件表示。

### 内容与大型自定义事件

Capability 事件保留原生名称和载荷，包括用户自定义 kind。文件编辑仍是前后对比数据，摘要仍是摘要数据，shell 状态仍是状态观测。协议不会把它们转换成 agent 回答或预渲染面板。展示方式由客户端决定。输入使用按来源区分的 CUSTOM 事件，`role` 和 `message_id` 位于 `value.event` 内，`ContentMetadata` 位于顶层；常规显示省略标记为 `display: false` 的内容。公开工具执行值可包含有序的文本、图像、音频、视频或文档 part。工具补充媒体仍仅供模型使用，二进制字节不会进入公开协议。

超过 48 KiB 的自定义事件使用通用 `a13n.stream.fragment` 帧。检查原事件前先重组：

```python
from a13n_stream_protocol import CustomEventAssembler

assembler = CustomEventAssembler()

# For each CUSTOM payload in one live subscription:
complete = assembler.accept(payload)
if complete is not None:
    render_custom(complete["name"], complete["value"])
```

消息帧保留完整 JSON 结构，不截断源内容。assembler 限制待处理内容，并拒绝不完整或不一致的序列；请检查 `assembler.gap`，重置订阅时替换 assembler。重组后的自定义事件是尽力交付的观测，不是持久事件日志。限制和字段见[消息帧契约](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-stream-protocol/00-overview.md#large-custom-events)。

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

默认 `retain_events=True` 时，事件日志没有保留上限。先读取一次 `event_count` 作为上界，再用 `snapshot(start=..., stop=...)` 分批读取固定前缀。位置仅在 observer 内有效，不是传输游标。

设置 `retain_events=False` 可在不保留日志的情况下转换和处理。`observe()` 产生相同事件，`event_count` 始终为零，`snapshot()` 抛出 `AguiObservationError`。只需累积展示内容而不是原始 token 历史时，使用[紧凑的展示检查点](replay.md#restore-a-compact-display-checkpoint)。

## 应用 Host 处理器

处理器可在事件累积并返回前，省略事件，或替换[处理器替换限制](api-reference.md#processor-replacement-limits)中列出的可修改内容字段：

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
