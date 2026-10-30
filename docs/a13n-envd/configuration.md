---
title: Daemon configuration and transports
sidebarTitle: Configuration and transports
description: Connect Envd to a Host, choose execution settings, and collect resource metrics.
---

Configure Envd at startup to choose its connection, execution account, filesystem isolation and network mode. The Host owns deployment, credentials and daemon lifetime. Each EIP Session then selects a working directory and operations within those settings.

Use the JSON and environment variables on this page for the daemon. Harness UI YAML and `EnvironmentProviderSpec` configure the Host-side adapter, including required methods and optional Session egress policies with secret references. For a prepared container with account, shell and sudo settings, use the [sandbox image](sandbox.md).

## Configuration layers

Envd applies five layers, with later layers taking precedence:

1. Built-in defaults.
2. The daemon JSON file selected by `--config`.
3. `A13N_ENVD_CONFIG_JSON`, validated against the same schema.
4. Scalar `A13N_ENVD_*` environment variables.
5. Explicit CLI arguments.

Objects merge recursively; arrays and scalar values replace earlier values. Each JSON layer must validate before merging. A launcher can supply nested settings entirely through the environment:

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

Scalar settings cover identity, execution, discovery, metadata and lifetimes: `A13N_ENVD_ALLOW_SUDO`, `A13N_ENVD_EXECUTION_UID`, `A13N_ENVD_EXECUTION_GID`, `A13N_ENVD_EGRESS_MODE`, `A13N_ENVD_FULL_CONTROL`, `A13N_ENVD_COMPUTER_USE`, `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`, `A13N_ENVD_DIRECTORY_DISCOVERY`, `A13N_ENVD_DEVICE_ID`, `A13N_ENVD_NAME`, `A13N_ENVD_DESCRIPTION`, `A13N_ENVD_DEFAULT_WORKING_DIRECTORY`, `A13N_ENVD_IDLE_TIMEOUT_MS` and `A13N_ENVD_DISCONNECT_GRACE_MS`. Boolean values accept `true`, `false`, `1` and `0`.

## Connect to Harness UI

Start an outbound connection from the computer that will run the work:

```console
a13n-envd connect https://host.example.com --host work
```

1. Open the printed approval address and sign in to the Host.
2. Approve the matching verification code.
3. Keep Envd running. On subsequent starts, use `a13n-envd connect work` to reuse the registration.

