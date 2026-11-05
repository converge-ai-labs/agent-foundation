---
title: 运维环境采集器
description: 配置数据源、采集器与反向 Device，并验证历史覆盖范围。
---

先验证[首张 Local Cache 图表](environment-monitoring.md)，再按本运维参考选择采集与存储位置，按需配置采集器，并分别验证写入和查询覆盖。工作空间管理员选择数据源，部署运维人员负责采集进程与凭证。

## 选择数据源

分别选择采集方式和历史查询方式。采集器将样本送入存储，查询后端让 Console 读取这些样本。

| 采集方式   | 适用场景                             | 需要配置                                             |
| ---------- | ------------------------------------ | ---------------------------------------------------- |
| `proxy`    | 需要临时图表或反向 Device 连接。     | Service 和 Redis；可选 OTLP HTTP 导出与查询后端。    |
| `native`   | 托管 Envd 目标直接导出。             | OTLP 接收端；查看图表需添加查询后端。                |
| `pull`     | 使用 vmagent 或 Alloy 拉取目标指标。 | 已配置的采集器及其传输链路；查看图表需添加查询后端。 |
| `external` | 已有自行运维的采集链路。             | Prometheus HTTP 查询后端。                           |

**Local Cache** 是配置固定的内置 proxy 数据源。工作空间管理员可在 Console 中创建自定义数据源，也可调用 `POST /api/v1/metrics-data-sources`。例如，以下请求体配置原生导出和独立查询目的地：

```json
{
  "name": "Production metrics",
  "config": {
    "collection": "native",
    "export": {"endpoint": "https://ingest.example.com/v1/metrics", "protocol": "http/protobuf"},
    "query": {"endpoint": "https://metrics.example.com", "scope_labels": {"workspace_id": "ws_example"}}
  },
  "export_credentials": {"headers": {"Authorization": "Bearer <write-token>"}},
  "query_credentials": {"headers": {"Authorization": "Bearer <read-token>"}}
}
```

按后端实际情况替换端点、标签和令牌，再为环境选择返回的数据源。原生 HTTP 导出使用完整指标 URL，原生 gRPC 使用接收端 origin。代理导出支持 HTTP/protobuf。查询端点是 `/api/v1/query` 和 `/api/v1/query_range` 之前的 API 基址。逐一检查部署端点策略、TLS 信任和网络可达性。

