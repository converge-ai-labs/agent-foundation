# a13n Service

This package contains the hosted a13n Service executable, its internal async-first storage substrate, and its service-owned relational schema lifecycle. It is a workspace package, not a public SDK or an independently distributed provider library.

The substrate exposes capability-specific interfaces instead of one generic storage facade:

- SQL uses SQLAlchemy's async `AsyncEngine` and `AsyncSession` APIs with PostgreSQL or SQLite.
- Redis-compatible data structures use `redis.asyncio.Redis` with Redis or process-local fakeredis.
- Objects use the small Service-owned `ObjectStore` protocol with S3-compatible or local-directory adapters.
- Local files and deployment-mounted NFS use the same confined `pathlib` and AnyIO helpers.

Backend selection happens once during process startup. A failed network backend never falls back to local state.

## Shared Configuration

Model Providers, Models, Connector Providers, Environment Providers, and Environment Templates support Organization and Workspace ownership. A null `workspace_id` denotes Organization ownership. Organization collections use `/api/v1/organizations/{organization_id}/...`; Workspace configuration lists include local and parent resources. Organization Admin manages shared configuration, while Workspace roles retain their local management and shared-use permissions. Connections, actual Environments, and Runs remain Workspace-owned.

The Host authenticator supplies exactly one credential boundary: `boundary_workspace_id` for Workspace requests, or `boundary_organization_id` for an Organization-scoped human session. Workspace API keys cannot manage Organization resources. Initial migrations define these scopes directly; recreate local databases initialized from older baseline files.

## Model Management

Control-plane and all-in-one roles expose the accepted Model Management API at `/api/v1`. The service includes trusted OpenAI, Anthropic, Gemini, Vertex AI, Azure OpenAI, Bedrock, OpenRouter, Ollama, Alibaba Model Studio, DeepSeek, Moonshot, Zhipu, and generic OpenAI-compatible Provider types. A Workspace can create multiple configured Providers of the same type. Provider-scoped discovery is advisory; an unknown bounded upstream model name remains valid.

`GET /api/v1/model-provider-types` returns definitions with `type`, `configuration_schema`, a separate write-only `credential_schema`, `supported_model_apis`, and `default_model_api`. Provider create, update, and read use `configuration`; the implementation-owned Pydantic model supplies both the schema and server validation. Each saved Model selects one explicit `model_api` and owns its editable `settings`. Capability profiles and limits appear only in discovery and description results.

Provider-scoped `discover-models` returns the complete bounded catalog for client-side search and pagination. `describe-model` accepts an arbitrary upstream ID and optional API, returning a native settings schema even when remote metadata is unavailable. Neither operation persists a Model or proves successful inference. Model testing uses its saved API and settings without an additional selector. See the [Model setup guide](../../docs/a13n-service/models.md) for HTTP examples, parameter validation, and override semantics.

Provider create and update accept a write-only credential value and persist only authenticated ciphertext. Configure an exact 32-byte master key as standard base64 together with its non-secret key identifier:

```bash
A13N_SERVICE_SECRET_MASTER_KEY_BASE64='<base64-encoded-32-byte-key>'
A13N_SERVICE_SECRET_ENCRYPTION_KEY_ID='master-2026-08'
```

The key has no default and is never stored in the database. Provider credentials reuse the managed Secret `aes_256_gcm_v1` primitive without becoming public Secret resources and are decrypted only after the database session closes. A Host can inject `Components.model_connection_tester` when model testing is supplied by another trusted composition.

Model Providers and Models are mutable resources protected by strong `ETag` and `If-Match`; neither has a version or revision. Provider type, Model key, and the Model-to-Provider relationship are immutable. Models are retired with `enabled=false`; the service exposes no copy or hard-delete route. Custom endpoints are limited to Provider types whose schema declares them and are checked against `A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_DOMAINS` and `A13N_SERVICE_MODEL_PRIVATE_ENDPOINT_CIDRS`. Redirects are not followed by built-in management operations.

