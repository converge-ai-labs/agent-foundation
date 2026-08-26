# Development Standards

This file explains the engineering choices shared by deployable Python services. Product semantics and subsystem ownership belong in `spec/`; contributor workflow belongs in [CONTRIBUTING.md](CONTRIBUTING.md); package command catalogs and exhaustive configuration references belong in the nearest package README. Stable repository workflows and safety-critical settings are named here only when they are part of the engineering contract.

## Service Shape

`foundation-service` ships one package and container image with two independently deployable roles and their all-in-one composition:

- `all`: control and worker in one process;
- `control`: APIs, scheduling, and control-plane maintenance;
- `worker`: Execution workers only.

A role is a process ownership and scaling boundary, not a separate product, schema, tenant, or authorization boundary. Every background loop must have one explicit owning role, and overlap during rolling deployment must be safe through durable leases, fencing, or idempotency.

## Application Structure

Organize business code by feature and add layers only for a real capability; do not prebuild global `controllers`, `dto`, `managers`, generic repositories, or abstract unit-of-work frameworks.

- FastAPI routers are thin transport adapters. Keep Pydantic request and response DTOs beside the owning feature API; routers validate, authorize, call one application use case, and map its typed result or error.
- Application services own use-case orchestration and short transaction boundaries. They do not import FastAPI or encode HTTP status.
- Repositories own SQLAlchemy queries, may flush, and never commit. ORM objects stay inside the persistence boundary and are not API responses or Harness contracts.
- Durable asynchronous lifecycles use idempotent reconcilers and fenced workers. Model, tool, queue, and stream waits happen outside database transactions.
- Process-role wiring selects routers, reconcilers, and workers; `control` and `worker` do not duplicate feature or domain models.

## HTTP Namespace and Browser Applications

Product-facing HTTP APIs use the `/api` namespace. Keep OpenAPI schemas and interactive API documentation under the same prefix. Individual resource layouts remain owned by their API contracts; the prefix is not permission to introduce an unversioned compatibility promise for every implementation route. Protocol daemons such as `agent-envd` retain their owning transport contracts rather than inheriting this product-API convention.

Operational liveness and readiness probes use explicit paths such as `/healthz` and `/readyz` outside `/api`. They expose only bounded process and dependency state and are not product resources. Browser history fallback must never turn an unknown `/api` request or an operational probe into an HTML application response.

A browser application deployed with a service lives under `apps/`, remains private rather than becoming a language package, and builds reproducibly from its own lock file. Production images build immutable browser assets in a dedicated stage, copy only the output into the non-root runtime image, and require no Node.js runtime. The service can serve those assets from `/` for roles that own product ingress. Worker-only roles do not expose the browser application or product APIs.

During local development, the browser dev server uses relative `/api` URLs and proxies that namespace unchanged to the backend. Repository commands start and stop the frontend and backend as one development stack while preserving each process's native diagnostics and shutdown behavior. Production remains same-origin and does not add CORS merely to accommodate local tooling.

## Generated Code and Static Analysis

Repository-wide static analysis uses a moderate profile focused on actionable correctness and maintainability signals rather than enabling every optional strict or opinionated rule. Tighten or relax that profile when recurring evidence justifies the change, not to silence one isolated finding.

Generated output must compile, type-check where applicable, and pass its contract, round-trip, and integration tests. Style-oriented lint rules can be disabled at the narrow generated-file or generated-module boundary when satisfying them would add renderer complexity without improving correctness. The generator, build integration, and all handwritten code remain under the normal repository checks. Do not weaken repository-wide checks merely to accommodate mechanical output, and do not complicate a generator solely to reproduce hand-written style.

## Async and Process Lifespan

Service I/O is async-first. Use async database, `httpx2`, Redis, queue, object-store, and subprocess clients. Do not introduce `httpx` or another general HTTP client alongside `httpx2`. Isolate unavoidable bounded blocking work with `anyio.to_thread.run_sync`; never block the event loop or call `asyncio.run()` from an active async path.

Create process-wide engines and clients during FastAPI lifespan, store them in explicit application state, and close them during shutdown. Module import must not open connections, start tasks, or configure logging. External calls have explicit timeouts; retries are bounded, observable, and restricted to retry-safe operations. Preserve cancellation and re-raise `CancelledError` after bounded cleanup.

## Database Sessions and Transactions

All service code obtains the canonical engine and session factory from `open_storage()` and uses `short_session()` and `transaction()` from `a13n_service.storage`. Do not construct local engines or session makers.

An `AsyncSession` is a mutable unit of work. Never share it across concurrent tasks or store it in a singleton. Keep each transaction around one small database operation, and do not hold a session, connection, transaction, or lock while waiting for:

- model, tool, or agent execution;
- HTTP, Redis, queue, object-store, or environment I/O;
- a sleep, retry, long poll, or another worker;
- SSE, WebSocket, file, or model-output streaming;
- a FastAPI background task.

