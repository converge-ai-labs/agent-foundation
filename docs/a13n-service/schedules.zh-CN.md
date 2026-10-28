---
title: 定时计划
description: 预览计划，测试一次触发，再启用重复或一次性的 agent 任务。
---

用定时计划在未来时间或按重复时间表向 agent 发送固定文本或 JSON。每次触发创建新线程，使用独立的对话历史。所有触发都属于计划的专用会话，可在那里持续查看输出。

先准备一个已能通过交互完成任务的 agent。创建计划需要工作空间的 `write` 和 `run` 权限。启用自动执行前，先测试一次手动触发。

## 创建和管理计划

以每日晨报为例，在 Console 中打开**计划 → 创建计划**：

1. 命名为 `Morning report`，选择**重复**。输入 `0 9 * * *`，IANA 时区设为 `Asia/Shanghai`，表示每天当地时间 09:00。
2. 选择**预览触发时间**，保存前检查当地时间与 UTC 时间。
3. 选择 agent，输入“Prepare the morning report.”。版本留空时跟随当前默认版本，填写版本 ID 则固定版本。JSON 编辑器还接受运行选项和显式记忆挂载。
4. 创建计划，初始状态为暂停。选择**运行一次**，再用**查看输出**检查 agent 结果，处理审批或输入请求。
5. 测试结束后选择**启用**。检查下次触发时间，并查看首次自动执行的结果。

保存后打开详情页，核对目标 agent、所选修订和固定会话。按保存顺序检查每个文本或 JSON 输入部分，输入与描述分开展示。会话链接打开执行历史；首次触发前则显示空会话。无法编辑时，页面会说明原因：计划已归档或完成、你不是创建者，或工作空间权限不足。

Cron 表达式有五个字段。夏令时切换时，不存在的当地时刻不会触发；重复时刻对应两个时间点，各自遵循下文的重叠规则。预览用于核对日历规则，实际执行仍取决于服务扫描和运行接收。

只有创建者能编辑、启用或手动触发计划。其他拥有 `write` 权限的成员可以暂停或归档。归档保留历史，且不可恢复。需要更换执行身份时，用新身份创建另一个计划。

计划保存创建者的执行授权。退出登录、凭据过期和撤销原 API 密钥不会停止计划。**要停止后续提交，请暂停或归档计划。** 禁用主体或撤销其执行权限也会阻止后续提交。工具审批仍然有效。

修改时间或调用配置会取消仍待提交的输入。暂停和归档也会取消待提交输入，已经提交的输入和运行则继续。最大延迟限制提交时间，不限制运行完成时间。

## 一次性计划

选择**一次性**，在界面显示的浏览器时区中选择未来日期和时间。创建暂停状态的计划，按需测试，然后启用。如果暂停期间已过指定时间，先设置新的未来时间再启用。

**运行一次**是独立的手动触发，保留自动触发。因此，尚未结束的手动测试可能让自动触发因重叠而跳过。

自动提交成功、被跳过或最终失败后，计划进入 `completed`，显示为**调度已结束**。执行结果需单独查看触发记录。已结束的计划可以读取或归档；要安排另一个时间，创建新计划。

服务中断后，仍可在原最大延迟窗口内提交，默认 300 秒。超出窗口的触发为 `skipped`，原因为 `missed_deadline`，一次性调度随之结束。重试沿用同一窗口。

## 查看结果

打开计划详情页，选择一条**触发记录**（occurrence），依次检查提交、输入处理和执行：

| 检查项               | 含义                                                          |
| -------------------- | ------------------------------------------------------------- |
| 触发记录 `pending`   | 已保存触发意图                                                |
| 触发记录 `submitted` | 已保存线程和收件箱条目                                        |
| 收件箱条目           | 输入待处理、已分配给运行、已消费或失败                        |
| 运行                 | 输出、工具调用和等待请求                                      |
| 触发记录 `completed` | 主线程执行结束；检查 `execution_outcome` 区分成功、失败或取消 |

触发记录的 `skipped`、`failed` 和 `cancelled` 描述提交结果。执行结束时，`completion_run_id` 标识主线程中首个完成、失败或取消的运行；`completed_at` 记录结束时间。`finished_at` 仍是提交结束时间。等待和尝试重试会保持执行未结束。

较早的触发记录仍待提交，或其主线程中还没有完成、失败或取消的运行时，调度器会避免重叠。等待中的运行在恢复期间仍算忙碌。已提交但尚无运行的输入也算忙碌，即使输入已被拒绝或撤回；仅归档线程不会使其结束。

主线程中的运行达到上述结束状态后，后续对话、分支和子任务不会推迟下次触发，也不会重新打开该记录。同一线程新增输入保留计划来源；显式分支和独立线程不继承来源。错过多个重复时间点时，只合并处理最近一个到期时间。临时提交失败在延迟和尝试次数限制内重试；执行失败不会重新提交输入。

