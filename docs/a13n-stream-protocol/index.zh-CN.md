---
title: Stream Protocol
sidebarTitle: 概览
description: 将 Harness 公开观测转换为结构化 AG-UI 事件，供终端、浏览器和传输层使用。
---

Stream Protocol（`a13n-stream-protocol`）将 Harness 输出转成 AG-UI 事件，再合并为终端或浏览器中的展示条目。保存检查点后，可以恢复尚未完成的展示。

## 从这里开始

| 任务                                       | 指南                                                      |
| ------------------------------------------ | --------------------------------------------------------- |
| 无需凭据，运行完整转换示例                 | [快速入门](getting-started.md)                            |
| 映射文本、推理、工具、自定义事件和终结结果 | [事件与处理器](events.md)                                 |
| 消费端重启后重建投影                       | [重放与恢复](replay.md)                                   |
| 查找公开导出、分片限制和载荷格式           | [API 与载荷参考](api-reference.md)                        |
| 从保存状态继续 agent 执行                  | [Harness 状态与恢复](../a13n-harness/state-and-resume.md) |

```mermaid
flowchart TB
    Run["包含内联子执行的根流"] --> Observer["HarnessAguiStreamObserver"]
    Observer --> Events["结构化 AG-UI 事件"]
    Events --> Fold["DisplayFold"]
    Fold --> Checkpoint["条目与续接状态"]
    Events --> Host["Host 传输"]
    Checkpoint --> UI["渲染器"]
    Host --> UI

    class Run,Observer,Fold a13n
    class Checkpoint store
    class Host,UI app
```

## 一个 observer 对应一次执行

`HarnessAguiObserver` 绑定单次执行。`HarnessAguiStreamObserver` 则绑定根流，独立跟踪其中的内联子执行，并用 `subagentRunId` 标识其输出归属。Host 管理的异步子执行仍使用独立 observer。

`observe()` 返回当前条目的事件。默认还保留供 `snapshot()` 使用的事件日志。Host 自行存储增量事件或使用 `DisplayFold` 时，设置 `retain_events=False`。fold 保留累积展示内容和解析续接状态，而不是每个 token 事件。[重放与恢复](replay.md)介绍检查点恢复、observer 续接和源重放。

## 安装

```console
uv add a13n-stream-protocol
```

发布版 Stream Protocol 固定依赖匹配的 Harness 版本。源码[快速入门](getting-started.md)使用仓库锁文件，与跟随 `main` 的本文档保持一致。

## 职责归属

| 职责                                            | 负责方          |
| ----------------------------------------------- | --------------- |
| Harness 执行、源生命周期、结果和 `HarnessState` | Harness         |
| Harness 到 AG-UI 转换及进程内重建               | Stream Protocol |
| 通过重放稳定的处理器表达可见性策略              | Host 处理器     |
| 源历史保留、游标、缺口检测和切换到实时流        | Host            |
| 持久 AG-UI ID、持久化、重放和扇出               | Host            |
| SSE、WebSocket、Redis 或进程内交付              | Host 传输层     |
| 展示条目和解析续接状态                          | Stream Protocol |
| 检查点存储、分页、确认和后续事件选择            | Host            |
| 视觉呈现                                        | 渲染器          |

## 升级到 AG-UI 1.0

请同步升级 Host 与渲染器。Python 使用 `ag-ui-protocol>=1,<2`；浏览器消费上游 `@ag-ui/core` 类型与 schema，不替换原有传输客户端。标准 wire 字段使用 camelCase，不提供 0.x 解码器或别名层。

- 逻辑执行在准备成功后、公开输出前只发送一次 `RUN_STARTED`。
- 取消和暂停使用带 cancelled 或 interrupt outcome 的 `RUN_FINISHED`，只有失败使用 `RUN_ERROR`。延后调用保留原生 ID，Host 回答验证策略不变。
- 输入使用 CUSTOM `value.event.role` 和 `value.event.message_id`，元数据仍在顶层。
- 工具结果可以包含有序的上游内容 part；隐藏的补充媒体不会公开。二进制数据和不安全 URL 转为省略载荷的描述，绝不包含内联字节；provider 文件句柄保持为 `FileSource` 引用，不提供可下载 URL。
- 内联子执行的展示键按 `subagentRunId` 隔离，子回答不成为根回答。旧记录中缺失的子执行展示不能从模型历史重建。

协议升级不删除 Harness 续接状态或用量账本。

## 下一步

- 阅读 [Harness 指南](../a13n-harness/index.md)，了解构建、流式输出和恢复 agent。
- 阅读[包 README](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-stream-protocol)，了解软件包和发布详情。
- 查阅 [Stream Protocol 规范](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/a13n-stream-protocol)，了解规范性观测契约和 schema 边界。

## 参考主题

| 主题                                                                            | 指南                                                                       |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| <span id="observe-a-harness-run"></span>观测 Harness 执行                       | [观测 Harness 执行](events.md#observe-a-harness-run)                       |
| <span id="read-the-accumulated-snapshot"></span>读取累积快照                    | [读取累积快照](events.md#read-the-accumulated-snapshot)                    |
| <span id="apply-a-host-processor"></span>应用 Host 处理器                       | [应用 Host 处理器](events.md#apply-a-host-processor)                       |
| <span id="resume-from-source-history"></span>从源历史恢复                       | [从源历史恢复](replay.md#resume-from-source-history)                       |
| <span id="resume-is-not-agent-recovery"></span>observer 恢复与 agent 恢复的区别 | [observer 恢复与 agent 恢复的区别](replay.md#resume-is-not-agent-recovery) |
| <span id="errors-and-atomicity"></span>错误与原子性                             | [错误与原子性](replay.md#errors-and-atomicity)                             |