Read durable state in one short session, close it, perform external work, then open a new short transaction to publish the result. Revalidate ownership or version fields when state may have changed. Repositories may flush; the application use case owns commit. Return typed values or identifiers rather than live ORM entities, and load relationships explicitly so serialization cannot trigger implicit async I/O. The canonical engine bounds PostgreSQL connection and statement time, readiness uses a shorter application deadline, and shielded rollback/close cleanup is bounded.

### FastAPI streaming footgun

FastAPI yield-dependency cleanup timing has changed across releases. A streaming route must therefore never receive a yielded database session, including indirectly through authentication.

Complete authentication, authorization, and initial reads in a short session that closes before constructing the response. Pass immutable values into the generator. If the stream needs database state, open a fresh short session for each bounded operation. Background tasks also create their own session from the factory. Release subscriptions and tasks in `finally`, and test that an open stream does not retain a pool connection.

## Migrations

Alembic metadata comes from `a13n_service.database.metadata`. Every concrete ORM model must be imported into that explicit registry before generating a revision. Domains own model meaning, while Foundation Service owns one combined metadata registry and one linear migration history.

Use the repository workflow rather than creating files manually:

```bash
make db-migrate msg="add session lease fields"
```

The command starts local PostgreSQL if needed, creates a disposable database, replays all existing history, autogenerates the model diff, formats the revision, and drops the database. This prevents a developer's normal database from hiding a missing migration. File names use `YYYYMMDD_<revision>_<slug>.py` and history stays linear unless a parallel branch is deliberately reviewed.

Autogenerate is only a draft. Review names, constraints, server defaults, nullability, indexes, data loss, downgrade behavior, lock level, scans or rewrites, old/new rolling compatibility, and interruption safety. Prefer additive expand-and-contract changes. Put large backfills in bounded restartable jobs rather than startup migrations, and prefer application rollback or forward repair over destructive schema downgrade.

### Auto migration and locking

The shared image enables auto migration by default for `all` and `control`, with PostgreSQL advisory locking serializing concurrent rollout replicas. Deployments that use a dedicated singleton migration job disable replica auto migration. The `worker` role never migrates; a non-owner performs `db current --check-heads` and fails closed when schema is incompatible.

PostgreSQL migrations use a dedicated synchronous `NullPool` connection and a service-scoped session advisory lock. The same connection holds the lock across revision inspection, transactional DDL, reviewed autocommit blocks, and stamping. Advisory-lock waiting temporarily uses `lock_timeout=0` and its own bounded `statement_timeout`; after acquisition, the normal short DDL lock timeout is restored. This keeps replica serialization independent from table-lock safety.

| Setting                                                 | Default | Reason                                              |
| ------------------------------------------------------- | ------- | --------------------------------------------------- |
| `FOUNDATION_MIGRATION_ADVISORY_LOCK_TIMEOUT_SECONDS`    | `900`   | Never wait forever for another migration runner     |
| `FOUNDATION_MIGRATION_LOCK_TIMEOUT_SECONDS`             | `3`     | Fail quickly when application traffic blocks DDL    |
| `FOUNDATION_MIGRATION_STATEMENT_TIMEOUT_SECONDS`        | `900`   | Bound each migration statement                      |
| `FOUNDATION_MIGRATION_IDLE_TRANSACTION_TIMEOUT_SECONDS` | `30`    | Prevent abandoned transactions from retaining locks |

These values apply only to migration connections. Override them only for a reviewed migration plan. A timeout stops startup; do not retry in a tight loop or stamp past failed work. The advisory lock serializes runners only—it does not pause traffic or make incompatible DDL safe.

## Logging

Configure Python logging once in the executable before Uvicorn or a worker starts. Libraries only obtain namespaced loggers through `a13n-logging`. Use Rich-backed `pretty` output locally and structured `json` output in deployments, writing to stdout or stderr.

Prefer stable event names and structured fields. Include service, role, build version, request or trace ID, and applicable conversation/session/run IDs. Log exceptions with stack traces at the boundary that handles them. Never log credentials, authorization headers, password-bearing URLs, cookies, raw prompts, model output, tool payloads, or uploaded content by default.

## Container Image

Build one reproducible multi-stage image from `uv.lock` for all roles. Run as non-root, keep credentials and environment configuration outside the image, use an init process when needed, and `exec` the final command so signals propagate. The runtime image installs the platform CA bundle, verifies `/etc/ssl/certs/ca-certificates.crt` at build time, and sets `SSL_CERT_FILE` to that path so `httpx2` uses a stable complete trust store instead of mutating a shared `truststore` OpenSSL context under concurrency. Do not remove or override it unless the replacement contains the deployment's complete CA set. Liveness reports process health; readiness verifies dependencies and schema compatibility. Stop accepting new work before draining or relinquishing ownership.

Image changes must verify build, non-root startup, role selection, migration ownership, health/readiness, and SIGTERM handling. Package-specific commands are documented in [packages/foundation-service/README.md](packages/foundation-service/README.md).

The separate sandbox image is a non-root Debian runtime with `agent-envd`, process supervision, the system CA bundle, and a small set of common command-line tools. It is built from the same source revision rather than downloading an unverified latest binary.
