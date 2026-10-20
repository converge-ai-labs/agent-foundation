"""Protected collector files, cancellation ownership, reconciliation and reader authority."""

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers import collectors
from a13n_service.providers.environment.metrics import ScrapeTarget, scrape_target
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.runs.environments.monitoring import collection, pull
from a13n_service.runs.environments.monitoring.query import collector_status
from a13n_service.settings import Monitoring
from pydantic import SecretStr, ValidationError
from sqlalchemy import text

from .test_metrics_queries import backend, builder
from .test_monitoring_proxy import controlled, external_row, ineligible_rows

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(os.name != "posix", reason="collector writers use POSIX locks")]
POLICY = EndpointPolicy()


def configuration(path, endpoint="http://127.0.0.1:9", *, writer=True):
    return collectors.Configuration(directory=path, endpoint=endpoint, writer=writer)


def test_render_preserves_route_identity_and_private_credentials():
    target = ScrapeTarget(
        "https://target.example/gateway",
        SecretStr("listener-secret"),
        False,
        {"X-Proxy-Auth": SecretStr("proxy-secret")},
    )
    job = collectors.Job("env_a", "ws_a", "org_a", target)
    data = json.loads(collectors.render("vmagent", [job], receiver=None, interval=10, timeout=5))[0]
    assert data["metrics_path"] == "/gateway/metrics"
    assert data["authorization"]["credentials"] == "listener-secret"
    assert data["headers"] == ["X-Proxy-Auth: proxy-secret"]
    assert data["tls_config"]["insecure_skip_verify"]
    assert data["static_configs"] == [{"targets": ["target.example"], "labels": job.labels}]
    assert not data["follow_redirects"] and not data["honor_labels"]
    alloy = collectors.render(
        "alloy", [job], receiver="prometheus.remote_write.operator.receiver", interval=10, timeout=5
    )
    assert 'http_headers = {"X-Proxy-Auth" = ["proxy-secret"]}' in alloy
    assert 'metrics_path = "/gateway/metrics"' in alloy
    assert "prometheus.remote_write.operator.receiver" in alloy
    assert "secret" not in repr(job) and "secret" not in repr(target)


def test_route_discovery_preserves_additional_headers_and_requires_portable_tls():
    runtime = HttpEnvdProviderRuntime(
        HttpEnvdConnectionConfiguration(endpoint="https://target.example"),
        HttpEnvdCredential(token=SecretStr("listener-secret"), headers={"X-Proxy-Auth": SecretStr("proxy-secret")}),
    )
    target = scrape_target(runtime)
    assert target.headers["X-Proxy-Auth"].get_secret_value() == "proxy-secret"
    assert target.verify_tls
    runtime.verify = "/operator-only/ca.pem"
    with pytest.raises(NotImplementedError):
        scrape_target(runtime)


def test_collector_configuration_requires_explicit_alloy_routing(tmp_path):
    assert not configuration(tmp_path, writer=False).writer
    with pytest.raises(ValidationError, match="receiver"):
        Monitoring(collectors={"alloy": configuration(tmp_path)})
    with pytest.raises(ValidationError):
        collectors.Configuration(directory=tmp_path, endpoint="https://user:pass@example.com")


async def test_atomic_file_reload_uses_protected_credentials_and_preserves_operator_files(tmp_path):
    operator = tmp_path / "operator.yml"
    operator.write_text("scrape_configs: []\n")
    async with backend() as (endpoint, calls):
        config = configuration(tmp_path, endpoint).model_copy(
            update={"headers": {"Authorization": SecretStr("reload-secret")}}
        )
        async with collectors.writer_lock(config, "vmagent") as acquired:
            assert acquired
            async with collectors.writer_lock(config, "vmagent") as overlap:
                assert not overlap
            await collectors.apply(POLICY, config, "vmagent", "[]\n", timeout=2)
        path = collectors.fragment_path(config, "vmagent")
        assert path.read_text() == "[]\n"
        assert path.stat().st_mode & 0o777 == 0o600
        assert operator.read_text() == "scrape_configs: []\n"
        assert calls[0][0] == "POST /-/reload HTTP/1.1"
        assert calls[0][1]["authorization"] == "reload-secret"
        assert calls[0][2] == {}  # reload reads the shared file; it is not an upload.


