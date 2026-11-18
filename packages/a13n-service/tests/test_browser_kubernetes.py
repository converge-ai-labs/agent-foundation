from unittest.mock import AsyncMock

import pytest
from a13n_service.providers.browser import kubernetes as k8s
from a13n_service.providers.browser.definition import BrowserCheckpoint, BrowserProviderError
from kubernetes_asyncio.config.config_exception import ConfigException

pytestmark = pytest.mark.anyio


@pytest.fixture
def provider(monkeypatch):
    cluster = k8s.Cluster(namespaces=("browsers",), ownership_id="test", image="browser:local", service_name="browser")
    adapter = k8s.Kubernetes(k8s.Configuration(cluster="in-cluster", namespace="browsers"), cluster, AsyncMock())
    monkeypatch.setattr(adapter, "call", AsyncMock())
    return adapter


def allocation(provider, *, replicas=0, generation=1, observed=1, uid="first"):
    obj = provider.manifest("browser_test")
    obj["metadata"].update(uid=uid, generation=generation, resourceVersion="12")
    obj["spec"]["replicas"] = replicas
    obj["status"] = {"observedGeneration": observed, "replicas": replicas}
    return obj


async def test_unknown_create_only_reads_and_persists_inert_identity(provider):
    provider.call.side_effect = [None, allocation(provider)]
    with pytest.raises(BrowserProviderError, match="Browser provider"):
        await provider.reconcile_create("browser_test", None)
    result = await provider.reconcile_create("browser_test", None)
    assert isinstance(result, BrowserCheckpoint)
    assert result.state["uid"] == "first"
    assert [call.args[:2] for call in provider.call.await_args_list] == [("sets", "GET")] * 2


async def test_delete_checkpoints_late_create_without_adopting_replacement(provider):
    provider.call.side_effect = [None, allocation(provider), allocation(provider, uid="replacement")]
    with pytest.raises(BrowserProviderError) as missing:
        await provider.delete("browser_test", None)
    assert missing.value.code == "kubernetes_create_unresolved"
    result = await provider.delete("browser_test", None)
    assert isinstance(result, BrowserCheckpoint)
    with pytest.raises(BrowserProviderError) as replaced:
        await provider.delete("browser_test", result.state)
    assert replaced.value.code == "kubernetes_statefulset_replaced"
    assert [call.args[:2] for call in provider.call.await_args_list] == [("sets", "GET")] * 3


async def test_release_fences_an_activation_on_same_uid_and_resource_version(provider, monkeypatch):
    obj = allocation(provider, replicas=1)
    terminal = allocation(provider, generation=2, observed=2)
    terminal["metadata"]["annotations"] = {k8s.TERMINAL: "true", k8s.GENERATION: "1"}
    provider.call.side_effect = [obj, {}, terminal, None, {}, None, None, None, None]
    monkeypatch.setattr(k8s.asyncio, "sleep", AsyncMock())
    state = k8s.State(allocation_id="browser_test", uid="first").model_dump()
    assert await provider.delete("browser_test", state) is None
    patch = provider.call.await_args_list[1].args[3]
    assert patch["metadata"]["resourceVersion"] == "12"
    assert patch["metadata"]["uid"] == "first"
    assert patch["metadata"]["annotations"][k8s.TERMINAL] == "true"
    assert patch["spec"]["replicas"] == 0
    provider.call.side_effect = [terminal]
    with pytest.raises(BrowserProviderError) as fenced:
        await provider.start("browser_test", state, generation=2)
    assert fenced.value.code == "kubernetes_start_fenced"


async def test_stop_waits_for_controller_even_when_pod_is_absent(provider, monkeypatch):
    old = allocation(provider, generation=3, observed=2)
    settled = allocation(provider, generation=3, observed=3)
    provider.call.side_effect = [old, None, settled, None, None]
    sleep = AsyncMock()
    monkeypatch.setattr(k8s.asyncio, "sleep", sleep)
    state = k8s.State(allocation_id="browser_test", uid="first").model_dump()
    assert (await provider.stop("browser_test", state)).status == "stopped"
    sleep.assert_awaited_once()


async def test_cluster_initialization_failure_is_not_dispatched(provider, monkeypatch):
    monkeypatch.setattr(k8s, "open_cluster_client", AsyncMock(side_effect=ConfigException()))
    with pytest.raises(BrowserProviderError) as failure:
        await k8s.open_provider(provider.config, {"in-cluster": provider.cluster})
    assert failure.value.certainty == "not_dispatched"
