---
title: 监控与排障
description: 从 Service 导出 Prometheus 指标与 OpenTelemetry 链路追踪，关联 HTTP 请求 ID 与运行记录，并查询工作区用量与费用。
---

| 排查问题                           | 入口                                                 |
| ---------------------------------- | ---------------------------------------------------- |
| 为什么某次请求或运行失败了？       | [排查请求与运行故障](#troubleshoot-a-request-or-run) |
| Worker 或队列是否出现积压？        | [Prometheus 指标](#metrics)                          |
| 智能体在某次尝试内部做了什么？     | 运行尝试的[链路追踪](inspect-runs.md#traces)         |
| 消耗了多少 Token 和费用？          | [工作区用量](#usage)                                 |
| 沙箱容器是否缺少 CPU、内存或磁盘？ | [运行环境资源监控](environment-monitoring.md)        |

## 排查请求与运行故障

1. 记录响应中的 `X-Request-Id`（即错误体中的 `error.request_id`），在 `control` 日志中按 `request_id` 检索 `Request finished` 与 `Run accepted`。
2. 提取 `run_id`，跨 `control` 与 `worker` 日志追踪生命周期（`Run accepted` → `Attempt claimed` → `Attempt ended` → `Run sealed`），并调用 `GET /api/v1/runs/{run_id}` 查看保存的状态与失败原因。
3. 若已启用追踪后端（[遥测配置](configuration.md#logs-metrics-and-traces)），访问 `GET /api/v1/runs/{run_id}/attempts/{attempt_id}/trace` 查看完整 Span。
4. 若 Webhook 或邮件未送达，按 `Outbox`（`outbox_id`、`kind`）检索日志并检查[发件箱容量](operations.md#outbox-retention-and-capacity)。

默认向标准输出打印 JSON 日志；也可在 `service.toml` 中配置滚动日志文件（`telemetry.log_file`、`log_file_max_mb`、`log_file_backups`）。

## 指标（Metrics）

在 `service.toml` 中配置独立于 `server.port` 的私网指标端口，并将**每一个 `control` 与 `worker` 副本**加入 Prometheus / VictoriaMetrics 抓取目标（预置 Grafana 看板与告警规则位于 [`deploy/monitoring`](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/monitoring)）：

```toml
[telemetry]
metrics_port = 9464
```

| 观测维度          | 关键 Prometheus 指标                                                                                       |
| ----------------- | ---------------------------------------------------------------------------------------------------------- |
| HTTP 延迟与错误率 | `http_server_request_duration_seconds`（筛选 `a13n_request_kind="api"`）                                   |
| 运行吞吐          | `a13n_runs_accepted_total`、`a13n_runs_sealed_total`                                                       |
| Worker 饱和度     | `a13n_worker_slots`（`free`/`busy`）、`a13n_attempt_queue_wait_seconds`、`a13n_attempt_duration_seconds`   |
| 队列积压          | `a13n_backlog_size`、`a13n_backlog_oldest_age_seconds`（配合 `a13n_backlog_updated_seconds` 校验新鲜度）   |
| 发件箱与后台巡检  | `a13n_outbox_deliveries_total`、`a13n_outbox_backlog_alert`、`a13n_outbox_dead`、`a13n_sweep_passes_total` |

已安装的 App 插件也可通过同一监听端口[上报自定义业务指标](app-plugins.md#report-custom-metrics)。

### 实时会话的容量和健康指标

用 `worker.realtime_slots` 设置每个 Worker 副本的实时槽位数，默认 8 个。实时槽位和批处理槽位独立分配。滚动更新期间也要预留足够槽位，满足新通话的接入需求。`[worker]` 中还可以配置以下工作区级别的接入上限：

| 配置项                    | 默认值 | 统计范围                                                         |
| ------------------------- | -----: | ---------------------------------------------------------------- |
| `realtime_call_limit`     |     32 | 已接受或运行中的 WebSocket 和 WebRTC 通话总数                    |
| `realtime_pcm_limit`      |      8 | 已接受或运行中的 WebSocket 通话数，此类通话可由 Service 转发 PCM |
| `realtime_accepted_limit` |     16 | 已接受但仍在等待 Worker 领取的实时运行数                         |
| `realtime_ticket_limit`   |    128 | 尚未使用且未过期的连接票据数                                     |

每个 Service 进程的实时 Redis 连接池最多有 128 个连接。启用实时转发后，其中两个连接用于共享的 Pub/Sub 订阅。连接全部占用时，操作最多等待 `redis.timeout`。估算 Redis 连接数时，计入所有副本和普通 Redis 客户端。每路通话最多使用两条 Redis 控制流，每条最多 512 个条目。估算内存时，结合并发峰值、控制帧的常见和峰值大小，以及过去 40 秒内结束但控制流尚未过期的通话量。PCM 走 Pub/Sub，进程内缓冲区有固定上限；WebRTC 音频直达模型提供商。按预期并发压测，验证 Redis 连接数和内存占用。

| Prometheus 指标                             | 用途                                                                                           |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `a13n_worker_realtime_slots`                | 按 `state` 区分空闲和占用槽位，跨 Worker 汇总                                                  |
| `a13n_realtime_connections_active`          | 按 `transport` 统计打开的信令 WebSocket，包括 WebRTC 信令                                      |
| `a13n_realtime_handshakes_duration_seconds` | 从接受连接到 `ready` 的耗时及握手结果（`transport`、`result`）；用 `_count` 计算失败率和超时率 |
| `a13n_realtime_calls_ended_total`           | 按 `transport`、`reason` 统计信令连接结束事件                                                  |
| `a13n_realtime_pcm_discontinuities_total`   | 按 `direction` 统计发现的 PCM 缺口（`ingress` 或 `egress`）                                    |
| `a13n_realtime_control_overflows_total`     | 按 `side` 统计有序控制流的序号缺口和队列溢出（`control` 或 `worker`）                          |
| `a13n_realtime_orphan_hangups_total`        | 按 `result` 统计 WebRTC 遗留通话的清理尝试                                                     |

可先使用运维看板，再按需启用容量和执行预设中的实时会话告警。握手失败率上升时，先对比空闲槽位、握手超时和控制流溢出，再决定是否扩容。单独观察 `abandoned`：客户端因自身的连接等待超时而关闭 WebSocket，也会计入这一结果。遗留通话持续清理失败时，检查模型提供商的连通性和清理操作使用的凭证。

## 用量统计

在 Console 打开 **Insights → Usage** 查看 Token 总量、缓存命中率与 USD 预估费用（工作区管理员支持按成员筛选），或在左闭右开 `[start, end)` RFC 3339 时间窗（最长 366 天）内通过 API 查询：

```sh
curl --get "$A13N_URL/api/v1/usage/overview" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode "timezone=UTC"
```

分页明细端点包括 `/api/v1/usage/agents`、`/api/v1/usage/models` 与 `/api/v1/usage/members`（需 `admin` 权限），单次运行/线程/会话汇总位于 `GET /api/v1/usage`（见[检查运行记录](inspect-runs.md#usage)）。

<span id="bind-and-verify-an-environment"></span> <span id="system-information-and-resource-monitoring"></span>
