---
title: 守护进程配置与传输
sidebarTitle: 配置与传输
description: 配置独立 Envd 守护进程及其连接 Host 的方式。
---

这是独立 Envd 配置，不是 Harness UI YAML 或 `EnvironmentProviderSpec`。Host 负责部署、账号选择、外层沙箱、凭据和守护进程生命周期。适配器配置选择工作目录、必需方法，以及可选的会话出站网络策略；该策略中的秘密是引用，不是秘密值。设备启动配置则单独固定执行、Sandbox 和网络模式。[沙箱镜像](sandbox.md)提供可直接使用的账号、shell 和 sudo 默认值，不改变独立守护进程默认值。

## 配置层级

启动优先级从低到高如下：

1. 内置默认值。
2. 由 `--config` 选择的守护进程 JSON。
3. `A13N_ENVD_CONFIG_JSON`，使用与文件相同的严格 schema。
4. 标量 `A13N_ENVD_*` 环境变量。
5. 显式命令行参数。

对象递归合并；数组和标量替换之前的值。即使被后续层覆盖，每个 JSON 层本身也必须有效。沙箱启动者因此可以完全通过环境变量配置 Envd，包括嵌套限制和 shell 配置，无需写配置文件：

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

标量快捷变量包括 `A13N_ENVD_ALLOW_SUDO`、`A13N_ENVD_EXECUTION_UID`、`A13N_ENVD_EXECUTION_GID`、`A13N_ENVD_EGRESS_MODE`、`A13N_ENVD_FULL_CONTROL`、`A13N_ENVD_COMPUTER_USE`、`A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`、`A13N_ENVD_DIRECTORY_DISCOVERY`、`A13N_ENVD_DEVICE_ID`、`A13N_ENVD_NAME`、`A13N_ENVD_DESCRIPTION`、`A13N_ENVD_DEFAULT_WORKING_DIRECTORY`、`A13N_ENVD_IDLE_TIMEOUT_MS` 和 `A13N_ENVD_DISCONNECT_GRACE_MS`。布尔值接受 `true`、`false`、`1` 或 `0`。

设备初始化协商协议并验证设备身份。EIP 会话选择固定 cwd，管理操作、进程、输出和传输；不能更改可信启动身份或 sudo 策略。

## 连接 Harness UI

连接 Harness UI 时，启动一个出站连接：

```console
a13n-envd connect https://host.example.com --host work
```

1. 打开 Envd 打印的批准地址。
2. 登录 Host。
3. 批准匹配的验证码。

