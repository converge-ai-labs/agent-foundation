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
        before = len(calls)
        await provider.delete("alloc-1", live.state)
        assert calls[before:] == [
            ("GET", "/v1/sessions/session-1"),
            ("POST", "/v1/sessions/session-1"),
            ("GET", "/v1/sessions/session-1"),
        ]
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
        before = len(calls)
        await provider.delete("alloc-1", live.state)
        assert calls[before:] == [
            ("GET", "/auth/context"),
            ("GET", "/browsers/session-1"),
            ("DELETE", "/browsers/session-1"),
            ("GET", "/browsers/session-1"),
        ]
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


@pytest.mark.parametrize("supplier", ["browserbase", "kernel"])
@pytest.mark.parametrize(
    "status,certainty",
    [
        (400, "rejected"),
        (401, "rejected"),
        (403, "rejected"),
        (404, "unknown"),
        (408, "unknown"),
        (409, "unknown"),
        (422, "unknown"),
        (429, "unknown"),
        (500, "unknown"),
    ],
)
async def test_create_rejection_is_distinct_from_unknown(supplier, status, certainty):
    async def handler(request):
        if request.url.path == "/auth/context":
            return httpx2.Response(
                200, json={"organization": {"id": "org"}, "authorization": {"effective_scope": {"project_id": None}}}
            )
        return httpx2.Response(status)

    client = api(supplier, handler)
    provider = (
        Browserbase(BrowserbaseConfiguration(project_id="project"), client)
        if supplier == "browserbase"
        else Kernel(KernelConfiguration(organization_id="org"), client)
    )
    recipe = BrowserbaseRecipe() if supplier == "browserbase" else KernelRecipe()
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.create("alloc-1", recipe)
        assert error.value.certainty == certainty
    finally:
        await provider.close()


def test_release_only_shortcuts_proven_unallocated_creates():
    from a13n_service.runs.browsers.lifecycle import release_intent
    from a13n_service.runs.browsers.tables import BrowserRow

    for dispatched, expected in [(False, "deleted"), (True, "deleting")]:
        row = BrowserRow(status="creating", source="managed", create_dispatched=dispatched)
        release_intent(row)
        assert row.status == expected


@pytest.mark.parametrize("supplier", ["browserbase", "kernel"])
@pytest.mark.parametrize("release", [True, False])
async def test_rejected_create_can_release_or_retry(service, runs_kit, monkeypatch, supplier, release):
    from dataclasses import replace

    from a13n_service.distribution import OSS
    from a13n_service.infra.db import transaction
    from a13n_service.providers.browser import cloud
    from a13n_service.providers.browser.builtins import builtins
    from a13n_service.providers.registry import Registry
    from a13n_service.runs.browsers import lifecycle
    from a13n_service.runs.browsers.tables import BrowserRow
    from a13n_service.settings import Browsers

    posts = 0

    async def handler(request):
        nonlocal posts
        if request.url.path == "/auth/context":
            return httpx2.Response(
                200, json={"organization": {"id": "org"}, "authorization": {"effective_scope": {"project_id": None}}}
            )
        if request.method == "POST":
            posts += 1
            if posts == 1:
                return httpx2.Response(400)
            body = json.loads(request.content)
            if supplier == "kernel":
                return httpx2.Response(200, json={**body, "session_id": "s", "cdp_ws_url": "wss://example.com/cdp"})
            return httpx2.Response(200, json={"id": "s"})
        return httpx2.Response(
            200,
            json={
                "id": "s",
                "projectId": "project",
                "userMetadata": {"a13n_allocation": browser["id"]},
                "status": "RUNNING",
                "connectUrl": "wss://example.com/cdp",
            },
        )

    original_init = cloud.CloudAPI.__init__

    def init(self, supplier, credential, policy, **kwargs):
        original_init(self, supplier, credential, policy, transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(cloud.CloudAPI, "__init__", init)
    await runs_kit.pause_sweeps(service)
    service.runtime = replace(service.runtime, registry=Registry.of((*OSS.providers, *builtins(Browsers(root=None)))))
    service.app.state.runtime = service.runtime
    config = {"project_id": "project"} if supplier == "browserbase" else {"organization_id": "org"}
    account = await service.client.post(
        f"{service.api}/browser-providers",
        json={"type": supplier, "name": supplier, "config": config, "credential": {"api_key": "test"}},
    )
    assert account.status_code == 201, account.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": account.json()["id"], "name": "Browser", "recipe": {}}
    )
    assert response.status_code == 201, response.text
    browser = response.json()
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "creating" and not row.create_dispatched
        if release:
            lifecycle.release_intent(row)
            assert row.status == "deleted"
    if not release:
        await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(BrowserRow, browser["id"])
            assert row.status == "ready"
    assert posts == (1 if release else 2)