Envd generates the credential on the Device, so pairing needs only the verification code in the browser. Reconnecting with saved state reuses approval. For Service, [register an HTTP daemon](../environments/remote-envd.md#connect-to-the-service) instead.

Use a Host URL reachable from this computer. HTTPS is required except for loopback HTTP such as `http://127.0.0.1:8765`. Add `--ca-file /path/to/ca.pem` for a private CA; certificate verification stays enabled. Set `--name` for the approval display, `--default-working-directory` for an existing native directory, and `--config` for daemon JSON settings.

### Saved instances and Hosts

Preserve the state directory across restarts and protect it as an account credential. Its default is `~/.a13n-envd` on Unix and `%LOCALAPPDATA%/.a13n-envd` on Windows, falling back to `%USERPROFILE%/.a13n-envd`. Override it with `--state-dir` or `A13N_ENVD_STATE_DIR`.

An instance stores its Device ID at `instances/<instance>/device-id`. Each Host's configuration, credential and private runtime live under `instances/<instance>/hosts/<host>/`. The default instance is `default`; omitting `--host` derives a stable alias from the URL.

Run a separate process for each Host. For two Hosts on one computer:

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

Restart each process with its original instance and Host alias. Each has its own registration and Sessions. `connect` runs in the foreground; to start it automatically, configure your service manager with the same account, state directory and command environment.

If authentication is rejected, Envd stops and preserves its credential. Check the Host address, registration status and mounted state directory. Use a new instance and explicit approval for a genuinely new installation.

## Minimal standalone configuration

For a Host-owned stdio daemon:

1. Create and protect an absolute runtime parent directory.
2. Save the JSON below as `/absolute/path/to/a13n-envd.json`, replacing the workspace path with an existing directory.
3. Have the Host launch Envd with piped stdin and stdout:

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

Envd creates an unpredictable private child for this generation and holds an exclusive runtime lock. The pipes carry framed EIP traffic; diagnostics go to stderr.

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

The default working directory becomes each Session's initial cwd. If omitted, Envd captures its startup cwd. A nonexistent directory fails Session opening rather than daemon startup. File access follows the execution account, Sandbox grants and outer sandbox, not a cwd boundary. Directory discovery reads one bounded level before a Session exists.

Choose a stable Device ID with `--device-id`, JSON `device_id` or `A13N_ENVD_DEVICE_ID`, or let the installation state directory retain a generated ID. CLI metadata options override JSON. Directory discovery uses JSON or `A13N_ENVD_DIRECTORY_DISCOVERY`. On Windows, bootstrap paths use native spelling; EIP paths use `/C:/...` or `/UNC/server/share/...`.

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

`device.metrics` uses the existing authenticated carrier and is advertised when sampling is enabled. Envd and `a13n-envd-client` use EIP 0.3; upgrade both when migrating a 0.1 or 0.2 deployment. Carrier framing and the `eip.v1` namespace stay unchanged.

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

Choose HTTP scraping when the collector can reach Envd, or native OTLP push for outbound-only deployments. With [HTTP transport](#carrier-profiles) and `"monitoring": {}`, `GET /metrics` serves Prometheus text format 0.0.4 from the same cache as EIP. It uses the existing listener and authentication without a Session or EIP initialization. Stdio and reverse WebSocket, including `connect`, support EIP observations and OTLP push rather than an HTTP scrape endpoint.

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

## Enable commands

Enable Full Control for a native shell running with the execution account's authority:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

JSON `"full_control": true` selects the same mode; the scalar environment setting overrides it. Full Control supplies `/bin/sh` on Unix or PowerShell on Windows and inherits the launcher's environment and `PATH` order. It excludes daemon bootstrap variables (`A13N_ENVD_*`, `EIP_*`) and OTLP metric settings. Shell aliases and unexported variables stay with the launching shell. Per-command overrides affect that command only.

Envd `full_control` enables execution, not isolation, and is separate from Harness UI's local mode. Choose either Full Control or manual executable roots/shell profiles; they cannot be combined. For explicit command configuration, use:

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

With Full Control off and both roots/profiles empty, Envd advertises no command methods. Roots and profiles select trusted executables; the Host's outer boundary and [execution isolation](isolation.md) govern access. [Controlled Session egress](egress.md) adds destination restrictions and credential injection.

Check the Device's advertised execution features for signal, limit and executable-bit support. Child environments combine supported inherited values with explicit command inputs; daemon control variables remain startup configuration.

## Native identity and sudo

> [!IMPORTANT]
> Native sudo is allowed by default. With Sandbox disabled, authorized package installation and system-file changes modify the original outer-sandbox filesystem and persist across Sessions.

On Linux, omitting execution UID/GID keeps the launcher's identity, including root. To switch from a root launcher, first provision the account and its home/workspace permissions, then set both `execution.uid`/`execution.gid`, both environment variables, or both CLI options. Explicit `0:0` selects root. For a provisioned `sandbox` account:

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

Commands, file RPCs, working-directory access and directory discovery use the selected account's native permissions. A file RPC reports insufficient permission rather than invoking sudo. Sudo in a command affects that command and descendants, not later file RPCs. Credential loading and broker management remain daemon responsibilities.

Account switching initializes supplementary groups and account environment defaults. Keeping the launcher's identity retains native capability inheritance. A root-to-user switch removes Envd's retained supervisor signaling capability before command execution. Outer-platform and controlled-egress restrictions still apply; an unprivileged launcher cannot select another account.

Install sudo and configure sudoers in the image or deployment. `allow_sudo` permits native privilege gains subject to that authorization and outer `no_new_privs`/`nosuid` restrictions. Envd supplies no passwordless rule.

Disable additional privilege gains with one of these launch settings:

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

The equivalent JSON setting is:

```json
{
  "execution": {"allow_sudo": false}
}
```

On Linux this applies `no_new_privs` to workers and descendants, blocking gains through sudo, setuid binaries and file capabilities. Existing authority, including root, remains. Startup precedence still applies (`--allow-sudo true` overrides an environment `false`); a command cannot change this policy through its own environment. Platforms without this implementation reject `false`. UID/GID selection is Linux-only; other platforms keep the native launching identity.

## Carrier profiles

Choose the connection direction for your Host:

| Profile             | Bootstrap                                                                        | Use                                         |
| ------------------- | -------------------------------------------------------------------------------- | ------------------------------------------- |
| `stdio`             | Parent-supplied process pipes                                                    | Host-owned local daemon                     |
| `http`              | Bind address, protected credential file, TLS or explicit trusted plaintext scope | Host-dialed Device                          |
| `reverse_websocket` | Outbound URL and protected credential file                                       | Device dials an authenticated Host listener |

Set `A13N_ENVD_TRANSPORT` to the chosen profile. For a local HTTP client:

1. Prepare a protected token file and an existing workspace.
2. Replace their paths in the command below, which creates a private runtime parent and binds loopback HTTP.
3. Launch Envd:

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

Connect the Python client to the base origin `http://127.0.0.1:8787` using the same credential and expected Device ID. The client selects the authenticated `/eip/control` and `/eip/transfer` routes.

For HTTPS, unset `A13N_ENVD_HTTP_PLAINTEXT_SCOPE`, set `A13N_ENVD_HTTP_TLS_CERT_FILE` and `A13N_ENVD_HTTP_TLS_KEY_FILE`, and configure client trust before connecting. A private provider route uses `provider_private_link` plus explicit client opt-in. Public endpoints require HTTPS. See [Python transport options](python-client.md#http-options) for proxy routing and TLS.

For outbound reverse WebSocket, set `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`; add private trust with `A13N_ENVD_REVERSE_WS_CA_FILE`. The Host owns the WebSocket listener.

Store credentials in protected files. Keep them out of argv, URLs, descriptors, logs and portable Environment state. A credential grants Device access; Session isolation comes from execution boundaries.

## Lifecycle and ownership

Initialize a Device connection, then open a Session for each resource scope. One daemon generation serves multiple Sessions; each captures its cwd and owns its operations. `session.close` closes that scope. Keepalive renews only the calling owner, so discovery traffic does not preserve abandoned Sessions.

After a framed-carrier disconnect, the owner can explicitly reattach during the grace period within the same generation. Reattachment gives access to existing resources without replaying commands or resuming transfers. After expiry, open a fresh Session and discard old references.

On shutdown, the Host closes its connections and terminates only daemons whose lifecycle it owns. Session cleanup reclaims processes and private output/staging storage while preserving workspace files. Treat a reported cleanup failure as incomplete reclamation.

## Check before admitting work

Check the daemon release:

```bash
a13n-envd --version
```

1. Match the exact Python client release.
2. Remove Host-only `A13N_ENVD_EXECUTABLE` from the daemon child environment. Unknown `A13N_ENVD_*` variables and JSON fields fail configuration.
3. Initialize the Device to verify protocol and identity, then check Session readiness for the selected scope.
4. Test the outer execution boundary through the deployment's own launcher and checks. Envd has no `isolation probe` or per-command isolation mode.

## Standalone field reference

Use these fields in daemon JSON:

| Root field                                   | Default or meaning                                                                                                                         |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `device_id`                                  | Explicit stable identity, otherwise installation identity                                                                                  |
| `installation_state_directory`               | Persistent identity storage when no explicit Device ID is supplied                                                                         |
| `name`, `description`                        | Optional display metadata                                                                                                                  |
| `default_working_directory`                  | Startup cwd if unset                                                                                                                       |
| `directory_discovery`                        | `true`                                                                                                                                     |
| `idle_timeout_ms`                            | Session idle lifetime                                                                                                                      |
| `disconnect_grace_ms`                        | Detached Session attachment grace                                                                                                          |
| `execution.uid`, `execution.gid`             | Optional paired Linux native IDs; omitted retains the launcher, including root                                                             |
| `execution.allow_sudo`                       | `true`; native sudoers still controls authorization                                                                                        |
| `sandbox`                                    | `{"mode":"disabled"}`; or `restricted` with directory `grants`                                                                             |
| `egress`                                     | `{"mode":"inherit"}`; or `deny` / `controlled`                                                                                             |
| `full_control`                               | `false`; automatic native shell and inherited command environment when enabled                                                             |
| `computer_use`                               | `false`; opt-in macOS/X11/Windows screenshot/input methods with disabled Sandbox and inherited egress; see [computer use](computer-use.md) |
| `computer_use_permission_timeout_ms`         | `120000`; positive startup deadline for macOS permissions or X11/Windows desktop readiness before any EIP transport starts                 |
| `trusted_executable_roots`, `shell_profiles` | Empty; command methods disabled unless Full Control is enabled                                                                             |
| `limits`                                     | Device aggregates and per-Session limits                                                                                                   |

Default capacity is 128 Sessions, 256 concurrent Device operations, 128 concurrent operations per Session, 4 GiB Device spool, 1 GiB Session spool, 256 MiB per output stream and 2 MiB previews. Keep Session capacities within Device aggregates, previews within stream capacity, and enough spool capacity reserved for both streams.

### Shell-profile fields

Supply `profile_id`, `display_name`, `native_executable`, `executable_search_roots` and a positive `max_script_bytes`. Use existing absolute native paths for executables and roots. Optional defaults are `fixed_arguments: []`, `safe_base_environment: {}` and `allow_login_mode: false`.

## Controlled Session egress

For per-Session destination restrictions and HTTPS credential injection on Linux, follow [Session egress](egress.md). Configure controlled mode at daemon startup, then submit policies and secrets through EIP.
