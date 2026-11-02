"""Cloud provider lifecycle invariants using fake supplier responses."""

import json

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.providers.browser.cloud import (
    Browserbase,
    BrowserbaseConfiguration,
    BrowserbaseRecipe,
    CloudAPI,
    CloudCredential,
    Kernel,
    KernelConfiguration,
    KernelRecipe,
)
from a13n_service.providers.browser.definition import BrowserProviderError
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


def api(supplier, handler):
    return CloudAPI(
        supplier, CloudCredential(api_key=SecretStr("test")), EndpointPolicy(), transport=httpx2.MockTransport(handler)
    )


async def test_browserbase_exact_session_lifecycle():
    calls = []
    item = {
        "id": "session-1",
        "projectId": "project",
        "userMetadata": {"a13n_allocation": "alloc-1"},
        "status": "RUNNING",
        "connectUrl": "wss://example.com/cdp",
    }

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/sessions":
            assert json.loads(request.content)["keepAlive"] is True
            return httpx2.Response(200, json={"id": "session-1"})
        if request.method == "POST":
            item["status"] = "COMPLETED"
            return httpx2.Response(200, json={})
        return httpx2.Response(200, json=item)

    provider = Browserbase(BrowserbaseConfiguration(project_id="project"), api("browserbase", handler))
    try:
        live = await provider.create("alloc-1", BrowserbaseRecipe())
        assert live.runtime_id == "session-1"
        assert (await provider.inspect("alloc-1", live.state)).runtime_id == "session-1"
        await provider.delete("alloc-1", live.state)
        assert await provider.inspect("alloc-1", live.state) is None
        assert calls.count(("POST", "/v1/sessions")) == 1
    finally:
        await provider.close()


async def test_kernel_unknown_create_is_never_resolved_by_mutable_history():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        assert request.url.path == "/auth/context"
        return httpx2.Response(
            200, json={"organization": {"id": "org"}, "authorization": {"effective_scope": {"project_id": None}}}
        )

    provider = Kernel(KernelConfiguration(organization_id="org"), api("kernel", handler))
    try:
        for action in (provider.inspect, provider.delete):
            with pytest.raises(BrowserProviderError) as error:
                await action("alloc-1", None)
            assert error.value.code.endswith("unresolved")
        assert paths == ["/auth/context", "/auth/context"]
    finally:
        await provider.close()


async def test_kernel_exact_session_and_scope():
    calls = []
    item = {
        "session_id": "session-1",
        "name": "alloc-1",
        "tags": {"a13n_allocation": "alloc-1"},
        "cdp_ws_url": "wss://example.com/cdp",
        "deleted_at": None,
    }

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/auth/context":
            return httpx2.Response(
                200, json={"organization": {"id": "org"}, "authorization": {"effective_scope": {"project_id": None}}}
            )
        if request.method == "POST":
            return httpx2.Response(200, json=item)
        if request.method == "DELETE":
            item["deleted_at"] = "2026-11-02T00:00:00Z"
            return httpx2.Response(200, json={})
        return httpx2.Response(200, json=item)

    provider = Kernel(KernelConfiguration(organization_id="org"), api("kernel", handler))
    try:
        live = await provider.create("alloc-1", KernelRecipe())
        await provider.delete("alloc-1", live.state)
        assert await provider.inspect("alloc-1", live.state) is None
        assert calls.count(("POST", "/browsers")) == 1
    finally:
        await provider.close()


async def test_kernel_rotated_account_does_not_dispatch_create():
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx2.Response(
            200, json={"organization": {"id": "other"}, "authorization": {"effective_scope": {"project_id": None}}}
        )

    provider = Kernel(KernelConfiguration(organization_id="org"), api("kernel", handler))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.create("alloc-1", KernelRecipe())
        assert error.value.certainty == "not_dispatched"
        assert calls == ["/auth/context"]
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "supplier_url",
    [
        "wss://connect.browserbase.com?apiKey=fixture-secret",
        "wss://gateway.onkernel.com/browser/session?token=fixture-secret",
    ],
)
async def test_connect_validates_authority_without_disclosing_supplier_query(monkeypatch, supplier_url):
    from a13n_service.providers.browser.cloud import Chromium

    seen = []
    sentinel = object()

    async def connect(url, *, timeout, discover):
        seen.append((url, timeout, discover))
        return sentinel

    monkeypatch.setattr(Chromium, "connect", connect)
    client = api("browserbase", lambda request: httpx2.Response(500))
    provider = Browserbase(BrowserbaseConfiguration(project_id="project"), client)
    try:
        assert await provider.connect({"cdp_url": supplier_url}) is sentinel
        assert seen == [(supplier_url, 15, False)]
        for bad in ("ws://example.com/?token=x", "wss://user@example.com/?token=x", "wss://example.com/#secret"):
            with pytest.raises(BrowserProviderError):
                await provider.connect({"cdp_url": bad})
        assert len(seen) == 1
    finally:
        await provider.close()


async def test_browserbase_pending_is_not_ready_and_delete_requires_terminal():
    item = {
        "id": "session-1",
        "projectId": "project",
        "userMetadata": {"a13n_allocation": "alloc-1"},
        "status": "PENDING",
        "connectUrl": "wss://example.com/cdp",
    }

    async def handler(request):
        return httpx2.Response(200, json=item)

    provider = Browserbase(BrowserbaseConfiguration(project_id="project"), api("browserbase", handler))
    try:
        with pytest.raises(BrowserProviderError, match="Browser provider") as error:
            await provider.inspect("alloc-1", {"id": "session-1"})
        assert error.value.code == "cloud_session_not_ready"
        with pytest.raises(BrowserProviderError) as error:
            await provider.delete("alloc-1", {"id": "session-1"})
        assert error.value.code == "cloud_delete_unresolved"
    finally:
        await provider.close()


