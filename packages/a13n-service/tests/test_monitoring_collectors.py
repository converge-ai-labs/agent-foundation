"""Protected collector files, cancellation ownership, reconciliation and reader authority."""

import asyncio
import json
import os
import threading
from dataclasses import replace

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
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

from .test_metrics_queries import backend, builder
from .test_monitoring_proxy import controlled, external_row

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
