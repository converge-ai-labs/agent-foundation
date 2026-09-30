"""Verify the real SDK request address through Service settings and object-store wiring, without sending I/O."""

from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import pytest
from a13n_service.app import open_objects
from a13n_service.infra.objects.s3 import S3Objects
from a13n_service.settings import Objects
from botocore.awsrequest import AWSPreparedRequest

pytestmark = pytest.mark.anyio


class RequestCaptured(Exception):
    pass


@pytest.mark.parametrize(
    "options,host,path",
    [
        ({}, "objects.example.com", "/agents"),
        ({"path_style": False}, "objects.example.com", "/agents"),
        ({"path_style": True}, "objects.example.com", "/agents"),
        ({"addressing_style": "auto"}, "objects.example.com", "/agents"),
        ({"addressing_style": "path"}, "objects.example.com", "/agents"),
        ({"path_style": True, "addressing_style": "path"}, "objects.example.com", "/agents"),
        ({"addressing_style": "virtual"}, "agents.objects.example.com", "/"),
        ({"path_style": False, "addressing_style": "virtual"}, "agents.objects.example.com", "/"),
    ],
)
async def test_service_s3_request_address(options: dict, host: str, path: str) -> None:
    config = Objects.model_validate(
        {
            "backend": "s3",
            "bucket": "agents",
            "region": "us-east-1",
            "endpoint_url": "https://objects.example.com",
            "access_key_id": "test-key",
            "secret_access_key": "test-secret",
            **options,
        }
    )

    def capture(request: AWSPreparedRequest, **kwargs: object) -> None:
        url = urlsplit(str(request.url))
        assert (url.hostname, url.path) == (host, path)
        raise RequestCaptured

    async with AsyncExitStack() as stack:
        objects = await open_objects(stack, config)
        assert isinstance(objects, S3Objects)
        objects.client.meta.events.register("before-send.s3.ListObjectsV2", capture)
        with pytest.raises(RequestCaptured):
            await objects.keys("probe", limit=1)
