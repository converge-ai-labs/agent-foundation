---
title: 守护进程配置与传输
sidebarTitle: 配置与传输
description: 将 Envd 连接到 Host，选择执行设置，并采集资源指标。
---

在启动时配置 Envd，选择连接方式、执行账号、文件系统隔离和网络模式。Host 负责部署、凭据和守护进程生命周期。随后，每个 EIP 会话在这些设置内选择工作目录并执行操作。

本页的 JSON 和环境变量用于守护进程。Harness UI YAML 和 `EnvironmentProviderSpec` 配置 Host 侧适配器，包括必需方法及带秘密引用的可选会话出站策略。需要预置账号、shell 和 sudo 的容器时，使用[沙箱镜像](sandbox.md)。

## 配置层级

Envd 按五层配置启动，后面的层优先：

1. 内置默认值。
2. `--config` 选择的守护进程 JSON 文件。
3. `A13N_ENVD_CONFIG_JSON`，使用相同 schema 校验。
4. 标量 `A13N_ENVD_*` 环境变量。
5. 显式 CLI 参数。

对象递归合并，数组和标量替换已有值。每个 JSON 层必须先通过校验，再参与合并。启动者可完全通过环境变量提供嵌套设置：

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

标量设置覆盖身份、执行、发现、元数据和生命周期：`A13N_ENVD_ALLOW_SUDO`、`A13N_ENVD_EXECUTION_UID`、`A13N_ENVD_EXECUTION_GID`、`A13N_ENVD_EGRESS_MODE`、`A13N_ENVD_FULL_CONTROL`、`A13N_ENVD_COMPUTER_USE`、`A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`、`A13N_ENVD_DIRECTORY_DISCOVERY`、`A13N_ENVD_DEVICE_ID`、`A13N_ENVD_NAME`、`A13N_ENVD_DESCRIPTION`、`A13N_ENVD_DEFAULT_WORKING_DIRECTORY`、`A13N_ENVD_IDLE_TIMEOUT_MS` 和 `A13N_ENVD_DISCONNECT_GRACE_MS`。布尔值接受 `true`、`false`、`1` 和 `0`。

## 连接 Harness UI

在实际执行工作的计算机上发起出站连接：

```console
a13n-envd connect https://host.example.com --host work
```

1. 打开打印的批准地址，并登录 Host。
2. 批准匹配的验证码。
3. 保持 Envd 运行。后续启动用 `a13n-envd connect work` 复用注册。

