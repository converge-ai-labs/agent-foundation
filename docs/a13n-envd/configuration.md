---
title: Daemon configuration and transports
sidebarTitle: Configuration and transports
description: Configure a standalone Envd daemon and how it connects to a Host.
---

This is standalone Envd configuration, not Harness UI YAML or an `EnvironmentProviderSpec`. The Host owns deployment, account selection, any outer sandbox, credentials and the daemon lifetime. An adapter configuration selects a working directory, required methods and an optional Session egress policy whose secrets are references, not values. The Device launch configuration separately fixes execution, Sandbox and network mode. The [sandbox image](sandbox.md) supplies ready-to-use account, shell and sudo defaults without changing the standalone daemon defaults.

## Configuration layers

Startup precedence, from lowest to highest:

1. Built-in defaults.
2. Daemon JSON selected by `--config`.
3. `A13N_ENVD_CONFIG_JSON`, using the same strict schema as the file.
4. Scalar `A13N_ENVD_*` environment variables.
5. Explicit command-line arguments.

Objects merge recursively; arrays and scalar values replace earlier values. Each JSON layer must be valid even if a later layer overrides it. This lets a sandbox launcher configure Envd entirely through environment variables, including nested limits and shell profiles, without writing a configuration file:

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

Scalar shortcuts include `A13N_ENVD_ALLOW_SUDO`, `A13N_ENVD_EXECUTION_UID`, `A13N_ENVD_EXECUTION_GID`, `A13N_ENVD_EGRESS_MODE`, `A13N_ENVD_FULL_CONTROL`, `A13N_ENVD_COMPUTER_USE`, `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`, `A13N_ENVD_DIRECTORY_DISCOVERY`, `A13N_ENVD_DEVICE_ID`, `A13N_ENVD_NAME`, `A13N_ENVD_DESCRIPTION`, `A13N_ENVD_DEFAULT_WORKING_DIRECTORY`, `A13N_ENVD_IDLE_TIMEOUT_MS`, and `A13N_ENVD_DISCONNECT_GRACE_MS`. Booleans accept `true`, `false`, `1`, or `0`.

Device initialization negotiates the protocol and verifies the Device identity. EIP Sessions select a fixed cwd and own operations, processes, output and transfers; they cannot change trusted startup identity or sudo policy.

## Connect to Harness UI

To connect to Harness UI, start one outbound connection:

```console
a13n-envd connect https://host.example.com --host work
```

1. Open the approval address that Envd prints.
2. Sign in to the Host.
3. Approve the matching verification code.

