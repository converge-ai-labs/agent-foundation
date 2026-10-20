---
title: 定时计划
description: 定期运行 Agent，并查看每次触发的输入和执行结果。
---

Schedule 按指定时间规则向 Agent 发送固定文本或 JSON。每次触发都会在计划专属 Session 中创建新 Thread，使用独立的对话历史。Session 提供固定的观测入口。

## 创建和管理计划

在 Console 中打开 **计划 → 创建计划**：

1. 填写名称、五字段 cron 表达式和明确的 IANA 时区。例如 `0 9 * * *` 配合 `Asia/Shanghai` 表示每天当地时间 09:00。
2. 点击 **预览触发时间**，检查当地时间和 UTC 时间。夏令时跳过的当地时刻不会触发；重复时刻对应两个独立时间点，仍需满足避免重叠的规则。
3. 选择 Agent 并填写输入。版本留空时跟随最新默认版本，填写版本 ID 则固定该版本。JSON 编辑器支持完整调用配置，包括 Run 选项和显式 Memory 挂载。
4. 创建后计划默认暂停。可以点击 **运行一次** 验证，或点击 **启用** 开始定期提交。

创建需要工作空间的 `write` 和 `run` 权限。只有创建者能编辑、启用或手动触发计划；其他拥有 `write` 权限的成员可以暂停或归档。归档后停止后续触发，保留历史，且不可恢复。需要更换执行身份时，应使用新身份创建另一个计划。

**计划会保存创建者授予的执行权限。退出登录、凭据过期或撤销原 API Key 不会停止计划。** 如需停止后续任务，请暂停或归档计划。禁用主体或撤销其执行权限也会阻止后续提交。工具审批仍然有效。

修改仅影响后续触发。时间或调用配置变更会取消尚未提交的输入；暂停和归档也会取消待提交输入，但不会取消已经提交的输入或 Run。最大延迟限制的是提交输入的时间，不是 Run 完成时间。

## 查看结果

详情页分别显示三种状态：

| 状态     | 含义                                                                                                                           |
| -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 提交状态 | `pending` 表示触发意图已保存，`submitted` 表示 Thread 和收件箱 Entry 已保存。`skipped`、`failed` 和 `cancelled` 可能没有 Run。 |
| 输入状态 | Entry 是否待处理、已分配给 Run、已消费或失败。                                                                                 |
| Run 状态 | 执行结果，包括等待审批或用户输入。                                                                                             |

**已提交不等于执行成功。** 点击 **查看输出** 可打开原有 Run 页面，查看输出、工具和排队输入。在同一 Thread 中继续提交输入会保留该计划来源；显式分支和独立 Thread 不继承来源。

较早触发的任务尚未结束时，新的触发会跳过，包括等待中的 Run 和未结束的子任务。错过多个时间点时只处理最近一个到期时间，不逐个补跑。临时提交失败会在延迟和尝试次数限制内重试；Run 执行失败不会重新提交原输入。

## 使用 HTTP API

所有接口使用标准的[工作空间和认证请求头](http.md#workspace)。`POST /api/v1/schedules` 需要 `Idempotency-Key`，请求示例：

```json
{
  "name": "Morning report",
  "schedule": {"type": "cron", "expression": "0 9 * * *", "timezone": "Asia/Shanghai"},
  "invocation": {
    "agent_id": "AGENT_ID",
    "agent_revision_id": null,
    "payload": {"content": [{"type": "text", "text": "Prepare the morning report."}]}
  }
}
```

响应包含 `id`、固定的 `session_id`、`stream_url` 和 ETag。首次创建返回 201，同一请求体使用相同请求键重试返回已有计划和 200。响应丢失时应保留原请求键。

- 通过 `/api/v1/schedules/{id}` 读取或修改配置。PATCH 需要在 `If-Match` 中提供上次读取的 ETag。
- 向 `/{id}/enable` 或 `/{id}/pause` POST 空对象，并携带 `If-Match`。DELETE `/{id}` 使用同样的版本条件归档计划。
- 向 `/{id}/dispatch` POST 空对象，携带 `If-Match` 和 `Idempotency-Key`。202 表示待提交 Occurrence 已保存；重放返回原 Occurrence 和 200，即使它已经结束。
- 通过 `/{id}/status` 读取当前进度，通过 `/{id}/occurrences` 分页读取触发记录，通过 `/{id}/occurrences/{occurrence_id}` 读取保存的调用快照。

在触发记录的列表或详情查询中添加 `?include=execution`，可以在同一响应中获得当前执行状态。每条记录的 `execution` 包含 `initial_entry_status`、`initial_run`（ID 和状态，或 null）、`has_unfinished_work` 和 `observed_at`。不提供此参数时，响应不包含该字段。列表查询按整页批量读取这些状态；Console 使用此展开结果，不再逐条查询 entry 和 run。

初始 run 是触发记录的第一条输入分配到的 run，后续 run 不会替换摘要中的初始 run。`has_unfinished_work` 包括关联子任务和等待中的 run；false 不代表执行成功。尚未提交输入的记录，其初始 entry 状态和 run 都为 null。执行状态变化时，触发记录仍可能保持相同版本和 `submitted` 状态。收到 thread 变化或重新连接后，应刷新展开的历史查询。完整输出继续通过原 run items 接口读取。

完整筛选条件和字段见 API 参考。计划接口目前仅支持 HTTP；Composer 和 MCP 接入属于后续工作。

## 订阅状态和加载历史

使用返回的 `stream_url`：`/api/v1/sessions/{session_id}/stream`，无需额外查询参数。计划 Session 在原有[会话输出协议](agents-and-runs.md#follow-a-session-stream)中增加 `schedule` 和 `occurrence` 状态帧。

每次连接先读取计划当前状态、触发记录摘要及 Session 中的所有 Thread，随后持续接收状态变化和实时输出。已完成的 Thread、子 Thread 和独立 Thread 都不会因时间较早而被排除。通过历史分页浏览触发记录，通过详情查询读取完整执行意图，通过原 Run items 接口读取已保存的输出。

事件流同步当前状态，不是持久化状态变更日志，快速变化的中间状态可能被合并。重连时还应刷新当前历史页和选中的旧 Thread、Entry、Run 及输出。旧 Run 可能在断线期间完成，而已经提交的 Occurrence 不会随之变化。Console 会自动执行这些刷新。
