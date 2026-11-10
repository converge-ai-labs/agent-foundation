"""S3-compatible object store; keys are written once, so every write is a plain `PutObject`."""

from asyncio import CancelledError
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, Literal, cast

from a13n_logging import get_logger
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from anyio import fail_after
from botocore.exceptions import BotoCoreError, ClientError

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.diagnostics import CURRENT, Diagnostics, ObservedSession, Operation
from a13n_service.infra.objects.interface import ObjectRef, reference, validate_key

if TYPE_CHECKING:
    from collections.abc import Iterator

    from types_aiobotocore_s3.client import S3Client

logger = get_logger(__name__)


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
        self.diagnostics = Diagnostics()

    def _key(self, key: str) -> str:
        return self.prefix + validate_key(key)

    @contextmanager
    def _deadline(self, operation: str) -> "Iterator[Operation]":
        observation = Operation()
        token = CURRENT.set(observation)
        outcome = "success"
        error_type = None
        try:
            with fail_after(self.timeout):
                yield observation
        except TimeoutError as error:
            outcome = "operation_timeout"
            logger.warning(
                "Object store request timed out",
                extra=dict(
                    operation=operation,
                    timeout_seconds=self.timeout,
                    error_type=type(error).__name__,
                    **observation.fields(),
                ),
            )
            raise _unavailable(error) from None
        except CancelledError:
            error_type = "CancelledError"
            outcome = "caller_cancelled"
            raise
        except ClientError as error:
            outcome = "not_found" if operation == "get" and _code(error) in {"NoSuchKey", "404"} else "error"
            error_type = type(error).__name__
            raise
        except BaseException as error:
            outcome = "error"
            error_type = type(error).__name__
            raise
        finally:
            fields = observation.fields()
            self.diagnostics.record(operation, outcome, fields)
            if outcome != "operation_timeout" and (
                outcome not in ("success", "not_found") or fields["duration_ms"] >= self.timeout * 500
            ):
                logger.warning(
                    "Object store operation observed",
                    extra=dict(
                        operation=operation,
                        outcome=outcome,
                        error_type=error_type,
                        timeout_seconds=self.timeout,
                        **fields,
                    ),
                )
            CURRENT.reset(token)

    async def put(self, key: str, data: bytes, *, content_type: str) -> ObjectRef:
        if len(data) > self.max_bytes:
            raise ServiceError("payload_too_large", "Object exceeds its byte limit", {"limit": self.max_bytes})
        try:
            with self._deadline("put") as observation:
                observation.payload_bytes = len(data)
                await self.client.put_object(
                    Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type
                )
        except (ClientError, BotoCoreError) as error:
            raise _unavailable(error) from None
        return reference(key, data, content_type)

    async def get(self, key: str) -> bytes | None:
        try:
            with self._deadline("get") as observation:
                with observation.timing("sdk_request"):
                    response = await self.client.get_object(Bucket=self.bucket, Key=self._key(key))
                observation.payload_bytes = response["ContentLength"]
                if response["ContentLength"] > self.max_bytes:
                    raise ServiceError("unavailable", "Stored object exceeds its byte limit", {"dependency": "objects"})
                with observation.timing("body_read"):
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
            with self._deadline("list"):
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
            with self._deadline("delete"):
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
    max_pool_connections: int,
) -> AsyncGenerator[S3Objects]:
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
                    http_session_cls=ObservedSession,
                    max_pool_connections=max_pool_connections,
                    connect_timeout=timeout,
                    read_timeout=timeout,
                    retries={"max_attempts": 2},
                    s3={"addressing_style": addressing_style},
                ),
            )
        )
        yield S3Objects(cast("S3Client", client), bucket, prefix=prefix, max_bytes=max_bytes, timeout=timeout)
