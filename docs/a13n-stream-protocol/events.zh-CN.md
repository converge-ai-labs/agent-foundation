---
title: 事件与处理器
description: 连接 Harness 流、发布事件批次，并在交付前过滤内容。
---

运行[离线快速入门](getting-started.md)后，将其中的打印替换为应用交付流程。按顺序转换每个公开源条目，序列化返回批次，再由 Host 存储或发送。

## 观测 Harness 执行

以下集成片段假设已有 `executable`、`input_value`、`bindings`，以及 Host 自有的 `persist_and_publish()` 方法：

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
async with executable.stream(input_value, bindings=bindings) as run_stream:
    async for item in run_stream:
        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)
```

`new_events` 只包含当前条目产生的事件。带初始内容的文本 part 可以同时产生开始和内容事件。被省略的观测可以产生空批次。发布这些增量批次，无需反复发送累积快照。

为每次独立运行的根执行或异步子执行创建一个 stream observer。首次成功观测会绑定 `thread_id` 和 `run_id`，转发的内联子执行则分别跟踪。严格只包含一次执行的源可使用 `HarnessAguiObserver`。

内联子执行输出携带 `subagentRunId`，同时保留原生消息与工具调用 ID。展示键中应包含该归属标识。子执行完成发生在输出投影、状态保留和委派结果被接受之后，因此父展示反映的是 Harness 实际接受的结果。

### 序列化事件

Observer 返回结构化 AG-UI 模型。向数据库或传输层交付 JSON 兼容载荷前，使用线上字段别名序列化：

```python
from ag_ui.core import Event
from pydantic import TypeAdapter

EVENT_ADAPTER = TypeAdapter(Event)

payloads = [
    EVENT_ADAPTER.dump_python(event, mode="json", by_alias=True)
    for event in new_events
]
```

将持久交付 ID 和游标放在事件外的 Host 封装中。这样既保留协议源关联，也为消费端提供稳定的重连位置。

### 事件映射

用标准事件类型处理消息、工具调用和执行结果；按名称与载荷处理 CUSTOM 事件：

| 源观测                 | AG-UI 输出                                                                            |
| ---------------------- | ------------------------------------------------------------------------------------- |
| 逻辑执行开始           | `RUN_STARTED`，包含 `protocolVersion: "1.0"`                                          |
| 文本开始、内容和结束   | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`                      |
| 推理内容与签名         | 推理消息事件和 `REASONING_ENCRYPTED_VALUE`                                            |
| 完整的工具调用参数     | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`                                  |
| 成功工具结果           | `TOOL_CALL_RESULT`                                                                    |
| 执行完成、取消或暂停   | `RUN_FINISHED`，包含对应 outcome                                                      |
| 执行失败               | `RUN_ERROR`                                                                           |
| 内联委派               | `SUBAGENT_STARTED`, `SUBAGENT_FINISHED`, `SUBAGENT_ERROR`                             |
| 原生 `CapabilityEvent` | 使用原生 `kind` 的 `CUSTOM`                                                           |
| 其他未匹配的公开观测   | 带命名空间的 `CUSTOM`，如 `a13n.harness.lifecycle` 或 `a13n.pydantic_ai.final_result` |

完整参数列表表示工具调用的输入已形成，不表示执行成功。结果需要单独读取。[终结事件](api-reference.md#terminal-events)介绍 outcome 和延后调用 ID。

### 内容与大型自定义事件

自定义载荷保留原有含义：文件编辑携带前后数据，压缩摘要携带摘要数据，shell 观测携带状态。按相应观测类型呈现。压缩摘要可以早于检查点发布；只有 Host 接受并持久化完成结果后，才显示“检查点已保存”。

输入观测的角色与消息 ID 位于 `value.event` 内，展示元数据位于顶层。正常呈现时遵循 `display: false`。工具结果可包含有序的文本、图像、音频、视频或文档 part。公开投影省略二进制载荷，工具补充媒体仅供模型使用；详见[输入元数据与媒体](api-reference.md#input-metadata-and-media)。

超过 48 KiB 的 CUSTOM 事件会变成 `a13n.stream.fragment` 帧。为订阅保留一个 assembler，并传入每个完整序列化 CUSTOM 封装：

```python
from a13n_stream_protocol import CustomEventAssembler

assembler = CustomEventAssembler()

# payload is a complete serialized CUSTOM event from this subscription.
complete = assembler.accept(payload)
if complete is not None:
    render_custom(complete["name"], complete["value"])
```

只解释组装完成的事件。检查 `assembler.gap` 以报告被拒绝或缺失的分片；启动新订阅时重置 assembler。末帧缺失需要 Host 通过终止或超时处理。[分片示例](api-reference.md#fragment-a-complete-custom-event)演示离线拆分与重组，并列出限制。

## 应用 Host 处理器

Host 需要隐藏事件或修改允许的内容字段时，传入同步处理器。本例省略诊断观测，并移除文本增量两端的空字符：

```python
from typing import Any

from ag_ui.core import Event
from ag_ui.core.events import CustomEvent, TextMessageContentEvent
from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiStreamObserver


def process_event(
    source: HarnessStreamEvent[Any],
    event: Event,
) -> Event | None:
    del source
    if isinstance(event, CustomEvent) and event.name == "a13n.harness.diagnostic":
        return None
    if isinstance(event, TextMessageContentEvent):
        return event.model_copy(update={"delta": event.delta.strip("\x00")})
    return event


observer = HarnessAguiStreamObserver(processor=process_event, retain_events=False)
```

Observer 在分片前应用处理器，并验证每个替换结果。保持事件身份、关联和结构不变；[替换字段表](api-reference.md#processor-replacement-limits)列出了可写字段。需要隐藏 CUSTOM 内容时，省略整个事件。

处理器应是只依赖输入与稳定 Host 配置的纯函数。同一源历史重放时应产生相同事件。在 `observe()` 返回后存储和发布，放在处理器之外。这样，无效替换也不会导致当前源条目被部分提交。

## 读取累积快照

调试或进程内检查时，使用默认 `retain_events=True` 构造 observer。捕获当前事件数，再分批读取固定前缀：

```python
stop = observer.event_count
for start in range(0, stop, 100):
    events = observer.snapshot(start=start, stop=min(start + 100, stop))
    inspect_batch(events)
```

返回的事件是独立副本，修改副本不影响 observer。日志没有保留上限，位置是本地索引，不是传输游标。

设置 `retain_events=False` 时，`observe()` 返回相同的转换事件，`event_count` 保持为零，`snapshot()` 抛出 `AguiObservationError`。需要累积展示内容且无需保留每个 token 事件时，选择 [DisplayFold 检查点](replay.md#restore-a-compact-display-checkpoint)。
