# Runtime and composition

This chapter owns the Service's processes: the roles one image runs, the command line, startup, readiness and shutdown, schema migration authority, settings and operational limits, HTTP ingress, the use of Redis, background sweeps, the worker's process loop and claim wakeups, and how a distribution assembles and extends the Service.

It does not own the rules those processes execute. The claim transaction, leases, heartbeats and recovery are in [05: runs](05-runs.md#claim-heartbeat-and-authority); outbox delivery semantics, objects and the thread stream in [07](07-facts-and-delivery.md); environment maintenance in [06](06-environments.md#one-outstanding-external-operation); routes and the error envelope in [10](10-api.md); package boundaries in [02](02-layout.md).

## Roles

One non-root image carries one executable, `a13n-service`:

| Command                                                   | Does                                                                                                                           |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `run --role all` (default)                                | the API, the control sweeps and the worker in one process; development and single-host deployments                             |
| `run --role control`                                      | the API, thread streams and the control sweeps                                                                                 |
| `run --role worker`                                       | claims and executes runs; serves only health and readiness                                                                     |
| `migrate`                                                 | upgrades the database to this build's schema head ([Schema migrations](#schema-migrations))                                    |
| `bootstrap --email EMAIL [--password-stdin]`              | creates the first organization and administrator ([03](03-tenancy.md#bootstrap)); exits with status 3 when already initialized |
| `user disable --email EMAIL`, `user enable --email EMAIL` | the operator's switch for a user account ([03](03-tenancy.md#disabling))                                                       |

The executable takes an optional `--config PATH` settings file before the command, as in `a13n-service --config PATH run`. `bootstrap` and `user` first require the schema at this build's head. An invalid configuration fails before anything runs and reports only the error type, because validation messages can contain secrets. The executable configures logging once and, for `run` when `telemetry.metrics_port` is set, serves metrics ([12](12-observability.md)); libraries only use namespaced `a13n-logging` loggers. The server listens on `server.host` and `server.port`, with TLS when `server.tls_certificate` and `server.tls_key` are set, and trusts forwarded client addresses and schemes only from `server.trusted_proxies`.

What each role runs:

| Component                                                                  | `all` | `control` | `worker` |
| -------------------------------------------------------------------------- | ----- | --------- | -------- |
| API routes, `/api/v1/openapi.json`, API docs                               | yes   | yes       | no       |
| Thread stream hub ([07](07-facts-and-delivery.md#the-thread-stream))       | yes   | yes       | no       |
| Control [sweeps](#sweeps)                                                  | yes   | yes       | no       |
| [Worker](#worker)                                                          | yes   | no        | yes      |
| Automatic migration when `database.auto_migrate`                           | yes   | yes       | never    |
| Harness trace export                                                       | yes   | no        | yes      |
| `/healthz`, `/readyz`                                                      | yes   | yes       | yes      |
| `/metrics` on `telemetry.metrics_port` ([12](12-observability.md#metrics)) | yes   | yes       | yes      |

Every replica of a role runs the same components; replicas coordinate only through PostgreSQL rows. The `a13n-service` executable assembles the built-in distribution. Another distribution provides its own entry point that loads settings with its sections, builds its application with `build_app(distribution, role=...)` and runs the migration runner with its composed graph ([Assembly](#assembly)).

## Startup, readiness and shutdown

A process starts in this order and serves nothing until it finishes:

1. Assembly checks ([Assembly](#assembly)): an invalid distribution fails here.
2. `all` and `control` upgrade the schema when `database.auto_migrate` is true.
3. The process opens its runtime: the database pool (`database.pool_size` connections and no overflow; `database.connect_timeout` also bounds the wait for a pooled connection; `database.statement_timeout` bounds each statement), the Redis client (`redis.timeout` for connecting and every call), the object store, the encryption key ring ([03](03-tenancy.md#credential-encryption)), the provider registry, the access configuration, the installed Harness plugin factories (`plugins.keys`), the admission policy, the trace backend that queries read (none when tracing is off) and, on executing roles, trace export. The registry offers environment types by `environments.allow_local`, `environments.docker_host` and `environments.docker_mount_roots` ([08](08-providers.md#registry)) and derives each calling API's settings schema as it is assembled. An invalid key ring or plugin key fails startup.
4. Within `server.readiness_timeout` the process checks that the database is exactly at its build's migration head; otherwise startup fails with an instruction to run `a13n-service migrate`.
5. The role's background tasks start: thread stream hub and sweeps, worker. API-serving roles also create the empty [model catalog](08-providers.md#model-catalog), which the first read fills.

`GET /healthz` answers 200 `{"status": "ok", "role": ...}` whenever the process serves HTTP; it is liveness only. `GET /readyz` answers 200 `{"status": "ready", "role": ...}` when startup completed, every background task of the role is still running and the schema check passes within `server.readiness_timeout`; otherwise it answers 503 `{"status": "unavailable", "dependency": "runtime" | "database"}`. A replica that cannot reach Redis stays ready and adds `"degraded": ["redis"]`, because Redis only accelerates work ([Redis](#redis)). Readiness never migrates and never calls a provider.

| Dependency                  | Used by                                            | While unavailable                                                                                                                                                                                                                                 |
| --------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL                  | every role                                         | startup fails; readiness reports `database`; requests answer `unavailable` ([HTTP ingress](#http-ingress-and-escaping-failures)); sweep passes fail and run again at their next interval; the worker retries claiming after `worker.scan_seconds` |
| Redis                       | every role                                         | readiness stays ready and reports `degraded`; rate limits are not enforced; claims fall back to the periodic scan; live thread streams are unavailable while durable reads keep working ([Redis](#redis))                                         |
| Object store                | every role                                         | object reads and writes fail with their owners' errors ([07](07-facts-and-delivery.md#objects)); the local backend requires every process to see the same `objects.root`                                                                          |
| SMTP (`auth.mail`)          | `all`, `control`                                   | email outbox rows retry and eventually dead-letter ([07](07-facts-and-delivery.md#outbox))                                                                                                                                                        |
| Trace backend (`telemetry`) | export: `all`, `worker`; queries: `all`, `control` | export runs in background batches and never delays execution; trace queries fail ([08](08-providers.md))                                                                                                                                          |

**Shutdown.** On termination the server stops accepting connections and waits up to `server.shutdown_timeout` for open requests. The process then cancels its background tasks: sweeps and the stream hub stop at once; the worker stops claiming, asks each running attempt to hand off at its next safe boundary and waits up to `worker.drain_seconds`, which must be shorter than `server.shutdown_timeout`. Attempts still running after the drain are cancelled and recovered through lease expiry ([05](05-runs.md#claim-heartbeat-and-authority)). Background tasks get at most `server.shutdown_timeout` to finish, and trace export flushes for at most 10 seconds.

**Compatibility across builds.** Every process requires the schema exactly at its build's head: startup fails on any other schema, and readiness reports `database` once a migration moves the schema away. A process already running keeps working, claiming included, until it is replaced. A rolling upgrade therefore migrates before new-build processes start and uses migrations that the previous build tolerates until its processes are gone. Checkpoint compatibility is separate from schema compatibility: a worker claims only runs whose committed checkpoint format it can continue, and leaves the rest for a compatible worker ([05](05-runs.md#claim-heartbeat-and-authority)). `run_attempts.worker_build` records the package version for diagnosis; it is not a compatibility test.

## Schema migrations

The schema is one Alembic graph composed from the distribution's migration directories and table metadata. The graph must have exactly one head, and every process requires the database at that head.

- **Authority.** `a13n-service migrate` upgrades the database; deployments with a dedicated migration job set `database.auto_migrate = false`. Otherwise `all` and `control` upgrade at startup. The worker role never migrates.
- **Coordination.** A migration runs on a dedicated connection outside the pool, holding a session-level PostgreSQL advisory lock for its whole duration. A concurrent migrator waits for that lock for at most `database.migration_advisory_lock_timeout` and fails when the wait runs out; once it holds the lock it finds the schema at head and changes nothing. Under the lock, statements run with `lock_timeout = database.migration_lock_timeout`, `statement_timeout = database.migration_statement_timeout` and `idle_in_transaction_session_timeout = database.migration_idle_transaction_timeout`. Closing the connection releases the lock after success or failure.
- **Checking.** `a13n-service migrate --check` fails unless the database is at head and the revisions match the composed metadata; deployments use it to wait for a migration job without migrating.
- **Generation.** Revisions are generated with `migrate --generate MESSAGE` against a disposable database and reviewed; they are never written by hand. Rules that declarative constraints cannot express (the version stamp, immutability and identity guards, transition and pointer guards) are declared by each table next to its constraints. The generated revision that creates a table also creates its rules, and the first revision creates the shared trigger functions. Tests build schemas from the same metadata and rules and check the revisions against both.

## Settings

Settings are loaded and validated once per process into frozen, typed sections:

- An optional TOML file is named by `--config` or `A13N_SETTINGS_FILE`.
- An environment variable `A13N_<SECTION>__<FIELD>` replaces one field of the file; it is not merged with the file's value. A field that takes a list, map or section is given as JSON, as in `A13N_PLUGINS__KEYS='["…"]'`, `A13N_ASSISTANT__MODELS`, `A13N_ENCRYPTION__KEYS` or `A13N_AUTH__MAIL`; a nested section is set whole, never field by field.
- Unknown sections, fields and variable names fail loading.
- The sections are `server`, `database`, `objects`, `redis`, `auth` (with `auth.mail`), `encryption`, `control`, `worker`, `environments`, `memory` (with `memory.default_guide`), `providers`, `plugins`, `assistant` and `telemetry`, plus the sections a distribution declares under their own names. A distribution section that shares a core section's name fails loading, naming it.

The generated [configuration reference](../../docs/a13n-service/configuration-reference.md) lists every field with its default and range. Beyond per-field ranges, loading refuses:

- a `database.url` that does not use `postgresql+psycopg`;
- the `s3` object backend without `objects.bucket`;
- incomplete SMTP settings (a sender is required with `auth.mail.smtp_host`; username and password come together; neither without a host), or SMTP without `encryption.active_key_id`, because queued mail carries encrypted links;
- a trace backend without its URL and keys;
- `telemetry.log_stdout = false` without `telemetry.log_file`, or a `telemetry.metrics_port` equal to `server.port`;
- an `environments.docker_mount_roots` entry that is not an absolute path or contains `..`;
- bounds that do not nest:

| Inner bound, with its margin                                            | Must stay below                            | Why                                                                                         |
| ----------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `worker.scan_seconds`                                                   | `redis.timeout`                            | the blocking wake wait returns before the client times out                                  |
| the thread stream's 1-second block                                      | `redis.timeout`                            | the same, for stream reads                                                                  |
| 3 × `worker.authority_seconds`                                          | `worker.lease_seconds`                     | an attempt survives one failed renewal                                                      |
| 3 × `objects.timeout`                                                   | `worker.lease_seconds`                     | an object write fits before the lease runs out                                              |
| 2 × `control.webhook_timeout`                                           | `control.outbox_lease_seconds`             | a sender finishes and settles within its claim                                              |
| 2 × `auth.mail.timeout`                                                 | `control.outbox_lease_seconds`             | the same, for mail                                                                          |
| 2 × `providers.operation_seconds`                                       | `control.outbox_lease_seconds`             | the same, for a memory namespace purge ([11](11-memory.md#namespace-purge))                 |
| `worker.drain_seconds`                                                  | `server.shutdown_timeout`                  | draining workers hand off before shutdown stops waiting                                     |
| `objects.upload_bytes`                                                  | `server.request_bytes`                     | an upload is one request body                                                               |
| `worker.output_bytes` + 65536                                           | `control.inbox_bytes`                      | a child result always fits its parent's empty inbox ([07](07-facts-and-delivery.md#outbox)) |
| `environments.scan_seconds` + 2 × `environments.renewal_seconds` + 10 s | 150 s, half the 300-second renewal horizon | a due renewal finishes before the sandbox ends ([06](06-environments.md#renewal))           |
| `memory.always_load_bytes`, inclusive                                   | `memory.context_bytes`                     | always-loaded files fit the run's memory context budget ([11](11-memory.md#settings))       |
| `memory.frontmatter_bytes`                                              | `memory.max_file_bytes`                    | a file's frontmatter leaves room for its body                                               |
| `memory.max_file_bytes`, inclusive                                      | `memory.max_total_bytes`                   | one file always fits its memory                                                             |
| `memory.default_guide.file` and `.record`, inclusive                    | `memory.guide_bytes`                       | the deployment's guides obey the bound a memory's own guide does                            |

### Operational limits

Every limit is finite and visible: a setting or a fixed bound in code. Reaching one returns a typed error or stops work with retained evidence; it never silently drops accepted input. Each limit belongs to the chapter that owns its rule:

| Limit                                                                                                                | Settings                                                                                                                                                                 | Owner                                                                    |
| -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| request body size and arrival time                                                                                   | `server.request_bytes`, `server.request_timeout`                                                                                                                         | [HTTP ingress](#http-ingress-and-escaping-failures)                      |
| credential attempts; session, invitation and link lifetimes                                                          | `auth.login_limit`, `auth.login_window_seconds`, `auth.session_seconds`, `auth.invitation_seconds`, `auth.link_seconds`                                                  | [03](03-tenancy.md#authentication)                                       |
| upload size and rate                                                                                                 | `objects.upload_bytes`, `objects.upload_limit`, `objects.upload_window_seconds`                                                                                          | [04](04-resources.md)                                                    |
| object size and store call deadline                                                                                  | `objects.max_bytes`, `objects.timeout`                                                                                                                                   | [07](07-facts-and-delivery.md#objects)                                   |
| outstanding inbox entries and bytes per thread                                                                       | `control.inbox_count`, `control.inbox_bytes`                                                                                                                             | [05](05-runs.md#inbox-capacity)                                          |
| subscriptions per workspace                                                                                          | `control.subscriptions`                                                                                                                                                  | [07](07-facts-and-delivery.md#lifecycle-webhooks)                        |
| outbox claims per pass, attempts, claim lease, retention, webhook POST                                               | `control.outbox_batch`, `control.outbox_attempts`, `control.outbox_lease_seconds`, `control.outbox_retention_days`, `control.webhook_timeout`                            | [07](07-facts-and-delivery.md#outbox)                                    |
| sweep batch                                                                                                          | `control.sweep_batch`                                                                                                                                                    | [Sweeps](#sweeps)                                                        |
| skill import request deadline                                                                                        | `control.import_timeout`                                                                                                                                                 | [04](04-resources.md)                                                    |
| stream authority refresh; fragment coalescing; covered-entry retention; length and idle lifetime                     | `control.stream_refresh_seconds`, `worker.stream_coalesce_seconds`, `worker.stream_trim_seconds`, `worker.stream_length`, `worker.stream_ttl`                            | [07](07-facts-and-delivery.md#the-thread-stream)                         |
| worker slots and drain                                                                                               | `worker.slots`, `worker.drain_seconds`                                                                                                                                   | [Worker](#worker)                                                        |
| attempts, lease, authority interval, steer batch, child depth and count                                              | `worker.max_attempts`, `worker.lease_seconds`, `worker.authority_seconds`, `worker.delivery_count`, `worker.delivery_bytes`, `worker.child_depth`, `worker.child_count`  | [05](05-runs.md)                                                         |
| display and output bytes per run                                                                                     | `worker.display_bytes`, `worker.output_bytes`                                                                                                                            | [05](05-runs.md), [07](07-facts-and-delivery.md#checkpoints-and-display) |
| environment maintenance batch, operation and renewal deadlines, mount wait                                           | `environments.batch`, `environments.operation_seconds`, `environments.renewal_seconds`, `environments.wait_seconds`                                                      | [06](06-environments.md)                                                 |
| managed environments per workspace                                                                                   | `environments.managed_count`                                                                                                                                             | [06](06-environments.md#mounts)                                          |
| memory file format, history, byte total, mounts per thread, guides, record length, a run's memory context and recall | `memory.*`                                                                                                                                                               | [11](11-memory.md#settings)                                              |
| provider operations, tool calls, model reads, response bytes, authorization flows, discovery cache                   | `providers.operation_seconds`, `providers.tool_call_seconds`, `providers.model_timeout`, `providers.response_bytes`, `providers.flow_seconds`, `providers.discovery_ttl` | [04](04-resources.md), [08](08-providers.md)                             |
| trace query deadline                                                                                                 | `telemetry.trace_query_timeout`                                                                                                                                          | [08](08-providers.md)                                                    |
| one whole SMTP send                                                                                                  | `auth.mail.timeout`                                                                                                                                                      | [07](07-facts-and-delivery.md#outbox)                                    |

A provider call, a credential refresh, an environment operation and an object write have separate deadlines; no single timeout makes them all recoverable.

## HTTP ingress and escaping failures

Every HTTP request body is read completely before routing, JSON parsing or database work. A body larger than `server.request_bytes` is refused with `payload_too_large` (413, `details.limit`), and a body that has not arrived within `server.request_timeout` with `request_timeout` (408); both responses close the connection. A client that disconnects while sending gets no response.

Failures that escape a route answer in the error envelope ([10](10-api.md#errors)), whose codes are listed in [02](02-layout.md#error-codes):

| Escaping failure                                                                 | Response                                                                     |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| a `ServiceError`                                                                 | its own code and details                                                     |
| request validation                                                               | `invalid_argument` with `details.fields` ([10](10-api.md#errors))            |
| a body that cannot be parsed                                                     | `invalid_argument`, `field` `body`, `reason` `unparsable`                    |
| no route for the path, or a method the path does not accept                      | 404 `not_found`, kind `route` ([10](10-api.md#errors))                       |
| database unreachable or too slow (connection and interface errors, pool timeout) | 503 `unavailable`, `details.dependency = "database"`                         |
| a Redis error                                                                    | 503 `unavailable`, `details.dependency = "redis"`                            |
| anything else                                                                    | 500 `internal` with a fixed message; the traceback is logged, never returned |

Every HTTP response, a 500 or an ingress refusal included, carries exactly one Service-generated `X-Request-Id` (never taken from the caller), which error bodies repeat as `request_id` and logs of escaping failures record; it correlates diagnostics and grants nothing. Error responses carry the headers [10](10-api.md#errors) lists. Route dependencies return detached values and never yield a database session, so no session outlives its operation or stays open on a streaming response.

## Redis

Redis is an accelerator, never an authority or a condition for correctness. PostgreSQL decides ownership and durable state, and every use of Redis stays correct, only slower or less live, when Redis is unreachable or has lost data. Every call is bounded by `redis.timeout`.

| Use                                                                      | When Redis fails                                                                                |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| the claim wake marker `a13n:wake`                                        | workers find due runs by the periodic scan ([Worker claim wakeups](#worker-claim-wakeups))      |
| rate-limit counters (fixed windows per hashed identity)                  | the limit is not enforced and each skipped check is logged                                      |
| thread streams ([07](07-facts-and-delivery.md#the-thread-stream))        | provisional output is lost and readers are told; committed display and run state are unaffected |
| connection tool and connector catalog caches (`providers.discovery_ttl`) | results are fetched again from the provider ([04](04-resources.md))                             |

## Sweeps

Background work consists of named sweeps. Each pass does a bounded amount of work over durable evidence and finds that work with an indexed query. The scheduler adds only timing: a sweep's first pass starts after a random delay of up to one interval, so replicas started together spread out, and each later pass starts one interval after the previous one ended. A pass that exceeds its deadline is cancelled. A failed or cancelled pass is logged with the sweep's name and error type, and the next pass runs on schedule. Sweep names are unique; a duplicate fails the startup of a process that runs sweeps before any pass runs.

Sweeps run on every `all` and `control` replica at once. Coordination belongs to each sweep, never to a process-wide lock: rows are claimed with `SKIP LOCKED` or operation tokens, and state is rechecked under row locks. No sweep holds a database session, row lock or advisory lock across external I/O.

| Sweep                           | Owner                                                       | Interval                           | Work per pass                                                                     | Pass deadline                               |
| ------------------------------- | ----------------------------------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------- |
| `advance_threads`               | [05](05-runs.md#seal-and-successor-scheduling)              | `control.scan_seconds`             | `control.sweep_batch` threads, in rotating ID order                               | max(30 s, 10 × `control.scan_seconds`)      |
| `expire_leases`                 | [05](05-runs.md#claim-heartbeat-and-authority)              | `worker.authority_seconds`         | `control.sweep_batch` expired attempts                                            | max(30 s, `worker.lease_seconds`)           |
| `expire_credentials`            | [03](03-tenancy.md#expiry)                                  | `auth.expiry_scan_seconds`         | `control.sweep_batch` rows per table                                              | 60 s                                        |
| `maintain_environments`         | [06](06-environments.md#one-outstanding-external-operation) | `environments.scan_seconds`        | `environments.batch` per phase                                                    | 2 × `environments.operation_seconds` + 30 s |
| `renew_environments`            | [06](06-environments.md#renewal)                            | `environments.scan_seconds`        | `environments.batch` due renewals, earliest first                                 | `environments.renewal_seconds` + 40 s       |
| `recover_connection_operations` | [04](04-resources.md#connections)                           | `providers.operation_scan_seconds` | `control.sweep_batch` operations past their deadline, failed as `outcome_unknown` | 60 s                                        |
| `deliver_outbox`                | [07](07-facts-and-delivery.md#outbox)                       | `control.scan_seconds`             | up to `control.outbox_batch` claims per kind                                      | 2 × `control.outbox_lease_seconds`          |
| `purge_outbox`                  | [07](07-facts-and-delivery.md#outbox)                       | one hour                           | `control.sweep_batch` settled rows older than `control.outbox_retention_days`     | 60 s                                        |
| `report_backlog`                | [12](12-observability.md#metrics)                           | 15 s                               | one count, bounded at 10,000, per queue                                           | 10 s                                        |

A `deliver_outbox` pass delivers its kinds side by side, so a slow kind never delays another. Within a kind it claims and handles at most 8 rows at a time, each under its own claim of `control.outbox_lease_seconds`, and starts no new batch once one claim lease has passed since the pass began, so a pass ends within two leases. Retries, backoff and dead-lettering belong to the outbox ([07](07-facts-and-delivery.md#outbox)). There is no generic jobs table.

## Worker

Each executing process runs one claim loop with `worker.slots` local slots and a worker ID, `wrk_` plus 32 hex characters, new for each worker incarnation and recorded on each attempt; `run_attempts.worker_build` records its package version. The start log names the worker ID, host and build separately. The loop reserves capacity before claiming: it asks for at most as many runs as it has free slots, and a full worker waits for a slot to free before scanning again. [05](05-runs.md#claim-heartbeat-and-authority) owns the claim transaction. A claim that fails, for example during a database outage, is logged and tried again after `worker.scan_seconds`; running attempts keep their slots and leases.

Each claimed attempt runs as its own task. One supervisor task renews the leases of all of them and polls cancellation and each principal's authority every `worker.authority_seconds`, independently of the Harness tasks ([05](05-runs.md#claim-heartbeat-and-authority)). An error that escapes an attempt is logged and left to lease expiry and recovery. Shutdown drains the worker as described in [Startup, readiness and shutdown](#startup-readiness-and-shutdown); the supervisor keeps renewing while it drains.

### Worker claim wakeups

The periodic claim scan is the mechanism; a Redis marker only makes it run sooner. PostgreSQL remains the ownership authority, and nothing is claimed by notification.

1. The marker means "look now". It carries no run ID; the database claim decides ownership.
2. The deployment-wide list `a13n:wake` holds at most one marker: `wake()` runs `RPUSH` then `LTRIM -1 -1` in one `MULTI` within `redis.timeout` and ignores failure.
3. A worker takes a marker only when it has a free slot; a full worker leaves it for another worker.
4. The marker wait is the periodic timer: `BLPOP` with a timeout of `worker.scan_seconds`. A Redis error sleeps for the same interval.

```python
async def claim_loop(worker, redis, scan_seconds: float) -> None:     # illustrative
    while not worker.stopping:
        free = worker.free_slots
        if free == 0:
            await worker.slot_released.wait()          # full: leave markers; rescan when a slot frees
            continue
        try:
            claimed = await claim_due_runs(limit=free) # SKIP LOCKED; PostgreSQL decides ownership
        except Exception:
            await sleep(scan_seconds)                  # logged; running attempts are unaffected
            continue
        if claimed < free:
            await wait_for_wake(redis, timeout=scan_seconds)
```

Exactly two places register `wake()` as an after-commit callback: `start_run`, which every run-creation path uses, and the transition that returns a run to accepted when it is immediately due (a handoff; recovery always waits out a backoff). A run whose backoff is pending is found by the scan once due. After-commit callbacks run only after a successful commit, and their failures are logged without affecting the committed state. A crash between commit and callback delays the run by at most one scan interval. A burst of wakes coalesces into one marker; workers not woken find the remaining runs within one interval. One loop per worker means scans never overlap within a worker, startup begins with a scan and shutdown cancels the wait. No database session survives a Redis call or wait.

## Assembly

A distribution lists what a build contributes explicitly; there is no package scanning and no import-time registration:

```python
@dataclass(frozen=True)
class Distribution:                                   # the composition contract
    name: str
    routers: tuple[APIRouter, ...] = ()
    tables: tuple[type[Base], ...] = ()
    migrations: tuple[Path, ...] = ()                 # revision directories of one graph
    settings: Mapping[str, type[Section]] = {}        # additional settings sections by name
    sweeps: tuple[Callable[[Runtime], Sweep], ...] = ()
    providers: tuple[ProviderDefinition, ...] = ()
    admission: AdmissionPolicy | None = None
    authenticator: Authenticator | None = None        # None keeps the local authenticator
    grant_sources: tuple[GrantSource, ...] = ()
    roles: Mapping[str, frozenset[Verb]] = {}         # role name to the verbs it grants

    def extend(self, extension: "Distribution") -> "Distribution": ...

def build_app(distribution: Distribution = OSS, *, role: ProcessRole = "all", settings: Settings | None = None) -> FastAPI: ...
```

`OSS` is the built-in distribution: every core table, router, migration directory, sweep, the built-in provider definitions and the four built-in roles. A distribution extends it with `OSS.extend(Distribution(...))`, which concatenates the tuples and merges the maps. Sweeps are factories because background work needs the assembled runtime; outbox delivery handlers are wired inside the core sweeps, because the outbox kinds are closed by a database CHECK ([07](07-facts-and-delivery.md#outbox)).

Assembly fails, before anything is served, on:

- a settings section or role name that two extended distributions both declare, or a second authenticator or admission policy in `extend` (a section named like a core section fails settings loading, [Settings](#settings));
- an invalid role definition ([03](03-tenancy.md#roles-and-grant-sources));
- a duplicate table, or a migration graph with more than one head;
- a duplicate provider type within a provider kind, or an unsupported kind ([08](08-providers.md));
- a router entry that is not a plain HTTP route, or a duplicate method and path, including the health, readiness, OpenAPI and docs paths. An extension can never shadow a core operation silently.

Every role applies these checks, although only API roles install the routers.

## Extension points

Core validation and authorization cannot be replaced. What a distribution adds can only restrict what core checks allow:

| Need                             | Boundary                                                                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| shared foundations for editions  | the distribution's routers, tables, migrations, settings sections, sweeps and provider definitions, assembled with the core     |
| replacing authentication         | one `Authenticator` returning validated identity and confinement; management services stay ([03](03-tenancy.md#authentication)) |
| external grants and custom roles | grant sources and additional role definitions ([03](03-tenancy.md#roles-and-grant-sources))                                     |
| additional run admission         | `AdmissionPolicy.accept`, called by `start_run` for every run-creation path                                                     |
| spend budgets                    | `AdmissionPolicy.proceed`, called before every paid dispatch                                                                    |

```python
class AcceptedIntent:            # conceptual
    organization_id: str; workspace_id: str; session_id: str; thread_id: str; run_id: str
    principal_id: str; agent_id: str; agent_revision_id: str; trigger: str; root_run_id: str

class CallContext:               # conceptual; one paid dispatch, identified before it is sent
    organization_id: str; workspace_id: str; session_id: str; thread_id: str
    run_id: str; run_attempt_id: str; root_run_id: str
    call_id: str; source: str
    provider_id: str | None      # None for a call served by no provider resource, such as a remote MCP tool
    model_id: str | None; connection_id: str | None; tool_name: str | None
    price_snapshot: dict | None  # None means the price is unknown; the policy decides how to treat that

class AdmissionPolicy(Protocol):
    async def accept(self, session: AsyncSession, intent: AcceptedIntent) -> None: ...
    async def proceed(self, session: AsyncSession, call: CallContext) -> None: ...
```

Both methods run in a short transaction the Service owns and refuse by raising a `ServiceError`. They are SQL-only, short, cancellation-safe and idempotent: they may read and write the policy's own tables in that transaction, but never commit it, contact another service or invoke the Harness. External policy data is prepared outside the transaction with an explicit validity period. Policy row locks follow the core's domain and resource locks.

- `accept` runs inside `start_run` after the run's revision, overrides and mounts are validated and its ID allocated, before the run is written, so every creation path passes it: submission, automatic advancement, resume, fork, child spawn and child-result successors. A refusal rolls back the whole creation, including a primary environment reserved for it ([05](05-runs.md#submit-and-accept)).
- `proceed` runs before every paid dispatch of an attempt: every model request, including inline subagents, every connection tool call and every web search or scrape. It runs after the attempt's cancellation and lease checks and, for a model request, the run's request limit ([05](05-runs.md#execute)). A refusal ends the run with a failure carrying the error's code, or a `conflict`'s reason; an `unavailable` error is a dependency failure, not a refusal.
- `call_id` is established before dispatch, and the usage records of that call carry the same identity ([07](07-facts-and-delivery.md#usage-records)), so a policy can correlate what it allowed with what was charged, including concurrent calls and late reports. A child run carries its root run's identity; creating a new run never yields a fresh allowance.

The built-in distribution installs no admission policy. Its built-in limits are the per-run request limit and the structural capacity limits; it keeps no monetary ledger.

## Trade-offs

- **Best-effort rate limits.** Credential-guessing and upload limits live in Redis. During a Redis outage they are not enforced, which keeps login available at the cost of unenforced attempt limits.
- **Soft budgets.** Recorded-usage budgets checked by `proceed` are soft limits. Concurrent calls can all pass before any of their usage arrives, and late reports can exceed a threshold by more than one call. Serializing the checks alone does not reserve allowance, so no one-call or fixed monetary overshoot bound is promised. Structural limits such as worker slots and inbox capacity are unaffected.
- **Exact schema match.** Requiring the exact head makes an incompatible schema visible immediately, at the cost of previous-build processes turning unready as soon as a migration lands.

## Invariants

- PostgreSQL alone decides run ownership; a lost or failed wakeup delays a due run by at most one scan interval.
- No sweep, claim or delivery holds a database session, row lock or advisory lock across external I/O.
- The worker role never migrates, and no process serves or claims on a schema other than its build's head.
- Duplicate routes, tables, settings sections, roles and provider types, and a second authenticator or admission policy, fail assembly or settings loading.
- Every operational limit is a finite setting or a fixed bound in code, and nested bounds are validated at load.
- Every creation path passes `accept`, and every paid dispatch passes `proceed` with a `call_id` its usage records share.