Keep Envd running. On later starts, `a13n-envd connect work` reuses its saved identity, credential and approved connection. The credential is generated on the Device and never needs to be copied into the browser. Reconnection does not require another approval. Service instead connects to an HTTP daemon you register; see [Connect to the Service](../environments/remote-envd.md#connect-to-the-service).

The Host URL must be reachable from this computer. HTTPS is required except for loopback HTTP, such as `http://127.0.0.1:8765`. Add `--ca-file /path/to/ca.pem` for a private CA; TLS verification is never disabled. `--name` sets the approval display name, and `--default-working-directory` selects an existing native directory. `--config` supplies ordinary daemon JSON, including resource limits and command settings.

### Saved instances and Hosts

State defaults to `~/.a13n-envd` on Unix and `%LOCALAPPDATA%/a13n-envd` on Windows (falling back to the user profile). Override it with `--state-dir` or `A13N_ENVD_STATE_DIR`. Each instance stores its identity at `instances/<instance>/device-id`; named Host configuration, credential and private runtime live under `instances/<instance>/hosts/<host>/`. The default instance name is `default`. Without `--host`, a stable name is derived from the URL. Keep this state across restarts and protect it as an account credential.

Each running Envd process connects to **one Host**. To use two Hosts on the same physical computer, run two independent processes:

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

Each process has its own registration and Sessions. Restart with the same `--instance` and Host alias. `connect` stays in the foreground; it does not install a system service or modify OS startup settings. If you use a service manager, preserve the launching account, state directory and intended command environment.

Authentication rejection stops the daemon without replacing its credential or requesting new trust. Check whether the registration was revoked or forgotten, whether this is the intended Host, and whether the correct state directory is mounted. Do not repeatedly delete credentials to hide an authorization failure. A genuinely new installation requires a new instance and explicit approval.

## Minimal standalone configuration

The default carrier is stdio. Supply an absolute private runtime directory:

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

The Host creates and protects the runtime parent. Envd creates an unpredictable generation-private child and takes an exclusive runtime lock. Stdin and stdout are reserved for framed EIP traffic; diagnostics use stderr.

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

A Session can open only in an existing directory; a missing default fails Session opening, not daemon startup. If `default_working_directory` is omitted, the daemon captures its startup cwd. The working directory is **not an access root**: file paths address the whole filesystem available to the execution account, the Sandbox grants and the outer sandbox. Directory discovery is a bounded, one-level read that works before any Session exists.

Explicit Device IDs can come from `--device-id`, JSON `device_id`, or `A13N_ENVD_DEVICE_ID`. Without an explicit ID, an installation state directory retains the generated identity across restart. `--default-working-directory`, `--name` and `--description` override JSON metadata. Set directory discovery through JSON or `A13N_ENVD_DIRECTORY_DISCOVERY`. EIP paths use `/C:/...` and `/UNC/server/share/...` on Windows; daemon bootstrap paths use native OS spelling.

## System information and resource monitoring

Linux, macOS and Windows expose basic system information in the Device descriptor returned by EIP `initialize` and `device.describe`: operating system/version, kernel version, native architecture and CPU model when available. Envd's own version is in `initialize.server.version`.

`a13n-envd connect` enables CPU and memory sampling every 10 seconds by default, including when reconnecting with a saved Host. Existing installations need an updated Envd binary and a daemon restart to use this default; updating Harness UI alone does not change a running remote daemon.

Set `"monitoring": null` in your `--config` file or `A13N_ENVD_CONFIG_JSON` to disable sampling. Other daemon launches remain opt-in. Enable or customize sampling with `monitoring` in your daemon configuration:

```json
{
  "monitoring": {
    "sample_interval_ms": 10000,
    "filesystems": {"workspace": "/workspace"}
  }
}
```

Use absolute native paths, such as `C:\\workspace` in Windows JSON. `monitoring: {}` enables CPU and memory sampling with defaults. Custom settings follow the usual configuration precedence and are preserved by `connect`. Disabling sampling preserves system information. The interval accepts 1000–60000 ms. At most 16 filesystem targets are allowed, with 1–64 character names containing ASCII letters, digits, `_` or `-`. Paths cannot contain `..`.

Read through your initialized Python Device connection, with no Session required:

```python
info = (await device.describe()).system_info
report = await device.metrics()
if report.sample is not None:
    sample = report.sample
    print(sample.system.cpu_busy_percent, sample.system.memory_available_bytes)
    print(sample.sample_age_ms, sample.sequence)
```

This uses EIP `device.metrics` over the same authenticated HTTP, reverse WebSocket or stdio connection. No additional port, token or TLS configuration is needed. The method is advertised only when sampling is enabled. Envd and `a13n-envd-client` use EIP 0.2; upgrade them together when moving from a 0.1 deployment. The `eip.v1` namespace and carrier framing are unchanged.

| Observation                                                  | Meaning                                                                                                                                                                                                                                             |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sample.system.cpu_busy_percent`                             | Overall CPU busy percentage averaged between native samples, normalized to 0–100. Absent for the first sample or invalid counter deltas.                                                                                                            |
| `sample.system.logical_cpu_count`                            | Observed system logical CPU count, not an effective cgroup quota or affinity allowance.                                                                                                                                                             |
| `sample.system.memory_total_bytes`, `memory_available_bytes` | Physical memory capacity and available-memory observation, in bytes. Linux uses MemAvailable; Windows uses native available physical memory; macOS estimates reclaimable memory as free + inactive pages without double-counting speculative pages. |
| `sample.system.cpu_time_seconds`                             | Optional cumulative native CPU modes: Linux exposes eight modes, Windows user/system/idle; macOS omits cumulative seconds.                                                                                                                          |
| `sample.filesystems.<name>.metrics`                          | Whole-filesystem total/free/available bytes and optional inode counts, not directory sizes. An unreadable target retains its name with absent metrics. Windows uses actual volume total rather than caller quota and omits inodes.                  |
| `sample.system.pressure`                                     | Optional Linux CPU/memory/I/O PSI.                                                                                                                                                                                                                  |
| `sample.cgroup_v2`                                           | Optional explicitly selected Linux cgroup v2 observations.                                                                                                                                                                                          |

On Linux, add `"cgroup_v2_directory": "/sys/fs/cgroup/your-group"` inside `monitoring` to observe that group and its descendants. Other platforms reject this Linux-only option. The group exposes CPU user/system/total microseconds, bandwidth periods/throttling, memory usage/events, local CPU/memory limits and PSI. Limits use tagged `unlimited` or `limited` values. These are local limits, not effective limits after ancestors or affinity.

PSI contains optional `some` and `full` records with `avg10_percent`, `avg60_percent`, `avg300_percent` and cumulative `total_usec`. The averages are kernel windows, independent of the sampling interval. System CPU `full` is absent because it is undefined at that scope. Missing fields remain absent on the wire and `None` in Python, never synthetic zero.

**System metrics may describe the host even inside a container.** Select the intended cgroup explicitly; missing group observations are never replaced by host totals. APFS space describes its shared-volume view. Windows filesystem observations require the volume-information API available since Windows 10 version 1809 / Server 2019; older systems still serve other capabilities. Calculate inode utilization only for a positive inode total.

One sampler runs per daemon, including with zero Sessions. Reads return its latest cached sample and never trigger collection. `report.sample` is `None` until the first collection completes. A slow source leaves the previous sample readable with increasing `sample_age_ms`. Managed Linux maps runtime targets to the actual runtime binding and other targets to the original system tree, rejecting masked bootstrap paths.

When consuming reports:

- Deduplicate by `(device_id, generation, sample.sequence)`.
- Check `sample.sample_age_ms` against your maximum age before presenting or exporting current values; retain missing data as missing.
- Compute counter rates from distinct samples; reset the baseline after generation changes, counter decreases or missing observations. Cgroups can reset independently of daemon generation.
- Convert cumulative times to seconds, event counts to counters, byte/inode observations to gauges, and percentages to ratios as needed by your exporter.
- Label chart peaks as sampled maxima. CPU percentages are interval averages; point observations can miss spikes.

Envd retains one sample and exposes it through EIP and standard HTTP scraping. History, alerts, downstream export and product charts belong to consumers and their observability infrastructure. Session cleanup does not stop sampling; daemon shutdown does.

## Prometheus and OpenTelemetry

When using the [HTTP transport](#carrier-profiles), enable `"monitoring": {}` and scrape `GET /metrics` on the same address. The response uses Prometheus text format 0.0.4. It reads the same cache as `device.metrics` without EIP initialization or a Session. No additional listener, token or TLS settings are needed. Stdio and reverse-WebSocket deployments, including `connect`, do not expose an HTTP scrape endpoint; their consumers can continue reading EIP observations.

Use the HTTP listener's Bearer credential and verified HTTPS. **This credential also grants the listener's EIP authority; it is not a read-only monitoring token.** Keep it in a collector-side secret file, restrict access to the collector and use a trusted network. Public links require TLS. Existing explicit loopback/provider-private plaintext settings remain available.

### Prometheus scrape configuration

Mount the Envd credential as `/run/secrets/envd-token` on the Prometheus machine. Configure the actual reachable Envd address, port and CA certificate:

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

Omit `ca_file` for certificates trusted by the scraper's system roots. The credential file contains only the token. For explicitly configured same-machine loopback HTTP, use `scheme: http`, a loopback target and omit `tls_config`. Keep the scrape interval at least as long as the daemon sampling interval; faster scraping only rereads the cache.

### OpenTelemetry Collector

A Collector distribution with the [Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver) can use the same scrape configuration and export through its usual pipeline:

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

Replace the exporter endpoint and configure its authentication for your backend. Envd itself does not push OTLP. Assign each target to one active scraper, or configure your backend's deliberate HA deduplication; identical Collector replicas otherwise duplicate scraping. The collector must be able to reach the existing Envd HTTP listener. An outbound reverse-WebSocket connection alone does not provide that route.

### Metrics and freshness

All series have `device_id` and `generation` labels. System metrics describe the daemon-visible system, which can be the host inside a container. `envd_cgroup_*` describes only the configured cgroup. Filesystem series use the configured `filesystem` name, never the native path, and measure the whole containing filesystem.

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

Missing values are omitted, not set to zero. Before the first completed collection, `/metrics` returns 200 with `sample_available=0` and `sample_fresh=0`, without resource values. When a cached sample is older than twice the sampling interval, it returns 200 with `sample_fresh=0` and retained sample metadata, omitting resource series until fresh observations resume. Disabled monitoring returns 404 after authentication; missing or invalid credentials return 401. Samples use scrape timestamps, with observation time carried separately.

For example, alert separately on `up{job="envd"} == 0` and `envd_sample_fresh{job="envd"} == 0`. Use `100 * envd_system_cpu_busy_ratio` to display percent and `rate(envd_cgroup_cpu_usage_seconds_total[5m])` for consumed CPU seconds per second. Counter decreases represent resets; a new daemon generation creates a distinct series. Cgroup recreation can reset within a generation. Missing or stale observations remain gaps; do not fill them with zero or infer complete collection from a successful scrape.

## Enable commands

For native shell execution with the configured execution account's authority, use:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

Or set `"full_control": true` in daemon JSON. `A13N_ENVD_FULL_CONTROL` overrides the JSON value; `false` or `0` disables it. Full Control creates the default native shell profile automatically: `/bin/sh` on Unix, or PowerShell on Windows. Commands inherit the launching process's environment and original `PATH` order, excluding daemon bootstrap variables (`A13N_ENVD_*` and `EIP_*`). Command-level environment changes do not modify the daemon or later commands. Shell aliases and unexported variables are not inherited.

Envd Full Control (`full_control`) is not a sandbox. It is independent of Harness UI's local Full Control or Sandbox mode. Do not combine it with manual executable roots or shell profiles. To configure command access explicitly instead, supply trusted executable search roots and optionally fixed shell profiles:

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

No command methods are advertised when Full Control is disabled and both executable roots and shell profiles are empty. Executable roots and profiles are trusted launch configuration, not a Session sandbox. The Host supplies the outer security boundary; optional [controlled Session egress](egress.md) adds per-Session destination restrictions and credential injection. See [execution boundaries and troubleshooting](isolation.md).

Child environments are built from the daemon's supported inherited values plus explicit command inputs. Daemon control variables are not command configuration. Inspect advertised execution features instead of assuming platform support for signals, limits or executable bits.

## Native identity and sudo

> [!IMPORTANT]
> **Native sudo is allowed by default.** With disabled Sandbox, Envd keeps the outer sandbox's original system tree writable. Installing packages or changing system files through authorized sudo changes that tree and persists across Sessions; no disposable copy of the payload root filesystem is introduced.

On Linux, **omitting execution UID/GID preserves the launching process's identity, including root**. Envd does not assume `1000:1000`, scan for a likely user, create an account, or require identity configuration merely because the launcher is root. To select a different provisioned account, set the paired `execution.uid`/`execution.gid`, `A13N_ENVD_EXECUTION_UID`/`A13N_ENVD_EXECUTION_GID`, or `--execution-uid`/`--execution-gid`. Root (`0:0`) is also a valid explicit identity. The Provider or deployment owns account provisioning, home and working-directory permissions. For example, a root launcher can select its provisioned sandbox account:

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

Commands, Session file RPCs (including reads, writes and transfers), working-directory access and Device directory discovery all use this execution identity and its native permissions. File RPCs never implicitly sudo; insufficient access returns a permission error. Explicit sudo affects only the command that invokes it and its descendants, not later file RPCs. Daemon credential loading and broker management remain separate from execution identity.

Switching accounts initializes native supplementary groups and account environment defaults. Retaining the launcher's identity preserves native capability inheritance for both root and non-root users; Envd does not unconditionally clear command capabilities. Only the supervisor signaling capability retained by Envd during a root-to-user switch is removed before executing commands. The outer platform and any explicitly enabled controlled-egress boundary still apply. An unprivileged launcher cannot choose a different identity.

`allow_sudo` permits native privilege gains; it does not grant authorization. Install sudo and configure sudoers through your image or launcher. Envd does not add passwordless sudo rules, bypass authentication, or override an outer `no_new_privs` setting or `nosuid` mount.

Disable privilege gains in any of these ways:

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

Or use a JSON file:

```json
{
  "execution": {"allow_sudo": false}
}
```

The normal precedence applies: for example, `--allow-sudo true` overrides an environment value of `false`. Disabling uses Linux `no_new_privs` for the worker and descendants, so direct sudo, setuid binaries and file capabilities cannot gain additional privilege. This does not demote an already-root execution identity or revoke its existing authority. A command's own environment cannot turn it back on. Platforms without this implementation reject `false` instead of ignoring it. UID/GID configuration is Linux-only; other platforms retain their native launching identity.

## Carrier profiles

| Profile             | Bootstrap                                                                        | Use                                         |
| ------------------- | -------------------------------------------------------------------------------- | ------------------------------------------- |
| `stdio`             | Parent-supplied process pipes                                                    | Host-owned local daemon                     |
| `http`              | Bind address, protected credential file, TLS or explicit trusted plaintext scope | Host-dialed Device                          |
| `reverse_websocket` | Outbound URL and protected credential file                                       | Device dials an authenticated Host listener |

Select the profile with `A13N_ENVD_TRANSPORT`.

To serve a local HTTP client, first create a private runtime parent and a protected credential file. Use an existing native working directory and numeric bind address:

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

Replace the credential-file and workspace paths. Connect the Python client to `http://127.0.0.1:8787` with the same credential and expected Device ID. The server exposes authenticated `/eip/control` and `/eip/transfer` routes; the client takes the base origin.

For HTTPS, run `unset A13N_ENVD_HTTP_PLAINTEXT_SCOPE` and set both `A13N_ENVD_HTTP_TLS_CERT_FILE` and `A13N_ENVD_HTTP_TLS_KEY_FILE` before starting Envd. Configure the client to trust the certificate. A provider-private plaintext deployment uses `provider_private_link` and an explicit client opt-in, not a public HTTP endpoint. [Python transport options](python-client.md#http-options) cover proxy routing and TLS verification.

Reverse WebSocket requires `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`. `A13N_ENVD_REVERSE_WS_CA_FILE` adds deployment trust for `wss`. The daemon does not expose an inbound WebSocket listener.

Credentials belong in protected files, not argv, endpoint URLs, descriptors, logs or portable Environment state. A credential authorizes Device access; it does not isolate Sessions from each other.

## Lifecycle and ownership

One daemon generation serves a Device with multiple independent Sessions. Initializing a carrier opens no Session. `session.open` captures a working directory and starts one resource scope; `session.close` closes only that scope. Session-local keepalive renews only its owner. Device discovery does not keep abandoned Sessions alive.

A lost framed carrier detaches its Sessions for bounded disconnect grace. An existing owner may explicitly attach the same Session in the same generation. Attachment never replays commands or resumes transfers. Expired Sessions require fresh scopes and old resource references remain invalid.

The Host closes its Device connections during shutdown and terminates a daemon only when it owns that daemon's lifecycle. Session close does not delete files in the working directory. Native processes and private output/staging storage are reclaimed under bounded cleanup; failure is reported rather than treated as successful reclamation.

## Check before admitting work

```bash
a13n-envd --version
```

Match the exact daemon and Python client release. `A13N_ENVD_EXECUTABLE` belongs to Python Host executable selection; remove it from the daemon child environment. Unknown `A13N_ENVD_*` variables and unknown JSON fields are rejected.

Device initialization verifies identity and protocol. Session readiness checks the selected scope. There is no `isolation probe` or per-command isolation mode: test the Host's outer boundary through that deployment's own launcher and checks.

## Standalone field reference

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

Important default limits include 128 Sessions, 256 Device concurrent operations, 128 concurrent operations per Session, 4 GiB Device spool capacity, 1 GiB Session spool capacity, 256 MiB output per stream and 2 MiB previews. Configured Session capacities cannot exceed Device aggregates. Output preview cannot exceed stream capacity; spool must reserve both streams.

### Shell-profile fields

Required fields are `profile_id`, `display_name`, `native_executable`, `executable_search_roots` and positive `max_script_bytes`. Executables and search roots are existing absolute native paths. `fixed_arguments` defaults to `[]`, `safe_base_environment` to `{}` and `allow_login_mode` to `false`.

## Controlled Session egress

Set `"egress": {"mode": "controlled"}` in daemon JSON, `A13N_ENVD_EGRESS_MODE=controlled`, or `--egress-mode controlled` to require Linux controlled Sessions. Every Session must provide explicit tagged destinations; inherit and deny reject Session egress policy. Secret values are submitted through EIP, never this file. See [Session egress](egress.md) for prerequisites, creation, live updates and capacity behavior.
