# System Information and Resource Monitoring

## Ownership and Lifecycle

Envd exposes Device system information and optional resource samples on Linux, macOS and Windows through EIP. The outer daemon owns these observations independently of Sessions. A Device connection can describe the system and read metrics without opening a Session. Session workers do not collect Device metrics.

`initialize` and `device.describe` return `DeviceDescriptor.system_info`. It describes the outer Device environment at daemon startup: operating system, observed OS version, kernel version, native architecture and CPU model where available. Optional facts remain absent when their native source is unavailable. Linux OS version is the distribution's `PRETTY_NAME`; macOS uses the product version; Windows uses the NT major/minor/build rather than an inferred marketing name. Managed Linux reads distribution information from its original system tree, not the private broker image. Architecture describes the native system rather than the executable build target. Envd's own version remains `initialize.server.version`.

Trusted startup configuration owns the sampling interval and observation targets. An optional `monitoring` JSON object enables one sampler; omission or `null` disables it. System information remains available with sampling disabled. Sampling starts with the outer daemon and runs with zero Sessions. Session closure and individual carrier disconnects do not stop it. Outer daemon shutdown stops the sampling loop; in-flight blocking native I/O follows the executable's bounded runtime shutdown policy.

Invalid monitoring configuration fails startup. An unreadable measurement is absent without failing execution. Sampling is sequential, with at most one native collection in flight. Missed ticks are skipped. The daemon retains only the latest completed sample, never a delivery queue or history. A stalled collection leaves the preceding sample readable and aging. An unexpected sampling task failure records a structured warning and leaves retained observations available with increasing age.

## EIP and Python Client

`device.metrics` is a Device-scoped, ledger-external, read-only request-response method. It is advertised in `DeviceDescriptor.available_methods` only when monitoring is configured. A disabled method returns `unsupported`. Requests supply `expected_device_id` and `expected_generation`; ordinary Device identity/generation checks apply. They carry no `eip_session`, operation ID, target paths or sampling overrides.

HTTP, reverse WebSocket and stdio use the same generated request and result types and existing carrier authentication. There is no separate metrics listener, credential, TLS configuration, URL version or permission model. Method reads serialize cached observations and never trigger collection.

Canonical messages live in `proto/a13n-envd/eip/v1/monitoring.proto`; the normal descriptor generation produces Rust/Python models, dispatch, typed client stubs and inspection artifacts. `EIPDeviceConnection.metrics()` checks advertised availability, supplies its Device identity/generation and validates returned identity. `describe()` exposes system information through its existing descriptor result. The client owns no polling loop or monitoring history.

EIP 0.2 adds these observation contracts. Client and daemon negotiate exactly 0.2; 0.1-only peers fail protocol negotiation before descriptor decoding. Deployments upgrade client and daemon together. The canonical `eip.v1` namespace, carrier subprotocol and binary data-frame profile 1 remain unchanged. No independent monitoring schema version or dual-version runtime is introduced.

## Configuration

The `monitoring` object accepts:

- `sample_interval_ms`: default 10000, range 1000–60000.
- `filesystems`: at most 16 named absolute native paths. Names contain 1–64 ASCII letters, digits, `_` or `-`. Paths cannot contain parent traversal. Names, not paths, appear in reports.
- `cgroup_v2_directory`: an optional absolute Linux cgroup v2 path. Other platforms reject this option rather than pretending to collect it.

Managed Linux maps targets in the runtime parent to its unmasked broker state binding and other targets into the original system tree. Targets at or below other masked bootstrap paths are rejected so protected mounts cannot be misreported as workload storage. EIP callers cannot change these trusted observation targets.

## Sample Model

`DeviceMetricsResult` contains `device_id`, `generation`, configured `sample_interval_ms`, and optional `sample`. The sample is absent until the first collection completes. The generation is the same nonzero u64 as the Device descriptor, encoded by the normal EIP JSON profile.

A sample contains:

| Field                    | Meaning                                                                                                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `sequence`               | Positive generation-local counter, incremented once per completed collection.                                               |
| `sampled_at`             | UTC timestamp at collection start.                                                                                          |
| `sample_age_ms`          | Monotonic elapsed milliseconds since collection began, calculated at response time.                                         |
| `collection_duration_ms` | Time spent collecting this sample. Fields are observed sequentially, not atomically.                                        |
| `system`                 | Native system CPU and memory observations.                                                                                  |
| `cgroup_v2`              | Optional Linux selected-group observation.                                                                                  |
| `filesystems`            | Configured names mapped to observations with optional `metrics`. An unreadable target retains its name with absent metrics. |

Optional unavailable values follow EIP's canonical omission rules and become `None` in Python. Real zero values remain zero. Percentages are finite numbers in 0–100; byte counts, durations and event counters use bounded unsigned integers. CPU seconds are finite nonnegative numbers.

## System Metrics

All three platforms provide overall CPU utilization, logical CPU count, physical memory total and available-memory observations through native APIs. These describe the system visible to the daemon, not one Session, workspace or cgroup. Container procfs may describe its host. Logical CPU count is not an effective quota or affinity allowance.

