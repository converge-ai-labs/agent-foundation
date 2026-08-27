"""S3-compatible object store adapter."""

from __future__ import annotations

import base64
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from anyio import create_task_group, move_on_after
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from aiobotocore.response import StreamingBody
    from types_aiobotocore_s3.client import S3Client
    from types_aiobotocore_s3.type_defs import (
        CompletedMultipartUploadTypeDef,
        CompletedPartTypeDef,
        CompleteMultipartUploadRequestTypeDef,
        CreateMultipartUploadRequestTypeDef,
        DeleteObjectRequestTypeDef,
        GetObjectRequestTypeDef,
        ListObjectsV2RequestTypeDef,
        PutObjectRequestTypeDef,
    )

from .api import (
    ByteRange,
    InvalidObjectRequest,
    ObjectConflict,
    ObjectInfo,
    ObjectNotFound,
    ObjectPage,
    ObjectReader,
    ObjectSource,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectSummary,
    iter_parts,
    normalize_metadata,
    validate_conditions,
    validate_key,
    validate_list_request,
)


class S3ObjectStore:
    def __init__(self, client: S3Client, bucket: str, *, multipart_part_size: int = 8 * 1024 * 1024) -> None:
        self._client = client
        self._bucket = bucket
        self._multipart_part_size = multipart_part_size

    async def put(
        self,
        key: str,
        source: ObjectSource,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectInfo:
        validate_key(key)
        validate_conditions(if_none_match=if_none_match, if_match=if_match)
        normalized_metadata = normalize_metadata(metadata)
        parts = iter_parts(source, self._multipart_part_size).__aiter__()
        first = await anext(parts, None)
        second = await anext(parts, None)
        try:
            if second is None:
                await self._put_small(
                    key,
                    first or b"",
                    content_type=content_type,
                    metadata=normalized_metadata,
                    if_none_match=if_none_match,
                    if_match=if_match,
                )
            else:
                await self._put_multipart(
                    key,
                    first or b"",
                    second,
                    parts,
                    content_type=content_type,
                    metadata=normalized_metadata,
                    if_none_match=if_none_match,
                    if_match=if_match,
                )
        except (BotoCoreError, ClientError) as error:
            raise _translate_error(error, key, conditional=if_none_match or if_match is not None) from error
        return await self.stat(key)

    @asynccontextmanager
    async def open(self, key: str, *, byte_range: ByteRange | None = None) -> AsyncIterator[ObjectReader]:
        validate_key(key)
        request: GetObjectRequestTypeDef = {"Bucket": self._bucket, "Key": key}
        if byte_range is not None:
            end = "" if byte_range.end_exclusive is None else str(byte_range.end_exclusive - 1)
            request["Range"] = f"bytes={byte_range.start}-{end}"
        try:
            response = await self._client.get_object(**request)
        except (BotoCoreError, ClientError) as error:
            raise _translate_error(error, key) from error
        body = response["Body"]
        try:
            size = _response_object_size(response.get("ContentRange"), response["ContentLength"])
            info = ObjectInfo(
                key=key,
                size=size,
                content_type=response.get("ContentType"),
                metadata=dict(response.get("Metadata", {})),
                modified_at=_utc(response["LastModified"]),
                version=_etag(response.get("ETag")),
            )
            yield _S3Reader(body, info)
        finally:
            with move_on_after(5, shield=True):
                closed = body.close()
                if inspect.isawaitable(closed):
                    await cast(Awaitable[None], closed)

    async def stat(self, key: str) -> ObjectInfo:
        validate_key(key)
        try:
            response = await self._client.head_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise _translate_error(error, key) from error
        return ObjectInfo(
            key=key,
            size=response["ContentLength"],
            content_type=response.get("ContentType"),
            metadata=dict(response.get("Metadata", {})),
            modified_at=_utc(response["LastModified"]),
            version=_etag(response.get("ETag")),
        )

    async def delete(self, key: str, *, if_match: str | None = None) -> None:
        validate_key(key)
        if if_match == "":
            raise InvalidObjectRequest("if_match must be non-empty")
        request: DeleteObjectRequestTypeDef = {"Bucket": self._bucket, "Key": key}
        if if_match is not None:
            request["IfMatch"] = if_match
        try:
            await self._client.delete_object(**request)
        except (BotoCoreError, ClientError) as error:
            translated = _translate_error(error, key, conditional=if_match is not None)
            if if_match is None and isinstance(translated, ObjectNotFound):
                return
            raise translated from error

    async def list(self, *, prefix: str = "", cursor: str | None = None, limit: int = 100) -> ObjectPage:
        validate_list_request(prefix, cursor, limit)
        request: ListObjectsV2RequestTypeDef = {"Bucket": self._bucket, "Prefix": prefix, "MaxKeys": limit}
        if cursor is not None:
            request["ContinuationToken"] = _decode_cursor(cursor, prefix)
        try:
            response = await self._client.list_objects_v2(**request)
        except (BotoCoreError, ClientError) as error:
            raise _translate_error(error, prefix) from error
        items = tuple(
            ObjectSummary(
                key=value["Key"],
                size=value["Size"],
                modified_at=_utc(value["LastModified"]),
                version=_etag(value.get("ETag")),
            )
            for value in response.get("Contents", [])
        )
        token = response.get("NextContinuationToken")
        return ObjectPage(items, _encode_cursor(prefix, token) if token else None)

    async def check_compatibility(self) -> None:
        try:
            await self._client.head_bucket(Bucket=self._bucket)
        except (BotoCoreError, ClientError) as error:
            raise _translate_error(error, self._bucket) from error

        prefix = f".a13n-storage-probe/{uuid.uuid4().hex}/"
        keys = [f"{prefix}{suffix}" for suffix in ("a", "b", "c", "race")]
        created: list[tuple[str, str]] = []
        try:
            first = await self.put(keys[0], b"alpha", if_none_match=True)
            created.append((keys[0], first.version))
            try:
                await self.put(keys[0], b"conflict", if_none_match=True)
            except ObjectConflict:
                pass
            else:
                raise ObjectStoreUnavailable("S3 endpoint does not enforce create-only writes")
            updated = await self.put(keys[0], b"updated", if_match=first.version)
            created[0] = (keys[0], updated.version)
            try:
                await self.delete(keys[0], if_match=first.version)
            except ObjectConflict:
                pass
            else:
                raise ObjectStoreUnavailable("S3 endpoint does not enforce conditional deletes")
            for key in keys[1:3]:
                info = await self.put(key, key.encode(), if_none_match=True)
                created.append((key, info.version))
            writes = 0

            async def create_once(value: bytes) -> None:
                nonlocal writes
                try:
                    await self.put(keys[3], value, if_none_match=True)
                except ObjectConflict:
                    return
                writes += 1

            async with create_task_group() as tasks:
                for index in range(4):
                    tasks.start_soon(create_once, str(index).encode())
            race = await self.stat(keys[3])
            created.append((keys[3], race.version))
            if writes != 1:
                raise ObjectStoreUnavailable("S3 endpoint does not serialize create-only writes")
            async with self.open(keys[0], byte_range=ByteRange(1, 4)) as reader:
                if b"".join([chunk async for chunk in reader]) != b"pda":
                    raise ObjectStoreUnavailable("S3 endpoint returned an invalid range")
            page = await self.list(prefix=prefix)
            if [item.key for item in page.items] != keys:
                raise ObjectStoreUnavailable("S3 endpoint does not preserve lexicographic listing")
        finally:
            with move_on_after(10, shield=True):
                for key, version in reversed(created):
                    try:
                        await self.delete(key, if_match=version)
                    except ObjectStoreError:
                        pass

    async def _put_small(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str | None,
        metadata: dict[str, str],
        if_none_match: bool,
        if_match: str | None,
    ) -> None:
        request: PutObjectRequestTypeDef = {
            "Body": body,
            "Bucket": self._bucket,
            "Key": key,
            "Metadata": metadata,
        }
        if content_type is not None:
            request["ContentType"] = content_type
        if if_none_match:
            request["IfNoneMatch"] = "*"
        if if_match is not None:
            request["IfMatch"] = if_match
        await self._client.put_object(**request)

    async def _put_multipart(
        self,
        key: str,
        first: bytes,
        second: bytes,
        remaining: AsyncIterator[bytes],
        *,
        content_type: str | None,
        metadata: dict[str, str],
        if_none_match: bool,
        if_match: str | None,
    ) -> None:
        create_request: CreateMultipartUploadRequestTypeDef = {
            "Bucket": self._bucket,
            "Key": key,
            "Metadata": metadata,
        }
        if content_type is not None:
            create_request["ContentType"] = content_type
        created = await self._client.create_multipart_upload(**create_request)
        upload_id = created["UploadId"]
        completed_parts: list[CompletedPartTypeDef] = []

        async def upload_parts() -> AsyncIterator[bytes]:
            yield first
            yield second
            async for part in remaining:
                yield part

        try:
            part_number = 1
            async for part in upload_parts():
                uploaded = await self._client.upload_part(
                    Bucket=self._bucket, Key=key, UploadId=upload_id, PartNumber=part_number, Body=part
                )
                completed_parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
                part_number += 1
            multipart: CompletedMultipartUploadTypeDef = {"Parts": completed_parts}
            request: CompleteMultipartUploadRequestTypeDef = {
                "Bucket": self._bucket,
                "Key": key,
                "UploadId": upload_id,
                "MultipartUpload": multipart,
            }
            if if_none_match:
                request["IfNoneMatch"] = "*"
            if if_match is not None:
                request["IfMatch"] = if_match
            await self._client.complete_multipart_upload(**request)
        except BaseException:
            with move_on_after(5, shield=True):
                try:
                    await self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)
                except (BotoCoreError, ClientError):
                    pass
            raise