保持 Envd 运行。后续启动时，`a13n-envd connect work` 会复用已保存的身份、凭据和已批准连接。凭据在设备上生成，始终无需复制到浏览器。重连无需再次批准。Service 则连接你注册的 HTTP 守护进程；参见[连接 Service](../environments/remote-envd.md#connect-to-the-service)。

本机必须能够访问 Host URL。除 `http://127.0.0.1:8765` 等回环 HTTP 外，必须使用 HTTPS。私有 CA 可添加 `--ca-file /path/to/ca.pem`；始终不能禁用 TLS 验证。`--name` 设置批准页面的显示名，`--default-working-directory` 选择已存在的原生目录。`--config` 提供普通守护进程 JSON，包括资源限制和命令设置。

### 保存的实例与 Host

状态在 Unix 上默认存入 `~/.a13n-envd`，Windows 上存入 `%LOCALAPPDATA%/a13n-envd`（不可用时回退到用户配置目录）。通过 `--state-dir` 或 `A13N_ENVD_STATE_DIR` 覆盖。每个实例在 `instances/<instance>/device-id` 保存身份；命名 Host 的配置、凭据和私有运行时位于 `instances/<instance>/hosts/<host>/`。默认实例名为 `default`。未指定 `--host` 时，从 URL 派生稳定名称。请跨重启保留状态，并像账号凭据一样保护它。

每个运行中的 Envd 进程连接**一个 Host**。同一台物理计算机需要使用两个 Host 时，运行两个独立进程：

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

每个进程有自己的注册和会话。重启时使用同一个 `--instance` 和 Host 别名。`connect` 保持前台运行，不安装系统服务，也不修改操作系统启动设置。使用服务管理器时，保留启动账号、状态目录和预期命令环境。

身份验证被拒绝时，守护进程停止，不会替换凭据或请求新的信任关系。检查注册是否被撤销或遗忘、是否连接正确的 Host，以及是否挂载正确的状态目录。不要反复删除凭据来掩盖授权失败。全新安装需要新实例和显式批准。

## 独立运行的最小配置

默认传输通道为 stdio。提供绝对路径的私有运行时目录：

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

Host 创建并保护运行时父目录。Envd 创建不可预测的代次私有子目录，并取得独占运行时锁。stdin 和 stdout 专用于 EIP 消息帧；诊断信息使用 stderr。

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

会话只能在已存在的目录中打开；默认目录不存在时，会话打开失败，守护进程启动不受影响。省略 `default_working_directory` 时，守护进程记录启动时的 cwd。工作目录**不是访问根目录**：文件路径可以指向执行账号、Sandbox 授权和外层沙箱允许的整个文件系统。目录发现是有界的单层读取，在任何会话创建前即可使用。

显式设备 ID 可通过 `--device-id`、JSON `device_id` 或 `A13N_ENVD_DEVICE_ID` 提供。没有显式 ID 时，安装状态目录跨重启保留生成的身份。`--default-working-directory`、`--name` 和 `--description` 覆盖 JSON 元数据。通过 JSON 或 `A13N_ENVD_DIRECTORY_DISCOVERY` 设置目录发现。Windows EIP 路径使用 `/C:/...` 和 `/UNC/server/share/...`；守护进程启动路径使用操作系统原生格式。

## 系统信息与资源监控

Linux、macOS 和 Windows 都通过 EIP `initialize`、`device.describe` 返回的 Device descriptor 暴露基础系统信息：操作系统及版本、内核版本、原生架构，以及可取得的 CPU 型号。Envd 自身版本位于 `initialize.server.version`。

`a13n-envd connect` 默认每 10 秒采集一次系统、网络、磁盘、套接字、本地文件系统和守护进程用量，使用已保存的 Host 重新连接时也适用。已有安装需要更新 Envd 二进制并重启守护进程才能使用此默认值；只更新 Harness UI 不会改变正在运行的远程守护进程。

在 `--config` 文件或 `A13N_ENVD_CONFIG_JSON` 中设置 `"monitoring": null` 可关闭采样。其他守护进程启动方式仍需显式开启；选择原生 OTLP 导出时，省略 `monitoring` 也会启用默认采样。在守护进程配置中通过 `monitoring` 启用或自定义采样：

```json
{
  "monitoring": {
    "sample_interval_ms": 10000,
    "filesystems": {"workspace": "/workspace"}
  }
}
```

使用原生绝对路径，例如 Windows JSON 中的 `C:\\workspace`。`monitoring: {}` 使用默认值采集资源概览，包括自动发现的本地文件系统。自定义设置遵循通常的配置优先级，`connect` 会保留这些设置。关闭采样后，系统信息仍可读取。采样间隔接受 1000–60000 毫秒。最多配置 16 个文件系统目标，名称为 1–64 个 ASCII 字母、数字、`_` 或 `-`。路径不能包含 `..`。

通过已初始化的 Python Device connection 读取，无需创建 Session：

```python
info = (await device.describe()).system_info
report = await device.metrics()
if report.sample is not None:
    sample = report.sample
    print(sample.system.cpu_busy_percent, sample.system.memory_available_bytes)
    print(sample.sample_age_ms, sample.sequence)
```

调用使用现有认证 HTTP、反向 WebSocket 或 stdio 连接上的 EIP `device.metrics`，不需要额外端口、令牌或 TLS 配置。只有启用采样才会声明该方法。Envd 与 `a13n-envd-client` 使用 EIP 0.3；从 0.1 或 0.2 部署迁移时一起升级。`eip.v1` 命名空间和传输帧保持不变。

| 观测                                                         | 含义                                                                                                                                                                      |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system.cpu_busy_percent`                             | 相邻有效原生采样之间的系统 CPU 忙碌百分比，归一化为 0–100。首次采样或无效计数器增量时缺失。                                                                               |
| `sample.system.logical_cpu_count`                            | 系统逻辑 CPU 数量，不代表 cgroup 配额或亲和性限制后的有效容量。                                                                                                           |
| `sample.system.memory_total_bytes`、`memory_available_bytes` | 物理内存容量与可用内存观测，单位为字节。Linux 使用 MemAvailable；Windows 使用原生可用物理内存；macOS 以 free + inactive 页面估计可回收内存，不重复计入 speculative 页面。 |
| `sample.system.cpu_time_seconds`                             | 可选原生累计 CPU 时间：Linux 提供八种模式，Windows 提供 user/system/idle；macOS 不提供累计秒数。                                                                          |
| `sample.filesystems.<name>.metrics`                          | 整个文件系统的总量、空闲量、可用字节数，以及可选 inode 计数，不是目录大小。目标不可读时保留名称，metrics 缺失。Windows 使用实际卷容量而非调用账户配额，不提供 inode。     |
| `sample.system.pressure`                                     | 可选的 Linux CPU、内存、I/O PSI。                                                                                                                                         |
| `sample.cgroup_v2`                                           | 可选的显式指定 Linux cgroup v2 观测。                                                                                                                                     |

资源概览还包括：

| 观测                        | 含义                                                                                                                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sample.system` 平台字段    | Linux/macOS 的交换空间用量和容量；macOS 的 wired/compressed 内存；Windows 的提交量、提交限制、缓存及全局进程、线程、句柄数。Windows 提交量不是交换空间用量。 |
| `sample.network.interfaces` | 最多 64 个接口，包含原生身份及可用的累计字节、包、错误和丢弃计数。                                                                                           |
| `sample.disks.devices`      | 最多 64 个磁盘，包含累计字节和操作计数，以及可选的原生耗时和队列值。Linux 排除分区及 loop/RAM 设备。                                                         |
| `sample.connections`        | TCP 状态和 UDP 套接字的聚合计数，不包含地址或逐连接记录。macOS 提供总数，不提供 TCP 状态明细。                                                               |
| `sample.local_filesystems`  | 最多 16 个自动发现的本地文件系统，与命名目标分开报告。使用不透明的原生身份，不暴露路径。                                                                     |
| `sample.daemon`             | Envd Session、活动命令组、传输、公共传输连接数及实际保留输出字节数。采集不完整时，依赖 worker 的总量缺失。                                                   |

清单会明确报告截断。原生数据源不可用时保留缺失值。接口和磁盘身份区分计数器生命周期；不能将堆叠设备相加作为整机总量。文件系统发现会过滤伪文件系统和远程文件系统，可能包含已挂载的可移动本地卷，不扫描目录内容。可用字段取决于平台和原生 API 权限。

Linux 可在 `monitoring` 内添加 `"cgroup_v2_directory": "/sys/fs/cgroup/your-group"`，观察该组及其后代；其他平台拒绝该 Linux 专用配置。数据包含 CPU 用户态/系统态/总微秒数、带宽周期和限流、内存用量及事件、本地 CPU/内存限制、任务数及限制、逐设备 I/O 字节和操作计数，以及 PSI。限制使用 `unlimited` 或 `limited` 标签，不代表考虑祖先限制和亲和性后的有效容量。

PSI 提供可选 `some`、`full` 记录，包含 `avg10_percent`、`avg60_percent`、`avg300_percent` 与累计 `total_usec`。平均值采用内核窗口，与采样间隔无关。系统级 CPU `full` 未定义，因此缺失。不可用值在线上省略，在 Python 中为 `None`，不填充为零。

**容器中的系统指标仍可能描述宿主机。** 显式选择目标 cgroup；缺失的组观测不会被宿主机数据替代。APFS 空间描述其共享卷视图。Windows 文件系统观测依赖 Windows 10 version 1809 / Server 2019 起提供的卷信息 API，旧系统仍能提供其他能力。仅在 inode 总数为正时计算使用率。

每个 daemon 只有一个 sampler，零 Session 时仍运行。读取返回最新缓存，不触发采样。首次采样完成前 `report.sample` 为 `None`；源阻塞时仍可读取旧样本，其 `sample_age_ms` 持续增加。Managed Linux 将 runtime 目标映射到真实 runtime 挂载，其余目标映射到原始系统目录，并拒绝被遮蔽的启动保护路径。

消费报告时：

- 按 `(device_id, generation, sample.sequence)` 去重。
- 展示或导出当前值前检查 `sample.sample_age_ms`，保留缺失值。
- 只用不同样本计算计数器速率；generation 改变、计数器下降或数据缺失后重建基线。cgroup 可以独立于 daemon generation 重置。
- 根据 exporter 需要，将累计时间换算为秒、事件数映射为 counter、字节和 inode 映射为 gauge、百分比换算为比例。
- 图表峰值标记为采样值的最大值。CPU 百分比已是区间平均，瞬时尖峰可能落在采样间隔内。

Envd 只保留一个样本，通过 EIP、标准 HTTP 抓取和可选的原生 OTLP 导出提供观测。历史、告警和产品图表由消费方及其可观测性基础设施负责。Session 清理不停止采样，daemon 关闭会停止采样。

## Prometheus 与 OpenTelemetry

使用 [HTTP 传输](#carrier-profiles) 时，启用 `"monitoring": {}`，即可在同一地址抓取 `GET /metrics`。响应采用 Prometheus text format 0.0.4，与 `device.metrics` 读取同一缓存，无需 EIP 初始化或 Session。无需新增监听端口、令牌或 TLS 配置。stdio 和反向 WebSocket 部署（包括 `connect`）不提供 HTTP 抓取端点，消费方仍可通过 EIP 读取观测。

使用 HTTP listener 的 Bearer 凭据和经过验证的 HTTPS。**该凭据同时拥有 listener 的 EIP 操作权限，不是只读监控令牌。** 在采集器端通过 secret 文件保存，限制访问者并使用可信网络。公网连接要求 TLS；现有显式 loopback/provider-private 明文配置仍可使用。

### 原生 OTLP 推送

任何 EIP 传输都可以直接向 OTLP receiver 推送指标，包括只有出站连接的 `connect` 部署：

```bash
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_METRIC_EXPORT_INTERVAL=10000
a13n-envd connect https://host.example.com
```

配置通用 OTLP endpoint 或 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` 也会选择导出，无需设置 `OTEL_METRICS_EXPORTER`。此时省略 `monitoring` 会启用默认 CPU/内存采样；显式 `"monitoring": null` 同时关闭采样和导出。`OTEL_METRICS_EXPORTER=none` 或 `OTEL_SDK_DISABLED=true` 关闭导出，但保留显式配置的采样。修改后重启 Envd 生效。

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

`[_METRICS]` 表示通用和指标专用两种形式，指标专用设置优先。可信本地网络之外使用经过验证的 TLS。导出凭据与 Envd HTTP listener 令牌相互独立。托管 Linux 将 TLS 文件复制到私有 broker 镜像，并在 Session 中屏蔽原始路径。Full Control 命令不会继承 daemon 的 exporter 设置；payload 可以显式提供自己的 OTEL 设置。

导出资源的 `service.instance.id` 始终为实际 Device ID。数据点携带 `device_id`、`generation`，使用下文列出的指标名。计数器保留原生累计绝对值，包括重置后的值。缺失或过期的资源值保持省略。OTLP 使用 double，极大的整数计数可能损失精度。导出错误不影响 EIP 和采样；关闭时只在有界时间内等待 exporter 刷出数据。每个 Device 选择一条采集路径，避免同时推送和抓取造成重复摄取。

### Prometheus 抓取配置

在 Prometheus 所在机器上，将 Envd 凭据挂载到 `/run/secrets/envd-token`。配置实际可达的 Envd 地址、端口和 CA 证书：

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

如果证书已受抓取端的系统根证书信任，可省略 `ca_file`。凭据文件只包含令牌。对于显式配置的同机 loopback HTTP，使用 `scheme: http`、loopback 地址，并省略 `tls_config`。抓取间隔应不短于 daemon 的采样间隔；更频繁的抓取只会重复读取缓存。

### OpenTelemetry Collector

包含 [Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver) 的 Collector 发行版可以使用相同抓取配置，再通过普通 pipeline 导出：

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

替换 exporter 地址，并按后端要求配置认证。使用原生推送时，以 OTLP receiver 替代 Prometheus receiver。每个目标应分配给一个活跃采集器，或明确配置后端的 HA 去重；相同配置的 Collector 副本会重复抓取。采集器必须能访问现有 Envd HTTP listener，仅有出站反向 WebSocket 连接并不提供这条网络路径。

### 指标与新鲜度

所有序列携带 `device_id` 和 `generation` 标签。系统指标描述 daemon 可见的系统，容器内也可能是宿主机。`envd_cgroup_*` 仅描述配置的 cgroup。文件系统序列使用配置的 `filesystem` 名称，不暴露原生路径，测量范围是整个所在文件系统。

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

缺失值保持省略，不填零。首次采集完成前，`/metrics` 返回 200，携带 `sample_available=0`、`sample_fresh=0`，不包含资源值。缓存年龄超过两倍采样间隔时，仍返回 200，携带 `sample_fresh=0` 和保留的样本元数据，并省略资源序列，直到恢复新鲜观测。关闭监控时，认证后返回 404；凭据缺失或无效返回 401。序列使用抓取时间戳，观测时间另行提供。

例如，分别对 `up{job="envd"} == 0` 和 `envd_sample_fresh{job="envd"} == 0` 告警。使用 `100 * envd_system_cpu_busy_ratio` 显示百分比，用 `rate(envd_cgroup_cpu_usage_seconds_total[5m])` 计算每秒消耗的 CPU 秒数。计数器下降表示重置；新的 daemon generation 会创建独立序列。cgroup 可在同一 generation 内重置。缺失或过期观测保持为空档，不要填零，也不要从抓取成功推断所有字段采集完整。

## 启用命令

需要以配置的执行账号权限运行原生 shell 时，使用：

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

也可在守护进程 JSON 中设置 `"full_control": true`。`A13N_ENVD_FULL_CONTROL` 覆盖 JSON 值；`false` 或 `0` 将其禁用。Full Control 自动创建默认原生 shell 配置：Unix 上为 `/bin/sh`，Windows 上为 PowerShell。命令继承启动进程的环境和原始 `PATH` 顺序，排除守护进程启动变量（`A13N_ENVD_*` 和 `EIP_*`）及 daemon 的 OTLP 指标设置。命令级环境修改不会影响守护进程或后续命令。shell 别名和未导出的变量不会被继承。

Envd Full Control（`full_control`）不是沙箱。它独立于 Harness UI 本地的 Full Control 或 Sandbox 模式。不要与手动可执行文件根目录或 shell 配置组合使用。需要显式配置命令访问时，提供可信可执行文件搜索根目录，并按需提供固定 shell 配置：

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

Full Control 禁用，且可执行文件根目录和 shell 配置都为空时，不声明任何命令方法。可执行文件根目录和配置是可信启动配置，不是会话沙箱。Host 提供外层安全边界；可选的[受控会话出站网络](egress.md)提供逐会话目标限制和凭据注入。参见[执行边界与故障排查](isolation.md)。

子进程环境由守护进程支持继承的值和显式命令输入构成。守护进程控制变量不是命令配置。请检查声明的执行功能，不要假定平台支持信号、限制或可执行位。

## 原生身份与 sudo

> [!IMPORTANT]
> **默认允许原生 sudo。** 禁用 Sandbox 时，Envd 保持外层沙箱的原始系统树可写。通过授权 sudo 安装软件包或修改系统文件会改动该树，并跨会话保留；不会为工作负载根文件系统引入临时副本。

Linux 上，**省略执行 UID/GID 会保留启动进程身份，包括 root**。Envd 不会假定 `1000:1000`、搜索可能的用户、创建账号，也不会仅因启动者为 root 就强制要求身份配置。要选择其他预置账号，成对设置 `execution.uid`/`execution.gid`、`A13N_ENVD_EXECUTION_UID`/`A13N_ENVD_EXECUTION_GID`，或 `--execution-uid`/`--execution-gid`。Root（`0:0`）也是有效的显式身份。provider 或部署负责账号预置、home 和工作目录权限。例如，root 启动者可以选择其预置的 sandbox 账号：

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

命令、会话文件 RPC（包括读取、写入和传输）、工作目录访问和设备目录发现都使用该执行身份及其原生权限。文件 RPC 绝不会隐式 sudo；访问不足会返回权限错误。显式 sudo 仅影响调用它的命令及其后代，不影响后续文件 RPC。守护进程凭据加载和 broker 管理独立于执行身份。

切换账号会初始化原生附加组和账号环境默认值。保留启动者身份时，root 与非 root 用户都保留原生 capability 继承；Envd 不会无条件清空命令 capabilities。只有从 root 切换用户时，Envd 为 supervisor 保留的信号 capability 会在执行命令前移除。外层平台及显式启用的受控出站网络边界仍然适用。非特权启动者不能选择其他身份。

`allow_sudo` 允许原生提权，并不授予授权。通过镜像或启动者安装 sudo 并配置 sudoers。Envd 不添加免密码 sudo 规则，不绕过身份验证，也不覆盖外层 `no_new_privs` 设置或 `nosuid` 挂载。

可用以下任一方式禁用提权：

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

或使用 JSON 文件：

```json
{
  "execution": {"allow_sudo": false}
}
```

遵循普通优先级：例如 `--allow-sudo true` 会覆盖值为 `false` 的环境变量。禁用时，在 Linux worker 及其后代上设置 `no_new_privs`，因此直接 sudo、setuid 二进制文件和文件 capabilities 都不能取得额外权限。这不会降低已经是 root 的执行身份，也不会撤销其已有权限。命令自身环境不能重新开启提权。不支持此实现的平台会拒绝 `false`，而非忽略它。UID/GID 配置仅限 Linux；其他平台保留原生启动身份。

## 传输通道配置

| 配置                | 启动设置                                         | 用途                                   |
| ------------------- | ------------------------------------------------ | -------------------------------------- |
| `stdio`             | 父进程提供的进程管道                             | Host 管理的本地守护进程                |
| `http`              | 绑定地址、受保护凭据文件、TLS 或显式可信明文范围 | Host 主动连接设备                      |
| `reverse_websocket` | 出站 URL 和受保护凭据文件                        | 设备主动连接经过身份验证的 Host 监听器 |

通过 `A13N_ENVD_TRANSPORT` 选择配置。

服务本地 HTTP 客户端前，先创建私有运行时父目录和受保护凭据文件。使用已有原生工作目录和数字绑定地址：

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

替换凭据文件和工作目录路径。Python 客户端使用相同凭据和预期设备 ID，连接 `http://127.0.0.1:8787`。服务器提供经身份验证的 `/eip/control` 和 `/eip/transfer` 路由，客户端接受基础 origin。

使用 HTTPS 时，先运行 `unset A13N_ENVD_HTTP_PLAINTEXT_SCOPE`，设置 `A13N_ENVD_HTTP_TLS_CERT_FILE` 和 `A13N_ENVD_HTTP_TLS_KEY_FILE`，再启动 Envd。配置客户端以信任该证书。provider 私有明文部署使用 `provider_private_link` 并要求客户端显式启用，不允许公共 HTTP 端点。[Python 传输选项](python-client.md#http-options)介绍代理路由和 TLS 验证。

反向 WebSocket 需要 `A13N_ENVD_REVERSE_WS_URL` 和 `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`。`A13N_ENVD_REVERSE_WS_CA_FILE` 为 `wss` 添加部署信任。守护进程不提供入站 WebSocket 监听器。

凭据应存入受保护文件，不能放入 argv、端点 URL、描述符、日志或可移植环境状态。凭据授权设备访问，不会将会话彼此隔离。

## 生命周期与归属

一个守护进程代次服务于一个设备和多个独立会话。初始化传输通道不会打开会话。`session.open` 记录工作目录并启动一个资源范围；`session.close` 只关闭该范围。会话内保活只续期自己的所有者。设备发现不会让已放弃会话持续存活。

消息帧传输通道丢失后，会话分离并进入有限的断线宽限期。已有所有者可以在同一代次显式重新附加到同一会话。附加绝不会重放命令或恢复传输。过期会话需要新资源范围，旧资源引用仍然无效。

Host 关闭时会关闭自己的设备连接，且只有拥有守护进程生命周期时才会终止该进程。会话关闭不会删除工作目录中的文件。原生进程和私有输出/暂存存储在有界清理中回收；失败会被报告，不会被视为已成功回收。

## 接纳工作前检查

```bash
a13n-envd --version
```

匹配守护进程和 Python 客户端的精确版本。`A13N_ENVD_EXECUTABLE` 用于 Python Host 选择可执行文件，应从守护进程子环境中移除。未知的 `A13N_ENVD_*` 变量和 JSON 字段会被拒绝。

设备初始化验证身份和协议。会话就绪检查验证所选范围。不存在 `isolation probe` 或逐命令隔离模式：通过部署自身的启动者和检查来测试 Host 外层边界。

## 独立字段参考

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

主要默认限制包括 128 个会话、设备 256 个并发操作、每个会话 128 个并发操作、设备 4 GiB spool 容量、会话 1 GiB spool 容量、每条流 256 MiB 输出和 2 MiB 预览。配置的会话容量不能超过设备总额。输出预览不能超过流容量；spool 必须为两条流预留空间。

### Shell 配置字段

必填字段为 `profile_id`、`display_name`、`native_executable`、`executable_search_roots` 和正数 `max_script_bytes`。可执行文件和搜索根目录必须是已有的原生绝对路径。`fixed_arguments` 默认为 `[]`，`safe_base_environment` 为 `{}`，`allow_login_mode` 为 `false`。

## 受控会话出站网络

在守护进程 JSON 中设置 `"egress": {"mode": "controlled"}`，或使用 `A13N_ENVD_EGRESS_MODE=controlled`、`--egress-mode controlled`，即可要求 Linux 受控会话。每个会话必须提供带显式类型的目标策略；inherit 和 deny 会拒绝会话出站网络策略。秘密值通过 EIP 提交，绝不能存入该文件。前置条件、创建、实时更新和容量行为见[会话出站网络](egress.md)。
