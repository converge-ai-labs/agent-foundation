---
title: Device monitoring and metrics
sidebarTitle: Monitoring
description: Sample Device resources, inspect freshness, and export metrics through Prometheus or OTLP.
---

Use this guide to monitor an existing Envd deployment. For daemon startup, identity, and transport settings, start with [daemon configuration](configuration.md).

## System information and resource monitoring

Read system identity through EIP `initialize` or `device.describe`: OS/version, kernel version, native architecture and CPU model when available on Linux, macOS and Windows. The daemon release is `initialize.server.version`.

Choose sampling for the launch mode:

- `connect` samples system, network, disk, socket, local-filesystem and daemon usage every 10 seconds by default, including saved-Host reconnections.
- Other launches enable sampling with `monitoring`; selecting native OTLP export also enables default sampling when this field is omitted.
- Set `"monitoring": null` in daemon JSON to disable sampling. System identity remains available.

Restart Envd to apply configuration or binary changes. Updating Harness UI alone leaves the running remote daemon unchanged. For a named filesystem and a 10-second interval, use:

```json
{
  "monitoring": {
    "sample_interval_ms": 10000,
    "filesystems": {"workspace": "/workspace"}
  }
}
```

`monitoring: {}` selects defaults, including automatic local-filesystem discovery. `connect` preserves explicit settings and ordinary precedence. Choose an interval from 1000 to 60000 ms and up to 16 named filesystem targets. Names use 1–64 ASCII letters, digits, `_` or `-`; paths must be native absolute paths without `..`, such as `C:\\workspace` in Windows JSON.

On an initialized Python Device connection, read the latest sample without opening a Session:

```python
info = (await device.describe()).system_info
report = await device.metrics()
if report.sample is not None:
    sample = report.sample
    print(sample.system.cpu_busy_percent, sample.system.memory_available_bytes)
    print(sample.sample_age_ms, sample.sequence)
```

`device.metrics` uses the existing authenticated carrier and is advertised when sampling is enabled. Envd and `a13n-envd-client` use EIP 0.4; upgrade both when migrating an older protocol deployment. Carrier framing and the `eip.v1` namespace stay unchanged.

Interpret the resource fields as follows:

| Observation                                                  | Meaning                                                                                                                                                                                                                                             |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system.cpu_busy_percent`                             | Overall CPU busy percentage averaged between native samples, normalized to 0–100. Absent for the first sample or invalid counter deltas.                                                                                                            |
| `sample.system.logical_cpu_count`                            | Observed system logical CPU count, not an effective cgroup quota or affinity allowance.                                                                                                                                                             |
| `sample.system.memory_total_bytes`, `memory_available_bytes` | Physical memory capacity and available-memory observation, in bytes. Linux uses MemAvailable; Windows uses native available physical memory; macOS estimates reclaimable memory as free + inactive pages without double-counting speculative pages. |
| `sample.system.cpu_time_seconds`                             | Optional cumulative native CPU modes: Linux exposes eight modes, Windows user/system/idle; macOS omits cumulative seconds.                                                                                                                          |
| `sample.filesystems.<name>.metrics`                          | Whole-filesystem total/free/available bytes and optional inode counts, not directory sizes. An unreadable target retains its name with absent metrics. Windows uses actual volume total rather than caller quota and omits inodes.                  |
| `sample.system.pressure`                                     | Optional Linux CPU/memory/I/O PSI.                                                                                                                                                                                                                  |
| `sample.cgroup_v2`                                           | Optional explicitly selected Linux cgroup v2 observations.                                                                                                                                                                                          |

The overview adds platform and device inventories:

| Observation                     | Meaning                                                                                                                                                                             |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system` platform fields | Linux/macOS swap usage and capacity; macOS wired/compressed memory; Windows commit, commit limit, cache, and global process/thread/handle counts. Windows commit is not swap usage. |
| `sample.network.interfaces`     | Up to 64 interfaces with native identity and available cumulative byte, packet, error and drop counters.                                                                            |
| `sample.disks.devices`          | Up to 64 disks with cumulative byte/operation counters and optional native timing/queue values. Linux excludes partitions and loop/RAM devices.                                     |
| `sample.connections`            | Aggregate TCP state and UDP socket counts, without addresses or per-connection records. macOS supplies totals without TCP states.                                                   |
| `sample.local_filesystems`      | Up to 16 automatically discovered local filesystems, separately from named targets. Reports opaque native identities rather than paths.                                             |
| `sample.daemon`                 | Envd Sessions, active command groups, transfers, public carrier connections and actual retained-output bytes. Worker-dependent totals are absent if collection is incomplete.       |

Check inventory truncation and source availability before aggregating. Interface and disk identities mark counter lifetimes; summing stacked devices can double-count work. Filesystem discovery reads mounts rather than directory contents, filters pseudo/remote filesystems and can include mounted removable local volumes. Native API permissions affect available fields.

