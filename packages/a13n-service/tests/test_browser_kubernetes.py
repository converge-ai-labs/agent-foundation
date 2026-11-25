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


async def test_unknown_create_retries_only_an_inert_set_when_absent(provider):
    provider.call.side_effect = [None, BrowserProviderError("kubernetes_sets_409"), allocation(provider)]
    with pytest.raises(BrowserProviderError) as collision:
        await provider.reconcile_create("browser_test", None)
    assert collision.value.code == "kubernetes_sets_409"
    post = provider.call.await_args.kwargs["body"]
    assert post["metadata"]["name"] == provider.name("browser_test")
    assert post["spec"]["replicas"] == 0
    result = await provider.reconcile_create("browser_test", None)
    assert isinstance(result, BrowserCheckpoint)
    assert result.state["uid"] == "first"
    provider.call.assert_awaited_with("sets", "GET", provider.name("browser_test"))
    assert [call.args[:2] for call in provider.call.await_args_list] == [
        ("sets", "GET"),
        ("sets", "POST"),
        ("sets", "GET"),
    ]


async def test_release_can_finish_when_no_inert_set_was_observed(provider):
    provider.call.return_value = None
    assert await provider.delete("browser_test", None) is None
    provider.call.assert_awaited_once_with("sets", "GET", provider.name("browser_test"))


async def test_delete_checkpoints_late_create_without_adopting_replacement(provider):
    provider.call.side_effect = [allocation(provider), allocation(provider, uid="replacement")]
    result = await provider.delete("browser_test", None)
    assert isinstance(result, BrowserCheckpoint)
    with pytest.raises(BrowserProviderError) as replaced:
        await provider.delete("browser_test", result.state)
    assert replaced.value.code == "kubernetes_statefulset_replaced"
    assert [call.args[:2] for call in provider.call.await_args_list] == [("sets", "GET")] * 2


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


async def test_old_pod_identity_cannot_connect_or_grant_viewer(provider):
    pod = {
        "metadata": {
            "uid": "replacement",
            "labels": provider.labels("browser_test"),
            "ownerReferences": [{"uid": "first", "kind": "StatefulSet"}],
        }
    }
    provider.call.return_value = pod
    state = k8s.State(allocation_id="browser_test", uid="first", pod_uid="old", runtime_id="old")
    with pytest.raises(BrowserProviderError) as stale:
        await provider.connect(state.model_dump())
    assert stale.value.code == "kubernetes_browser_changed"
    assert await provider.viewer_operation(state, "grant_old", 1) is None
    assert [call.args[:2] for call in provider.call.await_args_list] == [("pods", "GET")] * 2