Agent configuration selects only `model_key` and optional setting overrides. Run acceptance resolves the latest Model and freezes its upstream identity, single calling API, and the top-level merge of Model defaults, Agent settings, and Run overrides. Profile and limits are read-only discovery information; Model resources store only actual request defaults and never editable capability metadata. `SnapshotRunModelResolver` retains the request-selection fields while `LiveProviderResolver` reloads and decrypts current Provider state for every outbound request, including later calls and replacement attempts within the same Run.

### Automatic Model Prices

`worker` and `all` processes enable Pydantic AI's background price updater by default; `control` and `connectivity` do not start it. Bundled prices are available immediately, and download availability is not a readiness dependency. Subsequent Harness builds adopt validated updates automatically, while existing Agents and emitted usage retain their original prices and revision. Downloads run immediately and hourly; failures retain the latest usable data without a service price table or disk cache.

Set `A13N_SERVICE_PRICING_AUTO_UPDATE=false` before startup to disable this process's updater. Stopping or disabling an updater does not clear prices already published in the process. Custom build-time costing policies still take precedence. See [Harness pricing](../../docs/a13n-harness/agents-and-runs.md#keep-prices-current-in-a-host) for snapshot and override semantics.

## External Connectivity

The shared executable exposes Connectivity according to its process role:

- `control` and `all` expose authenticated Account, AccountTarget, Connector Provider, ConnectorConnection, and MCPConnection management below `/api/v1` and run fenced Connector setup and local OAuth-state expiration reconcilers.
- `connectivity` and `all` expose provider-authenticated event delivery at `POST /connectivity/v1/accounts/{account_id}/events`, run durable admission processing, and retain no browser or product API surface.
- `worker` constructs fresh native Ingress and Connector tool groups in process and connects directly to Remote MCP sources. It has no Connectivity management or provider-event routes.

Control and Connectivity replicas share relational and object-storage facts; they do not call a private cross-pod Service API. The `all` role installs the union once. During shutdown readiness fails before new requests receive `503`, and background reconcilers stop under the application lifespan.

Connector Provider types are explicitly registered through `Components.connector_provider_registry`. `GET /api/v1/connector-provider-types` returns safe configuration and credential schemas without upstream requests. Configured accounts use Organization or Workspace collections and `/api/v1/connector-providers/{connector_provider_id}` detail routes. Create accepts `type`, `configuration` (including its endpoint), and separate write-only `credentials`. Type and configuration are immutable; name, credentials, and administrative status retain management-version preconditions. Credentials are stored directly on the ConnectorProvider as one encrypted bundle using the configured master key. Account and MCPConnection credentials follow the same ownership pattern; OAuth sessions own their temporary encrypted setup material. User/Workspace Secrets remain independently managed values, and third-party ConnectorConnection tokens remain with the external integration service.

`POST /api/v1/connector-providers/{connector_provider_id}/discover-connectors` reads the exact account's current directory under `connector_provider.read`. It creates no connections and publishes no tool catalog. Discovery and setup revalidation share a 30-second deadline and bounds of 128 pages, 2,048 directory entries, and 16 MiB across toolkit and auth-config responses. Composio v3.1 combines `/toolkits` with project `/auth_configs` using cursor pagination. Explicit configured allowlists filter the results. Only hosted OAuth supported by both the toolkit and current account is advertised; third-party credential input and upstream secret fields are excluded.

Connection setup selects `connector_provider_id` and `connector_key`. Each operation constructs a fresh Provider runtime and binds the verified external account plus its opaque user correlation before inspection, live tool discovery, execution, or revocation. Construction and close never create or revoke accounts, and close does not dispose the process-owned HTTP client. Composio pins dated versions in tool-list, tool-detail, and execute requests. Worker composition supplies external tools to the Attempt preparation scope. Each Attempt receives one upstream MCP capability per authorized source; credentials and resource authority are rechecked for dispatch.

