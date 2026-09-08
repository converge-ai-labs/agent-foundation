from typing import Any, cast

import pytest
from a13n_service.storage.object_store import (
    ByteRange,
    ObjectAccessDenied,
    ObjectConflict,
    ObjectStoreUnavailable,
    S3ObjectStore,
)
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


@pytest.mark.parametrize("status,code", [(403, "AccessDenied"), (401, "Unauthorized"), (400, "InvalidAccessKeyId")])
async def test_access_failure_is_not_a_transient_outage(status: int, code: str) -> None:
    error = ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "DeleteObject",
    )
    store = S3ObjectStore(cast(Any, _DeleteClient(error)), "bucket")
    with pytest.raises(ObjectAccessDenied):
        await store.delete("key")


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


@pytest.mark.parametrize("operation", ["stat", "open", "range", "list"])
@pytest.mark.parametrize(
    "encoding,body,error",
    [
        (None, b"unframed object body long enough to pass a length check", "encoding is unsupported"),
        ("unsupported-object-v1", b"unknown object body long enough to pass a length check", "encoding is unsupported"),
        ("a13n-object-v1", b"short", "envelope is truncated"),
    ],
)
async def test_invalid_s3_envelope_is_rejected(
    s3_object_store: S3ObjectStore, operation: str, encoding: str | None, body: bytes, error: str
) -> None:
    store = s3_object_store
    if encoding is None:
        await store._client.put_object(Bucket=store._bucket, Key="invalid", Body=body)
    else:
        await store._client.put_object(Bucket=store._bucket, Key="invalid", Body=body, ContentEncoding=encoding)

    with pytest.raises(ObjectStoreUnavailable, match=error):
        if operation == "stat":
            await store.stat("invalid")
        elif operation == "list":
            await store.list(prefix="invalid")
        else:
            byte_range = ByteRange(1, 4) if operation == "range" else None
            async with store.open("invalid", byte_range=byte_range) as reader:
                _ = [chunk async for chunk in reader]


async def test_full_read_rejects_invalid_s3_envelope_header(s3_object_store: S3ObjectStore) -> None:
    store = s3_object_store
    await store._client.put_object(
        Bucket=store._bucket, Key="invalid", Body=b"wronghdr" + bytes(16) + b"body", ContentEncoding="a13n-object-v1"
    )

    with pytest.raises(ObjectStoreUnavailable, match="envelope is invalid"):
        async with store.open("invalid") as reader:
            _ = [chunk async for chunk in reader]