async def test_http_reload_exception_is_explicit_and_scoped_to_this_origin(tmp_path, monkeypatch):
    policy = EndpointPolicy.from_http_origins(require_https=True, http_origins=["http://existing.example"])
    async with backend() as (endpoint, calls):
        config = configuration(tmp_path, endpoint)
        with pytest.raises(EndpointPolicyError, match="HTTPS"):
            await collectors.apply(policy, config, "vmagent", "[]\n", timeout=2)
        assert not calls
        original = collectors.open_http

        @asynccontextmanager
        async def checked(scoped, **kwargs):
            await scoped.validate(endpoint + "/-/reload")
            await scoped.validate("http://existing.example")
            with pytest.raises(EndpointPolicyError, match="HTTPS"):
                await scoped.validate("http://other.example")
            with pytest.raises(EndpointPolicyError, match="HTTPS"):
                await scoped.validate_redirect(endpoint, "http://other.example")
            async with original(scoped, **kwargs) as client:
                yield client

        monkeypatch.setattr(collectors, "open_http", checked)
        await collectors.apply(
            policy, config.model_copy(update={"allow_http_reload": True}), "vmagent", "[]\n", timeout=2
        )
        assert calls[0][0] == "POST /-/reload HTTP/1.1"
        with pytest.raises(EndpointPolicyError, match="HTTPS"):
            await policy.validate(endpoint)  # Shared policy and target/query callers remain unchanged.


@pytest.mark.parametrize("phase", ["acquire", "replace"])
async def test_asyncio_cancellation_joins_file_worker_before_unlocking(tmp_path, monkeypatch, phase):
    config = configuration(tmp_path)
    entered, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    notified = asyncio.Event()
    owner = collectors._Lock if phase == "acquire" else collectors
    name = "acquire" if phase == "acquire" else "_replace"
    original = getattr(owner, name)

    def gated(*args):
        result = original(*args)
        entered.set()
        loop.call_soon_threadsafe(notified.set)
        assert release.wait(5)
        return result

    monkeypatch.setattr(owner, name, gated)

    async def write():
        async with collectors.writer_lock(config, "vmagent") as acquired:
            assert acquired
            await collectors.apply(POLICY, config, "vmagent", "[]\n", timeout=1)

    task = asyncio.create_task(write())
    try:
        await asyncio.wait_for(notified.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)  # Deliver cancellation while the worker still owns its descriptor.
        assert not task.done()
        monkeypatch.setattr(owner, name, original)
        async with collectors.writer_lock(config, "vmagent") as overlap:
            assert not overlap
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with collectors.writer_lock(config, "vmagent") as acquired:
        assert acquired


async def configure_service(service, tmp_path, endpoint):
    await controlled(service)
    config = configuration(tmp_path, endpoint)
    settings = service.runtime.settings.model_copy(
        update={
            "monitoring": service.runtime.settings.monitoring.model_copy(update={"collectors": {"vmagent": config}}),
        }
    )
    service.runtime = replace(service.runtime, settings=settings)
    service.app.state.runtime = service.runtime
    return config