For Linux container usage, add `"cgroup_v2_directory": "/sys/fs/cgroup/your-group"` to `monitoring`. This selects the group and descendants; other platforms reject the option. Fields cover CPU time and throttling, memory use/events, local CPU/memory limits, task counts/limits, device I/O and PSI. Tagged `unlimited`/`limited` limits describe the selected group locally; ancestor and affinity restrictions can further reduce capacity.

PSI reports optional `some`/`full` records with `avg10_percent`, `avg60_percent`, `avg300_percent` and cumulative `total_usec`. Those are kernel windows independent of your sampling interval. System CPU has no defined `full` record. Missing fields remain absent on the wire and `None` in Python.

System metrics can describe the host inside a container; use explicit cgroup observations for group usage. Missing group data stays missing. APFS reports a shared-volume view. Windows filesystem observations require the volume-information API from Windows 10 version 1809 / Server 2019; other capabilities remain available on older systems. Calculate inode utilization only when the total is positive.

One sampler runs per daemon, even with zero Sessions. Reads return its cache. Before the first completed collection, `report.sample` is `None`; slow sources leave the prior sample available with increasing age. Managed Linux resolves runtime targets to their actual binding and other targets to the original system tree, rejecting masked bootstrap paths.

To build a chart or exporter:

1. Deduplicate by `(device_id, generation, sample.sequence)`.
2. Compare `sample.sample_age_ms` with your freshness threshold and keep missing observations as gaps.
3. Calculate rates from distinct samples. Reset the baseline on generation changes, counter decreases or missing data; a cgroup can reset within one generation.
4. Map cumulative time to seconds, events to counters and bytes/inodes to gauges. Convert percentages to ratios when required.
5. Label peaks as sampled maxima: CPU values are interval averages and point observations can miss spikes.

Store history and manage alerts in the consumer. Envd retains one sample for EIP, HTTP scraping and optional OTLP export. Sampling lasts until daemon shutdown, independently of Session cleanup.

## Prometheus and OpenTelemetry

