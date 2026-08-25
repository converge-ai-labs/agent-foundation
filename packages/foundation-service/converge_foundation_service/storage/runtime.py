"""Application-lifespan construction for storage resources."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio
from aiobotocore.config import AioConfig
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import get_session
from anyio import CapacityLimiter, to_thread
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import LocalObjectConfig, S3ObjectConfig, SQLiteConfig, StorageSettings
from .filesystem import prepare_root
from .object_store import LocalObjectStore, ObjectStore, S3ObjectStore
from .redis import check_redis, open_redis
from .sql import check_database, create_session_factory, create_sql_engine

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client


@dataclass(frozen=True, slots=True)
class StorageResources:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    objects: ObjectStore
    files_root: Path
    file_limiter: CapacityLimiter


class StorageStartupError(RuntimeError):
    """A required storage capability could not be initialized."""


@asynccontextmanager
async def open_storage(settings: StorageSettings) -> AsyncIterator[StorageResources]:
    """Construct all selected backends and close them in reverse order."""

    async with AsyncExitStack() as stack:
        try:
            await _prepare_sqlite_parent(settings)
            engine = create_sql_engine(settings.database)
            stack.push_async_callback(
                _dispose_engine,
                engine,
                settings.database.cleanup_timeout_seconds,
            )
            sessions = create_session_factory(engine)

            redis = await stack.enter_async_context(open_redis(settings.redis))
            file_limiter = CapacityLimiter(settings.filesystem.worker_limit)
            files_root = await prepare_root(
                settings.filesystem.root,
                create=settings.filesystem.create_root,
                limiter=file_limiter,
            )
            objects = await _open_objects(stack, settings, file_limiter)

            await check_database(engine)
            await check_redis(redis)
            if isinstance(settings.objects, S3ObjectConfig):
                assert isinstance(objects, S3ObjectStore)
                await _check_s3(objects, settings.objects.compatibility_timeout_seconds)
        except StorageStartupError:
            raise
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as error:
            raise StorageStartupError("storage initialization failed") from error

        yield StorageResources(engine, sessions, redis, objects, files_root, file_limiter)


async def _prepare_sqlite_parent(settings: StorageSettings) -> None:
    if not isinstance(settings.database, SQLiteConfig) or str(settings.database.path) == ":memory:":
        return
    parent = settings.database.path.absolute().parent
    await to_thread.run_sync(lambda: parent.mkdir(parents=True, exist_ok=True, mode=0o700))


async def _open_objects(
    stack: AsyncExitStack,
    settings: StorageSettings,
    file_limiter: CapacityLimiter,
) -> ObjectStore:
    config = settings.objects
    if isinstance(config, LocalObjectConfig):
        return await LocalObjectStore.create(
            config.root,
            create_root=config.create_root,
            chunk_size=config.chunk_size,
            limiter=file_limiter,
        )

    addressing_style = "path" if config.force_path_style else "auto"
    client_config = AioConfig(
        connect_timeout=config.connect_timeout_seconds,
        read_timeout=config.read_timeout_seconds,
        max_pool_connections=config.max_pool_connections,
        retries={"total_max_attempts": 1, "mode": "standard"},
        s3={"addressing_style": addressing_style},
        http_session_cls=HttpxSession,
    )
    session = get_session()
    client = await stack.enter_async_context(
        session.create_client(
            "s3",
            region_name=config.region,
            endpoint_url=config.endpoint_url,
            config=client_config,
        )
    )
    return S3ObjectStore(
        cast("S3Client", client),
        config.bucket,
        multipart_part_size=config.multipart_part_size,
    )


async def _check_s3(store: S3ObjectStore, timeout_seconds: float) -> None:
    with anyio.fail_after(timeout_seconds):
        await store.check_compatibility()


async def _dispose_engine(engine: AsyncEngine, timeout_seconds: float) -> None:
    with anyio.move_on_after(timeout_seconds, shield=True):
        await engine.dispose()
