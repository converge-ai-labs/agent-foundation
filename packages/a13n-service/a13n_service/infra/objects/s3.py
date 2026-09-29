"""S3-compatible object store; keys are written once, so every write is a plain `PutObject`."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal, cast

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from anyio import fail_after
from botocore.exceptions import BotoCoreError, ClientError

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.interface import ObjectRef, reference, validate_key

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client


def _unavailable(error: Exception) -> ServiceError:
    return ServiceError(
        "unavailable", f"Object store request failed ({type(error).__name__})", {"dependency": "objects"}
    )


def _code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


class S3Objects:
    def __init__(self, client: "S3Client", bucket: str, *, prefix: str, max_bytes: int, timeout: float):
        self.client, self.bucket, self.prefix = client, bucket, prefix
        self.max_bytes, self.timeout = max_bytes, timeout

    def _key(self, key: str) -> str:
        return self.prefix + validate_key(key)

    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectRef:
        if len(data) > self.max_bytes:
            raise ServiceError("payload_too_large", "Object exceeds its byte limit", {"limit": self.max_bytes})
        try:
            with fail_after(self.timeout):
                await self.client.put_object(
                    Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type
                )
        except (ClientError, BotoCoreError) as error:
            raise _unavailable(error) from None
        return reference(key, data, content_type)

    async def get(self, key: str) -> bytes | None:
        try:
            with fail_after(self.timeout):
                response = await self.client.get_object(Bucket=self.bucket, Key=self._key(key))
                if response["ContentLength"] > self.max_bytes:
                    raise ServiceError("unavailable", "Stored object exceeds its byte limit", {"dependency": "objects"})
                async with response["Body"] as body:
                    return await body.read()
        except ClientError as error:
            if _code(error) in {"NoSuchKey", "404"}:
                return None
            raise _unavailable(error) from None
        except BotoCoreError as error:
            raise _unavailable(error) from None

    async def keys(self, prefix: str, *, limit: int, after: str | None = None) -> list[str]:
        found: list[str] = []
        token: str | None = None
        try:
            with fail_after(self.timeout):
                while len(found) < limit:
                    continuation: dict[str, Any] = (
                        {"ContinuationToken": token}
                        if token
                        else ({"StartAfter": self._key(after)} if after is not None else {})
                    )
                    page = await self.client.list_objects_v2(
                        Bucket=self.bucket,
                        Prefix=self._key(prefix) + "/",
                        MaxKeys=min(1000, limit - len(found)),
                        **continuation,
                    )
                    found.extend(item["Key"].removeprefix(self.prefix) for item in page.get("Contents", ()))
                    token = page.get("NextContinuationToken")
                    if not token:
                        break
        except (ClientError, BotoCoreError) as error:
            raise _unavailable(error) from None
        return found[:limit]

    async def delete(self, key: str) -> None:
        try:
            with fail_after(self.timeout):
                await self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except (ClientError, BotoCoreError) as error:
            raise _unavailable(error) from None


@asynccontextmanager
async def open_s3(
    *,
    bucket: str,
    prefix: str,
    region: str | None,
    endpoint_url: str | None,
    addressing_style: Literal["auto", "path", "virtual"],
    access_key_id: str | None,
    secret_access_key: str | None,
    max_bytes: int,
    timeout: float,
) -> AsyncIterator[S3Objects]:
    """Credentials default to the standard AWS provider chain when no static keys are configured."""
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            get_session().create_client(
                "s3",
                region_name=region,
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=AioConfig(
                    connect_timeout=timeout,
                    read_timeout=timeout,
                    retries={"max_attempts": 2},
                    s3={"addressing_style": addressing_style},
                ),
            )
        )
        yield S3Objects(cast("S3Client", client), bucket, prefix=prefix, max_bytes=max_bytes, timeout=timeout)