async def source(service):
    result = await service.client.post(
        f"{service.api}/metrics-data-sources",
        json={
            "name": "Pull",
            "config": {"collection": "pull", "collector": "vmagent"},
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


async def test_ineligible_targets_do_not_consume_collector_capacity(service, tmp_path):
    await configure_service(service, tmp_path, "http://127.0.0.1:9")
    runtime = service.runtime
    runtime = replace(
        runtime,
        settings=runtime.settings.model_copy(
            update={
                "monitoring": runtime.settings.monitoring.model_copy(update={"collector_targets": 1}),
            }
        ),
    )
    source_id = await source(service)
    ineligible = await ineligible_rows(service, source_id)
    ready = await external_row(service, source_id)
    selected, truncated, failures = await pull.selections(runtime, "vmagent")
    assert not failures
    assert [item.target_id for item in selected] == [ready]
    assert not truncated
    for identity, expected in zip(
        ineligible, ("not_ready", "not_ready", "unsupported", "unsupported", "unsupported"), strict=True
    ):
        status = await service.client.get(f"{service.api}/environments/{identity}/metrics/collector")
        assert status.status_code == 200, status.text
        assert status.json()["state"] == expected
    await external_row(service, source_id)
    selected, truncated, failures = await pull.selections(runtime, "vmagent")
    assert not failures
    assert len(selected) == 1 and truncated


@pytest.mark.parametrize("failed", [False, True])
async def test_collector_scan_budget_counts_ineligible_and_failed_rows(service, tmp_path, monkeypatch, failed):
    await configure_service(service, tmp_path, "http://127.0.0.1:9")
    runtime = replace(
        service.runtime,
        settings=service.runtime.settings.model_copy(
            update={
                "monitoring": service.runtime.settings.monitoring.model_copy(update={"collector_targets": 1}),
            }
        ),
    )
    source_id = await source(service)
    for _ in range(6):
        await external_row(service, source_id)
    original = collection.selected
    scanned = []

    async def selected(runtime, session, env, source):
        scanned.append(env.id)
        if failed:
            raise ValueError("broken target")
        return replace(await original(runtime, session, env, source), target=None)

    monkeypatch.setattr(collection, "selected", selected)
    items, truncated, failures = await pull.selections(runtime, "vmagent")
    assert not items and truncated
    assert len(scanned) == 4
    assert len(failures) == (1 if failed else 0)


async def test_missing_config_is_discoverable_without_fallback(service):
    await controlled(service)
    response = await service.client.get(f"{service.api}/metrics-data-sources/capabilities")
    assert response.status_code == 200, response.text
    assert all(not item["available"] and item["reason"] == "not_configured" for item in response.json()["collectors"])
    identity = await external_row(service, await source(service))
    status = await service.client.get(f"{service.api}/environments/{identity}/metrics/collector")
    assert status.json()["state"] == "not_configured"
    assert not service.runtime.metrics_proxy.entries


async def test_reconcile_fences_changed_targets_and_removes_disabled_sources(service, tmp_path, monkeypatch):
    async with backend() as (endpoint, calls):
        config = await configure_service(service, tmp_path, endpoint)
        source_id = await source(service)
        owner, other = await builder(service), await builder(service)
        identity = await external_row(service, source_id, owner_id=owner.id)
        real_scrape = collection.scrape

        async def scrape(runtime, selection):
            assert runtime.storage.engine.pool.checkedout() == 0
            return await real_scrape(runtime, selection)

        monkeypatch.setattr(collection, "scrape", scrape)
        await pull.reconcile(service.runtime, "vmagent")
        content = collectors.fragment_path(config, "vmagent").read_text()
        assert "test-observation-token" in content
        status = await collector_status(service.runtime, owner, service.tenant.workspace_id, identity)
        assert status.state == "reload_requested"  # No assertion of asynchronous collector application.
        with pytest.raises(ServiceError) as forbidden:
            await collector_status(service.runtime, other, service.tenant.workspace_id, identity)
        assert forbidden.value.code == "forbidden"
        assert not service.runtime.metrics_proxy.entries

        async def changed(runtime, selection):
            result = await real_scrape(runtime, selection)
            async with transaction(runtime.storage) as session:
                row = await session.get_one(DataSourceRow, source_id)
                row.enabled = False
            return result

        monkeypatch.setattr(collection, "scrape", changed)
        await pull.reconcile(service.runtime, "vmagent")
        assert json.loads(collectors.fragment_path(config, "vmagent").read_text()) == []
        assert len(calls) == 2
        status = await collector_status(service.runtime, owner, service.tenant.workspace_id, identity)
        assert status.state == "disabled"


async def test_reload_failure_is_explicit_and_reader_does_not_write(service, tmp_path):
    async with backend(status=500) as (endpoint, calls):
        config = await configure_service(service, tmp_path, endpoint)
        identity = await external_row(service, await source(service))
        await pull.reconcile(service.runtime, "vmagent")
        response = await service.client.get(f"{service.api}/environments/{identity}/metrics/collector")
        assert response.json()["state"] == "failed"
        assert "test-observation-token" not in response.text
        config = config.model_copy(update={"writer": False})
        settings = service.runtime.settings.model_copy(
            update={
                "monitoring": service.runtime.settings.monitoring.model_copy(update={"collectors": {"vmagent": config}})
            }
        )
        await pull.reconcile(replace(service.runtime, settings=settings), "vmagent")
        assert len(calls) == 1
        assert not service.runtime.metrics_proxy.entries


@pytest.mark.parametrize("failure", ["sql", "config", "recheck"])
async def test_bad_selection_does_not_poison_healthy_target_or_publish_bad_job(service, tmp_path, monkeypatch, failure):
    async with backend() as (endpoint, calls):
        config = await configure_service(service, tmp_path, endpoint)
        source_id = await source(service)
        ids = sorted([await external_row(service, source_id), await external_row(service, source_id)])
        bad, healthy = ids
        real_selected = collection.selected
        attempts = 0

        async def selected(runtime, session, env, source):
            nonlocal attempts
            if env.id == bad:
                attempts += 1
                if failure == "sql":
                    await session.execute(text("SELECT 1 / 0"))
                if failure != "recheck" or attempts > 1:
                    raise ValueError("private-broken-configuration")
            # This query fails if another target left this session's transaction aborted.
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
            return await real_selected(runtime, session, env, source)

        monkeypatch.setattr(collection, "selected", selected)
        await pull.reconcile(service.runtime, "vmagent")
        jobs = json.loads(collectors.fragment_path(config, "vmagent").read_text())
        assert [job["static_configs"][0]["labels"]["environment_id"] for job in jobs] == [healthy]
        receipt = pull.Receipt.model_validate_json(await service.runtime.redis.get(pull.receipt_key("vmagent")))
        assert receipt.state == "reload_requested"
        assert receipt.targets[bad].state == "discovery_failed"
        assert receipt.targets[healthy].state == "reload_requested"
        assert len(calls) == 1
        # Status retains the owner check and reports selection failures without exposing details.
        from a13n_service.runs.environments.monitoring import query as query_module

        monkeypatch.setattr(query_module, "selected", selected)
        response = await service.client.get(f"{service.api}/environments/{bad}/metrics/collector")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "discovery_failed"
        assert "private-broken" not in response.text


async def test_writer_setup_failure_records_failed_and_cancellation_propagates(service, tmp_path, monkeypatch):
    await configure_service(service, tmp_path / "missing", "http://127.0.0.1:9")
    identity = await external_row(service, await source(service))
    await pull.reconcile(service.runtime, "vmagent")
    response = await service.client.get(f"{service.api}/environments/{identity}/metrics/collector")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "failed"
    await service.runtime.redis.delete(pull.receipt_key("vmagent"))
    await configure_service(service, tmp_path, "http://127.0.0.1:9")

    async def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(collection, "selected", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await pull.reconcile(service.runtime, "vmagent")
    assert await service.runtime.redis.get(pull.receipt_key("vmagent")) is None
    async with collectors.writer_lock(service.runtime.settings.monitoring.collectors["vmagent"], "vmagent") as acquired:
        assert acquired


async def test_fragment_replacement_recovers_only_its_own_crash_staging_files(tmp_path):
    config = configuration(tmp_path)
    path = collectors.fragment_path(config, "vmagent")
    abandoned = tmp_path / f".{path.name}.write-crashed"
    other = tmp_path / ".a13n-managed.alloy.write-active"
    operator = tmp_path / "operator.yml"
    for file in (abandoned, other, operator):
        file.write_text("protected-fixture")
    async with collectors.writer_lock(config, "vmagent") as acquired:
        assert acquired
        await collectors._file_io(collectors._replace, path, "[]\n")
    assert not abandoned.exists()
    assert other.read_text() == operator.read_text() == "protected-fixture"
    assert path.read_text() == "[]\n"
    assert path.stat().st_mode & 0o777 == 0o600
