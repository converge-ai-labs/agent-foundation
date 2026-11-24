---
title: 设备监控与指标采集
sidebarTitle: 监控与指标
description: 采集 Envd 设备的宿主机与容器 CPU、内存、磁盘及网络遥测指标，并通过 Prometheus 或 OTLP 导出。
---

监控 Envd 部署中的宿主机、容器、文件系统与守护进程运行状态。守护进程启动与传输监听配置请参阅[守护进程配置](configuration.md)。

## 系统信息与资源采样监控

通过 EIP `initialize` 或 `device.describe` 可直接读取操作系统、内核、硬件架构与 CPU 型号信息。`connect` 模式（或配置了 OTLP 导出时）默认每 10 秒自动采样一次；其他启动模式可在守护进程 JSON 中显式开启 `"monitoring"`（设为 `"monitoring": null` 则禁用采样）：

```json
{
  "monitoring": {
    "sample_interval_ms": 10000,
    "filesystems": {"workspace": "/workspace"}
  }
}
```

`sample_interval_ms` 取值范围为 `1000`–`60000` 毫秒，`filesystems` 最多支持 16 个具名原生绝对路径（`monitoring: {}` 使用默认间隔并自动发现本地挂载点）；Linux 容器环境还可配置 `"cgroup_v2_directory": "/sys/fs/cgroup/your-group"` 采集容器级 CPU、内存、I/O 与 PSI 指标。

在已初始化的 Python `device` 连接上，无需创建会话即可读取最新缓存样本：

```python
info = (await device.describe()).system_info
report = await device.metrics()
if report.sample is not None:
    sample = report.sample
    print(sample.system.cpu_busy_percent, sample.system.memory_available_bytes)
    print(sample.sample_age_ms, sample.sequence)
```

| 采样分组                                    | 核心观测字段                                                                                                                                        |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system`                             | `cpu_busy_percent`（0–100）、`logical_cpu_count`、`memory_total_bytes`、`memory_available_bytes`、`cpu_time_seconds`、Swap/Commit、Linux `pressure` |
| `sample.cgroup_v2`                          | 可选 Linux cgroup v2 CPU 使用与限流时长、当前内存与事件/限额、任务数、设备 I/O 与 PSI                                                               |
| `sample.filesystems` 与 `local_filesystems` | 具名目标及最多 16 个自动发现本地文件系统的总容量/空闲/可用字节数与可选 inode 计数                                                                   |
| `sample.network`、`disks`、`connections`    | 最多 64 张网卡与 64 块磁盘的累计字节/包/操作计数，以及聚合 TCP 状态与 UDP 套接字数量                                                                |
| `sample.daemon`                             | 活跃 Envd 会话数、命令组数、文件传输数、连接数与已保留输出字节数                                                                                    |

聚合时序数据时请按 `(device_id, generation, sample.sequence)` 去重；当 `generation` 变化或累计计数器回退时重置速率基线。

## 通过 Prometheus 或 OpenTelemetry 导出

每台设备请选择**一种**采集路径，避免推拉同时开启造成指标重复：

### 原生 OTLP 推送（支持所有传输方式，含外呼 `connect`）

```bash
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_METRIC_EXPORT_INTERVAL=10000
a13n-envd connect https://host.example.com
```

| 环境变量                                                                       | 行为说明                                                                        |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `_METRICS_ENDPOINT`                            | OTLP 基础地址（自动追加 `/v1/metrics`）或完整指标上报地址                       |
| `OTEL_EXPORTER_OTLP[_METRICS]_PROTOCOL`                                        | `http/protobuf`（默认）或 `grpc`（通常为 `4317` 端口）                          |
| `OTEL_EXPORTER_OTLP[_METRICS]_HEADERS` / `_TIMEOUT`                            | URL 编码的 `key=value` 请求头与导出超时毫秒数                                   |
| `OTEL_EXPORTER_OTLP[_METRICS]_CERTIFICATE`、`CLIENT_*`                         | 自定义 PEM CA 根证书及可选 mTLS 客户端证书/私钥                                 |
| `OTEL_METRIC_EXPORT_INTERVAL`、`OTEL_SERVICE_NAME`、`OTEL_RESOURCE_ATTRIBUTES` | 推送间隔毫秒数（默认 `60000`）与资源标签（`service.instance.id` 固定为设备 ID） |

### Prometheus HTTP 拉取（适用于 HTTP 监听模式 `GET /metrics`）

将监听器 Bearer 令牌挂载到 Prometheus 或 OpenTelemetry Collector 所在机器的受限密钥文件中（该令牌同时具备 EIP 访问权限，请严格限制读取权限）：

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

### 核心指标序列与新鲜度判定

| 指标序列                                                                         | 含义说明                                                                         |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `envd_sample_available`、`envd_sample_fresh`、`envd_sample_age_seconds`          | 采样缓存健康度（样本年龄超过 `2 * sample_interval` 时 `envd_sample_fresh == 0`） |
| `envd_system_cpu_busy_ratio`、`envd_system_cpu_time_seconds_total`               | 宿主机 CPU 繁忙比例（`0`–`1`）与各模式累计秒数                                   |
| `envd_system_memory_total_bytes`、`envd_system_memory_available_bytes`           | 宿主机物理总内存与可用内存字节数                                                 |
| `envd_cgroup_cpu_usage_seconds_total`、`envd_cgroup_cpu_throttled_seconds_total` | 容器 cgroup v2 CPU 累计消耗与限流秒数                                            |
| `envd_cgroup_memory_current_bytes`、`envd_cgroup_memory_events_total`            | 容器 cgroup v2 当前内存占用与 OOM/超限累计事件数                                 |
| `envd_system_pressure_ratio`、`envd_cgroup_pressure_ratio`                       | Linux PSI 压力比例（含 `10`/`60`/`300` 秒窗口标签）                              |
| `envd_filesystem_available`、`envd_filesystem_available_bytes`                   | 具名文件系统可读状态与当前账号可用容量字节数                                     |

建议同时对 `up{job="envd"} == 0` 与 `envd_sample_fresh{job="envd"} == 0` 配置告警。禁用监控时 `/metrics` 返回 `404`；凭据缺失或无效返回 `401`。
