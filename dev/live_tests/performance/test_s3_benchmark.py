"""Single-request S3 matrix against an explicitly configured test bucket."""

from uuid import uuid4

import pytest

from .operation_config import read_config
from .operations import Benchmark
from .s3_config import STATE_ROOT, load_s3_config, open_s3_client
from .s3_operations import measure_s3
from .scenario_report import generate

pytestmark = pytest.mark.anyio


async def test_configured_s3_calls(request):
    try:
        connection = load_s3_config(enabled=request.config.getoption("--live-performance"))
        if connection is None:
            pytest.skip("Opt in and configure the private Provider [s3] section")
        config = read_config(request.config.getoption("--performance-profile"))
    except (OSError, ValueError) as error:
        raise pytest.UsageError(str(error)) from error
    root = STATE_ROOT / uuid4().hex
    root.mkdir(parents=True)
    benchmark = Benchmark(
        root / "operations.json",
        samples=config.samples,
        warmup=config.warmup_waves,
        environment={"s3": "configured endpoint", "profile": config.model_dump()},
        budgets=config.budgets_ms,
    )
    try:
        async with open_s3_client(connection, pool_size=config.s3_pool_size) as client:
            versioning = await client.get_bucket_versioning(Bucket=connection.bucket)
            if versioning.get("Status") in {"Enabled", "Suspended"}:
                raise ValueError("Configured benchmark requires an unversioned test bucket")
            await measure_s3(benchmark, client, connection.bucket, config)
        benchmark.finish()
    except BaseException:
        benchmark.report["status"] = "failed"
        benchmark.save()
        raise
    finally:
        if benchmark.report["cells"]:
            generate([benchmark.path], root / "performance.md")
        print(f"Configured S3 operation report: {benchmark.path}")
