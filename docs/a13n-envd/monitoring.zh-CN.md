---
title: Device 监控与指标
sidebarTitle: 监控
description: 采样 Device 资源、检查数据新鲜度，并通过 Prometheus 或 OTLP 导出指标。
---

本指南用于监控已有 Envd 部署。守护进程启动、身份和传输设置见[守护进程配置](configuration.md)。

## 系统信息与资源监控

通过 EIP `initialize` 或 `device.describe` 读取系统身份：Linux、macOS 和 Windows 上的操作系统及版本、内核版本、原生架构，以及可取得的 CPU 型号。守护进程版本为 `initialize.server.version`。

按启动方式选择采样：

- `connect` 默认每 10 秒采集系统、网络、磁盘、套接字、本地文件系统和守护进程用量，使用已保存 Host 重连时也如此。
- 其他启动方式通过 `monitoring` 启用采样；选择原生 OTLP 导出且省略此字段时，也会启用默认采样。
- 在守护进程 JSON 中设置 `"monitoring": null` 可关闭采样，系统身份仍可读取。

修改配置或二进制后，重启 Envd 生效。只更新 Harness UI 不会改变运行中的远程守护进程。配置一个命名文件系统和 10 秒间隔：

```json
{
  "monitoring": {
    "sample_interval_ms": 10000,
    "filesystems": {"workspace": "/workspace"}
  }
}
```

`monitoring: {}` 使用默认设置，包括自动发现本地文件系统。`connect` 保留显式设置和通常的优先级。采样间隔可选 1000–60000 毫秒，最多配置 16 个命名文件系统目标。名称由 1–64 个 ASCII 字母、数字、`_` 或 `-` 构成；路径必须是没有 `..` 的原生绝对路径，例如 Windows JSON 中的 `C:\\workspace`。

在已初始化的 Python Device connection 上读取最新样本，无需打开会话：

```python
info = (await device.describe()).system_info
report = await device.metrics()
if report.sample is not None:
    sample = report.sample
    print(sample.system.cpu_busy_percent, sample.system.memory_available_bytes)
    print(sample.sample_age_ms, sample.sequence)
```

`device.metrics` 复用现有认证传输，并在采样开启时声明。Envd 和 `a13n-envd-client` 使用 EIP 0.4；迁移旧协议部署时一起升级。传输帧和 `eip.v1` 命名空间保持不变。

资源字段的含义如下：

| 观测                                                         | 含义                                                                                                                                                                      |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system.cpu_busy_percent`                             | 相邻有效原生采样之间的系统 CPU 忙碌百分比，归一化为 0–100。首次采样或无效计数器增量时缺失。                                                                               |
| `sample.system.logical_cpu_count`                            | 系统逻辑 CPU 数量，不代表 cgroup 配额或亲和性限制后的有效容量。                                                                                                           |
| `sample.system.memory_total_bytes`、`memory_available_bytes` | 物理内存容量与可用内存观测，单位为字节。Linux 使用 MemAvailable；Windows 使用原生可用物理内存；macOS 以 free + inactive 页面估计可回收内存，不重复计入 speculative 页面。 |
| `sample.system.cpu_time_seconds`                             | 可选原生累计 CPU 时间：Linux 提供八种模式，Windows 提供 user/system/idle；macOS 不提供累计秒数。                                                                          |
| `sample.filesystems.<name>.metrics`                          | 整个文件系统的总量、空闲量、可用字节数，以及可选 inode 计数，不是目录大小。目标不可读时保留名称，metrics 缺失。Windows 使用实际卷容量而非调用账户配额，不提供 inode。     |
| `sample.system.pressure`                                     | 可选的 Linux CPU、内存、I/O PSI。                                                                                                                                         |
| `sample.cgroup_v2`                                           | 可选的显式指定 Linux cgroup v2 观测。                                                                                                                                     |

概览还包含平台和设备清单：

| 观测                        | 含义                                                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sample.system` 平台字段    | Linux/macOS 的交换空间用量和容量；macOS 的 wired/compressed 内存；Windows 的提交量、提交限制、缓存及全局进程、线程、句柄数。Windows 提交量不是交换空间用量。 |
| `sample.network.interfaces` | 最多 64 个接口，包含原生身份及可用的累计字节、包、错误和丢弃计数。                                                                                           |
| `sample.disks.devices`      | 最多 64 个磁盘，包含累计字节和操作计数，以及可选的原生耗时和队列值。Linux 排除分区及 loop/RAM 设备。                                                         |
| `sample.connections`        | TCP 状态和 UDP 套接字的聚合计数，不包含地址或逐连接记录。macOS 提供总数，不提供 TCP 状态明细。                                                               |
| `sample.local_filesystems`  | 最多 16 个自动发现的本地文件系统，与命名目标分开报告。使用不透明的原生身份，不暴露路径。                                                                     |
| `sample.daemon`             | Envd Session、活动命令组、传输、公共传输连接数及实际保留输出字节数。采集不完整时，依赖 worker 的总量缺失。                                                   |