The registered account adapter follows the [Composio v3.1 reference](https://docs.composio.dev/reference). The service does not expose an unfenced public tool-execute endpoint.

[OOMOL OpenConnector](https://github.com/oomol-lab/open-connector) is a registered Connector Provider at `https://connector.oomol.com`. Service initiates hosted authorization with a Project key, verifies the resulting account against the Workspace correlation, and executes tools using that exact account ID. A separate catalog key reads Provider and Action definitions. See the [external tools guide](../../docs/a13n-service/external-tools.md) for configuration and connection setup.

For Connector and MCP OAuth callback flows, set `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN` to the exact externally reachable control-plane origin. Noninteractive connection management and OpenConnector polling do not require it. HTTP origins and private endpoint destinations are denied unless explicitly allowed by `A13N_SERVICE_CONNECTIVITY_HTTP_ORIGINS`, `A13N_SERVICE_CONNECTIVITY_PRIVATE_ENDPOINT_DOMAINS`, or `A13N_SERVICE_CONNECTIVITY_PRIVATE_ENDPOINT_CIDRS`. Provider source-origin allowlists use `A13N_SERVICE_CONNECTIVITY_PROVIDER_ORIGINS`; provider signatures or tokens remain mandatory.

Live tool discovery is bounded to 16 MiB, 128 pages, and 2,048 tools; results are bounded to 1 MiB. Agent and Run configuration use connection-selection lists with optional exact tool names and `defer_loading`. External schemas are discovered per Attempt and are never persisted as catalogs or Run snapshots. See [External tools](../../docs/a13n-service/external-tools.md) for selection semantics and host integration. Initial migrations create the current schema directly; recreate local databases initialized from the previous schema and reauthor Agent selections.

Accounts embed reception, disabled by default, with a default Agent and same-Workspace execution Service Account required when enabled. Exact `/application-accounts/{account_id}/targets` configure object-specific Agent and narrow overrides. The canonical Run/Steer bridge is required at startup. Batch receipts, initial Thread binding, and the input frequency clock commit atomically. Raw webhook bodies are not retained; pending normalized input remains until terminal acceptance or rejection, and terminal identities expire through their deduplication horizon.

OAuth refresh runs on demand under a cross-Pod credential-generation lease. Interrupted exchanges require reauthorization. Connector and MCP deletion invalidate locally before one bounded remote cleanup attempt and return a persisted truthful receipt; command replay never repeats remote cleanup and no revoke/cleanup jobs remain.

Until the canonical Service input bridge is supplied by the application composition, eligible provider events are still durably acknowledged and remain `pending`; the Connectivity process stays ready in this deliberately degraded mode. This package does not create another Agent execution path or dispatch Connector/MCP tools directly from model input.

## Asset Management

Control-plane and all-in-one roles expose immutable Workspace Assets below `/api/v1`. Uploads accept exactly one `application/octet-stream` body plus `filename`, optional `media_type`, and `Idempotency-Key`; metadata and content reads never expose object keys or public object URLs. Every distinct publication gets a new `ast` ID, while replay of the same canonical request and key returns the original Asset for 24 hours. Delete immediately tombstones the Asset and commits an `asset_content_cleanup` Outbox intent; the control-plane reconciler removes the derived object asynchronously without restoring logical access on failure.

`A13N_SERVICE_ASSET_MAX_SIZE_BYTES` is the positive finite bound applied while streaming uploads and defaults to 100 MiB. Private staging uses `A13N_SERVICE_FILESYSTEM_ROOT`; object bytes use the selected object backend. Cleanup behavior can be operationally tuned with `A13N_SERVICE_ASSET_CLEANUP_POLL_INTERVAL_SECONDS`, `A13N_SERVICE_ASSET_CLEANUP_LEASE_SECONDS`, and `A13N_SERVICE_ASSET_CLEANUP_MAX_ATTEMPTS`. These settings do not change Asset identity, retention authority, or authorization semantics.

## Environment Capacity and Maintenance

Workers use one Environment lifecycle and maintenance loop. Configure all workers consistently:

| Variable                                                | Default | Meaning                                                                                          |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------ |
| `A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE`    | `1000`  | Prepared managed targets; stopped and unresolved targets count, deleted targets release capacity |
| `A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE`     | `100`   | Distinct Environments with running Runs that acquired use; shared Runs count once                |
| `A13N_SERVICE_ENVIRONMENT_MAINTENANCE_BATCH_SIZE`       | `64`    | Due records read per batch; all batches drain before the next poll                               |
| `A13N_SERVICE_ENVIRONMENT_MAINTENANCE_CONCURRENCY`      | `4`     | Concurrent maintenance operations per worker                                                     |
| `A13N_SERVICE_ENVIRONMENT_MAINTENANCE_INTERVAL_SECONDS` | `5`     | Poll interval after each pass                                                                    |
| `A13N_SERVICE_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS`    | `60`    | Provider operation timeout                                                                       |

Logical allocation does not start a target or consume capacity. First use atomically reserves capacity before Provider I/O; exhausted admission returns `environment_capacity_exceeded`. Limits never evict existing targets. Maintenance persists observed expiry, schedules retention and renewal deadlines, and backs off failures for 30 seconds. Provider latency and due volume can delay maintenance beyond one poll interval. Estimate a due burst as `due_targets * average_operation_seconds / concurrency`, plus database and polling time. A bounded queue keeps available workers processing later batches while another target is slow.

The initial Environment schema includes observed expiry and a Workspace/ownership/status index for admission counts. Initialize a fresh development database for this schema; superseded development schemas and target-identity encodings have no upgrade path.

## Hosted Subagents

Set `subagent_mode` in the Agent configuration to `inline` (the default) or `async`. Both use the selected named `subagents` graph. Inline descendants execute inside the parent Attempt and borrow its Environment. Async delegation creates an independently scheduled child Run; its own configured mode governs further delegation. Each child uses its own frozen Model settings, Skills, Plugins, and Connector/MCP selections. Native ingress contexts are not inherited by children.

Parent Run acceptance freezes the complete child execution graph. Later Model defaults or Skill heads do not change that accepted graph; current resource authorization and credentials are still checked during execution. Retry and recovery reuse the accepted snapshots. The Attempt closes its tools, input sources, and Environment object before releasing authority or committing its outcome.

The `control` and `all` roles publish sealed child results, request configured child cancellation, and accept eligible parent successors. `A13N_SERVICE_SUBAGENT_RECONCILE_POLL_INTERVAL_SECONDS` defaults to 1 second; `A13N_SERVICE_SUBAGENT_RECONCILE_DRAIN_SECONDS` defaults to 30 seconds. Run at least one control-capable process for this reconciliation.

## Observability and Trace Query

Service tracing is enabled by default with content set to `none`. The Service creates one parentless `a13n.service.run_attempt` trace root for each durable RunAttempt, admits only the a13n Service, Harness, and Pydantic AI instrumentation scopes, and passes the same `none`, `standard`, or `full` content value to Harness. The Worker execution domain supplies the durable correlation and owns the exact points at which the root and its `a13n.service.reconstruct`, `a13n.service.environment.prepare`, and `a13n.service.persist` children start and finish.

Export uses standard OpenTelemetry configuration only. With no exporter, structural instrumentation remains active but sends no telemetry. A direct OTLP deployment can use, for example:

```bash
A13N_SERVICE_OBSERVABILITY_TRACING=true
A13N_SERVICE_OBSERVABILITY_TRACE_CONTENT=none
OTEL_TRACES_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='https://collector.example.com/v1/traces'
OTEL_EXPORTER_OTLP_TRACES_HEADERS='Authorization=Bearer <deployment-secret>'
```

When the OTLP destination is Langfuse v4 itself, use its documented HTTP endpoint and include `x-langfuse-ingestion-version=4` in the standard exporter headers; Langfuse does not currently accept OTLP/gRPC:

```bash
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT='https://langfuse.example.com/api/public/otel'
OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic <deployment-secret>,x-langfuse-ingestion-version=4'
```

The control-plane Trace Query API is independent of export and is disabled by default. The OSS adapter reads Langfuse v4 through its documented Observations API v2; it does not access ClickHouse or persist another trace copy. Configure its separate read credentials as deployment secrets:

```bash
A13N_SERVICE_OBSERVABILITY_QUERY_PROVIDER=langfuse
A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_BASE_URL='https://langfuse.example.com'
A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_PUBLIC_KEY='pk-lf-...'
A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_SECRET_KEY='sk-lf-...'
```

Product distributions can register additional trusted adapters through `TraceQueryProviderRegistry`; runtime configuration selects only one key already fixed into that artifact. Duplicate keys and selections absent from the artifact fail startup.

`GET /api/v1/workspaces/{workspace_id}/traces` and `GET /api/v1/workspaces/{workspace_id}/traces/{trace_id}` remain present when querying is disabled and return the shared safe unavailable error. Backend correlation is never authorization evidence: a distribution must inject `Components.trace_access_authorizer` backed by its authoritative RunAttempt domain before results can be returned. The current repository does not yet contain that RunAttempt persistence domain, so configured querying fails closed until the owning implementation is composed.

Run `make langfuse-test` to start the repository's local Langfuse v4 stack and verify a real standard-OTLP write followed by input search and Observations v2 list/detail reads. The ordinary Python test suite keeps this integration test skipped so it does not require Docker.

## Runtime

`Settings` owns the `A13N_SERVICE_*` environment contract and maps it to the frozen `StorageSettings` model. The storage package accepts typed configuration and does not read process environment variables itself. `a13n-service serve` constructs all selected providers once in FastAPI lifespan, publishes one typed `ProcessRuntime` on `app.state.runtime`, and closes its shared, Control-plane, Worker, and Connectivity resources during supervised shutdown. Storage is available through `runtime.shared.storage`; role-specific capabilities are present only when that process owns them.

The default service profile keeps the existing PostgreSQL and Redis endpoints and uses separate local roots for objects and files. Set `A13N_SERVICE_OBJECT_BACKEND=s3` and `A13N_SERVICE_OBJECT_BUCKET` for a multi-process deployment; the local object adapter supports only one writing process. `A13N_SERVICE_FILESYSTEM_ROOT` may be an ordinary local directory or an NFS mount prepared by deployment.

```python
from pathlib import Path

from a13n_service.storage import StorageSettings, open_storage

settings = StorageSettings.model_validate(
    {
        "database": {"backend": "sqlite", "path": Path("var/a13n-service.sqlite3")},
        "redis": {"backend": "memory"},
        "objects": {"backend": "local", "root": Path("var/objects")},
        "filesystem": {"root": Path("var/files")},
    }
)

async with open_storage(settings) as storage:
    # Inject `storage` into application-owned services here.
    ...
```

`open_storage()` constructs one engine, session factory, Redis client, object store, and filesystem boundary. It checks required capabilities before yielding and closes all resources in reverse order.

A network deployment selects the corresponding backends without changing consumer code:

```python
network_settings = StorageSettings.model_validate(
    {
        "database": {
            "backend": "postgresql",
            "url": "postgresql://a13n_service:password@postgres/a13n_service",
        },
        "redis": {"backend": "redis", "url": "redis://redis:6379/0"},
        "objects": {
            "backend": "s3",
            "bucket": "a13n-service-objects",
            "region": "us-east-1",
        },
        "filesystem": {"root": Path("/mnt/a13n-service-files")},
    }
)
```

The S3 client uses the standard AWS credential chain. Set `endpoint_url` and `force_path_style` only for a compatible non-AWS endpoint. The deployment mounts NFS at the configured filesystem root before the process starts.

## Relational Usage

Consumers use SQLAlchemy directly. Generic storage does not define `get`, `insert`, or `do` wrappers.

```python
from sqlalchemy import select

from a13n_service.storage import transaction

async with transaction(storage.sessions) as session:
    result = await session.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()
```

Each operation gets a short session. Never retain a session or transaction across external I/O, agent execution, sleeps, background work, or a streaming response.

PostgreSQL is the distributed-service backend. SQLite is intended for a single-process, zero-service profile and must not be placed on NFS.

## Relational Schema and Migrations

The OSS schema uses one linear migration chain. Its 13-revision domain baseline runs from `01929f3846a5` (identity, secrets, and durable operations) to `023eff74515b` (lifecycle events), and later feature revisions extend that single-head graph. Each revision owns its tables, indexes, constraints, and reverse-order downgrade; cyclic foreign keys and triggers stay with their owning domain. The baseline preserves terminal Run/RunAttempt and lifecycle-fact immutability, Hook revision guards, and deferred cyclic foreign keys. Migration tests compare the PostgreSQL schema with current metadata and verify both SQLite and PostgreSQL upgrade/downgrade behavior.

Model migrations preserve invocation identity and actual settings while removing stored capability claims. Validate schema changes against the combined branch and current base so independently developed revisions cannot leave a multi-head graph unnoticed.

Generic relational storage and service schema ownership are deliberately separate. For the OSS distribution in this repository:

- `storage/relational.py` constructs async engines and short sessions for application I/O.
- `database/metadata.py` provides the explicit common registry selected by the OSS distribution descriptor.
- `database/migrations/` provides the OSS revision location assembled into one final Alembic graph.
- `database/migration.py` owns the dedicated synchronous migration connection, bounded PostgreSQL advisory locking, and Alembic invocation.

The Alembic environment does not read process settings or create an engine. The runner supplies one validated connection and the artifact distribution's resolved metadata and revision graph, so CLI settings, composition, lock policy, and schema comparison have distinct owners. Another product distribution adds reviewed model and revision contributions through its own fixed build descriptor rather than package discovery or runtime edition selection.

Select the database backend, then use the stable service CLI or repository commands:

```bash
A13N_SERVICE_DATABASE_BACKEND=postgresql
A13N_SERVICE_DATABASE_URL=postgresql+psycopg://a13n_service:a13n_service@127.0.0.1:5432/a13n_service

make db-upgrade
make db-current
make db-check
make db-history
```

The corresponding executable commands are `a13n-service db upgrade`, `a13n-service db current --check-heads`, `a13n-service db history`, and `a13n-service db migrate "description"`. There is one process CLI; migration implementation remains in `database/migration.py` rather than introducing a second database-only settings or command layer.

For the zero-service profile, set `A13N_SERVICE_DATABASE_BACKEND=sqlite` and `A13N_SERVICE_DATABASE_SQLITE_PATH=var/a13n-service.sqlite3`. The same accepted history is applied to both backends. A domain requiring PostgreSQL-only schema behavior must reject SQLite explicitly.

### Add an ORM Model

1. Define the model beside its owning domain using `a13n_service.database.Base`.
2. Import that domain model module explicitly in `database/metadata.py`; there is no package scanning or plugin discovery.
3. Run `make db-migrate msg="describe the schema change"`. The target rebuilds accepted history in a disposable PostgreSQL database before autogeneration.
4. Review the generated revision for names, constraints, data loss, lock behavior, rolling compatibility, interruption safety, and downgrade or forward repair.
5. Run the database tests on SQLite and PostgreSQL plus `make db-check` against an upgraded database.

Do not create revision files by hand and do not use runtime `metadata.create_all()` as schema bootstrap. Application request paths use async SQLAlchemy; migrations use a separate synchronous `NullPool` connection because they run before traffic or in a dedicated deployment job.

## Redis Usage

The injected async redis-py client exposes strings, hashes, lists, sets, sorted sets, Streams, pipelines, transactions, and Pub/Sub without local/network branches.

```python
await storage.redis.hset(b"run:1", mapping={b"status": b"running"})
await storage.redis.rpush(b"run:1:steps", b"step-1")
await storage.redis.xadd(b"run-events", {b"payload": payload})
```

Response decoding is disabled for binary safety. The fakeredis backend is process-local and non-durable; use a real Redis service for multiple processes, persistence, modules, or exact server failure behavior.

### Run Stream publication and upgrades

Workers confirm an atomic publication activation before Environment preparation or Harness output. Each Attempt uses its PostgreSQL `attempt_number` to fence observations and projection completion. A successor activation appends its committed `run_attempt.leased` event and then one `run.recovery` event before accepting its observations. Delayed historical lifecycle facts use a separate trusted projection path.

Run Stream scripts require Redis Streams, Lua scripting, and `INFO server` permission. Use one writable primary with `maxmemory-policy=noeviction`; active Streams and their activation/deduplication metadata have no expiry. Provision memory for active-Run deduplication evidence as well as the bounded event history. Both keys use one Redis Cluster hash tag, but the Service client currently connects to a primary endpoint rather than discovering Cluster slots.

The publication script compares the Redis primary's process incarnation with the Run's recorded incarnation. A primary restart or failover, missing keys, or inconsistent stream boundaries stops publication and makes live replay unavailable. Existing Runs are not transparently reopened after that loss; durable Run outcomes and already published replay objects remain authoritative for their own purposes. A process-local memory backend has the same fail-closed behavior across process restarts. Do not restore, roll back, rename, or flush publication keys on a serving primary. Administrative restoration, split-brain routing, and same-process metadata rollback require stopping and fencing all writers first; they are not supported live recovery mechanisms.

An interrupted activation is retried with the same event identities and Redis Stream entry IDs. Lifecycle repair can finish it after the Worker disappears only while the Attempt remains current with a valid lease. An expired or replaced Attempt is never reactivated; an unresolved partial operation eventually makes retained replay unavailable.

The terminal lifecycle projection retires unavailable Streams even after primary incarnation changes or key loss. It marks history incomplete and sets a fixed retention deadline on surviving keys. If Redis cleanup fails, that durable projection keeps retrying cleanup after its publication retry budget is exhausted, without extending the deadline or reopening publication. Run a control-capable process until terminal cleanup drains.

Drain every old Worker and lifecycle projector, revoke their Redis access, and replace them before enabling this version's publishers. A mixed rollout with an unfenced binary does not provide publication fencing. Legacy live Streams without activation metadata are unavailable to the new publisher; finish active Runs before the upgrade. Retained replay objects keep their existing format.

Native clients receive `run.recovery` with the normal Run Stream cursor. Hosted AG-UI clients receive `CUSTOM` named `a13n.service.run_recovery`, with `schema_version`, a stable `event_id`, their external `runId`, and `reason`. Clients can stop unfinished text or tool-argument accumulation, preserve completed results, and retain, mark, or remove partial content according to product policy. Apply the boundary idempotently by event ID and cursor. Recovery does not prove that an external tool failed or is safe to retry. The event reports a publisher switch, not successful execution or a new Run. Reconnect and sealed replay preserve its original position; a cursor beyond it does not emit it again.

## Object Usage

Object keys are opaque names rather than filesystem paths. `put` supports unconditional, create-only, and expected-version publication.

```python
from a13n_service.storage import ByteRange, ObjectConflict

created = await storage.objects.put(
    "artifacts/result.json",
    payload,
    content_type="application/json",
    metadata={"trace": trace_id},
    if_none_match=True,
)

async with storage.objects.open("artifacts/result.json", byte_range=ByteRange(0, 64)) as reader:
    prefix = b"".join([chunk async for chunk in reader])

try:
    updated = await storage.objects.put(
        "artifacts/result.json",
        new_payload,
        if_match=created.version,
    )
except ObjectConflict:
    # Re-read and make a new domain decision.
    ...
```

S3 endpoints must support AWS-compatible conditional writes and deletes, range reads, head requests, and ordered `ListObjectsV2` pagination. Startup rejects endpoints that silently ignore these conditions. The local adapter provides the common behavior for exactly one writing service process.

Each successful object publication receives a fresh opaque version, including an identical-body overwrite. The S3 adapter stores a private nonce-bearing envelope and exposes the original body, size, metadata, and ranges through `ObjectStore`; it also reads existing unframed S3 objects. Access service objects through `ObjectStore`, rather than treating physical bucket bytes as application payloads.

When upgrading from a release without this S3 envelope, drain old writers and replace all object readers before enabling new writes. Old binaries cannot read the new physical encoding. The outcome-recovery migration also permits successful Attempts without Harness entry; deploy compatible readers before enabling those Workers. Downgrading that constraint intentionally fails if such Attempts exist, so retain the expanded schema and roll forward in that case.

Metadata keys are normalized to lowercase and, like S3 REST metadata, keys and values must be ASCII. Keys must also be valid HTTP field names. Returned metadata mappings are immutable.

## Filesystem Usage

Deployment mounts NFS before process startup. Application code receives the mounted root and uses the same helpers as a local directory; there is no Python NFS provider.

```python
import anyio

from a13n_service.storage.filesystem import atomic_write, resolve_under_root

destination = await resolve_under_root(
    storage.files_root,
    "exports/result.json",
    limiter=storage.file_limiter,
)
await atomic_write(destination, chunks, limiter=storage.file_limiter)

async with await anyio.open_file(destination, "rb") as file:
    prefix = await file.read(64 * 1024)
```

Confined resolution rejects absolute paths, parent traversal, and symlink escape. Atomic replacement is guaranteed only within one filesystem.

## Verification

Run the infrastructure suites and package checks from the repository root:

```bash
uv run --package a13n-service pytest packages/a13n-service/tests/storage packages/a13n-service/tests/database -q
make lint
make typecheck
uv build --package a13n-service
```

Container-owned integration tests exercise PostgreSQL, Redis, and S3 HTTP behavior. The S3 startup-probe test also demonstrates that an endpoint missing required conditional-delete semantics is rejected rather than silently accepted.

## HTTP retries and accepted receipts

Ordinary retryable management commands accept `Idempotency-Key` values containing 1–512 visible ASCII bytes. Reuse the same key and semantic request after a lost response: for 24 hours from the original commit, an authorized replay returns the original accepted result before checking mutable version preconditions. A changed request with the same scoped key conflicts. Replaying does not extend expiry; after expiry, inspect the resource and apply its current preconditions before deciding to repeat a mutation. AG-UI/A2A external IDs and durable execution identities have their own retention contracts.

MCP mutation responses preserve the accepted connection snapshot. Read the connection to observe subsequent discovery readiness. Run overrides select managed resources and reject direct credential fields; the owning resource resolves its current credentials.

## Installed Harness Plugins

Package custom plugins and their dependencies into the Worker image, then select their installed `a13n_harness.plugins` entry points with `A13N_SERVICE_PLUGIN_KEYS='["support.audit"]'`. Only `worker` and `all` load this catalog; an invalid selection prevents their readiness. Control and Connectivity need no business plugins and do not load the catalog.

Agent configuration selects an instance, key, and credential-free configuration:

```json
{
  "plugins": [
    {"instance_name": "audit", "plugin_key": "support.audit", "config": {}}
  ]
}
```

Update code by building and rolling out a new Worker image; Control and Connectivity stay unchanged. Supply compatible Worker capacity before submitting configuration that needs the new code. Admission preserves authored JSON; missing factories and invalid business configuration fail during Worker preparation. The first Worker durably freezes normalized configuration before execution. Existing prepared Runs keep that configuration and checkpoint state, while each Attempt records the actual Worker build. Compatible new code may resume them; incompatible configuration or state requires migration or completion with compatible capacity before replacement. Service has no runtime Wheel upload, dependency installation, version activation, or plugin subprocess.

See [Installed Harness Plugins](../../spec/a13n-service/36-installed-harness-plugins.md) for the compatibility and trust contract.