`cpu_busy_percent` is the average over successive valid native CPU observations, normalized to 0–100 across the observed CPUs. The first observation, missing source, non-increasing total or decreasing counter leaves it absent and establishes a new baseline. Polling cadence does not determine this averaging window; actual collection intervals do. `logical_cpu_count` and `memory_total_bytes` are sampled rather than frozen in the startup descriptor.

| Platform | CPU utilization                                                                                                                                                                          | Memory available                                                                                                                                                                                                                               |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Linux    | Aggregate `/proc/stat` deltas; excludes idle and iowait, includes steal, does not double-count guest time.                                                                               | `/proc/meminfo` `MemAvailable`, KiB converted to bytes. Missing fields are not estimated.                                                                                                                                                      |
| macOS    | Aggregate Mach CPU tick deltas; busy is user + system + nice. Counter wrap resets the baseline.                                                                                          | Explicit reclaimability estimate: `(free_count + inactive_count) * kernel_page_size`. Speculative pages already belong to free_count; purgeable pages are not added again. This estimate is not a guarantee of immediately allocatable memory. |
| Windows  | `GetSystemTimes` deltas, subtracting idle once from kernel time. Multi-group systems aggregate groups on a short-lived native helper thread, without changing a Tokio worker's affinity. | `GlobalMemoryStatusEx.ullAvailPhys` in bytes.                                                                                                                                                                                                  |

Optional `cpu_time_seconds` retains native cumulative modes. Linux provides user/nice/system/idle/iowait/irq/softirq/steal using `_SC_CLK_TCK`; guest counters are already included. Windows provides user/system/idle from 100 ns counters, with idle excluded from system. macOS leaves cumulative seconds absent because its tick observation is used only for utilization. CPU mode fields are explicit optional fields, not arbitrary metric names.

Linux additionally exposes system `pressure` from `/proc/pressure/`. System CPU `full` is absent because it is undefined at system scope, even on kernels that emit a compatibility zero. Other platforms omit pressure.

## Linux Cgroup Metrics

`cgroup_v2` is absent when no group is configured. A configured but unreadable group remains an object with unavailable fields. Data comes from exactly the configured group, with no substitution of host values:

- `cpu_usage_usec`, `cpu_user_usec`, `cpu_system_usec`: cumulative CPU times from `cpu.stat`, including descendants.
- `cpu_periods_total`, `cpu_throttled_periods_total`, `cpu_throttled_usec`: this group's bandwidth throttling counters from `cpu.stat`.
- `cpu_max`: tagged `unlimited`, or `limited` with positive `quota_usec` and `period_usec`.
- `memory_current_bytes`: `memory.current`, including descendants.
- `memory_max`: tagged `unlimited`, or `limited` with nonnegative `bytes`.
- `memory_events`: optional cumulative `low`, `high`, `max`, `oom`, `oom_kill`, `oom_group_kill` from `memory.events`, including descendants.
- `pressure`: CPU, memory and I/O observations from the group's pressure files.

Limits are local values, not effective capacity after ancestor limits, cpusets or affinity. No quota-normalized utilization is inferred. The operator selects the group and owns its lifetime; recreation can reset counters independently of Device generation. Missing controllers, cgroup v1, missing/malformed files and permission errors produce absent observations.

Each pressure resource has optional `some` and `full` records containing `avg10_percent`, `avg60_percent`, `avg300_percent`, and cumulative `total_usec`. These are kernel 10-, 60- and 300-second windows, not daemon sampling averages. `some` means some tasks are stalled; `full` means all non-idle tasks are stalled simultaneously. Malformed records are omitted independently. Envd installs no pressure triggers and changes no controller settings.

## Filesystem Metrics

Each successful observation reports `total_bytes`, `free_bytes`, `available_bytes`, and optional `total_inodes`, `free_inodes`, `available_inodes`. It describes the entire containing filesystem or volume, not recursive directory size or workspace quota.

Linux and macOS use one `statvfs` observation, preserving reserved-block/account-aware availability and native inode counts. APFS values describe the selected volume's shared-space view rather than exclusive physical disk capacity. A zero inode total does not establish exhaustion; consumers calculate utilization only for positive totals.

Windows resolves the target's containing volume. `GetDiskSpaceInformationW` supplies actual volume total; `GetDiskFreeSpaceExW` supplies total free bytes and bytes available to the daemon account. The quota-limited total returned by `GetDiskFreeSpaceExW` is not used as volume capacity. These calls are sequential. Windows leaves inode counts absent. On Windows versions before the volume-information API is available (Windows 10 version 1809 / Server 2019), this filesystem observation is unavailable; the daemon still starts and serves its other capabilities.

## Consumer Interpretation

Consumers identify samples by `(device_id, generation, sample.sequence)` and do not ingest repeated reads as new observations. A successful EIP response establishes report availability, not freshness or completeness. Consumers apply a maximum sample age appropriate to their polling cadence. Counter rates use distinct samples, reset after generation changes or decreases, and do not bridge missing observations.

Export adapters own metric types and unit conversion: cumulative CPU/pressure/throttling times become counters in seconds, byte/inode observations become gauges, event counts become counters, and percentages become ratios where needed. Sampled maxima are not instantaneous peaks: CPU utilization is already an interval average and point observations can miss events between samples.

History aggregation, external export, push delivery, alerts and product UI belong to consuming Hosts. Envd provides bounded observations without durable storage or a Service dependency.
