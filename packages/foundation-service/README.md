# Foundation Service Storage

This package currently contains Foundation Service's internal, async-first storage substrate. It is a workspace package, not a public SDK or an independently distributed provider library.

The substrate exposes capability-specific interfaces instead of one generic storage facade:

- SQL uses SQLAlchemy's async `AsyncEngine` and `AsyncSession` APIs with PostgreSQL or SQLite.
- Redis-compatible data structures use `redis.asyncio.Redis` with Redis or process-local fakeredis.
- Objects use the small Foundation-owned `ObjectStore` protocol with S3-compatible or local-directory adapters.
- Local files and deployment-mounted NFS use the same confined `pathlib` and AnyIO helpers.

Backend selection happens once during process startup. A failed network backend never falls back to local state.

## Runtime

Executable settings code builds the frozen `StorageSettings` model and owns environment-variable mapping. The storage package accepts typed configuration and does not read process environment variables itself.

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

Run the storage suite and package checks from the repository root:

```bash
uv run --package converge-foundation-service pytest packages/foundation-service/tests/storage -q
make lint
make typecheck
uv build --package converge-foundation-service
```

Container-owned integration tests exercise PostgreSQL, Redis, and S3 HTTP behavior. The S3 startup-probe test also demonstrates that an endpoint missing required conditional-delete semantics is rejected rather than silently accepted.
