from typing import Any, cast

import pytest
from a13n_service.storage.object_store import ByteRange, ObjectConflict, ObjectStoreUnavailable, S3ObjectStore
from botocore.exceptions import ClientError

pytestmark = pytest.mark.anyio


async def test_startup_probe_rejects_an_endpoint_without_atomic_conditional_delete(
    s3_object_store: S3ObjectStore,
) -> None:
    with pytest.raises(ObjectStoreUnavailable, match="conditional deletes"):
        await s3_object_store.check_compatibility()


class _DeleteClient:
    def __init__(self, error: ClientError | None = None) -> None:
        self.error = error
        self.request: dict[str, object] | None = None

    async def delete_object(self, **request: object) -> None:
        self.request = request
        if self.error is not None:
            raise self.error


async def test_conditional_delete_forwards_opaque_version() -> None:
    client = _DeleteClient()
    store = S3ObjectStore(cast(Any, client), "bucket")

    await store.delete("key", if_match='"opaque-etag"')

    assert client.request == {"Bucket": "bucket", "Key": "key", "IfMatch": '"opaque-etag"'}


async def test_conditional_delete_maps_precondition_failure() -> None:
    error = ClientError(
        {
            "Error": {"Code": "PreconditionFailed", "Message": "mismatch"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        "DeleteObject",
    )
    store = S3ObjectStore(cast(Any, _DeleteClient(error)), "bucket")

    with pytest.raises(ObjectConflict):
        await store.delete("key", if_match='"stale"')


async def test_unconditional_delete_is_idempotent_when_endpoint_returns_not_found() -> None:
    error = ClientError(
        {
            "Error": {"Code": "NoSuchKey", "Message": "missing"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        "DeleteObject",
    )
    store = S3ObjectStore(cast(Any, _DeleteClient(error)), "bucket")

    await store.delete("missing")


async def test_legacy_unframed_s3_object_remains_readable_and_replaceable(s3_object_store: S3ObjectStore) -> None:
    store = s3_object_store
    await store._client.put_object(Bucket=store._bucket, Key="legacy", Body=b"legacy body", Metadata={"writer": "old"})
    legacy = await store.stat("legacy")
    assert legacy.size == 11
    async with store.open("legacy") as reader:
        assert b"".join([chunk async for chunk in reader]) == b"legacy body"
    async with store.open("legacy", byte_range=ByteRange(1, 4)) as reader:
        assert b"".join([chunk async for chunk in reader]) == b"ega"
    replacement = await store.put("legacy", b"legacy body", metadata={"writer": "new"}, if_match=legacy.version)
    assert replacement.version != legacy.version
    assert replacement.size == 11
    async with store.open("legacy", byte_range=ByteRange(1, 4)) as reader:
        assert b"".join([chunk async for chunk in reader]) == b"ega"
