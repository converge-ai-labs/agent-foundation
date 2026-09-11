"""Single-request timing and exact-key cleanup after uncertain S3 writes."""

from contextlib import asynccontextmanager
from hashlib import sha256

import pytest
from botocore.exceptions import ClientError

from ..performance.operation_config import OperationConfig
from ..performance.operations import Benchmark
from ..performance.s3_operations import measure_s3


class Body:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self):
        return self.value


class S3:
    def __init__(self, *, lose_put_response=False, fail_cleanup=False):
        self.objects = {"unrelated": b"preserve"}
        self.requests = []
        self.lose_put_response, self.fail_cleanup = lose_put_response, fail_cleanup

    async def put_object(self, *, Bucket, Key, Body, IfMatch=None):
        self.requests.append(("put", Key))
        current = self.objects.get(Key)
        if IfMatch is not None and (current is None or sha256(current).hexdigest() != IfMatch):
            raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        self.objects[Key] = Body
        if self.lose_put_response:
            self.lose_put_response = False
            raise RuntimeError("lost PUT response after persistence")
        return {"ETag": sha256(Body).hexdigest(), "ResponseMetadata": {"HTTPStatusCode": 200}}

    async def get_object(self, *, Bucket, Key):
        self.requests.append(("get", Key))
        return {"Body": Body(self.objects[Key])}

    async def head_object(self, *, Bucket, Key):
        self.requests.append(("head", Key))
        if Key not in self.objects:
            raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    async def delete_object(self, *, Bucket, Key):
        self.requests.append(("delete", Key))
        if self.fail_cleanup:
            raise RuntimeError("delete unavailable")
        self.objects.pop(Key, None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}


@pytest.mark.anyio
async def test_each_timed_s3_operation_makes_exactly_one_request(tmp_path):
    client = S3()
    config = OperationConfig(concurrency=[2], samples=2, warmup_waves=0, payload_bytes=[32])
    benchmark = Benchmark(tmp_path / "report.json", samples=2, warmup=0, environment={})
    measure = benchmark.measure

    async def inspect(scenario, concurrency, prepare, **kwargs):
        @asynccontextmanager
        async def wrapped(count):
            async with prepare(count) as selected:
                for operation in selected:
                    original = operation.call

                    async def call(action=original):
                        before = len(client.requests)
                        try:
                            return await action()
                        finally:
                            assert len(client.requests) == before + 1

                    operation.call = call
                yield selected

        return await measure(scenario, concurrency, wrapped, **kwargs)

    benchmark.measure = inspect
    await measure_s3(benchmark, client, "test", config)
    benchmark.finish()
    assert len(benchmark.report["cells"]) == 7
    assert client.objects == {"unrelated": b"preserve"}


@pytest.mark.anyio
@pytest.mark.parametrize("fail_cleanup", [False, True])
async def test_lost_put_response_cleans_only_registered_keys_and_reports_cleanup_failures(tmp_path, fail_cleanup):
    client = S3(lose_put_response=True, fail_cleanup=fail_cleanup)
    config = OperationConfig(concurrency=[2], samples=2, warmup_waves=0, payload_bytes=[32], scenarios=["s3.put"])
    benchmark = Benchmark(tmp_path / "report.json", samples=2, warmup=0, environment={})
    with pytest.raises(ExceptionGroup if fail_cleanup else AssertionError):
        await measure_s3(benchmark, client, "test", config)
    written = {key for operation, key in client.requests if operation == "put"}
    deleted = {key for operation, key in client.requests if operation == "delete"}
    assert len(written) == 2 and deleted == written
    assert "unrelated" not in deleted and client.objects["unrelated"] == b"preserve"
    assert benchmark.report["status"] == "failed"
    assert benchmark.report["cells"][0]["summary"]["error"]["n"] == 1
    if fail_cleanup:
        assert set(benchmark.report["cleanup_remaining_keys"]) == written
    else:
        assert client.objects == {"unrelated": b"preserve"}
