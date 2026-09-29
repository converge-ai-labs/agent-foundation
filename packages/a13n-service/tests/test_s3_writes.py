"""Plain S3 writes honor standard SDK checksum configuration without vendor-specific calls."""

from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
from a13n_service.app import open_objects
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects import s3
from a13n_service.settings import Objects
from aiobotocore.session import get_session
from botocore.awsrequest import AWSPreparedRequest
from botocore.exceptions import ClientError

pytestmark = pytest.mark.anyio


class RequestCaptured(Exception):
    pass


@pytest.mark.parametrize("checksum", ["when_required", "when_supported"])
async def test_plain_write_honors_sdk_checksum_environment(monkeypatch: pytest.MonkeyPatch, checksum: str) -> None:
    monkeypatch.setenv("AWS_REQUEST_CHECKSUM_CALCULATION", checksum)
    session = get_session()

    def capture(request: AWSPreparedRequest, **kwargs: object) -> None:
        headers = {key.lower(): value for key, value in request.headers.items()}
        assert "if-none-match" not in headers
        assert "x-oss-forbid-overwrite" not in headers
        assert ("x-amz-trailer" in headers) == (checksum == "when_supported")
        raise RequestCaptured

    def unexpected(**kwargs: object) -> None:
        pytest.fail("Opening an object store must not query bucket versioning")

    session.register("before-send.s3.PutObject", capture)
    session.register("before-call.s3.GetBucketVersioning", unexpected)
    monkeypatch.setattr(s3, "get_session", lambda: session)
    config = Objects.model_validate(
        {
            "backend": "s3",
            "bucket": "agents",
            "region": "us-east-1",
            "endpoint_url": "https://objects.example.com",
            "addressing_style": "virtual",
            "access_key_id": "test-key",
            "secret_access_key": "test-secret",
        }
    )
    async with AsyncExitStack() as stack:
        store = await open_objects(stack, config)
        with pytest.raises(RequestCaptured):
            await store.put("checkpoint", b"state", content_type="application/octet-stream")


@pytest.mark.parametrize("code", ["AccessDenied", "NotImplemented", "FileAlreadyExists"])
async def test_write_errors_are_unavailable(code: str) -> None:
    client = AsyncMock()
    client.put_object.side_effect = ClientError({"Error": {"Code": code}}, "PutObject")
    store = s3.S3Objects(client, "agents", prefix="", max_bytes=100, timeout=1)
    with pytest.raises(ServiceError) as error:
        await store.put("checkpoint", b"state", content_type="text/plain")
    assert error.value.code == "unavailable"
    client.get_object.assert_not_called()