async def test_connect_error_does_not_expose_credential(monkeypatch):
    from a13n_service.providers.browser.cloud import Chromium

    async def connect(url, *, timeout, discover):
        raise ValueError(url)

    monkeypatch.setattr(Chromium, "connect", connect)
    provider = Browserbase(BrowserbaseConfiguration(project_id="project"), api("browserbase", lambda request: None))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.connect({"cdp_url": "wss://example.com/?token=fixture-secret"})
        assert error.value.code == "cloud_connect_failed"
        assert "fixture-secret" not in str(error.value)
        assert error.value.__suppress_context__
    finally:
        await provider.close()


async def test_browserbase_unknown_no_match_and_ambiguous_never_confirm_deletion():
    items = []

    async def handler(request):
        assert request.method == "GET"
        assert request.url.params["q"] == "user_metadata['a13n_allocation']:'alloc-1'"
        return httpx2.Response(200, json=items)

    provider = Browserbase(BrowserbaseConfiguration(project_id="project"), api("browserbase", handler))
    try:
        assert await provider.inspect("alloc-1", None) is None
        with pytest.raises(BrowserProviderError) as error:
            await provider.delete("alloc-1", None)
        assert error.value.code == "cloud_delete_unresolved"
        item = {"id": "session-1", "projectId": "project", "userMetadata": {"a13n_allocation": "alloc-1"}}
        items.extend([item, {**item, "id": "session-2"}])
        with pytest.raises(BrowserProviderError) as error:
            await provider.delete("alloc-1", None)
        assert error.value.code == "cloud_ambiguous_allocation"
        with pytest.raises(BrowserProviderError) as error:
            await provider.inspect("alloc-1' OR 1=1", None)
        assert error.value.certainty == "not_dispatched"
    finally:
        await provider.close()


async def test_kernel_identity_mismatch_never_deletes():
    calls = []

    async def handler(request):
        calls.append(request.method)
        if request.url.path == "/auth/context":
            return httpx2.Response(
                200, json={"organization": {"id": "org"}, "authorization": {"effective_scope": {"project_id": None}}}
            )
        return httpx2.Response(
            200, json={"session_id": "other", "name": "alloc-1", "tags": {"a13n_allocation": "alloc-1"}}
        )

    provider = Kernel(KernelConfiguration(organization_id="org"), api("kernel", handler))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.delete("alloc-1", {"id": "session-1"})
        assert error.value.code == "cloud_identity_mismatch"
        assert "DELETE" not in calls
    finally:
        await provider.close()


@pytest.mark.parametrize("failure", ["status", "transport", "json"])
async def test_kernel_scope_preflight_failure_is_not_create_dispatch(failure):
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if failure == "status":
            return httpx2.Response(503)
        if failure == "transport":
            raise httpx2.ReadTimeout("sensitive", request=request)
        return httpx2.Response(200, content=b"not json")

    provider = Kernel(KernelConfiguration(organization_id="org"), api("kernel", handler))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.create("alloc-1", KernelRecipe())
        assert error.value.certainty == "not_dispatched"
        assert error.value.code == ("cloud_invalid_response" if failure == "json" else "cloud_request_failed")
        assert calls == [("GET", "/auth/context")]
    finally:
        await provider.close()


async def test_kernel_preflight_failure_reconciles_to_first_create(service, runs_kit, monkeypatch):
    from dataclasses import replace

    from a13n_service.distribution import OSS
    from a13n_service.infra.db import transaction
    from a13n_service.providers.browser import cloud
    from a13n_service.providers.browser.builtins import builtins
    from a13n_service.providers.registry import Registry
    from a13n_service.runs.browsers import lifecycle
    from a13n_service.runs.browsers.tables import BrowserRow
    from a13n_service.settings import Browsers

    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/auth/context":
            if len(calls) == 1:
                return httpx2.Response(503)
            return httpx2.Response(
                200, json={"organization": {"id": "org"}, "authorization": {"effective_scope": {"project_id": None}}}
            )
        body = json.loads(request.content)
        return httpx2.Response(200, json={**body, "session_id": "session-1", "cdp_ws_url": "wss://example.com/cdp"})

    original_init = cloud.CloudAPI.__init__

    def init(self, supplier, credential, policy, **kwargs):
        original_init(self, supplier, credential, policy, transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(cloud.CloudAPI, "__init__", init)
    await runs_kit.pause_sweeps(service)
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, *builtins(Browsers(root=None)))))
    service.app.state.runtime = service.runtime
    account = await service.client.post(
        f"{service.api}/browser-providers",
        json={
            "type": "kernel",
            "name": "Kernel",
            "config": {"organization_id": "org"},
            "credential": {"api_key": "test"},
        },
    )
    assert account.status_code == 201, account.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": account.json()["id"], "name": "Kernel browser", "recipe": {}}
    )
    assert response.status_code == 201, response.text
    browser = response.json()
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "creating" and not row.create_dispatched
    assert calls == [("GET", "/auth/context")]
    await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "ready"
    assert calls == [("GET", "/auth/context"), ("GET", "/auth/context"), ("POST", "/browsers")]