聚合前先检查清单截断和数据源可用性。接口及磁盘身份标记计数器生命周期；对堆叠设备求和会重复计算工作量。文件系统发现读取挂载而非目录内容，过滤伪文件系统和远程文件系统，可包含已挂载的可移动本地卷。原生 API 权限影响可用字段。

读取 Linux 容器用量时，在 `monitoring` 添加 `"cgroup_v2_directory": "/sys/fs/cgroup/your-group"`。它选择该组及其后代，其他平台拒绝此选项。字段涵盖 CPU 时间和限流、内存用量与事件、本地 CPU/内存限制、任务数与限制、设备 I/O 及 PSI。`unlimited`/`limited` 标签描述所选组的本地限制；祖先限制和亲和性可能进一步缩小容量。

PSI 返回可选的 `some`/`full` 记录，包括 `avg10_percent`、`avg60_percent`、`avg300_percent` 和累计 `total_usec`。这些内核窗口独立于采样间隔。系统级 CPU 没有定义 `full` 记录。缺失字段在线上省略，在 Python 中为 `None`。

容器中的系统指标可能描述宿主机；组用量应使用显式 cgroup 观测。缺失的组数据保持缺失。APFS 返回共享卷视图。Windows 文件系统观测需要 Windows 10 version 1809 / Server 2019 起的卷信息 API；旧系统仍可使用其他能力。仅在 inode 总量为正时计算使用率。

每个守护进程运行一个 sampler，零会话时也继续运行。读取返回缓存。首次采集完成前，`report.sample` 为 `None`；源较慢时保留前一个样本，样本年龄持续增加。托管 Linux 将运行时目标解析到实际挂载，其余目标解析到原始系统树，并拒绝被屏蔽的启动路径。

构建图表或 exporter 时：

1. 按 `(device_id, generation, sample.sequence)` 去重。
2. 比较 `sample.sample_age_ms` 与新鲜度阈值，将缺失观测保留为空档。
3. 用不同样本计算速率。代次变化、计数器下降或数据缺失后重建基线；cgroup 可以在同一代次内重置。
4. 累计时间映射为秒，事件映射为 counter，字节和 inode 映射为 gauge。按需要将百分比换算为比例。
5. 将峰值标为采样最大值：CPU 值是区间平均，点观测可能漏掉尖峰。

由消费方存储历史、管理告警。Envd 保留一个样本，供 EIP、HTTP 抓取和可选 OTLP 导出使用。采样持续到守护进程关闭，不受会话清理影响。

## Prometheus 与 OpenTelemetry

