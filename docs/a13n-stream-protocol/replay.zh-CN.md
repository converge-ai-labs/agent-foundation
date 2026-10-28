---
title: 重放与恢复
description: 保存尚未结束的展示，恢复后从下一事件或源条目继续。
---

消费端重启后，应继续尚未结束的消息，而不是将其显示两遍。在同一位置保存展示内容和解析状态，再提供完整的后续事件。以下示例将这项工作与重建事件转换、继续 Agent 执行分别说明。

## 选择恢复路径

根据 Host 保留的数据选择：

| 保存的数据                  | API                                                              | 结果                         |
| --------------------------- | ---------------------------------------------------------------- | ---------------------------- |
| 展示条目与续接状态          | `DisplayFold.restore(snapshot)`                                  | 恢复消息、工具调用和解析状态 |
| Observer 续接状态           | `HarnessAguiStreamObserver.restore(continuation, processor=...)` | 继续同一根流的转换           |
| 精确、有限的 Harness 源前缀 | `await observer.resume(history)`                                 | 重放源观测以重建转换状态     |
| `HarnessState`              | 启动新的 Harness 执行                                            | 继续 Agent 运行              |

保存检查点时，一并保存选择后续事件所用的 Host 位置。从该位置紧接的下一项继续，避免缺口或重叠。重建前后保持 Harness/Protocol 版本和处理器策略一致。

## 恢复紧凑的展示检查点

在[快速入门环境](getting-started.md)中运行这个完整示例。它在 `Hello` 之后保存消息，将检查点序列化，再恢复并接收 ` world`：

```python
from a13n_stream_protocol.display import DisplayFold, DisplaySnapshot

prefix = [
    {"type": "TEXT_MESSAGE_START", "messageId": "message-1", "role": "assistant", "timestamp": 1000},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": "Hello", "timestamp": 1001},
]
suffix = [
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": " world", "timestamp": 1002},
    {"type": "TEXT_MESSAGE_END", "messageId": "message-1", "timestamp": 1003},
]
fold = DisplayFold("run-example")
fold.fold(prefix)
checkpoint_json = fold.export().model_dump_json()

restored = DisplayFold.restore(DisplaySnapshot.model_validate_json(checkpoint_json))
restored.fold(suffix)

uninterrupted = DisplayFold("run-example")
uninterrupted.fold(prefix + suffix)
assert restored.export() == uninterrupted.export()
item = next(iter(restored.items.values()))
assert item.content["text"] == "Hello world"
assert item.state == "completed"
```

断言验证完整恢复快照、最终文本和完成状态。`export()` 返回独立副本，活跃消息和工具调用保持打开。在 Host 中，将 `checkpoint_json` 与对应交付位置一起持久化。使用处理器时，构造和恢复 fold 都传入相同的 `processor=`。

处理原生 Harness 流时，使用 fold 内置转换器。以下集成片段假设已有 `run_id`、`run_stream`，以及 Host 的存储和交付方法：

```python
fold = DisplayFold(run_id)
async for source in run_stream:
    observed = fold.fold(fold.events(source), source)
    await host.persist_and_publish(observed)
```

传入 `source` 后，fold 除了合并转换事件，还能标记失败的工具结果。在处理完一个源条目后导出。检查点包含原生转换状态，恢复后的 fold 可通过相同循环接收下一源条目。

完整导出成本变高时，使用 `capture_delta()`：

1. 保留前几次捕获的条目。
2. 按条目 ID 合并捕获的变更；修正保留现有 ID。
3. 将合并后的条目与新捕获的续接状态、匹配的 Host 位置一起保存。

增量只包含变更条目，需要先前前缀才能组成完整检查点。检查点捕获与实时交付跟踪彼此独立。分页时只淘汰不可变条目，并保留下一顺序号。展示类型与内容限制见 [API 参考](api-reference.md#display-apis)。

## 从源历史恢复

Host 保留的是 Harness 观测而非转换器检查点时，使用源重放。创建全新的 observer，从有限前缀重建，再跟进下一源条目。这里的 `source_journal`、`cursor` 和 `host` 由应用提供：

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
await observer.resume(source_journal.read_prefix(run_id=run_id, through=cursor))

async for item in source_journal.tail(run_id=run_id, after=cursor):
    await host.persist_and_publish(observer.observe(item))
```

`resume()` 消费异步可迭代对象并返回 `None`，只重建状态，不发布历史事件。设置 `retain_events=True` 时，这些事件也会出现在 `snapshot()` 中；示例避免保留这份额外日志。

### Host 必须保证什么

提供一个根流及其内联子执行的精确有序前缀，并解码为公开 Harness 源条目。序列化 AG-UI 记录应送入 `DisplayFold`，而非 `observer.resume()`。

使用同一个 Host 游标选择前缀边界和实时后续流。在转换前排除重复交付并检测缺口。等待重放完成后，再输入实时观测；一个 observer 接受串行调用。

如果已保存 `observer.export()`，可直接用 `HarnessAguiStreamObserver.restore(saved, processor=process_event)` 恢复。Restore 创建不保留日志的 observer。需再次提供处理器，因为可执行回调不属于保存的续接状态。

### 失败与重试

`resume()` 失败或取消后，observer 保持初始状态。修复历史来源后，用完整前缀重试。成功观测或重放后，使用 `observe()` 继续；该 observer 再次调用 `resume()` 会被拒绝。

## Observer 恢复与 agent 恢复的区别

通过 [Harness 状态与恢复](../a13n-harness/state-and-resume.md)继续模型或工具执行。它会创建新的执行，需要新的 observer；旧执行展示单独保留。Host 触发的压缩执行也遵循这一划分：执行检查点与摘要展示是两种独立保存的数据。

## 错误与原子性

`AguiObservationError` 表示无效关联、重放用法或处理器替换。`TypeError` 表示无效 Python 输入类型。`observe()` 失败时，observer 不提交该源条目，Host 可以修正原因后重试转换。

转换成功后，持久化与交付遵循 Host 的事务契约。该阶段失败不会撤销 observer 已完成的转换。使用保留的批次，或选定检查点与精确后续流恢复交付，不要直接重复输入同一源条目。
