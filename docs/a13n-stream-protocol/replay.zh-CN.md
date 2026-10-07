---
title: 重放与恢复
description: 恢复紧凑的展示检查点，或从精确源历史重建 observer。
---

按 Host 保留的数据选择恢复 API。

## 选择恢复路径

| 保留的数据                  | API                                                              | 恢复内容                  |
| --------------------------- | ---------------------------------------------------------------- | ------------------------- |
| 展示条目与解析续接状态      | `DisplayFold.restore(snapshot)`                                  | 展示内容和 AG-UI 解析状态 |
| Observer 转换续接状态       | `HarnessAguiStreamObserver.restore(continuation, processor=...)` | 同一根流的转换            |
| 精确、有限的 Harness 源前缀 | `await observer.resume(history)`                                 | 转换；可选历史事件快照    |
| `HarnessState`              | 启动新的 Harness 执行                                            | 新的执行中的 agent 运行   |

将检查点条目和续接状态一起保存。从下一事件继续，避免缺口和重叠。

## 恢复紧凑的展示检查点

此示例保存尚未结束的消息，恢复后追加剩余事件：

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

恢复后的消息与不中断的输出相同。导出返回独立检查点，活跃消息和工具调用保持打开。

转换原生 Harness 观测并累积展示：

```python
fold = DisplayFold(run_id)
async for source in run_stream:
    observed = fold.fold(fold.events(source), source)
    await host.persist_and_publish(observed)
```

传入 `source` 还会记录失败工具结果。此检查点保存原生转换状态；恢复后以相同循环处理下一源条目。`host.persist_and_publish` 代表应用自有存储和交付。

分页时只淘汰不可变条目，并保留下一顺序号。仓库私有 `a13n-ui/display` 浏览器模块把检查点和后续 AG-UI 事件合并为展示条目。展示类型和限制见 [API 参考](api-reference.md#display-apis)。

## 从源历史恢复

保留了精确 Harness 源历史时，对初始状态的 observer 使用 `resume()`。下面的 journal 和 Host 方法属于应用：

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
await observer.resume(source_journal.read_prefix(run_id=run_id, through=cursor))

async for item in source_journal.tail(run_id=run_id, after=cursor):
    await host.persist_and_publish(observer.observe(item))
```

传入一个根流及其内联子执行的精确、有限、有序前缀。从同一游标之后继续，排除重复并检测缺口。`resume()` 返回 `None`，不再次发布历史。默认 `retain_events=True` 时，`snapshot()` 还包含历史事件。

### Host 必须保证什么

保持 Harness/Protocol 版本和处理器策略不变。AG-UI 记录送入展示 fold，Harness 源记录送入 `observer.resume()`。

不做重放时，保存 `observer.export()`，再调用 `HarnessAguiStreamObserver.restore(saved, processor=process_event)` 恢复转换。需再次传入处理器。恢复后的 observer 不保留事件日志。

### 失败与重试

`resume()` 失败或取消时，observer 保持初始状态，可用完整历史重试。成功观测或恢复后，使用 `observe()`，不要再次 `resume()`。串行调用，重建完成后再观测实时条目。

## Observer 恢复与 agent 恢复的区别

继续模型或工具执行时，使用 [Harness 状态与恢复](../a13n-harness/state-and-resume.md)。新的 Harness 执行需要新 observer；单独保留旧执行投影。

## 错误与原子性

无效关联或处理器替换抛出 `AguiObservationError`，无效 Python 输入类型抛出 `TypeError`。`observe()` 失败不提交该源条目。通过 Host 自有事务和交付契约持久化成功批次。
