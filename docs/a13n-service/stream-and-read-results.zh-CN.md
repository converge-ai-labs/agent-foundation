---
title: 流式观察与结果读取
description: 关联提交消息与结果，重连后重建实时输出，并读取保存的历史。
---

先[从应用提交任务](connect-application.md)，保留回执中的 session、thread 和 inbox entry ID。最终结果从保存的 run 输出读取，界面需要实时进度时再接入会话事件流。观察者断连不会取消执行。

## 结果

读取 `GET /runs/{run_id}`，根据 `status` 选择后续操作：

| 状态                  | 操作                                                  |
| --------------------- | ----------------------------------------------------- |
| `accepted`、`running` | 继续观测该运行。                                      |
| `completed`           | 读取 `output`，它是文本或符合 `output_spec` 的 JSON。 |
| `waiting`             | 读取 `pending`，提交完整 resume 回复。                |
| `failed`              | 检查 `failure.code` 和 `failure.message`。            |
| `cancelled`           | 查看已保存历史，再决定继续或分叉。                    |

对于提交的消息，先确认哪个运行消费了收件箱条目。已分配但未纳入的条目可能返回 pending。线程最新运行不一定是某条消息的回答。

用 `/runs/{run_id}/items` 读取有序显示历史。响应包含最近显示项和 `complete`；以首项 ordinal 作为 `before` 向前翻页，或用 `after` 向后读取。超过 32 KiB 的字段使用 `content_refs`，通过 `/runs/{run_id}/contents/{content_id}` 读取已保存内容。每个字段最多保留 16 MiB，`truncated` 标记保存的是前缀。Console 的全文/已保存内容操作读取这些引用。

诊断时，`/runs/{run_id}/agent` 返回实际配置，`/lineage` 返回祖先，`/attempts` 说明首次执行、恢复或 worker 交接。`/threads/{thread_id}/runs` 按从新到旧列出运行。`current_run_id` 指向活跃工作；`last_run_id` 指向后续执行所接续的已结束历史。

## 跟踪会话事件流

用 `GET /sessions/{session_id}/stream` 显示整个会话的实时内容。它是 SSE 流，每次连接从当前状态开始，没有重连游标。

| 事件        | 客户端操作                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------- |
| `thread`    | 替换线程及其中指向的当前/最后运行。                                                         |
| `items`     | 替换该运行从 ordinal `first` 起的显示项，保留更早的最终项；从 `continuation` 继续实时显示。 |
| `delta`     | 将临时输出应用到该运行显示。                                                                |
| `gap`       | 标记输出缺失，等待下一个 `items` 帧修复。                                                   |
| `app_ready` | 启用指定的 [MCP Apps](mcp-apps.md#mcp-apps-for-embedding-applications)。                    |

`complete: true` 的 `items` 帧是运行最终显示；运行结果要看 status。重连时丢弃未完成显示，从新快照重建活跃运行。断开期间结束的运行通过 `/runs/{run_id}/items` 读取。忽略未知事件名。

使用上限为 30 秒的随机退避重连，429 或 503 按 `Retry-After` 等待。其他 4xx 拒绝及无效帧需要修正原因，而非自动重试。事件流是临时观测，已保存显示项才是持久记录。帧 Schema 导出到 `proto/a13n-service/session-stream.schema.json`。
