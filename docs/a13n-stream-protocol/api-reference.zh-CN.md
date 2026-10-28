---
title: 公开 API 与载荷参考
sidebarTitle: API 与载荷参考
description: 查阅 observer 与展示 API、解析自定义载荷，并选择处理器可替换的字段。
---

实现消费端时使用本页。可运行示例见[快速入门](getting-started.md)；集成顺序和检查点处理见[事件与处理器](events.md)及[重放与恢复](replay.md)。

## 公开名称

从 `a13n_stream_protocol` 导入以下名称：

| 名称                         | 用途                                                   |
| ---------------------------- | ------------------------------------------------------ |
| `HarnessAguiObserver`        | 转换严格只包含一次执行的源                             |
| `HarnessAguiStreamObserver`  | 转换一个根流及其带归属标识的内联子执行                 |
| `AguiEventProcessor`         | 为同步 `(source, event) -> event or None` 回调标注类型 |
| `AguiObservationError`       | 处理无效观测、关联、重放或替换                         |
| `AUTHORED_INPUT_EVENT_NAMES` | 选择用户与引导输入                                     |
| `ContentMetadata`            | 读取展示、来源、媒体和应用元数据                       |
| `tool_result_content`        | 将公开工具值投影为文本或有序内容 part，不执行媒体 I/O  |
| `fragment_custom_event`      | 拆分过大的 CUSTOM 事件                                 |
| `CustomEventAssembler`       | 从有序帧重组完整 CUSTOM 事件                           |
| `__version__`                | 读取已安装分发包版本                                   |

两个 observer 类都接受 `processor=None` 和 `retain_events=True`：

| 成员                                   | 行为                                        |
| -------------------------------------- | ------------------------------------------- |
| `observe(item)`                        | 以独立元组返回新转换事件                    |
| `event_count`                          | 统计保留的、经处理器处理后的帧              |
| `snapshot(*, start=0, stop=None)`      | 读取半开区间 `[start, stop)` 内的独立帧副本 |
| `await resume(history)`                | 用有限异步源前缀重建全新 observer           |
| `export()`                             | 导出独立转换续接状态，不包含事件日志        |
| `restore(continuation, processor=...)` | 从保存状态创建不保留日志的 observer         |
| `thread_id`, `run_id`                  | 读取绑定的源身份；初始为 `None`             |

### 展示 API

从 `a13n_stream_protocol.display` 导入 `DisplayFold`、`DisplaySnapshot`、`DisplayContinuation`、`Item` 和 `StreamPosition`。

| 操作                                                                    | 结果                                     |
| ----------------------------------------------------------------------- | ---------------------------------------- |
| `DisplayFold(run_id, *, attempt=0, full_content=False, processor=None)` | 创建用于转换与展示累积的 fold            |
| `fold.events(source)`                                                   | 将原生源条目转换为序列化 AG-UI 事件      |
| `fold.fold(events, source=None)`                                        | 累积事件并返回序号与条目引用             |
| `fold.export()`                                                         | 导出全部条目及续接状态的独立副本         |
| `DisplayFold.restore(snapshot, processor=None)`                         | 恢复该检查点                             |
| `fold.capture_delta()`                                                  | 导出上次捕获以来的变更条目及当前续接状态 |
| `fold.export_continuation()`                                            | 单独导出解析状态                         |

