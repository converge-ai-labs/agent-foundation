---
title: 监控环境
description: 为环境和 Device 指标选择采集与历史查询后端，并配置可选的拉取采集器。
---

环境监控独立于 agent 运行，展示 Envd 目标的资源使用情况。为每个环境选择工作空间中的指标数据源。未选择数据源的环境不会启用 Service 后台采集或自动配置导出器。[Service 监控](monitoring.md)介绍 Service 自身的健康状态。

首次使用时，将环境的指标数据源设为 **Local Cache**，在目标上执行一些操作，再打开 **观察 → 指标** 并选择该环境。已有目标需要运行中的 Envd sampler；没有样本时，请按[绑定并验证环境](#bind-and-verify-an-environment)检查。Local Cache 在 Redis 中保存临时历史，无需外部指标数据库。

## 在 Console 中使用仪表盘

在工作空间中打开 **观察 → 指标**。选择环境或 Device，查看其已配置的指标。内置 **Environment overview** 与自定义仪表盘一样可以编辑。

1. 选择 **编辑仪表盘**，添加、编辑或移除面板。拖动面板手柄调整位置，拖动角落调整大小。面板编辑器也支持输入位置和大小；小屏幕按顺序堆叠已保存的布局。
2. 选择预定义的 Environment / Device 指标，或选择数据源并编写 PromQL。PromQL 需要工作空间管理员权限。Local Cache 支持预定义指标，不支持任意 PromQL。
3. 选择 **运行查询** 预览面板，再选择 **应用面板** 更新草稿。按需选择单位或统计值视图。
4. 选择 **保存仪表盘**，与工作空间成员共享修改。**丢弃** 会取消修改。**恢复默认配置** 替换内置仪表盘的草稿，保存后才会持久化。其他人先保存时，系统保留你的草稿，直到你明确重新加载。应用内切换页面会为每个仪表盘保留独立草稿；刷新页面或退出登录前请先保存。

在目标选择器中搜索名称、ID 或 `region=west` 等环境标签；多个搜索词可以与类型、状态和数据源筛选组合使用。数据源和 Device 列表也支持搜索和状态筛选。

点击时间范围按钮，选择预设范围、自动刷新或绝对日期。悬停图表可查看同步数值，点击图例可显示或隐藏序列。超过四个序列的面板提供搜索框，同时筛选曲线和图例，无需重新查询。拖动图表选区进行缩放，或设置绝对时间范围。查看历史范围时自动刷新暂停；**返回实时** 恢复刷新。刷新失败或尚未完成时，可保留上次结果，并显示标记和查询错误。切换查询或目标后使用其各自的数据。

图表请求约 600 个求值时间点，每个面板最多显示 32 个序列。**查询详情** 显示截断信息、临时保留时长和实际覆盖范围。缩小高基数查询可查看其他序列。仪表盘配置持久保存；选择 Local Cache 不会让样本历史持久保存。

## 选择数据源

| 采集方式   | 适用场景                                       | 基础设施                                                    |
| ---------- | ---------------------------------------------- | ----------------------------------------------------------- |
| `native`   | 托管 Envd 目标直接导出。                       | OTLP 接收端；可选的 Prometheus HTTP 查询后端。              |
| `pull`     | 已有采集器拉取目标指标。                       | Chart 托管的 vmagent 或外部 vmagent/Alloy；可选的查询后端。 |
| `proxy`    | 小型部署需要临时历史，或目标反向连接 Service。 | Service 和 Redis；可选的 OTLP HTTP 导出与查询后端。         |
| `external` | 已有采集链路，只需查询。                       | Prometheus HTTP 查询后端。                                  |

**Local Cache** 是内置代理数据源，配置固定。自定义数据源分别配置采集、导出和历史查询。Helm chart 可选安装 vmagent；历史存储由外部后端提供。后端失败不会静默切换到 Local Cache。

工作空间管理员通过 `/api/v1/metrics-data-sources` 创建数据源。例如，以下请求体选择原生导出和独立的查询端点：

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

使用 [HTTP API](http.md)介绍的工作空间上下文和认证方式。凭据加密保存且只写，响应仅返回是否配置凭据。更新时省略凭据字段表示保留，传入 `null` 表示清除，传入对象表示替换。修改端点时必须明确替换或清除对应凭据。更新使用资源 ETag 作为 `If-Match`。环境或 Device 仍引用的数据源不能删除。

读取凭据必须在后端限定可访问的数据范围。范围标签只过滤内置查询，不能限制任意 PromQL。自定义 PromQL 需要工作空间管理权限。私有环境查询还会检查环境所有权。监控端点面向用户，不向 agent 工具和 MCP 开放。

> [!WARNING]
> Native 数据源会将写入凭据交付给目标进程。环境管理者可以选择该数据源，在目标上执行代码的用户或 agent 可以读取并复用凭据。请使用接收端限定范围的只写凭据，并确保这些执行者在允许的摄取范围内可信。资源标签不能防止伪造样本身份。如果接收端凭据必须保留在目标之外，请使用 pull 或 proxy 采集。

## 确认部署预设

运维可以向明确列出的工作空间 ID 提供脱敏的拉取采集与查询预设。允许范围内的管理员打开 **添加数据源**，检查预填字段，再选择 **保存数据源**。部署不会自动创建数据源或环境绑定。预设加载失败时，可以重试或选择 **手动配置**。

**使用部署查询凭据** 默认为 **否**。选择 **是** 后，服务器加密保存当前部署读取令牌的快照，令牌不会进入浏览器。编辑查询目的地会清除授权；只有原始端点可以继承凭据。公开预设变化后需要重新打开表单。仅轮换令牌时，确认操作使用当时的令牌；已有数据源在轮换或移除预设后仍保留原快照。需要更新时，请明确替换已保存数据源的凭据。

只有管理员可以读取 `/api/v1/metrics-data-sources/deployment-preset`。监控启用、采集器已配置且工作空间在允许列表内时才返回预设，否则返回 `null`。响应包含配置、是否有凭据和修订标识，不含令牌。API 客户端创建时通过 `deployment_preset_revision` 和 `use_deployment_query_credentials: true` 明确选择继承，不能同时提交普通查询凭据。读取令牌必须由后端限定到允许工作空间的数据范围；允许列表和查询标签不能替代后端租户隔离。

Service 设置见[配置说明](configuration.md#environment-monitoring)，values 和 Secret 引用见 [Helm 部署指南](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors)。

## 绑定并验证环境

1. 创建或更新环境时选择 `metrics_data_source_id`，使用 `null` 清除选择。外部身份映射和自定义数据源需要管理员批准。
2. 创建托管 Docker 或 E2B `envd_http` 环境时，在创建开始前选择数据源。Native、proxy 和 pull 选择会启用 10 秒采样器。Native 还会注入指标专用 OTEL 设置和写入凭据。
3. 检查 `metrics_application`。它记录创建时冻结的数据源版本和采集方式。`applied_at` 表示已使用这些输入成功创建，不表示已成功摄取指标。
4. 查询 `/api/v1/environments/{environment_id}/metrics/query`。使用 pull 时，还要检查 `/api/v1/environments/{environment_id}/metrics/collector` 和采集器自身的目标状态。

已有目标的守护进程未启用采样器时，需要运维配置。启动或恢复不会重新注入设置，也不会重建目标。后续选择可以改变实时 proxy/pull 采集，但不会改写已启动的原生导出器。解除绑定或禁用数据源不会撤销已经交付给目标的凭据；需要单独停用或重新配置导出器。

原生 HTTP 导出使用完整的指标 URL；原生 gRPC 使用接收端 origin。代理导出支持 HTTP/protobuf。查询端点是 `/api/v1/query` 和 `/api/v1/query_range` 之前的 API 基址。端点策略、TLS 信任和网络必须允许访问各目的地。

## 初始化可选采集器

Service 自动管理以下两种采集器的抓取配置和重载：

| 采集器        | 配置键    | Service 管理的片段                                  | 实测版本 |
| ------------- | --------- | --------------------------------------------------- | -------- |
| vmagent       | `vmagent` | `a13n-managed.yml`，通过 `scrape_config_files` 引入 | 1.128.0  |
| Grafana Alloy | `alloy`   | `a13n-managed.alloy`，连接已有接收端                | 1.10.2   |

Linux 互操作测试覆盖带认证的 Envd 抓取、重载、租户标签、目标移除，以及保留运维已有的导出配置。测试使用 Prometheus 3.2.1 作为临时远程写入接收端。这些是实测版本，不是最低版本保证。测试需显式启用，未覆盖采集器 TLS 或云 provider 部署。

OTLP 接收端（包括运维自行配置的 OpenTelemetry Collector）可独立用于 native 或 proxy 导出。Service 尚无 OpenTelemetry Collector 或 Prometheus Server 的自动抓取配置适配器。

使用托管 vmagent 时，在 Helm 中启用 `monitoring.vmagent.enabled`，并设置完整的远程写入 URL 或 Prometheus 简便 URL。Chart 提供独立单例写入进程、受保护的片段、回环重载接口和持久化转发队列。存储写入 URL 与查询 URL 独立配置，普通 API 副本不会写入片段。关闭采集前先移除目标并排空队列；禁用或卸载后保留 PVC。遵循 [Helm 生命周期说明](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors)，强制替换前需隔离失联节点。

使用外部模式时，采用已有采集器安装及运维管理的导出链路。Service 只替换自己生成的采集片段。采集器必须能够访问目标的 HTTP 监听器，Service 写入进程必须能够访问采集器重载端点。历史存储和 Prometheus HTTP 查询端点需要单独配置；仅部署 pull 采集器不会提供仪表盘历史。

1. 创建受保护的共享目录。将目录挂载到 Service 和采集器，不要只挂载生成的文件：更新使用原子替换。
2. 两个进程使用能够读取 Service 的 0600 权限片段的 UID。目录仅向受信任的运维人员开放。片段包含监听器凭据，这些凭据也能访问 EIP。
3. 使用支持 POSIX 建议锁和原子重命名的存储。滚动替换的写入进程必须共享同一目录。
4. 每个采集器只配置一个 `all` 或 `control` 进程为 `writer = true`。其他 API 副本使用相同采集器配置，并设置 `writer = false`。
5. 用网络策略或需要认证的入口保护重载接口。在受保护的 Service 设置中配置可选的 `headers`。端点填写 origin 或网关前缀；Service 会追加 `/-/reload`。

### vmagent

在共享目录中创建初始 `a13n-managed.yml`，内容为 `[]`，权限为 0600。在已有 vmagent 配置中加入采集器一侧的文件路径：

```yaml
scrape_config_files:
  - /etc/vmagent/a13n/a13n-managed.yml
```

保留已有采集任务和 `-remoteWrite.url` 设置。使用 Service 一侧的挂载路径配置：

```toml
[monitoring.collectors.vmagent]
directory = "/var/lib/a13n/vmagent"
endpoint = "http://vmagent:8429"
allow_http_reload = true
writer = true
```

生成片段的顶层是采集任务列表，不是完整的 `scrape_configs` 文档。Service 替换片段后提交重载请求。

### Grafana Alloy

使用配置目录启动 Alloy。接收端和已有链路保留在该目录中由运维维护的 `.alloy` 文件内。例如，已有 `prometheus.remote_write "metrics"` 组件会暴露 `prometheus.remote_write.metrics.receiver`：

```toml
[monitoring.collectors.alloy]
directory = "/var/lib/a13n/alloy"
endpoint = "http://alloy:12345"
allow_http_reload = true
receiver = "prometheus.remote_write.metrics.receiver"
writer = true
```

Service 只写入 `a13n-managed.alloy`。将同一目录挂载到 Alloy 的配置路径。接收端表达式必须已经存在；Service 不创建或修改其远程写入目的地和凭据。

### 同步与停用

使用 `{"collection":"pull","collector":"vmagent"}` 或 `{"collection":"pull","collector":"alloy"}` 创建数据源。需要历史查询时单独添加 `query`。`/api/v1/metrics-data-sources/capabilities` 返回已配置的采集器。配置可用性和运行健康状态是两个独立信号。

默认每 30 秒同步一次，每个采集器最多处理 512 个环境，同时执行八个路由发现请求。每次选择按 ID 顺序检查最多四倍目标上限的记录，不支持的目标和失败记录也计入扫描预算。回执中的 `truncated: true` 表示扫描或目标预算之外仍有未处理的资源。支持外部 HTTP 和托管 Docker/E2B `envd_http` 目标。不支持或不可用的目标会被移除并报告，不会改由 Service 代理采集。

`reload_requested` 表示采集器接受了重载请求。需要在采集器端确认配置生效、采集健康和导出情况；vmagent 异步重载。重载失败会在下次同步重试。等待中或已过期的回执不能证明采集正常。

写入进程运行期间，可以禁用或解除绑定数据源，再确认下一次成功同步移除了对应任务。使用外部采集器时，在停止写入进程、删除其配置或设置 `monitoring.enabled = false` 之前，运维必须自行移除托管片段并重载采集器。停止同步不会停用已加载的任务。

## 连接反向 Device

监控 Device 注册独立于可执行环境。工作空间管理员指定预期的原生 Device 身份并选择 proxy 数据源。创建时返回一次性连接令牌。请安全保存；元数据读取无法取回令牌。

Envd 反向连接使用 WSS、`eip.v1` 子协议和 Bearer 连接令牌，连接到 `/api/v1/devices/{device_id}/connect`。开发时使用直接回环 WS，需显式设置 `A13N_MONITORING__ALLOW_LOOPBACK_WS=true`（默认关闭）。反向代理后方保持关闭：回环地址的对端可能正在转发远程流量。代理终止 TLS 时，配置 `server.trusted_proxies`，让 Service 接受可信代理提供的安全协议标记。拒绝查询字符串凭据、浏览器 Origin 和仅使用 cookie 的认证。连接前配置 Envd 采样器。

接收副本拥有连接和临时历史，其他 API 副本将授权后的查询路由到该副本。令牌轮换、禁用、移除数据源和所有权丢失会关闭连接。反向 Device 不提供采集器可达的 HTTP 端点，因此不能选择 pull。Native/external Device 历史查询不依赖活动连接。

## 查询与估算临时历史容量

环境和 Device 的类型化查询选择一个指标和时间范围。`history = "configured"` 使用选定数据源的查询后端；没有查询后端时使用其临时缓冲区。`history = "temporary"` 明确选择 proxy 数据源的缓冲区。`include_snapshot = true` 为概览和资源清单视图附加最新 EIP 快照；普通图表查询只返回新鲜度和请求的序列。

Local Cache 保存 24 小时的分钟聚合概览指标，以及一小时的逐接口网络和逐设备磁盘/cgroup I/O 速率。每类资源历史最多保留 64 个来源，包括最近消失的来源。响应报告实际覆盖范围和截断情况。缺失及过期观测保留为空缺；计数器重置和来源替换会中断速率连续性。接口和磁盘保持独立身份，不进行无差别求和。压力和文件系统历史需要外部后端。

每个 control 进程默认允许 64 个代理连接，最多可配置 512 个。完整资源清单每个连接的数值历史占用低于 2 MiB，此外还有 Python 对象、最新快照和导出状态。提高容量前，按实际资源清单估算进程内存。重启或所有权变化可能丢失临时历史。Redis 传递所有权和有界查询消息，不保存历史样本。数据库短暂故障时，只要 Redis 所有权仍然有效，已有条目可保留历史和反向连接，宽限期最多为三个轮询间隔。观测、查询和导出仍须确认当前权限；无法确认时缓存标记为离线。故障超过宽限期或所有权丢失后，条目会被移除。

外部查询使用独立的并发预算，默认为八个请求、五秒截止时间和 4 MiB 响应上限。每个查询最多返回 200 个序列，时间范围不超过 90 天，每个序列不超过 11,000 个求值点。根据图表宽度选择步长。外部速率查询使用五分钟 PromQL 窗口；临时速率使用相邻观测，因此结果可能不同。

配置范围参见[设置参考](configuration-reference.md#monitoring)。需要持久保留、更长资源历史或更大规模设备时，使用外部历史后端。