## 使用 HTTP API

使用标准的[工作空间和认证请求头](http.md#workspace)。将 `AGENT_ID` 替换为可用 agent 的 ID，用新的 `Idempotency-Key` 向 `POST /api/v1/schedules` 发送以下请求体：

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

`agent_revision_id` 是必填字段：用 `null` 跟随当前默认版本。响应包含计划 `id`、固定的 `session_id`、`stream_url` 和 ETag。首次创建返回 201；相同请求体与请求键重放返回已有计划和 200。响应丢失时保留原请求键。

一次性任务将 `schedule` 对象替换为未来的带偏移时间戳：

```json
{"type": "once", "run_at": "2030-10-21T09:00:00+08:00"}
```

Service 保存和返回 UTC，拒绝没有偏移的时间戳和相对延迟。`POST /api/v1/schedules/preview` 接受 `{schedule, after, count}`；`after` 需要偏移，`count` 为 1–10。一次性预览返回零个或一个较晚时间点，`utc` 和 `local` 均使用 UTC。

测试暂停中的计划时，设置 `A13N_URL` 和限定到工作空间的 `A13N_API_KEY`。将 `SCHEDULE` 设为返回的 ID，`SCHEDULE_ETAG` 设为包含引号的 ETag，`DISPATCH_KEY` 设为本次手动触发的新请求键：

```sh
curl -X POST "$A13N_URL/api/v1/schedules/$SCHEDULE/dispatch" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "If-Match: $SCHEDULE_ETAG" -H "Idempotency-Key: $DISPATCH_KEY" \
  -d '{}'
```

HTTP 202 表示已保存待提交的触发记录。响应不确定时复用原请求键；即使记录已经结束，重放仍返回该记录和 200。通过 `GET /api/v1/schedules/{id}/occurrences?include=execution` 读取进度，再用返回的线程和运行 ID 检查输出。

| 操作                   | 请求                                                          |
| ---------------------- | ------------------------------------------------------------- |
| 读取配置与当前 ETag    | `GET /api/v1/schedules/{id}`                                  |
| 修改后续配置           | `PATCH /api/v1/schedules/{id}`，携带 `If-Match`               |
| 启用或暂停             | 向 `/{id}/enable` 或 `/{id}/pause` POST `{}`，携带 `If-Match` |
| 归档                   | DELETE `/{id}`，携带 `If-Match`                               |
| 读取当前进度           | `GET /api/v1/schedules/{id}/status`                           |
| 分页读取触发历史       | `GET /api/v1/schedules/{id}/occurrences`                      |
| 读取触发保存的调用配置 | `GET /api/v1/schedules/{id}/occurrences/{occurrence_id}`      |

表中短路径相对于 `/api/v1/schedules`。在计划列表上使用 `?state=completed`，查找已结束调度的一次性计划。

触发记录列表和详情接受 `include=execution`，增加 `initial_entry_status`、可为 null 的 `initial_run`、`has_unfinished_work` 和 `observed_at`。初始运行始终是首条输入分配到的运行，后续恢复不会替换它。`has_unfinished_work` 遵循重叠规则，因此 `false` 不代表成功。尚未提交的记录，其条目与运行值为 null。

执行观测可以在触发记录版本不变时变化；持久化完成状态会改变状态和版本。线程变化或重连后，刷新展开的历史。通过普通 run items API 读取完整保存输出。

计划管理也可通过 MCP 和 `schedules` 平台工具组使用。[Agent Composer](agent-composer.md) 已启用该工具组，其他 agent 可以主动启用。读取和预览默认直接执行，修改请求审批。HTTP 和 MCP 共用请求体、ETag、请求键和创建者权限。Composer 创建的计划在对话结束后仍保留保存的授权。实时输出使用 HTTP 会话事件流。

## 订阅状态和加载历史

连接返回的 `stream_url`：`/api/v1/sessions/{session_id}/stream`，无需额外查询参数。普通[会话输出协议](agents-and-runs.md#follow-a-session-stream)为该会话增加 `schedule` 和 `occurrence` 状态帧。

每次连接先提供当前计划状态、触发记录摘要和会话中的所有线程，再跟随变化与实时输出。已完成、子线程和独立线程无论时间早晚都可见。通过分页历史浏览记录，通过触发详情读取完整调用，通过 run items 读取保存输出。

事件流同步当前状态，短暂的中间状态可能被合并。重连后刷新当前历史页，以及选中的线程、条目、运行和输出。Console 会自动执行这些刷新。

需要完成通知时，工作空间管理员可在详情页选择**添加 Webhook**，或配置**设置 → Webhooks**。订阅 `schedule_occurrence.completed` 并检查 `execution_outcome`。`schedule.completed` 表示一次性调度结束，而非执行结束。[Webhook](files-and-webhooks.md#webhooks) 也覆盖跳过、提交失败、取消和自动暂停。
