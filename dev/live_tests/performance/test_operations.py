"""Explicitly opted-in PG/S3 calls and native Service operation measurements."""

import logging

import pytest
from a13n_service.configuration.sources import load_settings
from a13n_service.database.metadata import service_metadata
from a13n_service.storage.object_store import S3ObjectStore
from a13n_service.storage.relational import create_sql_engine

from .operation_config import read_config
from .operations import Benchmark
from .pg_operations import measure_postgres
from .s3_config import S3ConnectionConfig, open_s3_client
from .s3_operations import measure_s3
from .scenario_report import generate
from .service_fixtures import ServiceFixture
from .service_operations import measure_service

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.anyio


async def test_bounded_operations(request):
    if not request.config.getoption("--live-performance"):
        pytest.skip("Opt in with make live-test-performance; no infrastructure starts by default")
    try:
        config = read_config(request.config.getoption("--performance-profile"))
    except (OSError, ValueError) as error:
        raise pytest.UsageError(f"Invalid operation profile: {error}") from error
    from ..infrastructure.round_two_lab import open_lab

    service_metadata()
    async with open_lab(performance=config) as lab:
        env = lab.environment
        connection = S3ConnectionConfig(
            dedicated_test_bucket=True,
            endpoint_url=env["A13N_SERVICE_OBJECT_ENDPOINT_URL"],
            region=env["A13N_SERVICE_OBJECT_REGION"],
            bucket=env["A13N_SERVICE_OBJECT_BUCKET"],
            access_key=env["AWS_ACCESS_KEY_ID"],
            secret_key=env["AWS_SECRET_ACCESS_KEY"],
        )
        database = (
            load_settings(environ=env)
            .storage_settings()
            .database.model_copy(update={"pool_size": config.pg_pool_size, "max_overflow": 0})
        )
        engine = create_sql_engine(database)
        report = Benchmark(
            lab.root / "operations.json",
            samples=config.samples,
            warmup=config.warmup_waves,
            environment={
                "postgres": "owned PostgreSQL 17",
                "s3": "owned RustFS",
                "workers": 0,
                "control": "fixture preparation only; PG pool <=8",
                "profile": config.model_dump(),
            },
            budgets=config.budgets_ms,
        )
        try:
            async with open_s3_client(connection, pool_size=config.s3_pool_size) as s3:
                service = ServiceFixture(lab, engine, S3ObjectStore(s3, connection.bucket))
                report.report["environment"]["service_preparations"] = service.preparations
                await measure_postgres(report, engine, config)
                await measure_s3(report, s3, connection.bucket, config)
                await measure_service(
                    report,
                    config,
                    service,
                )
            report.finish()
        except BaseException:
            report.report["status"] = "failed"
            report.save()
            raise
        finally:
            await engine.dispose()
            if report.report["cells"]:
                generate([report.path], lab.root / "performance.md")
            logger.info("operation_report path=%s table=%s", report.path, lab.root / "performance.md")
