"""Real SDK HTTP hooks distinguish pool waits, reads and caller cancellation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from a13n_service.infra.objects.s3 import S3Objects, open_s3
from a13n_service.runs import checkpoints
from fastapi import FastAPI
from starlette.responses import Response, StreamingResponse

from .http_support import serve_http

pytestmark = pytest.mark.anyio


async def test_pool_wait_and_body_read_are_observed_without_payloads(caplog):
    app = FastAPI()

    @app.get("/{bucket}/{key}")
    async def get(bucket: str, key: str):
        async def body():
            yield b"a"
            await asyncio.sleep(0.06)
            yield b"b"

        return StreamingResponse(body(), headers={"content-length": "2"})

    async with (
        serve_http(app) as url,
        open_s3(
            bucket="private-bucket",
            prefix="",
            region="us-east-1",
            endpoint_url=url,
            addressing_style="path",
            access_key_id="private-access",
            secret_access_key="private-secret",
            max_bytes=100,
            timeout=2,
            max_pool_connections=1,
        ) as store,
    ):
        assert await store.get("warmup") == b"ab"
        assert await asyncio.gather(store.get("private-key"), store.get("private-key")) == [b"ab", b"ab"]
        row = store.diagnostics.snapshot()["operations"]["get"]
        assert row["calls"] == 3 and row["outcomes"] == {"success": 3}
        assert row["http_attempts"] == 3
        assert row["phase_milliseconds"]["connection_queued"] > 20
        assert row["phase_milliseconds"]["body_read"] > 80
        assert row["payload_bytes"] == 6
        assert sum(row["histogram"]) == 3
        assert "private-" not in str(store.diagnostics.snapshot())
    assert "private-" not in caplog.text


async def test_sdk_retries_count_http_attempts():
    app = FastAPI()
    calls = 0

    @app.delete("/{bucket}/{key}")
    async def delete(bucket: str, key: str):
        nonlocal calls
        calls += 1
        return Response(status_code=503 if calls == 1 else 204)

    async with (
        serve_http(app) as url,
        open_s3(
            bucket="bucket",
            prefix="",
            region="us-east-1",
            endpoint_url=url,
            addressing_style="path",
            access_key_id="test",
            secret_access_key="test",
            max_bytes=100,
            timeout=5,
            max_pool_connections=1,
        ) as store,
    ):
        await store.delete("key")
        row = store.diagnostics.snapshot()["operations"]["delete"]
        assert row["calls"] == 1 and row["http_attempts"] == 2
        assert row["outcomes"] == {"success": 1}


@pytest.mark.parametrize("phase", ["list", "delete"])
async def test_cleanup_budget_reports_where_zero_progress_occurred(phase, caplog):
    async def blocked(**kwargs):
        await anyio.sleep_forever()

    client = AsyncMock()
    client.list_objects_v2.return_value = {"Contents": [{"Key": "orgs/org/runs/run/state/1/object"}]}
    getattr(client, "list_objects_v2" if phase == "list" else "delete_object").side_effect = blocked
    # Outer batch budget expires well before this individual operation's budget.
    objects = S3Objects(client, "private-bucket", prefix="", max_bytes=100, timeout=1)
    runtime = SimpleNamespace(
        objects=objects, cleanup_objects=None, settings=SimpleNamespace(objects=SimpleNamespace(timeout=0.02))
    )
    claimed = SimpleNamespace(
        organization_id="org",
        id="obx_test",
        target={"run_id": "run"},
        payload={"after": None, "before_attempt": None, "keep": []},
    )
    with pytest.raises(TimeoutError, match="made no progress"):
        await checkpoints.clean(runtime, claimed)
    record = next(r for r in caplog.records if r.message == "Checkpoint cleanup observed")
    assert record.budget_expired and not record.progressed
    assert record.phase == phase and record.outcome == "no_progress"
    assert record.delete_started == (1 if phase == "delete" else 0)
    assert record.delete_completed == 0
    assert objects.diagnostics.snapshot()["operations"][phase]["outcomes"] == {"caller_cancelled": 1}
    assert not any(r.message == "Object store request timed out" for r in caplog.records)
    assert "private-" not in caplog.text


async def test_cleanup_can_exhaust_batch_budget_in_pool_without_sending_list(caplog):
    app = FastAPI()
    body_started, release = asyncio.Event(), asyncio.Event()
    list_calls = 0

    @app.get("/{bucket}/{key}")
    async def get(bucket: str, key: str):
        if key == "warmup":
            return Response(b"ab")

        async def body():
            yield b"a"
            body_started.set()
            await release.wait()
            yield b"b"

        return StreamingResponse(body(), headers={"content-length": "2"})

    @app.get("/{bucket}")
    async def listing(bucket: str):
        nonlocal list_calls
        list_calls += 1
        return Response(
            b"<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>", media_type="application/xml"
        )

    async with (
        serve_http(app) as url,
        open_s3(
            bucket="bucket",
            prefix="",
            region="us-east-1",
            endpoint_url=url,
            addressing_style="path",
            access_key_id="test",
            secret_access_key="test",
            max_bytes=100,
            timeout=2,
            max_pool_connections=1,
        ) as objects,
    ):
        assert await objects.get("warmup") == b"ab"
        reading = asyncio.create_task(objects.get("blocked"))
        await asyncio.wait_for(body_started.wait(), 1)
        runtime = SimpleNamespace(
            objects=objects, cleanup_objects=None, settings=SimpleNamespace(objects=SimpleNamespace(timeout=0.03))
        )
        claimed = SimpleNamespace(
            organization_id="org",
            id="obx_pool",
            target={"run_id": "run"},
            payload={"after": None, "before_attempt": None, "keep": []},
        )
        try:
            with pytest.raises(TimeoutError, match="made no progress"):
                await checkpoints.clean(runtime, claimed)
            row = objects.diagnostics.snapshot()["operations"]["list"]
            assert row["outcomes"] == {"caller_cancelled": 1}
            assert row["phase_milliseconds"]["connection_queued"] > 15
            assert list_calls == 0
            record = next(r for r in caplog.records if r.message == "Checkpoint cleanup observed")
            assert record.phase == "list" and record.delete_started == 0
        finally:
            release.set()
            assert await reading == b"ab"