Envd 在设备上生成凭据，因此浏览器配对只需验证码。带已保存状态的重连会复用批准。接入 Service 时，改为[注册 HTTP 守护进程](../environments/remote-envd.md#connect-to-the-service)。

使用本机可访问的 Host URL。除 `http://127.0.0.1:8765` 等回环 HTTP 外，必须使用 HTTPS。私有 CA 通过 `--ca-file /path/to/ca.pem` 添加，证书验证保持开启。用 `--name` 设置批准页面的显示名，`--default-working-directory` 选择已有原生目录，`--config` 提供守护进程 JSON 设置。

### 保存的实例与 Host

跨重启保留状态目录，并像账号凭据一样保护它。Unix 默认位置为 `~/.a13n-envd`；Windows 为 `%LOCALAPPDATA%/.a13n-envd`，不可用时回退到 `%USERPROFILE%/.a13n-envd`。用 `--state-dir` 或 `A13N_ENVD_STATE_DIR` 覆盖。

实例将设备 ID 保存在 `instances/<instance>/device-id`。各 Host 的配置、凭据和私有运行时位于 `instances/<instance>/hosts/<host>/`。默认实例为 `default`；省略 `--host` 时会从 URL 派生稳定别名。

为每个 Host 运行独立进程。同一台计算机连接两个 Host 时：

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

重启各进程时，使用原来的实例和 Host 别名。每个进程都有独立注册和会话。`connect` 在前台运行；需要自动启动时，在服务管理器中配置相同的账号、状态目录和命令环境。

认证被拒绝时，Envd 停止并保留凭据。检查 Host 地址、注册状态和挂载的状态目录。真正的新安装应使用新实例并显式批准。

## 独立运行的最小配置

启动由 Host 管理的 stdio 守护进程：

1. 创建并保护绝对路径的运行时父目录。
2. 将下方 JSON 保存为 `/absolute/path/to/a13n-envd.json`，把工作目录路径替换为已有目录。
3. 由 Host 通过 stdin/stdout 管道启动 Envd：

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

Envd 为当前代次创建不可预测的私有子目录，并持有独占运行时锁。管道承载 EIP 消息帧，诊断信息写入 stderr。

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

默认工作目录作为各会话的初始 cwd。省略时，Envd 记录启动 cwd。目录不存在会导致会话打开失败，而非守护进程启动失败。文件访问遵循执行账号、Sandbox 授权及外层沙箱的边界，不以 cwd 为边界。目录发现可在会话创建前读取有界的一层目录。

用 `--device-id`、JSON `device_id` 或 `A13N_ENVD_DEVICE_ID` 指定稳定设备 ID，或让安装状态目录保留生成的 ID。CLI 元数据选项覆盖 JSON。目录发现通过 JSON 或 `A13N_ENVD_DIRECTORY_DISCOVERY` 设置。Windows 启动路径使用原生格式，EIP 路径使用 `/C:/...` 或 `/UNC/server/share/...`。

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

`device.metrics` 复用现有认证传输，并在采样开启时声明。Envd 和 `a13n-envd-client` 使用 EIP 0.3；迁移 0.1 或 0.2 部署时一起升级。传输帧和 `eip.v1` 命名空间保持不变。

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

采集器能访问 Envd 时可选择 HTTP 抓取；只有出站连接的部署可选择原生 OTLP 推送。[HTTP 传输](#carrier-profiles) 启用 `"monitoring": {}` 后，`GET /metrics` 从与 EIP 相同的缓存提供 Prometheus text format 0.0.4。它复用监听器和认证，无需会话或 EIP 初始化。stdio 和反向 WebSocket（包括 `connect`）支持 EIP 观测和 OTLP 推送，不提供 HTTP 抓取端点。

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

## 启用命令

启用 Full Control，以执行账号的权限运行原生 shell：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

JSON `"full_control": true` 选择相同模式，标量环境设置优先。Full Control 在 Unix 提供 `/bin/sh`，在 Windows 提供 PowerShell，并继承启动者环境和 `PATH` 顺序。它排除守护进程启动变量（`A13N_ENVD_*`、`EIP_*`）及 OTLP 指标设置。Shell 别名和未导出变量留在启动 shell 中。逐命令覆盖只影响该命令。

Envd `full_control` 启用执行能力，不提供隔离，并且独立于 Harness UI 的本地模式。Full Control 与手动可执行文件根目录/shell 配置二选一，不能组合。显式命令配置示例：

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "trusted_executable_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
  "shell_profiles": [
    {
      "profile_id": "sh",
      "display_name": "POSIX shell",
      "native_executable": "/bin/sh",
      "fixed_arguments": ["-c"],
      "safe_base_environment": {},
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576,
      "allow_login_mode": false
    }
  ]
}
```

关闭 Full Control，且根目录和配置都为空时，Envd 不声明命令方法。根目录和配置选择可信可执行文件；Host 外层边界及[执行隔离](isolation.md)决定访问范围。[受控会话出站网络](egress.md)添加目标限制和凭据注入。

检查设备声明的执行功能，确认信号、限制和可执行位支持。子进程环境由可继承值和显式命令输入组合而成；守护进程控制变量仍属于启动配置。

## 原生身份与 sudo

> [!IMPORTANT]
> 默认允许原生 sudo。禁用 Sandbox 时，经过授权的软件包安装和系统文件修改会作用于外层沙箱的原始文件系统，并跨会话保留。

Linux 上省略执行 UID/GID 会保留启动者身份，包括 root。Root 启动者切换账号前，先预置账号及 home/工作目录权限，再同时设置 `execution.uid`/`execution.gid`、两个环境变量或两个 CLI 选项。显式 `0:0` 选择 root。对于预置的 `sandbox` 账号：

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

命令、文件 RPC、工作目录访问和目录发现使用所选账号的原生权限。文件 RPC 在权限不足时报告错误，不调用 sudo。命令中的 sudo 影响该命令及其后代，不影响后续文件 RPC。凭据加载和 broker 管理仍由守护进程负责。

切换账号会初始化附加组和账号环境默认值。保留启动者身份会保留原生 capability 继承。从 root 切换用户时，执行命令前移除 Envd 为 supervisor 保留的信号 capability。外层平台和受控出站限制仍然适用；非特权启动者不能选择其他账号。

在镜像或部署中安装 sudo 并配置 sudoers。`allow_sudo` 允许原生提权，但仍受该授权及外层 `no_new_privs`/`nosuid` 限制。Envd 不提供免密码规则。

用以下任一启动设置禁用额外提权：

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

等价的 JSON 设置为：

```json
{
  "execution": {"allow_sudo": false}
}
```

Linux 会对 worker 及其后代应用 `no_new_privs`，阻止通过 sudo、setuid 二进制和文件 capabilities 提权。包括 root 在内的已有权限保留。启动优先级仍然适用（`--allow-sudo true` 覆盖环境变量 `false`）；命令不能通过自身环境改变此策略。不支持该实现的平台拒绝 `false`。UID/GID 选择仅限 Linux，其他平台保留原生启动身份。

## 传输通道配置

按 Host 所需的连接方向选择：

| 配置                | 启动设置                                         | 用途                                   |
| ------------------- | ------------------------------------------------ | -------------------------------------- |
| `stdio`             | 父进程提供的进程管道                             | Host 管理的本地守护进程                |
| `http`              | 绑定地址、受保护凭据文件、TLS 或显式可信明文范围 | Host 主动连接设备                      |
| `reverse_websocket` | 出站 URL 和受保护凭据文件                        | 设备主动连接经过身份验证的 Host 监听器 |

将 `A13N_ENVD_TRANSPORT` 设为所选通道。使用本地 HTTP 客户端时：

1. 准备受保护的 token 文件和已有工作目录。
2. 替换下方命令中的对应路径。命令会创建私有运行时父目录并绑定回环 HTTP。
3. 启动 Envd：

```bash
mkdir -p "$HOME/.a13n-envd-runtime"
chmod 700 "$HOME/.a13n-envd-runtime"
export A13N_ENVD_RUNTIME_DIR="$HOME/.a13n-envd-runtime"
export A13N_ENVD_DEVICE_ID=device-http-example
export A13N_ENVD_TRANSPORT=http
export A13N_ENVD_HTTP_BIND=127.0.0.1:8787
export A13N_ENVD_HTTP_CREDENTIAL_FILE=/private/envd-token
export A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback
a13n-envd --default-working-directory /absolute/path/to/workspace
```

Python 客户端使用相同凭据和预期设备 ID，连接基础 origin `http://127.0.0.1:8787`。客户端会选择经过认证的 `/eip/control` 和 `/eip/transfer` 路由。

使用 HTTPS 时，取消设置 `A13N_ENVD_HTTP_PLAINTEXT_SCOPE`，设置 `A13N_ENVD_HTTP_TLS_CERT_FILE` 和 `A13N_ENVD_HTTP_TLS_KEY_FILE`，连接前配置客户端信任。Provider 私网路径使用 `provider_private_link`，并要求客户端显式启用。公共端点要求 HTTPS。代理路由和 TLS 见 [Python 传输选项](python-client.md#http-options)。

出站反向 WebSocket 设置 `A13N_ENVD_REVERSE_WS_URL` 和 `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`；用 `A13N_ENVD_REVERSE_WS_CA_FILE` 添加私有信任。WebSocket 监听器由 Host 管理。

将凭据保存在受保护文件中，不放入 argv、URL、描述符、日志或可移植环境状态。凭据授予设备访问权，会话隔离由执行边界提供。

## 生命周期与归属

先初始化设备连接，再为每个资源范围打开会话。一个守护进程代次服务多个会话，各会话记录 cwd 并管理自己的操作。`session.close` 关闭该范围。保活只续期调用者自己的所有权，因此发现流量不会保留已放弃的会话。

消息帧传输断开后，所有者可在宽限期内、同一代次中显式重新附加。附加恢复对已有资源的访问，不重放命令或恢复传输。过期后应打开新会话并丢弃旧引用。

关闭时，Host 关闭自己的连接，只终止由它管理生命周期的守护进程。会话清理回收进程及私有输出/暂存存储，同时保留工作目录文件。报告清理失败时，应视为回收未完成。

## 接纳工作前检查

检查守护进程版本：

```bash
a13n-envd --version
```

1. 匹配 Python 客户端的精确版本。
2. 从守护进程子环境中移除仅供 Host 使用的 `A13N_ENVD_EXECUTABLE`。未知的 `A13N_ENVD_*` 变量和 JSON 字段会使配置失败。
3. 初始化设备，验证协议和身份，再检查所选会话范围的就绪状态。
4. 通过部署自己的启动者和检查测试外层执行边界。Envd 没有 `isolation probe` 或逐命令隔离模式。

## 独立字段参考

在守护进程 JSON 中使用以下字段：

| 根字段                                       | 默认值或含义                                                                                                         |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `device_id`                                  | 显式稳定身份，否则使用安装身份                                                                                       |
| `installation_state_directory`               | 未提供显式设备 ID 时用于持久保存身份                                                                                 |
| `name`, `description`                        | 可选显示元数据                                                                                                       |
| `default_working_directory`                  | 未设置时使用启动 cwd                                                                                                 |
| `directory_discovery`                        | `true`                                                                                                               |
| `idle_timeout_ms`                            | 会话空闲寿命                                                                                                         |
| `disconnect_grace_ms`                        | 已分离会话重新附加的宽限期                                                                                           |
| `execution.uid`, `execution.gid`             | 可选、成对的 Linux 原生 ID；省略时保留启动者身份，包括 root                                                          |
| `execution.allow_sudo`                       | `true`；原生 sudoers 仍控制授权                                                                                      |
| `sandbox`                                    | `{"mode":"disabled"}`；或包含目录 `grants` 的 `restricted`                                                           |
| `egress`                                     | `{"mode":"inherit"}`；或 `deny` / `controlled`                                                                       |
| `full_control`                               | `false`；启用时自动配置原生 shell 并继承命令环境                                                                     |
| `computer_use`                               | `false`；主动启用 macOS/X11/Windows 截图与输入方法，要求禁用 Sandbox 并继承出站网络；参见[电脑操作](computer-use.md) |
| `computer_use_permission_timeout_ms`         | `120000`；开始任何 EIP 传输前，等待 macOS 权限或 X11/Windows 桌面就绪的正数启动期限                                  |
| `trusted_executable_roots`, `shell_profiles` | 空；除非启用 Full Control，否则命令方法禁用                                                                          |
| `limits`                                     | 设备总限制和逐会话限制                                                                                               |

默认容量为 128 个会话、设备 256 个并发操作、每个会话 128 个并发操作、设备 4 GiB spool、会话 1 GiB spool、每条输出流 256 MiB 及 2 MiB 预览。会话容量应不超过设备总额，预览不超过流容量，spool 为两条流保留足够容量。

### Shell 配置字段

提供 `profile_id`、`display_name`、`native_executable`、`executable_search_roots` 和正数 `max_script_bytes`。可执行文件和根目录使用已存在的原生绝对路径。可选默认值为 `fixed_arguments: []`、`safe_base_environment: {}` 和 `allow_login_mode: false`。

## 受控会话出站网络

在 Linux 上为各会话限制目标并注入 HTTPS 凭据，请按[会话出站网络](egress.md)操作。启动守护进程时配置受控模式，再通过 EIP 提交策略和秘密值。