async def test_browserbase_confirmation_failure_does_not_recreate(service, runs_kit, monkeypatch):
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
        if request.method == "POST":
            return httpx2.Response(200, json={"id": "s"})
        return httpx2.Response(401)

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
            "type": "browserbase",
            "name": "BB",
            "config": {"project_id": "project"},
            "credential": {"api_key": "test"},
        },
    )
    assert account.status_code == 201, account.text
    response = await service.client.post(
        f"{service.api}/browsers", json={"provider_id": account.json()["id"], "name": "Browser", "recipe": {}}
    )
    assert response.status_code == 201, response.text
    browser = response.json()
    await lifecycle.prepare(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "creating" and row.create_dispatched
    await lifecycle.reconcile(service.runtime, browser["workspace_id"], browser["id"])
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(BrowserRow, browser["id"])
        assert row.status == "creating" and row.create_dispatched
        lifecycle.release_intent(row)
        assert row.status == "deleting"
    assert sum(method == "POST" for method, _ in calls) == 1


async def test_browser_use_lifecycle_and_scope():
    from a13n_service.providers.browser.cloud import BrowserUse, BrowserUseConfiguration, BrowserUseRecipe

    item = {
        "id": "session-1",
        "status": "active",
        "metadata": {"a13n_allocation": "alloc-1"},
        "timeoutAt": "2026-11-03T08:00:00Z",
        "cdpUrl": "wss://example.com/cdp?token=secret",
    }
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v2/billing/account":
            return httpx2.Response(200, json={"projectId": "project"})
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["proxyCountryCode"] is None and body["solveCaptchas"] is False
            return httpx2.Response(201, json=item)
        if request.method == "PATCH":
            item["status"] = "stopped"
        return httpx2.Response(200, json=item)

    provider = BrowserUse(BrowserUseConfiguration(project_id="project"), api("browser_use", handler))
    try:
        live = await provider.create("alloc-1", BrowserUseRecipe())
        assert (await provider.inspect("alloc-1", live.state)).runtime_id == live.runtime_id
        await provider.delete("alloc-1", live.state)
        assert await provider.inspect("alloc-1", live.state) is None
        assert calls.count(("POST", "/api/v4/browsers")) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize("timeout", [None, 123, "not-a-date"])
async def test_browser_use_invalid_timeout_is_rejected(timeout):
    from a13n_service.providers.browser.cloud import BrowserUse, BrowserUseConfiguration

    async def handler(request):
        return httpx2.Response(200)

    provider = BrowserUse(BrowserUseConfiguration(project_id="project"), api("browser_use", handler))
    try:
        item = {"id": "session-1", "status": "active", "cdpUrl": "wss://example.com/cdp", "timeoutAt": timeout}
        with pytest.raises(BrowserProviderError) as error:
            provider._live("alloc-1", item)
        assert error.value.code == "cloud_invalid_response"
    finally:
        await provider.close()


async def test_browser_use_wrong_account_never_dispatches_create():
    from a13n_service.providers.browser.cloud import BrowserUse, BrowserUseConfiguration, BrowserUseRecipe

    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx2.Response(200, json={"projectId": "wrong"})

    provider = BrowserUse(BrowserUseConfiguration(project_id="project"), api("browser_use", handler))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.create("alloc-1", BrowserUseRecipe())
        assert error.value.code == "cloud_account_mismatch"
        assert error.value.certainty == "not_dispatched"
        assert calls == ["/api/v2/billing/account"]
    finally:
        await provider.close()


async def test_browser_use_unresolved_and_ambiguous_metadata_are_not_deleted():
    from a13n_service.providers.browser.cloud import BrowserUse, BrowserUseConfiguration

    items = []
    methods = []

    async def handler(request):
        methods.append(request.method)
        if request.url.path == "/api/v2/billing/account":
            return httpx2.Response(200, json={"projectId": "project"})
        return httpx2.Response(200, json={"items": items, "pageNumber": 1, "pageSize": 100, "totalItems": len(items)})

    provider = BrowserUse(BrowserUseConfiguration(project_id="project"), api("browser_use", handler))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.inspect("alloc-1", None)
        assert error.value.code == "cloud_inspect_unresolved"
        with pytest.raises(BrowserProviderError) as error:
            await provider.delete("alloc-1", None)
        assert error.value.code == "cloud_delete_unresolved"
        items.extend(
            [
                {"id": "s1", "metadata": {"a13n_allocation": "alloc-1"}},
                {"id": "s2", "metadata": {"a13n_allocation": "alloc-1"}},
            ]
        )
        with pytest.raises(BrowserProviderError) as error:
            await provider.delete("alloc-1", None)
        assert error.value.code == "cloud_ambiguous_allocation"
        assert set(methods) == {"GET"}
    finally:
        await provider.close()


async def test_browser_use_incomplete_filtered_results_are_not_absence():
    from a13n_service.providers.browser.cloud import BrowserUse, BrowserUseConfiguration

    async def handler(request):
        if request.url.path == "/api/v2/billing/account":
            return httpx2.Response(200, json={"projectId": "project"})
        return httpx2.Response(200, json={"items": [], "pageNumber": 1, "pageSize": 100, "totalItems": 1})

    provider = BrowserUse(BrowserUseConfiguration(project_id="project"), api("browser_use", handler))
    try:
        with pytest.raises(BrowserProviderError) as error:
            await provider.inspect("alloc-1", None)
        assert error.value.code == "cloud_invalid_response"
    finally:
        await provider.close()
