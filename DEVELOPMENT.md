# Development Standards

This guide defines code quality principles for all repository code and engineering conventions for the components they concern. Service rules apply within their stated boundaries. Product semantics and subsystem ownership belong in `spec/`, contribution workflow in [CONTRIBUTING.md](CONTRIBUTING.md), and component setup and commands in package READMEs. Stable repository workflows and safety-critical settings are named here only when they are part of the engineering contract.

## Code Quality and Design

Good code expresses the problem clearly and makes behavior and change easy to follow. Apply these principles to features, bug fixes, refactoring, and reviews while meeting required capabilities, reliability, and performance:

- Use consistent domain terms and clear responsibilities. Give shared rules one owner, preserving real lifecycle, protocol, and security differences rather than abstracting merely similar code.
- Prefer direct flows and cohesive modules. Make interfaces predictable and state ownership, side effects, resource lifetimes, and failure handling easy to trace.
- Justify abstractions, options, and extra paths with current needs. Reduce what maintainers must understand and change together; line counts and layer counts alone do not establish quality.
- Explain necessary concepts and prerequisites. Keep common workflows understandable without first learning unrelated mechanisms or exceptional cases; use realistic tasks to assess ease of use and change.
- Fix faulty rules at their owner, check affected callers, and update related contracts, tests, and documentation. Remove artifacts that no longer serve a requirement, keeping unrelated cleanup outside the task.

Consider runtime, recovery, operational, and maintenance costs. Support performance trade-offs with measurements or an explicit capacity model. Match explanation and validation to the change; routine fixes do not need a separate design exercise.

## Frontend Tests

Across Console, Harness UI, and shared UI, default to **logic tests plus core UI flows**.