采集器能访问 Envd 时可选择 HTTP 抓取；只有出站连接的部署可选择原生 OTLP 推送。[HTTP 传输](configuration.md#carrier-profiles) 启用 `"monitoring": {}` 后，`GET /metrics` 从与 EIP 相同的缓存提供 Prometheus text format 0.0.4。它复用监听器和认证，无需会话或 EIP 初始化。stdio 和反向 WebSocket（包括 `connect`）支持 EIP 观测和 OTLP 推送，不提供 HTTP 抓取端点。

将监听器凭据放在采集器侧 secret 文件中，并限制读取者：**该凭据同时授权 EIP 操作和指标访问**。公网连接使用经过验证的 HTTPS。显式 loopback/provider-private 明文设置仍可用于其可信范围。

### 原生 OTLP 推送

配置 OTLP receiver，然后重启 Envd。任何 EIP 传输均可使用：

```bash
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_METRIC_EXPORT_INTERVAL=10000
a13n-envd connect https://host.example.com
```

通用或指标专用 OTLP endpoint 也会选择导出，无需 `OTEL_METRICS_EXPORTER`。省略 `monitoring` 时启用默认采样；显式 `"monitoring": null` 关闭采样和导出。只停止导出、保留显式配置的采样时，设置 `OTEL_METRICS_EXPORTER=none` 或 `OTEL_SDK_DISABLED=true`。

| 设置                                                            | 行为                                                                                                                   |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT`                                   | HTTP 基础 URL，exporter 会追加 `/v1/metrics`。                                                                         |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`                           | 完整的指标 URL，直接使用原值。                                                                                         |
| `OTEL_EXPORTER_OTLP[_METRICS]_PROTOCOL`                         | `http/protobuf`（默认）或 `grpc`。gRPC 使用 receiver 的 gRPC 端口，通常为 4317。                                       |
| `OTEL_EXPORTER_OTLP[_METRICS]_HEADERS`                          | 逗号分隔的 `key=value` 请求头；值使用百分号编码，例如 `authorization=Bearer%20TOKEN`。真实值通过 secret 管理系统注入。 |
| `OTEL_EXPORTER_OTLP[_METRICS]_TIMEOUT`                          | 导出请求超时，单位为毫秒。                                                                                             |
| `OTEL_EXPORTER_OTLP[_METRICS]_CERTIFICATE`                      | 加入系统信任根的 PEM CA 文件。                                                                                         |
| `OTEL_EXPORTER_OTLP[_METRICS]_CLIENT_CERTIFICATE`、`CLIENT_KEY` | mTLS 使用的 PEM 客户端证书和私钥，必须同时提供。                                                                       |
| `OTEL_METRIC_EXPORT_INTERVAL`                                   | 导出间隔，单位为毫秒，默认 60000。采样使用独立的配置间隔。                                                             |
| `OTEL_SERVICE_NAME`、`OTEL_RESOURCE_ATTRIBUTES`                 | 服务名称和资源属性，例如 `workspace.id=example`。                                                                      |

`[_METRICS]` 表示通用和指标专用两种形式，指标专用值优先。可信本地网络之外使用经过验证的 TLS。导出凭据独立于 HTTP 监听器凭据。托管 Linux 将 TLS 文件复制到私有 broker 镜像，并在会话中屏蔽原始路径。命令不会继承守护进程的 exporter 设置，可显式提供自己的 OTEL 设置。

资源的 `service.instance.id` 是实际设备 ID。数据点携带 `device_id` 和 `generation`，使用下文的指标名。Counter 导出原生累计值，包括重置后的值；缺失或过期的资源值省略。Double 精度可能对极大的整数计数产生舍入。导出失败不影响采样和 EIP，关闭时允许有界时间刷出数据。每个设备选择一条采集路径，避免推送和抓取重复摄取。

### Prometheus 抓取配置

1. 在 Prometheus 机器上，将监听器凭据挂载到 `/run/secrets/envd-token`。
2. 将下方目标和 CA 路径替换为可达的 Envd 端点及其信任证书。
3. 抓取间隔应不短于守护进程采样间隔：

```yaml
scrape_configs:
  - job_name: envd
    scrape_interval: 10s
    scrape_timeout: 5s
    scheme: https
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/envd-token
    tls_config:
      ca_file: /etc/prometheus/envd-ca.pem
    static_configs:
      - targets: [envd.example.com:8443]
```

Secret 文件只包含 token。系统根证书已信任服务端时，省略 `ca_file`。显式配置的同机回环 HTTP 使用回环目标及 `scheme: http`，并省略 `tls_config`。更频繁地抓取只会重复读取缓存。

### OpenTelemetry Collector

对于包含 [Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver) 的 Collector，将抓取配置放入 metrics pipeline：

```yaml
receivers:
  prometheus/envd:
    config:
      scrape_configs:
        - job_name: envd
          scrape_interval: 10s
          scrape_timeout: 5s
          scheme: https
          metrics_path: /metrics
          authorization:
            type: Bearer
            credentials_file: /run/secrets/envd-token
          tls_config:
            ca_file: /etc/otelcol/envd-ca.pem
          static_configs:
            - targets: [envd.example.com:8443]
processors:
  batch: {}
exporters:
  otlphttp/metrics:
    endpoint: https://metrics.example.com:4318
service:
  pipelines:
    metrics:
      receivers: [prometheus/envd]
      processors: [batch]
      exporters: [otlphttp/metrics]
```

设置 exporter 端点和后端认证。每个目标分配给一个活跃采集器，或为副本配置后端 HA 去重。Collector 需要访问 Envd HTTP 监听器的网络路径，仅有反向 WebSocket 连接不能提供该路径。使用原生推送时，改为配置 OTLP receiver。

### 指标与新鲜度

用 `device_id` 和 `generation` 区分序列。系统序列描述守护进程可见的系统，可能是容器宿主机；`envd_cgroup_*` 描述所选组。文件系统序列使用配置的名称而非路径，测量范围为所在文件系统。

| 序列                                                                                        | 含义                                                                            |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `envd_sample_available`、`envd_sample_fresh`                                                | 缓存和采集健康状态，与 Prometheus `up` 分开。                                   |
| `envd_sample_age_seconds`、`envd_sample_interval_seconds`                                   | 单调时钟样本年龄与配置的采样间隔。                                              |
| `envd_sample_timestamp_seconds`、`envd_sample_sequence`、`envd_collection_duration_seconds` | 采集开始时间、generation 内序号和采集耗时。                                     |
| `envd_system_cpu_busy_ratio`                                                                | 0–1 的 CPU 忙碌比例，已是相邻原生样本间的平均值。                               |
| `envd_system_cpu_time_seconds_total{mode="user"}`                                           | 累计 CPU 秒数，使用跨多个采样间隔的 `rate`。                                    |
| `envd_system_memory_total_bytes`、`envd_system_memory_available_bytes`                      | 系统内存 gauge。                                                                |
| `envd_cgroup_cpu_usage_seconds_total`、`envd_cgroup_cpu_throttled_seconds_total`            | 所选组累计 CPU 和限流秒数。                                                     |
| `envd_cgroup_memory_current_bytes`、`envd_cgroup_memory_events_total{event="oom"}`          | 所选组内存 gauge 和累计事件。                                                   |
| `envd_cgroup_cpu_limit_unlimited`、`envd_cgroup_memory_limit_unlimited`                     | 本地限制无限为 1，有限为 0，不可读时缺失；只有有限值才有数值 quota/limit 序列。 |
| `envd_system_pressure_ratio`、`envd_cgroup_pressure_ratio`                                  | PSI 比例，标签为 `resource`、`level`、`window`，窗口为 10/60/300 秒。           |
| `envd_system_pressure_stall_seconds_total`、`envd_cgroup_pressure_stall_seconds_total`      | 累计 stall 秒数，标签为 `resource`、`level`。                                   |
| `envd_filesystem_available`、`envd_filesystem_available_bytes`                              | 目标可读性与当前账户可用字节数；同时提供总量、空闲字节和原生 inode gauge。      |

分别判断抓取健康和资源新鲜度：

- 首次采集前，HTTP 200 携带 `sample_available=0` 和 `sample_fresh=0`，不含资源值。
- 样本年龄超过两倍采样间隔后，HTTP 200 携带 `sample_fresh=0` 和保留的样本元数据。资源序列省略，直到新样本到达。
- 关闭监控时，认证后返回 404。凭据缺失或无效时返回 401。

序列使用抓取时间戳，并单独携带观测时间。同时对 `up{job="envd"} == 0` 和 `envd_sample_fresh{job="envd"} == 0` 告警。用 `100 * envd_system_cpu_busy_ratio` 显示 CPU 百分比，用 `rate(envd_cgroup_cpu_usage_seconds_total[5m])` 表示每秒消耗的 CPU 秒数。计数器下降标记重置；新代次创建新序列，cgroup 重建则可能在同一代次内重置计数器。缺失和过期数据保留为空档。