默认预览将每个文本、参数或结果字段限制为 262,144 个字符，并标记截断。大型观测载荷保留名称，而非复制完整载荷。`full_content=True` 保留完整展示内容；保留的对话大小仍随条目数量和内容大小增长。按照[检查点步骤](replay.md#restore-a-compact-display-checkpoint)，将内容、续接状态和后续事件选择保持一致。

## 对完整自定义事件分片

运行这个内存中的拆分与重组示例，检查消费端分片处理流程：

```python
from ag_ui.core.events import CustomEvent
from a13n_stream_protocol import CustomEventAssembler, fragment_custom_event

original = CustomEvent(name="example.document", value={"text": "x" * 60_000})
frames = fragment_custom_event(original, identity="document-1")
assembler = CustomEventAssembler()
complete = None
for frame in frames:
    complete = assembler.accept(frame.model_dump(mode="json", by_alias=True))
assert complete is not None
assert complete["name"] == "example.document"
assert complete["value"] == original.value
assert not assembler.gap
```

向 `accept()` 传入完整序列化 CUSTOM 封装。JSON 的 UTF-8 编码不超过 48 KiB 时，事件保持完整。更大事件转换为包含 `id`、`index`、`count` 和字符串 `data` 的 `a13n.stream.fragment` 帧。可能交错的事件应使用不同标识。

Assembler 默认允许 64 MiB 待处理字节和八个待处理标识，两个上限均须为正数。`accept()` 返回完整事件；等待或拒绝序列时返回 `None`。无效、不一致、乱序、嵌套或超预算的分片会设置 `gap`，该标志在此 assembler 中持续保持。

新订阅使用新的 assembler。续接检查点时，`export()` 和 `restore(state)` 可保留未完成分片；`DisplayFold` 的检查点已包含该状态。Host 负责传输终止与超时，因为无法仅凭静默发现末帧缺失。

## 输入元数据与媒体

从 CUSTOM `value.event.role` 和 `value.event.message_id` 读取输入角色与消息 ID，元数据仍在顶层。`ContentMetadata` 默认为 `display=True`、`source_id=None` 和 `media=False`；`from_native()` 读取字典，其他输入类型返回默认值。额外应用元数据作为展示数据保留。

正常呈现时隐藏 `display=False` 内容。使用 `AUTHORED_INPUT_EVENT_NAMES` 选择用户与引导文本。缓存标记没有展示内容。媒体观测使用 `a13n.input.media` 和 `media=True`：

| 原生输入         | 公开描述                                      |
| ---------------- | --------------------------------------------- |
| 二进制字节       | kind、媒体类型、大小和 `payload_omitted=True` |
| HTTP(S) 文件 URL | URL 和可用的媒体类型                          |
| 其他 URL scheme  | 省略载荷                                      |
| 已上传文件       | 文件 ID、provider 名称和媒体类型              |

按 Host 当前访问策略解析引用。投影不会获取媒体，也不授予文件访问权限。应用若需要恢复原始模型输入，应单独保存原始输入。

## 终结事件

读取 outcome，选择下一步界面行为：

| 事件                                                      | 含义                                 |
| --------------------------------------------------------- | ------------------------------------ |
| `RUN_FINISHED`，success                                   | 展示完成结果与用量                   |
| `RUN_FINISHED`，`outcome.type="interrupt"`                | 将延后调用或审批交给 Host 回答流程   |
| `RUN_FINISHED`，`outcome.type="cancelled"`                | 显示取消                             |
| `RUN_ERROR`                                               | 显示可安全公开的失败代码、消息和用量 |
| `SUBAGENT_STARTED`, `SUBAGENT_FINISHED`, `SUBAGENT_ERROR` | 更新对应内联子执行的展示             |

Interrupt 中，`id` 标识用于回答的延后目标，`toolCallId` 标识用于历史关联的模型调用。直接调用的两者相同。回答验证仍由 Host 决定。不兼容 JSON 的完成结果会被省略，并在 `rawEvent` 中标记 `result_omitted`。

源元数据保留线程、执行、序号和发生时间。通过 Host 记录确认结果持久化、外部交付和计费；仅有终结展示事件无法确认这些结果。

## 处理器替换限制

处理器在分片前接收完整事件。返回 `None` 省略事件，或替换以下内容字段，同时保留全部结构字段：

| 事件类别                    | 可修改字段        |
| --------------------------- | ----------------- |
| 文本/推理内容和工具参数增量 | `delta`           |
| 加密推理                    | `encrypted_value` |
| 工具结果                    | `content`         |
| 执行完成                    | `result`          |
| 执行错误                    | `message`         |
| CUSTOM 和未列出的事件       | 无                |

保持类型、ID、时间戳、生命周期和源关联不变。策略要求隐藏 CUSTOM 载荷时，省略整个事件。验证在源条目提交前完成。处理器保持确定且无副作用；仅在 `observe()` 返回后持久化或发布批次。
