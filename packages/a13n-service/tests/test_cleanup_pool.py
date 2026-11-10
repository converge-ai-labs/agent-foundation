"""Checkpoint cleanup has reserved HTTP capacity and claims only work it can start."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace

import pytest
from a13n_service.app import open_runtime
from a13n_service.distribution import OSS, _deliver_outbox
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.objects.s3 import S3Objects
from a13n_service.infra.outbox import Delivery, OutboxRow, prepare, settle
from a13n_service.runs.checkpoints import CLEANUP
from a13n_service.settings import Objects
from fastapi import FastAPI
from sqlalchemy import select
from starlette.responses import Response, StreamingResponse

from .http_support import serve_http

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["scan", "keys"])
async def test_cleanup_finishes_while_foreground_get_holds_its_only_connection(settings, tenant, mode):
    app = FastAPI()
    body_started, release = asyncio.Event(), asyncio.Event()
    key = f"orgs/{tenant.organization_id}/runs/run_cleanup/state/1/orphan"
    deleted = []
    listed = []

    @app.get("/{bucket}/{key:path}")
    async def get(bucket: str, key: str):
        if key == "owned/warmup":
            return Response(b"ab")

        async def body():
            yield b"a"
            body_started.set()
            await release.wait()
            yield b"b"

        return StreamingResponse(body(), headers={"content-length": "2"})

    @app.get("/{bucket}")
    async def listing(bucket: str, prefix: str):
        listed.append(prefix)
        return Response(
            f"<ListBucketResult><IsTruncated>false</IsTruncated><Contents>"
            f"<Key>owned/{key}</Key><Size>2</Size></Contents></ListBucketResult>",
            media_type="application/xml",
        )

    @app.delete("/{bucket}/{key:path}")
    async def delete(bucket: str, key: str):
        deleted.append(key)
        return Response(status_code=204)

    async with serve_http(app) as url:
        config = settings.model_copy(
            update={
                "objects": Objects(
                    backend="s3",
                    bucket="bucket",
                    prefix="owned/",
                    region="us-east-1",
                    endpoint_url=url,
                    addressing_style="path",
                    access_key_id="test",
                    secret_access_key="test",
                    max_pool_connections=1,
                    cleanup_max_pool_connections=1,
                    timeout=5,
                )
            }
        )
        async with AsyncExitStack() as stack:
            runtime = await open_runtime(stack, OSS, config, executes=False, maintains=True)
            foreground, cleanup = runtime.objects, runtime.cleanup_objects
            assert isinstance(foreground, S3Objects) and isinstance(cleanup, S3Objects)
            assert foreground is not cleanup
            assert foreground.client.meta.config.max_pool_connections == 1
            assert cleanup.client.meta.config.max_pool_connections == 1
            assert await foreground.get("warmup") == b"ab"
            reading = asyncio.create_task(foreground.get("blocked"))
            try:
                await asyncio.wait_for(body_started.wait(), 2)
                async with transaction(runtime.storage) as session:
                    row = prepare(
                        organization_id=tenant.organization_id,
                        workspace_id=tenant.workspace_id,
                        kind=CLEANUP,
                        target={"run_id": "run_cleanup"},
                        payload={"keys": [key]}
                        if mode == "keys"
                        else {
                            "after": None,
                            "before_attempt": None,
                            "keep": [],
                        },
                    )
                    session.add(row)
                    row_id = row.id
                # The GET remains incomplete throughout native delivery and settlement.
                await asyncio.wait_for(_deliver_outbox(runtime).run(), 2)
                assert not reading.done()
                assert deleted == [f"owned/{key}"]
                assert listed == ([f"owned/orgs/{tenant.organization_id}/runs/run_cleanup/"] if mode == "scan" else [])
                async with short_session(runtime.storage) as session:
                    settled = await session.get_one(OutboxRow, row_id)
                    assert (settled.status, settled.attempts) == ("delivered", 1)
                assert set(foreground.diagnostics.snapshot()["operations"]) == {"get"}
                assert cleanup.diagnostics.snapshot()["operations"]["delete"]["outcomes"] == {"success": 1}
                sessions = [
                    session
                    for store in (foreground, cleanup)
                    for session in store.client._endpoint.http_session._sessions.values()
                ]
                assert len(sessions) == 2 and not any(session.closed for session in sessions)
            finally:
                release.set()
                assert await reading == b"ab"
        assert all(session.closed for session in sessions)


@pytest.mark.parametrize("maintains", [False, True])
async def test_only_maintenance_runtime_opens_reserved_s3_client(settings, maintains):
    config = settings.model_copy(
        update={
            "objects": Objects(
                backend="s3",
                bucket="bucket",
                region="us-east-1",
                access_key_id="test",
                secret_access_key="test",
            )
        }
    )
    async with AsyncExitStack() as stack:
        runtime = await open_runtime(stack, OSS, config, executes=False, maintains=maintains)
        assert runtime.objects.client.meta.config.max_pool_connections == 32
        if maintains:
            assert runtime.cleanup_objects.client.meta.config.max_pool_connections == 4
        else:
            assert runtime.cleanup_objects is None


@pytest.mark.parametrize("backend,parallel,expected", [("s3", 8, 2), ("s3", 1, 1), ("local", 8, 8)])
async def test_cleanup_claim_limit_respects_pool_and_smaller_policy(runtime, tenant, backend, parallel, expected):
    policies = dict(runtime.settings.outbox.policies)
    policies[CLEANUP] = policies[CLEANUP].model_copy(update={"parallel": parallel})
    runtime = replace(
        runtime,
        settings=runtime.settings.model_copy(
            update={
                "objects": runtime.settings.objects.model_copy(
                    update={"backend": backend, "cleanup_max_pool_connections": 2}
                ),
                "outbox": runtime.settings.outbox.model_copy(update={"policies": policies}),
            }
        ),
    )
    delivery = _deliver_outbox(runtime).run
    assert isinstance(delivery, Delivery)
    assert delivery.policies[CLEANUP].parallel == expected
    assert runtime.settings.outbox.policies[CLEANUP].parallel == parallel
    assert delivery.policies["webhook"] == policies["webhook"]
    started, release = asyncio.Event(), asyncio.Event()
    active = peak = completed = 0
    total = expected + 3

    async def handler(claimed):
        nonlocal active, peak, completed
        active += 1
        peak = max(peak, active)
        if active == expected:
            started.set()
        try:
            await release.wait()
            async with transaction(runtime.storage) as session:
                assert await settle(session, claimed, "delivered")
            completed += 1
        finally:
            active -= 1

    async with transaction(runtime.storage) as session:
        session.add_all(
            [
                prepare(
                    organization_id=tenant.organization_id,
                    workspace_id=tenant.workspace_id,
                    kind=CLEANUP,
                    target={"run_id": f"run_{i}"},
                    payload={"keys": []},
                )
                for i in range(total)
            ]
        )
    task = asyncio.create_task(replace(delivery, handlers={CLEANUP: handler})())
    try:
        await asyncio.wait_for(started.wait(), 2)
        async with short_session(runtime.storage) as session:
            rows = list(await session.scalars(select(OutboxRow).where(OutboxRow.kind == CLEANUP)))
            assert sum(row.lease_owner is not None for row in rows) == expected
            assert sum(row.attempts == 0 for row in rows) == total - expected
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    assert peak == expected and completed == total


async def test_local_runtime_reuses_its_existing_object_store(runtime):
    assert runtime.settings.objects.backend == "local"
    assert runtime.cleanup_objects is None
