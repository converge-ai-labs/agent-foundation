"""Owned storage cleanup must not depend on listing a large object collection."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from . import local_storage


@pytest.mark.anyio
@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("workload_fails", [False, True])
async def test_cleanup_matches_storage_ownership(monkeypatch, owned, workload_fails):
    events = []
    allocated = []

    class Client:
        async def create_bucket(self, *, Bucket):
            allocated.append(Bucket)

        async def list_objects_v2(self, *, Bucket, MaxKeys):
            assert not owned, "Owned-container cleanup must work even when listing cannot complete"
            assert Bucket == allocated[0] and MaxKeys == 1000
            events.append("list")
            return {"Contents": [{"Key": "retained-checkpoint"}]}

        async def delete_object(self, *, Bucket, Key):
            assert Bucket == allocated[0] and Key == "retained-checkpoint"
            events.append("delete-object")

        async def delete_bucket(self, *, Bucket):
            assert Bucket == allocated[0]
            events.append("delete-bucket")

    @asynccontextmanager
    async def client_context(*args, **kwargs):
        try:
            yield Client()
        finally:
            events.append("close-client")

    async def start_owned(stack, access, secret):
        stack.callback(events.append, "remove-owned-container")
        return "http://127.0.0.1:9000"

    async def ready(*args):
        pass

    monkeypatch.setattr(local_storage, "get_session", lambda: SimpleNamespace(create_client=client_context))
    monkeypatch.setattr(local_storage, "start_rustfs", start_owned)
    monkeypatch.setattr(local_storage, "wait_ready", ready)
    monkeypatch.setattr(local_storage.S3ObjectStore, "check_compatibility", ready)

    async def workload():
        async with local_storage.open_object_storage(endpoint_url=None if owned else "http://127.0.0.1:9000"):
            if workload_fails:
                raise RuntimeError("workload failed")

    if workload_fails:
        with pytest.raises(RuntimeError, match="workload failed"):
            await workload()
    else:
        await workload()
    assert len(allocated) == 1
    assert events == (
        ["close-client", "remove-owned-container"]
        if owned
        else ["list", "delete-object", "delete-bucket", "close-client"]
    )
