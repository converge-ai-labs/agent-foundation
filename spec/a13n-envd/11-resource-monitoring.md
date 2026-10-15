# Resource Monitoring

## Ownership and Lifecycle

Envd provides opt-in Linux resource observations through a dedicated authenticated HTTP listener. Monitoring belongs to the outer Device generation and runs independently of EIP carriers and Sessions. There is one sampler per daemon, including periods with zero Sessions. Session workers receive no monitoring configuration or credentials and start no listener. Session closure does not stop sampling.

Trusted startup configuration owns the listener, credential, sampling interval and observation targets. The optional `monitoring` JSON object enables it; omission or `null` disables it without opening a socket or starting a sampler. Unsupported platforms reject enabled monitoring. EIP callers cannot enable, configure or query this surface. The monitoring protocol does not change the EIP version, descriptor method catalog, framing or generated clients.

Invalid configuration, credential loading, TLS preparation or listener binding fails daemon startup. Once started, an unreadable measurement becomes unavailable without failing execution. A stalled observation leaves the preceding snapshot readable. An unexpected monitoring loop failure closes monitoring and records a structured warning without stopping EIP. Monitoring never writes to EIP stdout or emits EIP notifications. Carrier termination stops the sampler and admitted monitoring connections; monitoring cannot extend stdio ownership beyond EOF. In-flight native blocking I/O follows the executable's bounded runtime shutdown policy.

## Authentication and Transport

The listener is separate from the execution listener and accepts `GET /monitoring/v1/report`. Every request requires exactly one `Authorization: Bearer <token>` header. The dedicated token must differ from the configured HTTP or reverse-WebSocket execution credential, including when different files contain equal tokens. A monitoring credential grants observation access only. Credential and private-key paths join protected bootstrap files; managed Linux startup retains private credential copies and resolves observation targets in the original system tree. Targets inside the runtime parent use its unmasked broker binding. Other targets at or below masked bootstrap paths are rejected at startup so protective mounts cannot be misreported as workload storage. Native account and outer deployment protections remain authoritative.

Public connections use verified TLS. Native TLS requires a paired certificate/private key. Explicit plaintext scopes are `loopback` (requiring a loopback bind) and `provider_private_link` (the deployment's private transport boundary). TLS and plaintext scope are mutually exclusive. Bind addresses are numeric with an explicit nonzero port. The daemon does not provision routes, tunnels, port publication or network exceptions. A reverse-WebSocket connection is not an inbound monitoring route.

Requests have no body, query, cookies, Origin, content encoding or transfer encoding. There are no mutation routes, Session selectors, browser CORS headers or redirects. All responses have `Cache-Control: no-store`; connections close after one response. The listener admits at most 32 simultaneous connections, closes excess connections, and gives each connection at most five seconds including TLS, request headers and response delivery. Reads only serialize a cached bounded snapshot; they perform no native resource queries.

| Condition                                                         | HTTP status |
| ----------------------------------------------------------------- | ----------- |
| Missing, repeated or invalid authorization                        | 401         |
| Authenticated unknown path                                        | 404         |
| Authenticated non-GET report request                              | 405         |
| Report request with a forbidden query, header or body declaration | 400         |
| First sample not yet available                                    | 503         |
| Cached report available, including partial or stale observations  | 200         |

Malformed HTTP framing can be rejected or disconnected by the HTTP parser before routing.

## Report v1

The response is JSON with `schema_version: 1`. Clients accept additive fields within v1; incompatible schema changes require another URL version. Identity and time fields are:

| Field                           | Type and meaning                                                                                                    |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `device_id`                     | String; the configured Device identity.                                                                             |
| `generation`                    | Decimal string of the same nonzero u64 used by the EIP Device descriptor. Equality fence, not ordering.             |
| `sample_interval_ms`            | Integer configured interval, default 10000, range 1000–60000.                                                       |
| `sample_age_ms`                 | Nonnegative integer elapsed monotonic milliseconds since collection began. It increases while a sample is retained. |
| `sample.sequence`               | Positive generation-local integer, incremented once per completed collection. Repeated reads retain the sequence.   |
| `sample.sampled_at`             | UTC RFC 3339 timestamp at collection start. Wall-clock changes do not alter monotonic age.                          |
| `sample.collection_duration_ms` | Nonnegative integer collection duration. Fields are sampled sequentially, not atomically.                           |

`sample.system` always contains these nullable measurements:

- `cpu_busy_percent`: number in 0–100 from successive aggregate `/proc/stat` CPU counters. Busy excludes idle and I/O wait and includes steal; guest counters are not double-counted. The first observation, a missing source, non-increasing total or any counter decrease produces `null`. The next valid pair can recover. This is system-wide utilization, not a cgroup quota percentage.
- `memory_total_bytes` and `memory_available_bytes`: nonnegative integers from `MemTotal` and `MemAvailable` in `/proc/meminfo`, converted from KiB to bytes. No estimate substitutes for an absent field.

System metrics describe the Linux system visible through procfs. In a container they may describe the host rather than the container. They are not attributed to one workspace or Session.

`sample.cgroup_v2` is `null` when no target is configured. Otherwise it contains nullable observations from exactly the trusted `cgroup_v2_directory`:

- `cpu_usage_usec`: nonnegative integer cumulative CPU time from `cpu.stat/usage_usec`, including descendants.
- `memory_current_bytes`: nonnegative integer from `memory.current`, including descendants.
- `cpu_max`: `{"kind":"unlimited"}` or `{"kind":"limited","quota_usec":<positive integer>,"period_usec":<positive integer>}` from `cpu.max`.
- `memory_max`: `{"kind":"unlimited"}` or `{"kind":"limited","bytes":<nonnegative integer>}` from `memory.max`.

Limits are local file values, not effective capacity after ancestor limits, cpusets or affinity. No quota-normalized percentage is inferred. The operator selects the intended cgroup and owns its lifetime; recreation can reset counters independently of Device generation. Unsupported controllers, cgroup v1, missing files, parse failures and permission errors yield `null`, never host values or synthetic zero. A configured but unreadable group remains an object with unavailable fields, distinct from no selected group.

`sample.filesystems` maps configured names to either `null` or `{total_bytes, free_bytes, available_bytes}` with nonnegative integer byte counts. Each observation describes the entire filesystem containing the target path. Available space accounts for reserved blocks. It is not a recursive directory size or a workspace quota. The configuration accepts at most 16 absolute target paths; names are 1–64 ASCII letters, digits, `_` or `-`. Targets cannot contain parent traversal. Names, not paths, appear in reports.

## Retention and Failure Interpretation

The sampler retains one completed snapshot in memory. It does not accumulate history, enqueue deliveries, catch up missed ticks or create parallel collections. Polling frequency does not change collection frequency. Slow local filesystem observations can delay the next snapshot; clients use age and duration to judge freshness. Missing data remains unavailable rather than becoming zero utilization. A readable report proves monitoring access, not EIP readiness or successful execution.

History aggregation, external export, push delivery and product UI belong to consuming Hosts. This surface supplies observations without durable storage, alerts or a Service dependency.
