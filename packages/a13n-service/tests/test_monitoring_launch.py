"""Native launch snapshots survive desired-source changes and fenced creation retries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.environments import lifecycle
from a13n_service.runs.environments.monitoring.launch import restore
from a13n_service.runs.environments.tables import EnvironmentRow

pytestmark = pytest.mark.anyio


@pytest.fixture
async def launch_service(service):
    sweeps = [task for task in service.app.state.background if task.get_name() == "control-sweeps"]
    for task in sweeps:
        task.cancel()
    await asyncio.gather(*sweeps, return_exceptions=True)
    base = service.api
    source = await service.client.post(
        f"{base}/metrics-data-sources",
        json={
            "name": "Native",
            "config": {
                "collection": "native",
                "export": {"endpoint": "http://127.0.0.1:4318/v1/metrics"},
                "query": {"endpoint": "http://127.0.0.1:9090"},
            },
            "export_credentials": {"headers": {"authorization": "Bearer write-only,=% token"}},
            "query_credentials": {"headers": {"authorization": "read-only-token"}},
        },
    )
    assert source.status_code == 201, source.text
    provider = await service.client.post(
        f"{base}/environment-providers",
        json={
            "name": "Sandbox",
            "type": "e2b",
            "credential": {"api_key": "test-key"},
        },
    )
    assert provider.status_code == 201, provider.text
    template = await service.client.post(
        f"{base}/environment-templates",
        json={
            "name": "Envd",
            "provider_id": provider.json()["id"],
            "config": {"recipe": {"execution": "envd_http", "template": "team-envd"}},
        },
    )
    assert template.status_code == 201, template.text
    service.source, service.template = source, template
    return service


async def reserve(service, *, selected=True):
    response = await service.client.post(
        f"{service.api}/environments",
        json={
            "template_id": service.template.json()["id"],
            "metrics_data_source_id": service.source.json()["id"] if selected else None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_native_snapshot_is_private_frozen_and_fenced(launch_service, monkeypatch):
    service = launch_service
    runtime = service.runtime
    environment_id = await reserve(service)
    source = service.source.json()
    first = await lifecycle.claim(runtime, environment_id, owner="first")
    assert first is not None and first.metrics is not None
    values = first.metrics.environment
    assert values["OTEL_EXPORTER_OTLP_METRICS_HEADERS"] == "authorization=Bearer%20write-only%2C%3D%25%20token"
    assert "read-only-token" not in json.dumps(values)
    assert values["OTEL_METRIC_EXPORT_INTERVAL"] == "30000"
    assert f"environment_id={environment_id}" in values["OTEL_RESOURCE_ATTRIBUTES"]
    assert "write-only" not in repr(first)
    async with short_session(runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.metrics_application == {
            "source_id": source["id"],
            "source_version": source["version"],
            "collection": "native",
            "sample_interval_ms": 10_000,
            "applied_at": None,
        }
        assert "write-only" not in json.dumps([row.handle, row.metrics_launch, row.metrics_application])
        assert restore(runtime.keys, row) == first.metrics

    # A lost response settles no launch outcome. Live desired source updates do
    # not mutate credentials that might already have reached the native target.
    await lifecycle.publish(
        runtime, first, lifecycle.Outcome(None, lifecycle.Fault("lost", "Lost response", "unknown"))
    )
    config = source["config"]
    config["export"]["endpoint"] = "http://127.0.0.1:4319/v1/metrics"
    changed = await service.client.patch(
        f"{service.api}/metrics-data-sources/{source['id']}",
        json={
            "config": config,
            "export_credentials": {"headers": {"authorization": "replacement"}},
        },
        headers={"if-match": service.source.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    item = f"{service.api}/environments/{environment_id}"
    current = await service.client.get(item)
    cleared = await service.client.put(
        f"{item}/metrics-binding", json={"metrics_data_source_id": None}, headers={"if-match": current.headers["etag"]}
    )
    assert cleared.status_code == 200, cleared.text
    second = await lifecycle.claim(runtime, environment_id, owner="recovery")
    assert second is not None and second.operation_id == first.operation_id
    assert second.metrics == first.metrics
    await lifecycle.publish(runtime, first, lifecycle.Outcome(None))
    assert (await service.client.get(item)).json()["metrics_application"]["applied_at"] is None

    async def create(*args, **kwargs):
        assert kwargs["metrics"] == first.metrics
        assert "write-only" not in json.dumps(args)
        assert runtime.storage.engine.pool.checkedout() == 0
        return None

    provider = SimpleNamespace(create=AsyncMock(side_effect=create), close=AsyncMock())
    monkeypatch.setattr(lifecycle, "open_provider", AsyncMock(return_value=provider))
    outcome = await lifecycle.perform(runtime, second)
    assert outcome.fault is None
    await lifecycle.publish(runtime, second, outcome)
    view = (await service.client.get(item)).json()
    assert view["status"] == "ready" and view["metrics_data_source_id"] is None
    assert view["metrics_application"]["source_version"] == source["version"]
    assert view["metrics_application"]["applied_at"] is not None
    assert "write-only" not in json.dumps(view) and "metrics_launch" not in view
    async with transaction(runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.metrics_launch is None
        await lifecycle.begin(session, row, "starting")
    start = await lifecycle.claim(runtime, environment_id, owner="restart")
    assert start is not None and start.metrics is None


async def test_late_selection_does_not_rewrite_an_already_frozen_launch(launch_service):
    service = launch_service
    environment_id = await reserve(service, selected=False)
    first = await lifecycle.claim(service.runtime, environment_id, owner="first")
    assert first is not None and first.metrics is None
    item = f"{service.api}/environments/{environment_id}"
    current = await service.client.get(item)
    selected = await service.client.put(
        f"{item}/metrics-binding",
        json={"metrics_data_source_id": service.source.json()["id"]},
        headers={"if-match": current.headers["etag"]},
    )
    assert selected.status_code == 200, selected.text
    await lifecycle.publish(
        service.runtime, first, lifecycle.Outcome(None, lifecycle.Fault("lost", "Unknown", "unknown"))
    )
    retry = await lifecycle.claim(service.runtime, environment_id, owner="retry")
    assert retry is not None and retry.metrics is None
    await lifecycle.publish(service.runtime, retry, lifecycle.Outcome(None))
    assert (await service.client.get(item)).json()["metrics_application"] is None


async def test_native_query_uses_envd_sample_identity(launch_service, monkeypatch):
    from a13n_service.providers.metrics import Queries, QueryResult

    service = launch_service
    environment_id = await reserve(service)
    execute = AsyncMock(
        return_value=QueryResult.model_validate({"status": "success", "data": {"resultType": "matrix", "result": []}})
    )
    monkeypatch.setattr(Queries, "execute", execute)
    result = await service.client.post(
        f"{service.api}/environments/{environment_id}/metrics/query",
        json={"metric": "system_cpu_busy_ratio", "start": 100, "end": 120, "step": 10},
    )
    assert result.status_code == 200, result.text
    _, destination, query = execute.call_args.args
    assert query.expression == f'envd_system_cpu_busy_ratio{{device_id="{environment_id}"}}'
    assert destination.headers == {"authorization": "read-only-token"}


async def test_unsupported_native_launch_is_refused_before_recipe_freeze(launch_service):
    service = launch_service
    template = service.template.json()
    changed = await service.client.patch(
        f"{service.api}/environment-templates/{template['id']}",
        json={"config": {"recipe": {"execution": "native"}}},
        headers={"if-match": service.template.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    environment_id = await reserve(service)
    assert await lifecycle.claim(service.runtime, environment_id, owner="unsupported") is None
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.handle is None and row.metrics_launch is None and row.metrics_application is None
        assert row.failure["code"] == "native_metrics_unsupported"
        assert row.failure["certainty"] == "not_dispatched"


@pytest.mark.parametrize("collection", ["proxy", "pull"])
async def test_sampler_snapshot_survives_rebind_without_export_credentials(launch_service, monkeypatch, collection):
    service = launch_service
    if collection == "proxy":
        local = await service.client.get(f"{service.api}/metrics-data-sources")
        assert local.status_code == 200, local.text
        source = next(item for item in local.json()["items"] if item["builtin"])
    else:
        created = await service.client.post(
            f"{service.api}/metrics-data-sources",
            json={"name": "Pull", "config": {"collection": "pull", "collector": "vmagent"}},
        )
        assert created.status_code == 201, created.text
        source = created.json()
    service.source = SimpleNamespace(json=lambda: source)
    environment_id = await reserve(service)
    first = await lifecycle.claim(service.runtime, environment_id, owner="sampler")
    assert first is not None and first.metrics is not None
    assert first.metrics.environment == {}
    assert first.metrics.sample_interval_ms == 10_000
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert restore(service.runtime.keys, row) == first.metrics
        assert row.metrics_application["collection"] == collection
        assert row.metrics_application["applied_at"] is None
    item = f"{service.api}/environments/{environment_id}"
    current = await service.client.get(item)
    cleared = await service.client.put(
        f"{item}/metrics-binding",
        json={"metrics_data_source_id": None},
        headers={"if-match": current.headers["etag"]},
    )
    assert cleared.status_code == 200, cleared.text
    await lifecycle.publish(
        service.runtime, first, lifecycle.Outcome(None, lifecycle.Fault("lost", "Unknown", "unknown"))
    )
    retry = await lifecycle.claim(service.runtime, environment_id, owner="sampler-retry")
    assert retry is not None and retry.metrics == first.metrics
    provider = SimpleNamespace(create=AsyncMock(return_value=None), close=AsyncMock())
    monkeypatch.setattr(lifecycle, "open_provider", AsyncMock(return_value=provider))
    outcome = await lifecycle.perform(service.runtime, retry)
    assert outcome.fault is None
    assert provider.create.await_count == 1
    assert provider.create.call_args.kwargs["metrics"] == first.metrics
    provider.close.assert_awaited_once()
    await lifecycle.publish(service.runtime, retry, outcome)
    result = (await service.client.get(item)).json()
    assert result["metrics_data_source_id"] is None
    assert result["metrics_application"]["source_id"] == source["id"]
    assert result["metrics_application"]["collection"] == collection
    assert result["metrics_application"]["applied_at"] is not None
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, environment_id)
        assert row.metrics_launch is None
        await lifecycle.begin(session, row, "starting")
    restart = await lifecycle.claim(service.runtime, environment_id, owner="restart")
    assert restart is not None and restart.metrics is None
