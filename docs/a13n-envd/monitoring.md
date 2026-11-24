---
title: Device monitoring and metrics
sidebarTitle: Monitoring
description: Collect host and container CPU, memory, disk, and network telemetry from Envd devices and export metrics over Prometheus or OTLP.
---

Monitor host, container, filesystem, and daemon health across your Envd deployments. For daemon startup and listener settings, see [Daemon configuration](configuration.md).

## System information and resource monitoring

Read static OS, kernel, architecture, and CPU identity via EIP `initialize` or `device.describe`. Resource sampling runs automatically every 10 seconds in `connect` mode (or when OTLP export is configured); for other launch modes, enable `"monitoring"` in your daemon JSON (`"monitoring": null` disables sampling):

```json
{
  "monitoring": {
    "sample_interval_ms": 10000,
    "filesystems": {"workspace": "/workspace"}
  }
}
```

`sample_interval_ms` accepts `1000`–`60000` ms, `filesystems` accepts up to 16 named native absolute paths (`monitoring: {}` uses defaults plus automatic local-filesystem discovery), and Linux containers can add `"cgroup_v2_directory": "/sys/fs/cgroup/your-group"` for container-scoped CPU, memory, I/O, and PSI metrics.

Query the cached sample over an initialized Python `device` connection without opening a Session:

```python
info = (await device.describe()).system_info
report = await device.metrics()
if report.sample is not None:
    sample = report.sample
    print(sample.system.cpu_busy_percent, sample.system.memory_available_bytes)
    print(sample.sample_age_ms, sample.sequence)
```

| Sample group                               | Key observations                                                                                                                                   |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system`                            | `cpu_busy_percent` (0–100), `logical_cpu_count`, `memory_total_bytes`, `memory_available_bytes`, `cpu_time_seconds`, swap/commit, Linux `pressure` |
| `sample.cgroup_v2`                         | Optional Linux cgroup v2 CPU usage/throttling, memory current/events/limits, task counts, device I/O, and PSI                                      |
| `sample.filesystems` & `local_filesystems` | Total/free/available bytes and optional inodes for named targets plus up to 16 discovered local mounts                                             |
| `sample.network`, `disks`, `connections`   | Up to 64 NICs and 64 block devices with cumulative counters, plus aggregate TCP state and UDP socket counts                                        |
| `sample.daemon`                            | Active Envd Sessions, command groups, file transfers, carrier connections, and retained-output bytes                                               |

Deduplicate time series by `(device_id, generation, sample.sequence)` and reset rate baselines when `generation` changes or cumulative counters decrease.

## Export via Prometheus or OpenTelemetry

Choose **one** ingestion path per Device to avoid duplicate series:

### Native OTLP push (all transports, including outbound `connect`)

```bash
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_METRIC_EXPORT_INTERVAL=10000
a13n-envd connect https://host.example.com
```

| Environment variable                                                           | Behavior                                                                                    |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `_METRICS_ENDPOINT`                            | Base URL (appends `/v1/metrics`) or exact metrics URL                                       |
| `OTEL_EXPORTER_OTLP[_METRICS]_PROTOCOL`                                        | `http/protobuf` (default) or `grpc` (typically port `4317`)                                 |
| `OTEL_EXPORTER_OTLP[_METRICS]_HEADERS` / `_TIMEOUT`                            | Percent-encoded `key=value` headers and export timeout in ms                                |
| `OTEL_EXPORTER_OTLP[_METRICS]_CERTIFICATE`, `CLIENT_*`                         | Custom PEM CA root and optional mTLS client cert/key pair                                   |
| `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | Push interval in ms (default `60000`) and resource tags (`service.instance.id` = Device ID) |

### Prometheus HTTP scrape (`GET /metrics` on HTTP listeners)

Mount the listener Bearer token in a restricted secret file on your Prometheus or OpenTelemetry Collector host (it authorizes EIP as well as `/metrics`):

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

### Core metric series and freshness

| Series                                                                           | Meaning                                                                                |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `envd_sample_available`, `envd_sample_fresh`, `envd_sample_age_seconds`          | Sampler cache health (`envd_sample_fresh == 0` when age exceeds `2 * sample_interval`) |
| `envd_system_cpu_busy_ratio`, `envd_system_cpu_time_seconds_total`               | Host CPU busy ratio (`0`–`1`) and cumulative mode seconds                              |
| `envd_system_memory_total_bytes`, `envd_system_memory_available_bytes`           | Host physical capacity and available memory                                            |
| `envd_cgroup_cpu_usage_seconds_total`, `envd_cgroup_cpu_throttled_seconds_total` | Container cgroup v2 CPU usage and throttling seconds                                   |
| `envd_cgroup_memory_current_bytes`, `envd_cgroup_memory_events_total`            | Container cgroup v2 memory usage and OOM/limit events                                  |
| `envd_system_pressure_ratio`, `envd_cgroup_pressure_ratio`                       | Linux PSI ratios across `10`/`60`/`300`s windows                                       |
| `envd_filesystem_available`, `envd_filesystem_available_bytes`                   | Named filesystem readability and account-available bytes                               |

Alert on both `up{job="envd"} == 0` and `envd_sample_fresh{job="envd"} == 0`. Disabled monitoring returns `404` on `/metrics`; invalid credentials return `401`.