- Test business rules and input combinations through the production functions in Node. Use narrow imports and verify the test environment; file extensions alone do not select it in every package.
- Keep UI tests for core user journeys and failures that require interaction: request wiring, visible errors, permissions, credential clearing, recovery, navigation, and keyboard behavior.
- Test shared behavior at its owner. Avoid repeating full UI flows for equivalent variants or adding standalone tests for incidental copy and menu inventories.
- When simplifying, identify the surviving coverage for each required behavior. Extract cohesive production logic shared by the UI and tests; do not duplicate implementations or add abstractions solely for tests.
- Wait for observable completion instead of fixed sleeps unless elapsed time is itself under test. Validate with the [required checks](CONTRIBUTING.md#local-validation); measure performance over the same scope, including replacement tests.

## Service Shape

`a13n-service` ships one package and container image with two independently deployable roles and their all-in-one composition:

- `all`: control and worker capabilities in one process;
- `control`: APIs, maintenance and delivery;
- `worker`: claims and executes runs through the Harness.

A role is a process ownership and scaling boundary, not a separate product, schema, organization, or authorization boundary. Every background loop must have one explicit owning role, and overlap during rolling deployment must be safe through durable leases, fencing, or idempotency.

## Application Structure

Organize business code by feature and add layers only for a real capability; do not prebuild global `controllers`, `dto`, `managers`, generic repositories, or abstract unit-of-work frameworks.

- FastAPI routers are thin transport adapters. Keep Pydantic request and response types beside the owning feature API; routers parse the request, call one use case, and shape its typed result. Use cases authorize, so tools, sweeps, and extensions that call them get the same checks as HTTP.
- Application services own use-case orchestration and short transaction boundaries. They do not import FastAPI or encode HTTP status.
- A function that takes a session belongs to its caller's transaction: it may flush and never commits. ORM rows stay inside the persistence boundary and are not API responses or Harness contracts.
- Durable asynchronous lifecycles use idempotent sweeps and fenced attempts. Model, tool, Redis, and stream waits happen outside database transactions.
- Process-role wiring selects routers and bounded background work; roles share the same domain models. [Runtime composition](spec/a13n-service/09-runtime.md) owns their business contributions.

### Naming

Use the package and module hierarchy as a namespace instead of repeating it in every identifier.

- Name feature packages after precise domain nouns, such as `agents`, `assets`, `models`, `secrets`, and `skills`. Do not append generic ownership words such as `_management`, `_manager`, `_service`, or `_system` to a feature namespace.
- Name a type for what it represents. Do not prefix it with the repository, distribution, service, or containing feature name merely to provide context. Retain a qualifier such as `Workspace`, `Run`, or `Environment` only when it distinguishes real concepts at the same boundary.
- Domain suffixes keep the meanings defined by the [data conventions](spec/data-conventions.md#public-and-internal-naming), and Service row, read and configuration types follow the [Service naming rules](spec/a13n-service/02-layout.md#naming-rules). Do not add a suffix only to make a name longer or more architectural.
- Application `Service`, `Resolver`, `Factory`, and `Preparer` types must describe one cohesive role that is not already clear from a function. Avoid generic `Manager`, `Helper`, `Common`, and `Utils` abstractions.
- Python refactors do not rename stable wire fields, error codes, event names, table names, indexes, or migration history merely to mirror an internal identifier.

Formatters and general-purpose naming rules can enforce syntax and casing, but they cannot decide whether a qualifier carries domain meaning. Semantic naming clarity remains a design and review responsibility.

## HTTP Namespace

Product-facing HTTP APIs use the `/api` namespace. Keep OpenAPI schemas and interactive API documentation under the same prefix. Individual resource layouts remain owned by their API contracts; the prefix is not permission to introduce an unversioned compatibility promise for every implementation route. Protocol daemons such as `a13n-envd` retain their owning transport contracts rather than inheriting this product-API convention.

Operational liveness and readiness probes use explicit paths such as `/healthz` and `/readyz` outside `/api`. They expose only bounded process and dependency state and are not product resources. Unknown product API paths return API errors rather than an HTML application response.

a13n Service exposes APIs and operational probes without hosting browser assets. Worker-only roles do not expose product APIs. Browser clients follow the shared ingress Origin, cookie, and CSRF contract; local tooling does not justify permissive CORS.

## Generated Code and Static Analysis

Repository-wide static analysis uses a moderate profile focused on actionable correctness and maintainability signals rather than enabling every optional strict or opinionated rule. Tighten or relax that profile when recurring evidence justifies the change, not to silence one isolated finding.

Generated output must compile, type-check where applicable, and pass its contract, round-trip, and integration tests. Style-oriented lint rules can be disabled at the narrow generated-file or generated-module boundary when satisfying them would add renderer complexity without improving correctness. The generator, build integration, and all handwritten code remain under the normal repository checks. Do not weaken repository-wide checks merely to accommodate mechanical output, and do not complicate a generator solely to reproduce hand-written style.

## Async and Process Lifespan

Service I/O is async-first. Use async database, `httpx2`, Redis, object-store, and subprocess clients. Do not introduce `httpx` or another general HTTP client alongside `httpx2`. Isolate unavoidable bounded blocking work with `anyio.to_thread.run_sync`; never block the event loop or call `asyncio.run()` from an active async path.

Create process-wide engines and clients during FastAPI lifespan, store them in explicit application state, and close them during shutdown. Module import must not open connections, start tasks, or configure logging. External calls have explicit timeouts; retries are bounded, observable, and restricted to retry-safe operations. Preserve cancellation and re-raise `CancelledError` after bounded cleanup.

## Database Sessions and Transactions

All service code obtains the canonical engine and session factory from `a13n_service.infra.db.Storage` and opens sessions only through the canonical `short_session(storage)` and `transaction(storage)` scopes in `a13n_service.infra.db`. Do not construct local engines or session makers.

An `AsyncSession` is a mutable unit of work. Never share it across concurrent tasks or store it in a singleton. Keep each transaction around one small database operation, and do not hold a session, connection, transaction, or lock while waiting for:

- model, tool, or agent execution;
- HTTP, Redis, object-store, or environment I/O;
- a sleep, retry, long poll, or another worker;
- SSE, WebSocket, file, or model-output streaming;
- a FastAPI background task.

Read durable state in one short session, close it, perform external work, then open a new short transaction to publish the result. Revalidate ownership or version fields when state may have changed. Functions that take a session may flush; the use case that opened the transaction owns commit. Return typed values or identifiers rather than live ORM entities, and load relationships explicitly so serialization cannot trigger implicit async I/O. The canonical engine bounds PostgreSQL connection and statement time, readiness uses a shorter application deadline, and shielded rollback/close cleanup is bounded.

### FastAPI streaming footgun

FastAPI yield-dependency cleanup timing has changed across releases. A streaming route must therefore never receive a yielded database session, including indirectly through authentication.

Complete authentication, authorization, and initial reads in a short session that closes before constructing the response. Pass immutable values into the generator. If the stream needs database state, open a fresh short session for each bounded operation. Background tasks also create their own session from the factory. Release subscriptions and tasks in `finally`, and test that an open stream does not retain a pool connection.

## SQL Operation Design

Minimize database work across the complete application operation using the short-read, external-preparation, short-commit flow above. Simple database-only operations can stay in one transaction. Preserve the owning specification's observation and concurrency boundaries.

- **Observe authority once.** Follow the [tenancy authorization contract](spec/a13n-service/03-tenancy.md#authorization): read each principal, workspace, and credential on first use, then reuse detached facts while still checking each action, target, and credential boundary. Take tenancy row locks only where that contract requires them: administrative changes serialize on the organization and recheck the actor's grants inside the changing transaction. Later revocation affects the next operation; requests, polls, and independent background items do not share an operation snapshot. A running attempt rechecks its frozen authority separately ([claim, heartbeat and authority](spec/a13n-service/05-runs.md#claim-heartbeat-and-authority)).
- **Select configuration once.** For [Run acceptance](spec/a13n-service/05-runs.md), validate and freeze the complete selection, including descendants, using ordinary reads. Do not reread defaults or rebuild prepared state because of later edits. No shared database timestamp is required. Management serialization and runtime eligibility checks retain their own contracts.
- **Keep commit-time arbitration.** Recheck required state, versions, source integrity, capacity, leases, generations, and idempotency. Keep command replay preflight read-only; arbitrate evidence with the business mutation and roll back tentative writes before replaying a concurrent winner.
- **Pass known facts forward.** Reuse existing IDs, selections, and returned values. Prefer existing parameters or small cohesive types; avoid giant Context objects and long forwarding chains. Reuse does not replace authoritative scope or relation checks.
- **Batch repeated work.** Deduplicate inputs and use bounded set reads and writes. Batch recurring renewals when justified, preserving per-item authority, deadlines, cancellation, conflicts, and accounting.
- **Combine mutations and narrow locks.** Prefer conditional `UPDATE ... RETURNING` when it expresses the complete invariant. For claims that allow skipping contention, such as run attempts and outbox deliveries, use bounded `FOR UPDATE SKIP LOCKED` with an update and returned claims; reuse canonical helpers. Preserve necessary locks, ordering, and fences, and evaluate lease expiry after contention. Commit before external execution or reporting success.
- **Verify the reduction.** Compare equivalent scenarios with an explicit SQL-counting method under the [existing validation workflow](CONTRIBUTING.md#local-validation). Pair count assertions with relevant concurrency and rollback tests. Fewer statements must preserve correctness and must not introduce unbounded reads, longer transactions, or unnecessary abstractions.

## Migrations

Each a13n Service build artifact supplies one final metadata registry and ordered migration graph through its fixed distribution descriptor. Domains own model and revision meaning; the distribution explicitly assembles their contributions; a13n Service owns one resolved registry, one graph, and at most one head for that artifact. Package scanning, import side effects, organization state, and runtime edition selection never change migration contents.

The OSS artifact resolves its registry from `a13n_service.distribution.OSS` and its service revision location. A private EE or Cloud artifact adds reviewed model and revision contributions through its own fixed descriptor before invoking the same generator and runner contract. Generation, current-head verification, migration application, and readiness must consume the same resolved composition.

Use the repository workflow rather than creating files manually:

```bash
make db-migrate msg="describe the schema change"
```

For this repository, the command selects the OSS artifact descriptor, starts local PostgreSQL if needed, creates a disposable database, replays its complete history, autogenerates the model diff against its final metadata, formats the revision, and drops the database. The owning repository for another distribution invokes the same workflow with that distribution's fixed descriptor. This prevents a developer's normal database or ambient package set from hiding a missing migration. File names use `YYYYMMDD_<revision>_<slug>.py` and the final graph has at most one head; a deliberately reviewed merge revision reconciles concurrent branches before release.

Autogenerate is only a draft. Review names, constraints, server defaults, nullability, indexes, data loss, downgrade behavior, lock level, scans or rewrites, old/new rolling compatibility, and interruption safety. Prefer additive expand-and-contract changes. Put large backfills in bounded restartable jobs rather than startup migrations, and prefer application rollback or forward repair over destructive schema downgrade.

### Auto migration and locking

The shared image enables auto migration by default for `all` and `control`, with PostgreSQL advisory locking serializing concurrent rollout replicas. Deployments that use a dedicated singleton migration job disable replica auto migration. The `worker` role never migrates; a non-owner checks the composed schema head and fails closed when schema is incompatible.

PostgreSQL migrations use a dedicated synchronous `NullPool` connection and a service-scoped session advisory lock. The same connection holds the lock across revision inspection, transactional DDL, reviewed autocommit blocks, and stamping. Advisory-lock waiting temporarily uses `lock_timeout=0` and its own bounded `statement_timeout`; after acquisition, the normal short DDL lock timeout is restored. This keeps replica serialization independent from table-lock safety.

| Setting                                             | Default | Reason                                              |
| --------------------------------------------------- | ------- | --------------------------------------------------- |
| `A13N_DATABASE__MIGRATION_ADVISORY_LOCK_TIMEOUT`    | `900`   | Never wait forever for another migration runner     |
| `A13N_DATABASE__MIGRATION_LOCK_TIMEOUT`             | `3`     | Fail quickly when application traffic blocks DDL    |
| `A13N_DATABASE__MIGRATION_STATEMENT_TIMEOUT`        | `900`   | Bound each migration statement                      |
| `A13N_DATABASE__MIGRATION_IDLE_TRANSACTION_TIMEOUT` | `30`    | Prevent abandoned transactions from retaining locks |

These values apply only to migration connections. Override them only for a reviewed migration plan. A timeout stops startup; do not retry in a tight loop or stamp past failed work. The advisory lock serializes runners only—it does not pause traffic or make incompatible DDL safe.

## Logging

Configure Python logging once in the executable before Uvicorn or a worker starts. Libraries only obtain namespaced loggers through `a13n-logging`. Use Rich-backed `pretty` output locally and structured `json` output in deployments, writing to stdout or stderr.

Prefer stable event names and structured fields. Include service, role, build version, request or trace ID, and applicable session, thread, run, and attempt IDs. Log exceptions with stack traces at the boundary that handles them. Never log credentials, authorization headers, password-bearing URLs, cookies, raw prompts, model output, tool payloads, or uploaded content by default.

## Container Image

Build one reproducible multi-stage image from `uv.lock` for all roles. Run as non-root, keep credentials and environment configuration outside the image, use an init process when needed, and `exec` the final command so signals propagate. The runtime image installs the platform CA bundle, verifies `/etc/ssl/certs/ca-certificates.crt` at build time, and sets `SSL_CERT_FILE` to that path so `httpx2` uses a stable complete trust store instead of mutating a shared `truststore` OpenSSL context under concurrency. Do not remove or override it unless the replacement contains the deployment's complete CA set. Liveness reports process health; readiness verifies dependencies and schema compatibility. Stop accepting new work before draining or relinquishing ownership.

Image changes must verify build, non-root startup, role selection, migration ownership, health/readiness, and SIGTERM handling. Package-specific commands are documented in [packages/a13n-service/README.md](packages/a13n-service/README.md).

The separate sandbox image is a Debian development runtime with `a13n-envd`, process supervision, the system CA bundle, and common command-line tools. Its root daemon starts Session workers as the provisioned `sandbox` account (`1000:1000`); native shell execution and passwordless sudo are available for Agent development, while controlled egress stays opt-in. The outer container supplies isolation, not the default execution UID. It is built from the same source revision rather than downloading an unverified latest binary. `make image-check-sandbox` checks the configured identity, writable development directories, passwordless sudo and daemon startup under ordinary Docker permissions. See the [sandbox image guide](docs/a13n-envd/sandbox.md).
