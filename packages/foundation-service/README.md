# Foundation Service

This package contains the hosted Foundation Service executable, its internal async-first storage substrate, and its service-owned relational schema lifecycle. It is a workspace package, not a public SDK or an independently distributed provider library.

The substrate exposes capability-specific interfaces instead of one generic storage facade:

- SQL uses SQLAlchemy's async `AsyncEngine` and `AsyncSession` APIs with PostgreSQL or SQLite.
- Redis-compatible data structures use `redis.asyncio.Redis` with Redis or process-local fakeredis.
- Objects use the small Foundation-owned `ObjectStore` protocol with S3-compatible or local-directory adapters.
- Local files and deployment-mounted NFS use the same confined `pathlib` and AnyIO helpers.

Backend selection happens once during process startup. A failed network backend never falls back to local state.

## Runtime

`ServiceSettings` owns the `FOUNDATION_*` environment contract and maps it to the frozen `StorageSettings` model. The storage package accepts typed configuration and does not read process environment variables itself. `foundation-service serve` constructs all selected providers once in FastAPI lifespan, publishes the resulting `StorageResources` on `app.state.storage`, and closes the resources during shutdown.

The default service profile keeps the existing PostgreSQL and Redis endpoints and uses separate local roots for objects and files. Set `FOUNDATION_OBJECT_BACKEND=s3` and `FOUNDATION_OBJECT_BUCKET` for a multi-process deployment; the local object adapter supports only one writing process. `FOUNDATION_FILESYSTEM_ROOT` may be an ordinary local directory or an NFS mount prepared by deployment.

```python
from pathlib import Path

from converge_foundation_service.storage import StorageSettings, open_storage

settings = StorageSettings.model_validate(
    {
        "database": {"backend": "sqlite", "path": Path("var/foundation.sqlite3")},
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
            "url": "postgresql://foundation:password@postgres/foundation",
        },
        "redis": {"backend": "redis", "url": "redis://redis:6379/0"},
        "objects": {
            "backend": "s3",
            "bucket": "foundation-objects",
            "region": "us-east-1",
        },
        "filesystem": {"root": Path("/mnt/foundation-files")},
    }
)
```

The S3 client uses the standard AWS credential chain. Set `endpoint_url` and `force_path_style` only for a compatible non-AWS endpoint. The deployment mounts NFS at the configured filesystem root before the process starts.

## Relational Usage

Consumers use SQLAlchemy directly. Generic storage does not define `get`, `insert`, or `do` wrappers.

```python
from sqlalchemy import select

from converge_foundation_service.storage import transaction

async with transaction(storage.sessions) as session:
    result = await session.execute(select(Record).where(Record.id == record_id))
    record = result.scalar_one_or_none()
```

Each operation gets a short session. Never retain a session or transaction across external I/O, agent execution, sleeps, background work, or a streaming response.

PostgreSQL is the distributed-service backend. SQLite is intended for a single-process, zero-service profile and must not be placed on NFS.

## Relational Schema and Migrations

Generic relational storage and service schema ownership are deliberately separate:

- `storage/relational.py` constructs async engines and short sessions for application I/O.
- `database/metadata.py` aggregates every service-owned ORM model.
- `database/migrations/` contains one linear Alembic history for the complete service database.
- `database/migration.py` owns the dedicated synchronous migration connection, bounded PostgreSQL advisory locking, and Alembic invocation.

The Alembic environment does not read process settings or create an engine. The runner supplies one validated connection, so CLI settings, lock policy, and schema comparison have distinct owners.

Select the database backend, then use the stable service CLI or repository commands:

```bash
FOUNDATION_DATABASE_BACKEND=postgresql
FOUNDATION_DATABASE_URL=postgresql+psycopg://foundation:foundation@127.0.0.1:5432/foundation

make db-upgrade
make db-current
make db-check
make db-history
```

The corresponding executable commands are `foundation-service db upgrade`, `foundation-service db current --check-heads`, `foundation-service db history`, and `foundation-service db migrate "description"`. There is one process CLI; migration implementation remains in `database/migration.py` rather than introducing a second database-only settings or command layer.

For the zero-service profile, set `FOUNDATION_DATABASE_BACKEND=sqlite` and `FOUNDATION_DATABASE_SQLITE_PATH=var/foundation.sqlite3`. The same accepted history is applied to both backends. A domain requiring PostgreSQL-only schema behavior must reject SQLite explicitly.

### Add an ORM Model

1. Define the model beside its owning domain using `converge_foundation_service.database.Base`.
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

## Object Usage

Object keys are opaque names rather than filesystem paths. `put` supports unconditional, create-only, and expected-version publication.

```python
from converge_foundation_service.storage import ByteRange, ObjectConflict

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

Metadata keys are normalized to lowercase and, like S3 REST metadata, keys and values must be ASCII. Keys must also be valid HTTP field names. Returned metadata mappings are immutable.

## Filesystem Usage

Deployment mounts NFS before process startup. Application code receives the mounted root and uses the same helpers as a local directory; there is no Python NFS provider.

```python
import anyio

from converge_foundation_service.storage.filesystem import atomic_write, resolve_under_root

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
uv run --package converge-foundation-service pytest packages/foundation-service/tests/storage packages/foundation-service/tests/database -q
make lint
make typecheck
uv build --package converge-foundation-service
```

Container-owned integration tests exercise PostgreSQL, Redis, and S3 HTTP behavior. The S3 startup-probe test also demonstrates that an endpoint missing required conditional-delete semantics is rejected rather than silently accepted.
