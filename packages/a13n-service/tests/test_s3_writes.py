"""Exercise signed requests, immutable retries and OSS startup safety without network I/O."""

from contextlib import AsyncExitStack
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from a13n_service.app import open_objects
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects import s3
from a13n_service.settings import Objects
from aiobotocore.session import get_session
from botocore.awsrequest import AWSPreparedRequest, AWSResponse
from botocore.exceptions import ClientError

pytestmark = pytest.mark.anyio


class RequestCaptured(Exception):
    pass


def configured_store(mode: Literal["s3", "oss"]) -> Objects:
    return Objects(
        backend="s3",
        bucket="agents",
        region="us-east-1",
        endpoint_url="https://objects.example.com",
        addressing_style="virtual",
        write_mode=mode,
        access_key_id="test-key",
        secret_access_key="test-secret",
    )


@pytest.mark.parametrize("mode", ["s3", "oss"])
async def test_real_sdk_signs_only_selected_write_guard(
    monkeypatch: pytest.MonkeyPatch, mode: Literal["s3", "oss"]
) -> None:
    session = get_session()
    version_checks = []

    def versioning(**kwargs: Any) -> tuple[AWSResponse, dict]:
        version_checks.append(True)
        return AWSResponse("https://objects.example.com", 200, {}, None), {}

    def capture(request: AWSPreparedRequest, **kwargs: Any) -> None:
        headers = {key.lower(): value for key, value in request.headers.items()}
        if mode == "oss":
            assert headers["x-oss-forbid-overwrite"] == b"true"
            assert b"x-oss-forbid-overwrite" in headers["authorization"]
            assert "if-none-match" not in headers
            assert "x-amz-trailer" not in headers
        else:
            assert headers["if-none-match"] == b"*"
            assert "x-oss-forbid-overwrite" not in headers
        raise RequestCaptured

    session.register("before-call.s3.GetBucketVersioning", versioning)
    session.register("before-send.s3.PutObject", capture)
    monkeypatch.setattr(s3, "get_session", lambda: session)
    async with AsyncExitStack() as stack:
        store = await open_objects(stack, configured_store(mode))
        with pytest.raises(RequestCaptured):
            await store.put("checkpoint", b"state", content_type="application/octet-stream")
    assert len(version_checks) == (1 if mode == "oss" else 0)


@pytest.mark.parametrize("status", ["Enabled", "Suspended"])
async def test_oss_refuses_versioned_buckets(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    session = get_session()
    session.register(
        "before-call.s3.GetBucketVersioning",
        lambda **kwargs: (
            AWSResponse("https://objects.example.com", 200, {}, None),
            {"Status": status},
        ),
    )
    monkeypatch.setattr(s3, "get_session", lambda: session)
    async with AsyncExitStack() as stack:
        with pytest.raises(ValueError, match="versioning never enabled"):
            await open_objects(stack, configured_store("oss"))


async def test_oss_version_check_failure_prevents_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    session = get_session()
    session.register(
        "before-call.s3.GetBucketVersioning",
        lambda **kwargs: (
            AWSResponse("https://objects.example.com", 403, {}, None),
            {"Error": {"Code": "AccessDenied"}},
        ),
    )
    monkeypatch.setattr(s3, "get_session", lambda: session)
    async with AsyncExitStack() as stack:
        with pytest.raises(ServiceError, match="Object store request failed"):
            await open_objects(stack, configured_store("oss"))


@pytest.mark.parametrize(
    "mode,code", [("s3", "PreconditionFailed"), ("s3", "ConditionalRequestConflict"), ("oss", "FileAlreadyExists")]
)
@pytest.mark.parametrize("existing", [b"state", b"different", None])
async def test_existing_key_never_overwrites(mode: Literal["s3", "oss"], code: str, existing: bytes | None) -> None:
    client = AsyncMock()
    client.put_object.side_effect = ClientError({"Error": {"Code": code}}, "PutObject")
    store = s3.S3Objects(client, "agents", prefix="", max_bytes=100, timeout=1, write_mode=mode)
    store.get = AsyncMock(return_value=existing)
    if existing == b"state":
        assert (await store.put("checkpoint", b"state", content_type="text/plain")).size == 5
    else:
        with pytest.raises(ServiceError) as error:
            await store.put("checkpoint", b"state", content_type="text/plain")
        assert error.value.code == "conflict"
    assert client.put_object.await_count == 1


@pytest.mark.parametrize("mode,code", [("s3", "FileAlreadyExists"), ("oss", "AccessDenied"), ("oss", "NotImplemented")])
async def test_other_errors_do_not_become_success(mode: Literal["s3", "oss"], code: str) -> None:
    client = AsyncMock()
    client.put_object.side_effect = ClientError({"Error": {"Code": code}}, "PutObject")
    store = s3.S3Objects(client, "agents", prefix="", max_bytes=100, timeout=1, write_mode=mode)
    with pytest.raises(ServiceError) as error:
        await store.put("checkpoint", b"state", content_type="text/plain")
    assert error.value.code == "unavailable"
    client.get_object.assert_not_called()
