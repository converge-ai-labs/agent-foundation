---
title: 计划调度
description: 借助 Cron 定时表达式与一次性计划实现智能体自动化后台执行，提供防重入保护与独立审计流水。
---

\*\*Schedules（计划调度）\*\*让 Service 能够在指定的未来时间点或按照固定周期，自动向特定智能体下发提示词或结构化任务。无论是生成每日工程晨报、在深夜自动审计错误日志，还是定期为客户出具运营周报，计划调度都能在后台分布式计算节点上自主可靠运行。

## 创建和管理计划

在 Console 网页控制台中可以可视化创建并调试周期性计划：

1. 侧边栏进入 **Schedules → Create schedule**。
2. 填写计划名称（例如：`每日工程晨报生成`）。
3. 选择 **Recurring（周期性）**，输入标准的 5 段式 Cron 表达式（例如 `0 9 * * 1-5` 代表周一至周五的上午 09:00），并挑选对应的 IANA 时区（如 `Asia/Shanghai` 或 `America/New_York`）。
4. 点击 **Preview next times**，在保存前直观核对接下来 5 次触发的具体本地时间与 UTC 时间点。
5. 选择目标智能体，指定固定版本或始终跟随最新版本，并填写下发提示词（例如：*“汇总前一天的代码提交、合并的 PR 与关键报错，生成结构化的早会同步摘要。”*）。
6. 在 **Delivery target** 中，选择是**每次创建全新线程（New thread each time）**（适合独立单次报表）还是**复用同一会话线程（Reuse one thread）**（适合需要长期连贯记忆的历史日志）。
7. 保存计划。新建计划默认处于 `paused`（暂停）保护状态。
8. 点击 **Run once** 立即发起单次手动测试，查看输出与工具调用是否符合预期。
9. 确认无误后点击 **Enable**，正式激活全自动定时调度。

Service 调度引擎严格执行**重叠防重入保护（Overlap prevention）**：若上一次触发的任务仍在繁重计算中，新到达的周期触发会被安全阻塞等待，直至前序任务结算，杜绝资源挤占与数据并发写入冲突。

## 一次性计划

对于仅需在未来特定时刻执行一次的任务（例如在凌晨维护窗口自动执行数据库迁移脚本）：

1. 在创建弹窗中选择 **One time（单次）**。
2. 选择执行的具体日期与分钟级时间点。
3. 保存并启用该计划。
4. 到达设定时间后，Service 会自动派发任务，将计划状态置为 `completed`（已完成），并落盘审计结果。

若遭遇偶发网络抖动，Service 保证在最大容忍延迟窗口（默认 300 秒）内安全补发。

## 查看结果

计划的每一次触发，Service 均会生成一条独立的 **Occurrence（发生记录）** 用于全生命周期审计追踪：

| Occurrence 状态 | 所处阶段                                          |
| :-------------- | :------------------------------------------------ |
| `pending`       | 已到达触发时间点，触发意图安全暂存于任务队列中    |
| `submitted`     | 已分配收件箱条目，并正调度至计算 Worker 节点执行  |
| `running`       | Worker 节点正在执行模型交互与工具调用             |
| `completed`     | 运行完结；终态输出文本、耗时及 Token 统计均已落盘 |
| `skipped`       | 因前序任务仍在执行（Overlap）或严重超时被跳过     |

在计划历史列表中点击任意一条 Occurrence，即可展开查看生成的文本回复、具体的工具入参，或进入 Debug 视图复盘全链路追踪。

## 使用 HTTP API

所有定时计划均支持通过 Service 标准 HTTP 接口完成创建、修改与手动下发。

调用 `POST /api/v1/schedules` 并携带幂等键：

```json title="create_schedule.json"
{
  "name": "每日工程晨报生成",
  "schedule": {
    "type": "cron",
    "expression": "0 9 * * 1-5",
    "timezone": "Asia/Shanghai"
  },
  "target": {
    "mode": "new_thread",
    "session_id": null
  },
  "invocation": {
    "agent_id": "agent_reporter_123",
    "agent_revision_id": null,
    "payload": {
      "content": [
        {
          "type": "text",
          "text": "自动生成今天上午的团队工程晨报。"
        }
      ]
    }
  }
}
```

接口返回生成的计划对象及其唯一 ID 和当前版本 ETag：

```json
{
  "id": "sched_abc123",
  "name": "每日工程晨报生成",
  "state": "paused",
  "created_at": "2026-11-20T10:00:00Z"
}
```

如需通过 API 立即触发一次手动测试运行：

```bash
curl -X POST "$A13N_URL/api/v1/schedules/sched_abc123/dispatch" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "If-Match: $SCHEDULE_ETAG" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{}'
```

常用管理接口：

| 操作行为             | HTTP 请求方式与路径                               |
| :------------------- | :------------------------------------------------ |
| **查询计划详情**     | `GET /api/v1/schedules/{id}`                      |
| **修改计划配置**     | `PATCH /api/v1/schedules/{id}`（需带 `If-Match`） |
| **激活计划**         | `POST /api/v1/schedules/{id}/enable`              |
| **暂停计划**         | `POST /api/v1/schedules/{id}/pause`               |
| **归档计划**         | `DELETE /api/v1/schedules/{id}`                   |
| **分页查询触发历史** | `GET /api/v1/schedules/{id}/occurrences`          |

## 订阅状态和加载历史

如需实时感知后台定时任务的推进过程，客户端可直接长连接监听对应会话流：

```text
GET /api/v1/sessions/{session_id}/stream
```

该 SSE 协议会在实时传输输出内容的同时，广播 `schedule` 与 `occurrence` 状态流。对于自动化告警与协同系统，建议在工作区配置 [Webhook](webhooks.md)，订阅 `schedule_occurrence.completed` 事件，即可在任务执行结束时将生成结果秒级推送至企业微信、飞书或 Slack 群组。
