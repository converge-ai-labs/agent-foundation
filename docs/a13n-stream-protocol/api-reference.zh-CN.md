---
title: 公开 API 与载荷参考
sidebarTitle: API 与载荷参考
description: 包的公开名称、自定义事件、输入元数据和终结事件。
---

Stream Protocol 将 Harness 输出转成 AG-UI 1.0 事件，再合并为展示条目。Host 负责传输和保存检查点；Harness 负责继续执行 agent。

## 公开名称

| `a13n_stream_protocol` 导出项 | 用途                                                                |
| ----------------------------- | ------------------------------------------------------------------- |
| `HarnessAguiObserver`         | 绑定一个线程/执行，观测源条目、获取事件独立快照，或从有限源历史恢复 |
| `HarnessAguiStreamObserver`   | 观测一个根流及其带归属标识的内联子执行，各自维护独立分片状态        |
| `AUTHORED_INPUT_EVENT_NAMES`  | 用户输入与引导输入的事件名称集合                                    |
| `tool_result_content`         | 将公开执行值投影为文本或有序的上游内容 part，不执行媒体 I/O         |
| `AguiEventProcessor`          | 同步 `(source, event) -> event or None` Host 投影回调               |
| `AguiObservationError`        | 关联、观测、重放或处理器替换无效                                    |
| `ContentMetadata`             | 展示约定及不透明的额外元数据                                        |
| `fragment_custom_event`       | 拆分过大的 CUSTOM 事件，不丢失领域 JSON                             |
| `CustomEventAssembler`        | 在一个有界订阅内重组有序帧                                          |
| `__version__`                 | 已安装分发包版本                                                    |

两个 observer 类均接受 `processor=None` 和 `retain_events=True`。提供 `observe(item)`、`snapshot(*, start=0, stop=None)`、`event_count`、异步 `resume(history)`、`export()`、类方法 `restore(continuation, processor=...)` 及只读 `thread_id` / `run_id`。Restore 创建不保留日志的 observer。观测或重建成功前不绑定 ID。

### 展示 API

从 `a13n_stream_protocol.display` 导入展示类型，而不是从包根导入：

| API                                                                | 用途                                                        |
| ------------------------------------------------------------------ | ----------------------------------------------------------- |
| `DisplayFold(run_id, *, attempt=0, full_content=False)`            | 转换原生观测，将事件合并为展示条目                          |
| `fold.events(source)`                                              | 使用 fold 内不保留日志的 stream observer 转换一个原生源条目 |
| `fold.fold(events, source=None)`                                   | 累积事件，返回序号和条目引用                                |
| `fold.export()` / `DisplayFold.restore(snapshot)`                  | 导出独立的 `DisplaySnapshot` 并恢复，包含条目和续接状态     |
| `fold.export_continuation()`                                       | 单独导出解析状态，不复制展示条目                            |
| `DisplaySnapshot`, `DisplayContinuation`, `Item`, `StreamPosition` | 验证并序列化检查点内容和续接状态                            |

默认展示预览将每个文本、参数或结果字段限制为 262,144 个字符，并标记截断。大型观测载荷只保留名称，不无限复制载荷。`full_content=True` 保留完整展示内容。两种模式都不是恒定内存：保留的条目仍随对话增长。

实时转换见[事件与处理器](events.md)；可运行的检查点恢复示例和源重建见[重放与恢复](replay.md)。

## 对完整自定义事件分片

这个完整示例在内存中完成拆分与重组，无需网络或 agent：

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

向 `accept()` 传入**完整序列化 CUSTOM 封装**，不要只传 `value`。UTF-8 编码后不超过 48 KiB 的事件保持完整。更大事件转换为 `a13n.stream.fragment`，携带 `id`、`index`、`count` 和字符串 `data`。标识应足够唯一，避免订阅中交错事件相互混淆。

assembler 默认允许 64 MiB 待处理字节和八个待处理标识；两个上限都必须为正数。整个事件重建完成前，或分片被拒绝时，返回 `None`。无效、不一致、乱序、嵌套或超预算序列会设置持续保持的 `gap` 标志。绝不发布不完整的领域事件。

一个 assembler 对应一个实时订阅。重连时创建新的 assembler。没有后续帧时，不能仅凭静默检测缺失尾部；Host 负责流终止、超时和缺口展示。分片组装不是持久重放。

## 输入元数据与媒体

`ContentMetadata` 默认为 `display=True`、`source_id=None` 和 `media=False`，允许不透明的额外元数据。`from_native()` 读取元数据字典，输入不是字典时返回默认值。元数据用于展示，不是指令或授权。

文本输入使用 `a13n.input.user` 等按来源区分的 CUSTOM 事件；`role` 和 `message_id` 位于 `value.event` 内，展示元数据仍在顶层。客户端通常隐藏 `display=False` 的内容。缓存标记不产生展示内容。媒体使用 `a13n.input.media`，并设置 `media=True`：

| 原生输入         | 投影                                                        |
| ---------------- | ----------------------------------------------------------- |
| 二进制字节       | kind、媒体类型、大小和 `payload_omitted=True`；不含字节载荷 |
| HTTP(S) 文件 URL | 引用 URL 和可用时的可选媒体类型                             |
| 其他 URL scheme  | 省略载荷，不嵌入内联载荷                                    |
| 已上传文件       | 文件 ID、provider 名称和媒体类型                            |

这是单向观测，不是恢复模型输入的编解码器，也不是媒体存储服务。解析应用媒体引用仍需要当前 Host 访问策略。

## 终结事件

- 已完成输出转换为 `RUN_FINISHED`，包含成功结果和用量。不兼容 JSON 的输出会被省略，在 `rawEvent` 中标记 `result_omitted`，不会任意序列化 Python 对象。
- 暂停输出转换为 `RUN_FINISHED`，其 `outcome.type="interrupt"`，每个延后调用或审批对应一个 interrupt。`id` 和 `toolCallId` 保留原生调用 ID；待处理回答策略仍由 Host 决定。
- 取消转换为 `RUN_FINISHED`，其 `outcome.type="cancelled"`。
- 失败转换为 `RUN_ERROR`，包含可安全公开的失败代码、消息和用量。
- 内联子执行使用 `SUBAGENT_STARTED`、`SUBAGENT_FINISHED` 和 `SUBAGENT_ERROR`；内容携带 `subagentRunId`，不嵌套根执行生命周期。

源关联包含线程、执行、序号和发生时间。终结展示事件不代表 Host 已持久提交结果、交付消息或结算计费。

## 处理器替换限制

处理器在分片**之前** 看到完整领域事件。返回 `None` 可以省略事件。替换保留事件类型、ID、时间戳、生命周期/源关联和所有结构字段。只有以下内容字段可修改：

| 事件类别                        | 可修改字段        |
| ------------------------------- | ----------------- |
| 文本/推理内容、工具调用参数增量 | `delta`           |
| 加密推理                        | `encrypted_value` |
| 工具结果                        | `content`         |
| 执行完成                        | `result`          |
| 执行错误                        | `message`         |
| CUSTOM 和未列出的事件           | 无                |

不支持改写 CUSTOM 载荷；策略要求隐藏时，省略整个事件。替换在源条目提交前验证。重放处理器必须确定且无副作用；只有 observer 返回新提交事件后才发布或持久化。
