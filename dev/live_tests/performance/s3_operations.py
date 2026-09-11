"""Exactly one S3 SDK request per sample; GET includes the complete response body."""

from contextlib import asynccontextmanager
from uuid import uuid4

import anyio
from botocore.exceptions import ClientError

from .operations import Operation


async def measure_s3(benchmark, client, bucket, config):
    for size in config.payload_bytes:
        body = b"s" * size
        for concurrency in config.concurrency:
            for name in (
                "put",
                "get",
                "head",
                "delete",
                "conditional_put",
                "stale_conditional_put",
                "contended_conditional_put",
            ):
                if f"s3.{name}" not in config.scenarios:
                    continue

                @asynccontextmanager
                async def prepare(count, operation=name, payload=body):
                    contended = operation == "contended_conditional_put"
                    keys = [f"bounded-operations/{uuid4().hex}" for _ in range(1 if contended else count)]
                    calls = []
                    winners = []
                    try:
                        versions = {}
                        for key in keys:
                            etag = None
                            if operation != "put":
                                response = await client.put_object(Bucket=bucket, Key=key, Body=payload)
                                etag = response["ETag"]
                                if operation == "stale_conditional_put":
                                    await client.put_object(Bucket=bucket, Key=key, Body=b"changed")
                            versions[key] = etag
                        for index in range(count):
                            key = keys[0] if contended else keys[index]
                            etag = versions[key]

                            async def call(selected=key, version=etag):
                                request = {"Bucket": bucket, "Key": selected}
                                if operation == "get":
                                    response = await client.get_object(**request)
                                    async with response["Body"] as reader:
                                        return await reader.read()
                                if operation == "head":
                                    return await client.head_object(**request)
                                if operation == "delete":
                                    return await client.delete_object(**request)
                                if operation in {
                                    "conditional_put",
                                    "stale_conditional_put",
                                    "contended_conditional_put",
                                }:
                                    request["IfMatch"] = version
                                return await client.put_object(
                                    **request, Body=b"u" * len(payload) if "conditional" in operation else payload
                                )

                            async def verify(result, selected=key):
                                if contended and isinstance(result, ClientError):
                                    assert result.response["ResponseMetadata"]["HTTPStatusCode"] in {409, 412}
                                elif operation == "stale_conditional_put":
                                    assert result.response["ResponseMetadata"]["HTTPStatusCode"] == 412
                                elif operation == "get":
                                    assert result == payload
                                elif operation == "head":
                                    assert result["ContentLength"] == len(payload)
                                elif operation == "delete":
                                    assert result["ResponseMetadata"]["HTTPStatusCode"] == 204
                                else:
                                    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
                                    winners.append(selected)
                                if operation == "delete":
                                    try:
                                        await client.head_object(Bucket=bucket, Key=selected)
                                    except ClientError as error:
                                        assert error.response["ResponseMetadata"]["HTTPStatusCode"] == 404
                                    else:
                                        raise AssertionError("Deleted object still exists")
                                    return
                                response = await client.get_object(Bucket=bucket, Key=selected)
                                async with response["Body"] as reader:
                                    expected = (
                                        b"changed"
                                        if operation == "stale_conditional_put"
                                        else b"u" * len(payload)
                                        if "conditional" in operation
                                        else payload
                                    )
                                    assert await reader.read() == expected

                            calls.append(
                                Operation(
                                    call,
                                    verify,
                                    ClientError if operation == "stale_conditional_put" or contended else None,
                                    allow_success=contended,
                                )
                            )
                        yield calls
                        if contended:
                            assert len(winners) == 1, "Conditional same-key write must have exactly one winner"
                    finally:
                        # Own only these exact random keys, including lost PUT responses.
                        failures = []
                        with anyio.CancelScope(shield=True):
                            for key in keys:
                                try:
                                    with anyio.fail_after(35):
                                        await client.delete_object(Bucket=bucket, Key=key)
                                except Exception as error:
                                    failures.append(error)
                                    benchmark.report.setdefault("cleanup_remaining_keys", []).append(key)
                        if failures:
                            benchmark.report["status"] = "failed"
                            benchmark.save()
                            raise ExceptionGroup("Could not remove exact benchmark-owned S3 keys", failures)

                await benchmark.measure(
                    f"s3.{name}",
                    concurrency,
                    prepare,
                    configuration={
                        "payload_bytes": size,
                        "keys": "shared" if name == "contended_conditional_put" else "independent",
                        "pool_size": config.s3_pool_size,
                        "sdk_attempts": 1,
                    },
                    boundary="One S3 HTTP request including signing, HTTP pool wait and response; GET drains the entire body; no follow-up HEAD in the timer",
                )