凭据加密保存且只写，响应仅表示是否已配置。更新时省略凭据表示保留，传入 `null` 表示清除，传入对象表示替换。更改目的地时，必须明确替换或清除对应凭据。使用[条件更新](http.md#concurrency-control)。删除数据源前，先解除所有环境和 Device 的绑定。

在后端将读取凭据限定到工作空间允许的数据。范围标签只过滤内置查询；任意 PromQL 需要工作空间管理权限，可以查询标签范围之外的数据。监控端点供用户和应用使用，不向 agent 工具和 MCP 开放。后端错误会直接显示，不会切换到 Local Cache。

> [!WARNING]
> 原生导出会将写入凭据交付给目标进程。在目标上执行代码的用户和 agent 可以读取并复用它。请使用由接收端限定范围的只写凭据，并确保这些执行者在该摄取范围内可信。标签不能证明样本身份。如果接收端凭据必须保留在目标之外，请选择 pull 或 proxy。

## 确认部署预设

运维可向明确列出的工作空间提供 pull/query 预设。允许范围内的工作空间管理员可据此创建数据源：

1. 打开 **添加数据源**，检查预填的采集器和查询端点。
2. 将 **使用部署查询凭据** 保持为 **否**，或选择 **是**，在原始端点使用部署当前的读取令牌。编辑端点会清除该选择。
3. 选择 **保存数据源**，再将其绑定到环境。预设不会自动创建这两种资源。

加载失败时，重试或选择 **手动配置**。公开预设变化后，请重新打开表单。凭据继承会在服务器加密保存快照，令牌不会进入浏览器。后续部署轮换或移除预设不会更改已保存的数据源，需要明确替换其凭据。

API 客户端先读取 `GET /api/v1/metrics-data-sources/deployment-preset`，再在创建时提交其 `deployment_preset_revision`，需要继承时加上 `use_deployment_query_credentials: true`。继承和显式查询凭据互斥。查询预设需要管理权限；监控未启用、采集器未配置或工作空间不符合条件时，返回 `null`。后端必须限定所有允许工作空间的数据访问范围；允许列表不能替代后端租户隔离。

运维通过 [Service 设置](configuration.md#environment-monitoring)或 [Helm 部署](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors)配置预设。

## 初始化可选采集器

本节用于配置 `pull` 采集。Service 写入抓取配置片段并请求重载，采集器通过已有传输链路将样本发送到存储。历史查询端点需要单独配置。

使用 Helm 托管 vmagent 时，启用 `monitoring.vmagent.enabled` 并配置远程写入目的地。Chart 提供独立写入进程、受保护的共享片段、回环重载接口和持久化转发队列。排空和停用步骤见 [Helm 生命周期说明](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors)。禁用或卸载后保留 PVC；强制替换前，先隔离失联节点。

使用外部 vmagent 或 Alloy 安装时：

1. 将受保护的共享**目录**挂载到 Service 和采集器。生成文件使用原子替换，因此不能只挂载单个文件。
2. 使用兼容的进程 UID，确保双方都能读取权限为 0600 的片段。片段中的监听器凭据也能授权 EIP 访问。
3. 使用支持 POSIX 建议锁和原子重命名的存储。替换写入进程时必须共享同一目录。
4. 每个采集器只在一个 `all` 或 `control` 进程上设置 `writer = true`。其他 API 副本配置同一采集器并设置 `writer = false`。
5. 允许采集器访问目标 HTTP 监听器，允许写入进程访问受保护的重载端点。Service 在配置的 origin 或网关前缀后追加 `/-/reload`。可选重载 `headers` 应放在受保护的 Service 设置中。

### vmagent

在共享目录中创建内容为 `[]`、权限为 0600 的 `a13n-managed.yml`。在已有 vmagent 配置中引入采集器一侧的路径：

```yaml
scrape_config_files:
  - /etc/vmagent/a13n/a13n-managed.yml
```

保留已有抓取任务和 `-remoteWrite.url`。用 Service 自己的挂载路径配置：

```toml
[monitoring.collectors.vmagent]
directory = "/var/lib/a13n/vmagent"
endpoint = "http://vmagent:8429"
allow_http_reload = true
writer = true
```

Service 只替换包含抓取任务列表的 `a13n-managed.yml`，再请求重载。主配置和传输设置仍由运维管理。

### Grafana Alloy

使用包含接收端和传输链路的配置目录启动 Alloy。如果目录中定义了 `prometheus.remote_write "metrics"`，则让 Service 使用该接收端：

```toml
[monitoring.collectors.alloy]
directory = "/var/lib/a13n/alloy"
endpoint = "http://alloy:12345"
allow_http_reload = true
receiver = "prometheus.remote_write.metrics.receiver"
writer = true
```

将同一目录挂载到 Alloy。Service 只替换 `a13n-managed.alloy`；命名接收端、目的地和凭据必须已在你自己的文件中配置。OpenTelemetry Collector 和 Prometheus Server 可用于运维自行管理的链路，但 Service 不提供它们的抓取配置适配器。

### 同步与停用

创建配置为 `{"collection":"pull","collector":"vmagent"}` 或 `{"collection":"pull","collector":"alloy"}` 的数据源；查看图表时需另加 `query` 配置。将其绑定到受支持的 HTTP 目标，再逐阶段检查：

| 检查                                        | 能确认什么             | 下一步                                   |
| ------------------------------------------- | ---------------------- | ---------------------------------------- |
| `/api/v1/metrics-data-sources/capabilities` | 已配置采集器。         | 检查环境的 `/metrics/collector` 状态。   |
| `reload_requested`                          | 采集器已接受重载请求。 | 在采集器端检查已应用配置和抓取健康状态。 |
| 抓取成功                                    | 采集器能读取目标。     | 验证数据已送入存储，并查询最近历史。     |

默认每 30 秒同步一次，每个采集器最多处理 512 个目标。`truncated: true` 表示扫描或目标预算之外仍有资源。系统会跳过并报告不支持或不可用的目标。重载失败后在下一轮重试；vmagent 异步应用重载。

停用目标时，在写入进程仍运行的情况下解除绑定或禁用数据源，再确认下一次成功同步移除了任务。停止外部写入进程或禁用监控前，需自行移除托管片段并重载采集器。停止写入进程后，已加载的任务仍会运行。

## 连接反向 Device

Envd 目标需要反向连接 Service 时，注册一个监控 Device。注册独立于可执行环境，不创建挂载或执行目标。

1. 以工作空间管理员身份创建注册，填写预期原生 Device 身份并选择 proxy 数据源。
2. 保存创建时仅返回一次的连接令牌。元数据读取无法取回它。
3. 启用 Envd 采样器，再将反向连接配置为 `/api/v1/devices/{device_id}/connect`，使用 WSS、`eip.v1` 子协议，并在 Authorization Bearer 头中提供连接令牌。
4. 在 Console 中选择 Device，确认指标新鲜。接收副本拥有连接和临时历史，其他副本将已授权的查询路由到它。

仅限直接回环开发连接，可通过 `A13N_MONITORING__ALLOW_LOOPBACK_WS=true` 允许 WS。在反向代理后方保持关闭。由代理终止 TLS 时，配置 `server.trusted_proxies`，使 Service 接受可信的安全协议头。连接拒绝查询字符串凭据、浏览器 Origin 和仅使用 cookie 的认证。

轮换令牌、禁用或解除注册绑定，以及所有权丢失，都会关闭连接。反向 Device 不能使用 pull，因为它没有采集器可达的 HTTP 端点。Native/external Device 历史由运维管理的采集链路提供，无需活动反向连接即可查询。

## 查询与估算临时历史容量

类型化查询选择一个指标和时间范围。`history = "configured"` 使用选中的查询后端；没有后端时使用 proxy 缓冲区。`history = "temporary"` 明确选择该缓冲区。构建概览或资源清单视图时，添加 `include_snapshot = true` 可返回最新 EIP 快照。

| Local Cache 历史                       | 保留时长                   |
| -------------------------------------- | -------------------------- |
| 分钟聚合的概览指标                     | 24 小时                    |
| 逐接口网络和逐设备磁盘/cgroup I/O 速率 | 1 小时，每类最多 64 个来源 |

检查每个结果的覆盖范围和截断信息。计数器重置和来源替换会中断速率连续性；缺失和过期观测保留为空缺。接口和磁盘保留各自身份。压力和文件系统历史需要外部后端。

每个 control 进程默认允许 64 个代理连接，最多可配置 512 个。完整资源清单下，每个连接的数值历史占用低于 2 MiB，此外还有对象、快照和导出开销。提高容量前先估算进程内存。数据库故障时，只要 Redis 所有权仍有效，已有历史和反向连接可保留最多三个轮询间隔；观测、查询和导出等待重新确认当前权限。故障持续更久或所有权丢失后，缓存会被移除。

外部查询默认允许八个并发请求，截止时间为五秒，响应上限为 4 MiB。每个查询最多返回 200 个序列，时间范围不超过 90 天，每个序列不超过 11,000 个点。按图表宽度选择步长。外部速率使用五分钟 PromQL 窗口，临时速率使用相邻观测，因此估算结果可能不同。配置范围见[设置参考](configuration-reference.md#monitoring)；需要持久历史或更大规模设备时，使用外部存储。