Choose HTTP scraping when the collector can reach Envd, or native OTLP push for outbound-only deployments. With [HTTP transport](configuration.md#carrier-profiles) and `"monitoring": {}`, `GET /metrics` serves Prometheus text format 0.0.4 from the same cache as EIP. It uses the existing listener and authentication without a Session or EIP initialization. Stdio and reverse WebSocket, including `connect`, support EIP observations and OTLP push rather than an HTTP scrape endpoint.

Keep the listener credential in a collector-side secret file and limit its readers: **it authorizes EIP operations as well as metrics**. Use verified HTTPS on public links. Explicit loopback/provider-private plaintext settings remain available for their trusted scopes.

### Native OTLP push

Configure an OTLP receiver and restart Envd. This works with any EIP carrier:

```bash
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_METRIC_EXPORT_INTERVAL=10000
a13n-envd connect https://host.example.com
```

A generic or metrics-specific OTLP endpoint also selects export without `OTEL_METRICS_EXPORTER`. Omitted `monitoring` then enables default sampling; explicit `"monitoring": null` disables sampling and export. To stop only export while keeping explicitly configured sampling, set `OTEL_METRICS_EXPORTER=none` or `OTEL_SDK_DISABLED=true`.

| Setting                                                         | Behavior                                                                                                                                                   |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT`                                   | HTTP base URL; the exporter appends `/v1/metrics`.                                                                                                         |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`                           | Complete metrics URL, used as supplied.                                                                                                                    |
| `OTEL_EXPORTER_OTLP[_METRICS]_PROTOCOL`                         | `http/protobuf` (default) or `grpc`. For gRPC, use the receiver's gRPC port, commonly 4317.                                                                |
| `OTEL_EXPORTER_OTLP[_METRICS]_HEADERS`                          | Comma-separated `key=value` headers; percent-encode values, for example `authorization=Bearer%20TOKEN`. Inject the real value through your secret manager. |
| `OTEL_EXPORTER_OTLP[_METRICS]_TIMEOUT`                          | Export request timeout in milliseconds.                                                                                                                    |
| `OTEL_EXPORTER_OTLP[_METRICS]_CERTIFICATE`                      | PEM CA file added to system trust roots.                                                                                                                   |
| `OTEL_EXPORTER_OTLP[_METRICS]_CLIENT_CERTIFICATE`, `CLIENT_KEY` | PEM client certificate and private key for mTLS; supply both.                                                                                              |
| `OTEL_METRIC_EXPORT_INTERVAL`                                   | Export interval in milliseconds; defaults to 60000. Sampling keeps its own configured interval.                                                            |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`                 | Service name and resource attributes, such as `workspace.id=example`.                                                                                      |

`[_METRICS]` marks generic and metrics-specific forms; the metrics-specific value wins. Use verified TLS outside a trusted local network. Export credentials are separate from the HTTP listener credential. Managed Linux copies TLS files into the private broker image and masks their original paths from Sessions. Commands exclude daemon exporter settings from inherited environments and can supply their own OTEL settings explicitly.

The resource's `service.instance.id` is the actual Device ID. Points carry `device_id` and `generation` and use the metric names below. Counters export cumulative native values, including resets; missing or stale resource values are omitted. Double precision can round very large integer counts. Export failures leave sampling and EIP available, and shutdown allows a bounded flush. Choose one ingestion path per Device to avoid duplicate push/scrape data.

### Prometheus scrape configuration

1. Mount the listener credential at `/run/secrets/envd-token` on the Prometheus machine.
2. Replace the target and CA path below with the reachable Envd endpoint and its trust certificate.
3. Keep the scrape interval at least as long as the daemon sampling interval:

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

The secret file contains only the token. Omit `ca_file` when system roots already trust the server. For explicitly configured same-machine loopback HTTP, use a loopback target and `scheme: http`, and omit `tls_config`. Faster scraping only rereads the cached sample.

### OpenTelemetry Collector

For a Collector with the [Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver), place the scrape configuration in a metrics pipeline:

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

Set the exporter endpoint and backend authentication. Assign each target to one active scraper, or configure backend HA deduplication for replicas. The Collector needs a route to Envd's HTTP listener; reverse WebSocket connectivity alone supplies none. For native push, configure an OTLP receiver instead.

### Metrics and freshness

Use `device_id` and `generation` to distinguish series. System series describe what the daemon sees, possibly the container host; `envd_cgroup_*` describes the selected group. Filesystem series use configured names rather than paths and measure the containing filesystem.

| Series                                                                                      | Interpretation                                                                                                                 |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `envd_sample_available`, `envd_sample_fresh`                                                | Cache and collection health, independently of Prometheus `up`.                                                                 |
| `envd_sample_age_seconds`, `envd_sample_interval_seconds`                                   | Monotonic age and configured interval.                                                                                         |
| `envd_sample_timestamp_seconds`, `envd_sample_sequence`, `envd_collection_duration_seconds` | Collection start time, generation-local sequence and duration.                                                                 |
| `envd_system_cpu_busy_ratio`                                                                | CPU busy fraction, 0–1, already averaged between native samples.                                                               |
| `envd_system_cpu_time_seconds_total{mode="user"}`                                           | Cumulative CPU seconds; use `rate` over several sampling intervals.                                                            |
| `envd_system_memory_total_bytes`, `envd_system_memory_available_bytes`                      | System memory gauges.                                                                                                          |
| `envd_cgroup_cpu_usage_seconds_total`, `envd_cgroup_cpu_throttled_seconds_total`            | Selected-group cumulative CPU and throttling seconds.                                                                          |
| `envd_cgroup_memory_current_bytes`, `envd_cgroup_memory_events_total{event="oom"}`          | Selected-group memory gauge and cumulative events.                                                                             |
| `envd_cgroup_cpu_limit_unlimited`, `envd_cgroup_memory_limit_unlimited`                     | 1 for unlimited local limits, 0 for limited; absent when unreadable. Numeric quota/limit series exist only for limited values. |
| `envd_system_pressure_ratio`, `envd_cgroup_pressure_ratio`                                  | PSI fractions with `resource`, `level` and `window` labels; windows are 10/60/300 seconds.                                     |
| `envd_system_pressure_stall_seconds_total`, `envd_cgroup_pressure_stall_seconds_total`      | Cumulative stall seconds with `resource` and `level` labels.                                                                   |
| `envd_filesystem_available`, `envd_filesystem_available_bytes`                              | Target readability and account-available filesystem bytes. Total/free bytes and native inode gauges are also exposed.          |

Interpret scrape health separately from resource freshness:

- Before the first collection, HTTP 200 carries `sample_available=0` and `sample_fresh=0`, with no resource values.
- When sample age exceeds twice the sampling interval, HTTP 200 carries `sample_fresh=0` and retained sample metadata. Resource series are omitted until a fresh sample arrives.
- Disabled monitoring returns 404 after authentication. Missing or invalid credentials return 401.

Series use scrape timestamps and carry observation time separately. Alert on both `up{job="envd"} == 0` and `envd_sample_fresh{job="envd"} == 0`. Display CPU percent with `100 * envd_system_cpu_busy_ratio`, and consumed CPU seconds per second with `rate(envd_cgroup_cpu_usage_seconds_total[5m])`. Counter decreases mark resets; a new generation creates new series, while cgroup recreation can reset counters within a generation. Preserve missing and stale data as gaps.
