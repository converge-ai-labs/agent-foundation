---
title: 重放与恢复
description: 重建同一次执行的 UI 投影，并判断何时应创建新的 Harness 执行。
---

重启有两种不同问题：为**同一次** 执行重建 UI 投影，以及从保存的 `HarnessState` 启动**新的** Harness 执行。`HarnessAguiObserver.resume()` 只解决前者。

## 选择恢复路径

| 发生了什么变化？                               | 处理方式                                                       |
| ---------------------------------------------- | -------------------------------------------------------------- |
| 观测消费端重启；同一次源执行仍可用             | 将精确、有限的源前缀重放到新 observer，再消费后续实时流        |
| Host 从 `HarnessState` 启动了新的 Harness 执行 | 为新的执行 ID 创建新 observer                                  |
| 只保留了渲染消息或 AG-UI 记录                  | 不要将它们传给 `resume()`；它们无法重建 Harness 多 part 源状态 |
| 源历史存在缺口                                 | 在 Host 中报告并核对，不能悄悄跳过                             |

## 从源历史恢复

新进程需要为已有 Harness 执行重建 observer 时，使用 `resume()`。下面的 `source_journal` 方法演示 Host 管理的历史和实时尾流接口，不由此包提供：

```python
observer = HarnessAguiObserver(processor=process_event)

await observer.resume(
    source_journal.read_prefix(
        run_id=run_id,
        through=handoff_cursor,
    )
)

async for item in source_journal.tail(
    run_id=run_id,
    after=handoff_cursor,
):
    new_events = observer.observe(item)
    await host.persist_and_publish(new_events)
```

参数是有限的 `AsyncIterable[HarnessStreamEvent[Any]]`。它必须按原顺序产出一次执行（`HarnessAguiObserver`）或一个根流及其内联子执行（`HarnessAguiStreamObserver`）的精确公开源前缀，并在 Host 选择的交接点结束。`resume()` 返回后，将后续实时条目传给 `observe()`。

`resume()`:

1. 创建独立暂存状态；
2. 按与 `observe()` 相同的转换和处理器路径处理每个历史条目；
3. 仅在迭代成功结束后，以原子方式接纳重建状态；
4. 返回 `None`，避免意外再次发布历史事件。

恢复成功后：

```python
historical_projection = observer.snapshot()
next_events = observer.observe(next_live_item)
```

快照包含重建的历史 AG-UI 投影，`next_events` 只包含新产生的实时输出。

### Host 必须保证什么

Host 负责 `resume()` 周围的源历史契约：

- 保留或重建结构化公开 `HarnessStreamEvent` 值；
- 为精确的一个 `run_id`（`HarnessAguiObserver`）或一个根流及其内联子执行（`HarnessAguiStreamObserver`）选择有限前缀；
- 保留源顺序并排除重复交付；
- 检测保留缺口，不悄悄省略源条目；
- 从重放切换到实时流时，既没有缺口也没有重叠；
- 为所选 Harness/Protocol 版本解码或迁移保留的源值；
- 独立于 Harness 源序号保留持久 AG-UI 事件 ID。

AG-UI 交付记录、压缩后的显示消息和渲染器快照不能替代 Harness 源历史。它们是有损投影，不包含重建多 part 转换状态所需的全部信息。

### 失败与重试

历史迭代、转换、关联验证或处理失败，或任务取消时，原 observer 仍保持初始状态。Host 可以打开另一个完整历史迭代器并重试：

```python
observer = HarnessAguiObserver(processor=process_event)

try:
    await observer.resume(primary_history)
except Exception:
    await observer.resume(reopened_complete_history)
```

成功观测或成功恢复后，不要再次调用 `resume()`。恢复期间，`observe()` 和另一个 `resume()` 会抛出 `AguiObservationError`。重建提交前，属性和 `snapshot()` 继续公开恢复前的初始状态。

## Observer 恢复与 agent 恢复的区别

observer 重建与 Harness 恢复是两个独立操作。

只重启观测消费端，且同一次执行源历史仍可用时，从该执行前缀重建一个 observer，再继续处理其实时尾流。

worker 接管从 `HarnessState` 启动新 Harness 执行时，新执行具有新的 `run_id` 和序号域。创建新 observer：

```python
previous_observer = HarnessAguiObserver(processor=process_event)
await previous_observer.resume(previous_run_history)

# Worker recovery starts another Harness Run.
current_observer = HarnessAguiObserver(processor=process_event)
async for item in current_run_stream:
    new_events = current_observer.observe(item)
    await host.persist_and_publish(new_events)
```

Host 可以在同一会话、Service Run 或 Execution 时间线中保留两次 Harness 执行的投影，但不能把较早执行送入新 observer。`resume()` 绝不会重建模型执行、工具、凭据、环境权限、租约或 `HarnessState`。

## 错误与原子性

`AguiObservationError` 报告以下语义转换错误：

- 同一 observer 内的线程或执行关联发生变化；
- 多 part 中某个 part 的 kind 或身份改变；
- 处理器修改结构字段或事件类型；
- 对非初始状态的 observer 调用 `resume()`；
- 恢复期间进行观测或启动另一恢复。

无效 Python 输入类型抛出 `TypeError`。`observe()` 失败时，该源条目不会累积。`resume()` 失败或取消时，原 observer 整体保持初始状态。

将改变状态的 `observe()` 和 `resume()` 调用串行执行。恢复期间可以读取属性和 `snapshot()`，它们公开恢复前的初始状态。显式恢复门控防止异步历史重建等待下一个历史条目时被实时观测覆盖。
