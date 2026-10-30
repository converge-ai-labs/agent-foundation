---
title: 监控与排障
description: 从请求追踪到运行，排查队列和投递问题，并检查工作空间用量。
---

先明确要回答的问题。请求失败时，沿请求 ID 查日志；运行缓慢时，检查队列和执行尝试；查看成本时，读取工作空间保存的用量。

| 问题                         | 从哪里开始                                       |
| ---------------------------- | ------------------------------------------------ |
| 请求或运行为什么失败？       | [请求与运行排障](#troubleshoot-a-request-or-run) |
| Worker 能跟上工作量吗？      | [Service 指标](#metrics)                         |
| Agent 在一次尝试中做了什么？ | 该尝试的 [trace](agents-and-runs.md#traces)      |
| 哪些 agent 或模型用量最多？  | [工作空间用量](#usage)                           |
| 环境是否缺少 CPU 或内存？    | [环境监控](environment-monitoring.md)            |

排查请求需要访问进程日志。指标需要配置抓取端点，trace 需要配置追踪后端。工作空间用量来自 PostgreSQL，可独立于这些后端使用。

## 排查请求或运行

保留失败响应的状态、错误体和 `X-Request-Id`。错误体中的 `request_id` 与该响应头相同。然后按以下步骤追踪：

1. 在进程日志中搜索 `request_id`。`Request finished` 给出路由模板、状态和时长。
2. 在同一请求的记录中找到 `Run accepted`，复制 `run_id`。如果请求被拒绝，根据错误码修正请求，此时没有接收运行。如果消息排在活跃运行之后，跟进其[收件箱条目](agents-and-runs.md)，直到条目指明取走消息的运行。
3. 在所有相关进程日志中搜索 `run_id`，包括 worker 日志。根据下表选择下一步检查项，再调用 `GET /api/v1/runs/{run_id}` 查看当前保存的状态和失败消息。
4. 对于正在执行或已失败的尝试，如果配置了追踪，检查其 trace。调用 `GET /api/v1/runs/{run_id}/attempts/{attempt_id}/trace`，或在后端搜索 `service_run_id` 和 `run_attempt_id`。

| 记录              | 说明                    | 下一步检查                                                                                            |
| ----------------- | ----------------------- | ----------------------------------------------------------------------------------------------------- |
| `Run accepted`    | 工作已被接收。          | 如果没有后续尝试，检查 worker 就绪状态、空闲槽位和队列等待时间。                                      |
| `Attempt claimed` | Worker 开始了一次尝试。 | 检查该尝试的 trace 和后续尝试记录。                                                                   |
| `Attempt ended`   | 本次尝试已停止。        | 阅读 `status` 和 `reason`；重试或继续执行会出现新的 `Attempt claimed`，封存则表示运行已结束。         |
| `Run sealed`      | 运行已结束。            | 阅读保存的状态和原因；等待结果可能需要[人工输入和后继运行](agents-and-runs.md#resume-a-waiting-run)。 |

端点策略失败会说明违反的规则，例如必须使用 HTTPS。再次启动前先修正配置；相同的被拒绝配置不会重试。模型认证失败可能包含 `HTTP 401`。失败消息不包含上游原始响应体。

Webhook 或邮件未送达时，先按 `kind` 搜索 `Outbox` 记录，再按 `outbox_id` 追踪。`Outbox delivery failed` 会重试；`Outbox delivery dead` 记录最终原因。投递持续积压时，检查 [outbox 保留与容量](operations.md#outbox-retention-and-capacity)。

## 日志

默认的 JSON stdout 日志可以交给进程管理器或日志采集器。本地阅读时，可设置 `telemetry.log_format = "pretty"`。部署需要文件日志时，在配置中添加轮转 JSON 日志：

```toml
[telemetry]
log_level = "INFO"
log_file = "/var/log/a13n/service.log"
log_file_max_mb = 100
log_file_backups = 5
log_stdout = false
```

为每个进程选择独立且可写的路径，然后重启该进程。本例保留当前文件和最多五份备份，只向文件输出日志。`migrate` 等运维命令继续向 stdout 输出。

根据正在追踪的工作选择 ID：

| 工作              | 日志字段                                                                           |
| ----------------- | ---------------------------------------------------------------------------------- |
| HTTP 请求         | `request_id`；`Request finished` 还包含 `method`、`route`、`status`、`duration_ms` |
| Worker 与执行尝试 | `worker_id`，以及 `run_id` 和 `attempt_id`                                         |
| 后台扫描          | `sweep`；`Sweep failed` 包含 `error_type` 和有界 `exception_details`               |
| Webhook 或邮件    | `outbox_id`、`kind` 和投递 `reason`                                                |

请求日志不记录健康和就绪探测。Service 日志与指标不包含凭据、请求和响应体、原始 URL 或查询字符串。反向代理的访问日志需要单独配置查询字符串隐藏。Trace 内容有独立的[导出设置](configuration.md#logs-metrics-and-traces)；向后端发送提示、输出或工具载荷前，先选择 `telemetry.trace_content`。

## 指标

监控 Service 进程：

1. 在部署配置中添加指标端口，该端口必须与 `server.port` 不同：

```toml
[telemetry]
metrics_port = 9464
```

2. 重启进程。从私有监控网络访问 `server.host` 上配置端口的 `/metrics`。进程可通过本机访问时：

```sh
curl --fail http://127.0.0.1:9464/metrics
```

3. 将**每个 control 和 worker 进程**加入 Prometheus 或 VictoriaMetrics 抓取配置。使用仓库[监控套件](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/monitoring)时，将任务名设为 `a13n-service`。
4. 导入套件的运维看板和告警规则。确认目标正在被抓取，再检查请求、运行、worker 容量和投递情况。

指标监听器独立于 API 监听器，应保留在私有网络中。Helm chart 默认启用 9464 端口并支持 PodMonitor；套件中包含 operator 和静态主机配置说明。

| 运维问题               | 可用指标                                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------------------- |
| 请求是否缓慢或失败？   | `http_server_request_duration_seconds`，按方法、路由模板和状态码分组                                     |
| 运行是否被接收并完成？ | `a13n_runs_accepted_total`、`a13n_runs_sealed_total`，按触发源或结果分组                                 |
| Worker 是否饱和？      | `a13n_worker_slots`（`free`/`busy`）、`a13n_attempt_queue_wait_seconds`、`a13n_attempt_duration_seconds` |
| 到期工作是否积压？     | `a13n_backlog_size` 和 `a13n_backlog_oldest_age_seconds`，按 `queue` 分组                                |
| 投递是否失败？         | `a13n_outbox_deliveries_total`、`a13n_outbox_backlog_alert`、`a13n_outbox_dead`                          |
| 后台任务是否失败？     | `a13n_sweep_passes_total`，按 `sweep` 和 `result` 分组                                                   |

积压指已经到执行时间的工作，不包含等待中的运行和未来的定时工作。`all` 和 `control` 副本每 15 秒刷新同一组积压值。跨副本应取**最大值**，不要相加。计数上限取 10,000 和该 outbox 类型 `backlog_count` 中的较大值。

两个 outbox gauge 同样取跨副本最大值。某类型持续超过阈值达 `backlog_alert_seconds` 后，`a13n_outbox_backlog_alert` 变为 1；进程重启会重置计时。保留有 dead 投递时，`a13n_outbox_dead` 为 1。

Worker 还导出 [Harness 指标](../a13n-harness/observation.md)，包括 `a13n_harness_run_duration_seconds` 和模型 token 用量 `gen_ai_client_token_usage`。Service 指标描述进程健康和全局工作情况；工作空间级数据使用用量 API。

## 用量

通过 Console 或 API 读取工作空间保存的运行和模型用量记录。运维人员需要组织级报告时，可以导入监控套件的用量看板，并通过只读 PostgreSQL 角色连接数据库。

生成 API 报告时，设置 `A13N_URL`，并提供具有读取权限的工作空间 `A13N_API_KEY`。将 `START` 和 `END` 设为 RFC 3339 时间戳，范围大于零且不超过 366 天；`END` 为不包含的结束边界。以下请求按 UTC 日期分组：

```sh
curl --get "$A13N_URL/api/v1/usage/overview" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode "timezone=UTC"
```

读取概览总计和每日分桶。使用相同时间范围调用 `GET /api/v1/usage/agents` 或 `/api/v1/usage/models`，得到分页明细（`limit` 和 `cursor`）。`GET /api/v1/usage` 提供运行、线程和会话汇总；参阅[用量查询](agents-and-runs.md#usage)。

### Console 中的工作空间用量

1. 在工作空间中打开 **洞察 → 用量**。
2. 选择最近 7 天、30 天，或最长 366 天的自定义范围。
3. 比较费用、运行数、模型请求数、平均运行时长和 token 总量。
4. 在 **费用** 和 **Token 数** 之间切换每日图表，再比较 agent 或模型明细。

![Console 中的成本、token、缓存命中率与每日花费](../../.github/assets/service-console.webp)

*截图使用本地 Seed 数据中的虚构历史。*

结合以下口径理解数据：

- **成本：** 记录的模型成本以美元计。未定价请求单独计数。部分小计只包含已知成本；成本全部未知时显示不可用。
- **缓存：** 缓存输入已包含在输入 token 中。缓存命中率为缓存输入总量除以输入总量，不是各请求比例的平均值。
- **时间：** 用量按入库时间分组，运行按启动时间计数。平均运行时长覆盖已启动且封存的运行，从启动到封存计算，包括中途的执行等待。等待运行会在人工审批前封存；恢复后的后继运行单独计数。

延迟到达的用量可能更新较早的总计。Console 按浏览器时区划分日期；将 API 报告与图表对比时，使用相同的时区和时间范围。

工作空间管理员可以按成员查看用量：

1. 在时间范围旁的**成员**选择器中选择用户或服务账号。总览、每日趋势及 agent/模型明细会一起筛选。
2. 在明细中选择**按成员**以比较账号，点击一行的**查看用量**检查该账号。
3. 点击**清除成员筛选**恢复工作空间视图。

`GET /api/v1/usage/members` 返回分页成员明细。总览、agent、模型和成员接口均接受可选的 `principal_id`。成员查询和任何按身份筛选的查询都需要工作空间 `admin` 权限；未筛选的总览、agent 和模型查询需要 `read`。不存在或属于其他工作空间的身份返回空用量，不暴露其资料。

用量归属依据运行的执行身份。共享 bot 账号的请求计入该账号，不会拆分为外部聊天用户。停用或移除成员后仍保留其历史用量；显示的名称和状态取当前值。