class _S3Reader:
    def __init__(self, body: StreamingBody, info: ObjectInfo, *, chunk_size: int = 256 * 1024) -> None:
        self._body = body
        self.info = info
        self._chunk_size = chunk_size

    def __aiter__(self) -> _S3Reader:
        return self

    async def __anext__(self) -> bytes:
        chunk = await self._body.read(self._chunk_size)
        if not chunk:
            raise StopAsyncIteration
        return chunk


def _translate_error(error: BotoCoreError | ClientError, key: str, *, conditional: bool = False) -> ObjectStoreError:
    if isinstance(error, ClientError):
        response = error.response
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = response.get("Error", {}).get("Code")
        if status in {409, 412} or code in {"ConditionalRequestConflict", "PreconditionFailed"}:
            return ObjectConflict(key)
        if status == 404 or code in {"NoSuchKey", "NotFound", "404"}:
            return ObjectConflict(key) if conditional else ObjectNotFound(key)
        if status in {400, 416} or code == "InvalidRange":
            return InvalidObjectRequest(f"invalid S3 object request for {key}")
    return ObjectStoreUnavailable(f"S3 operation failed for {key}")


def _etag(value: str | None) -> str:
    if not value:
        raise ObjectStoreUnavailable("S3 response omitted an ETag")
    return value


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _response_object_size(content_range: str | None, content_length: int) -> int:
    if content_range is None:
        return content_length
    try:
        return int(content_range.rsplit("/", 1)[1])
    except (IndexError, ValueError) as error:
        raise ObjectStoreUnavailable("S3 response contained an invalid Content-Range") from error


def _encode_cursor(prefix: str, token: str) -> str:
    payload = json.dumps({"prefix": prefix, "token": token}, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, prefix: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if value["prefix"] != prefix or not isinstance(value["token"], str):
            raise ValueError
        return value["token"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise InvalidObjectRequest("invalid S3 object list cursor") from error
