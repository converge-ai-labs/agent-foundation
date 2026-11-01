"""Real workspace asset authority and publication around a fake private Browser filesystem."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.capabilities.browser import BrowserError
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.browser.definition import BrowserProvider
from a13n_service.resources.assets.schemas import AssetCreate
from a13n_service.resources.assets.service import create_asset, read_asset_content
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.uploads.service import stage
from a13n_service.runs.browsers import execution, files
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal

from .test_browsers import bound_run, setup

pytestmark = pytest.mark.anyio


async def fixture(service, runs_kit, scripted_model, monkeypatch):
    _, browser = await setup(service, runs_kit)
    lease, binding = await bound_run(service, runs_kit, scripted_model, browser)
    invocation = SimpleNamespace(invocation=SimpleNamespace(tool_call_id="file-call", tool_name="browser_files"))
    monkeypatch.setattr(files, "current_invocation_scope", lambda: invocation)
    monkeypatch.setattr(execution, "current_invocation_scope", lambda: invocation)
    provider = AsyncMock(spec=BrowserProvider)
    provider.upload_file.return_value = "/private/uploads/file.txt"
    provider.download_file.return_value = b"downloaded"
    transfer = files.Files(service.runtime, lease, browser["id"], 1, provider, {"runtime": "one"})
    actor = Principal(
        service.tenant.principal_id,
        "user",
        (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["builder"]),),
    )
    return transfer, actor, binding


async def asset(service, actor, workspace_id=None):
    workspace_id = workspace_id or service.tenant.workspace_id
    upload = await stage(
        service.runtime.storage,
        service.runtime.objects,
        actor,
        workspace_id,
        request_key="browser-input",
        filename="input.txt",
        content_type="text/plain",
        content=b"upload bytes",
    )
    result, _ = await create_asset(
        service.runtime.storage, actor, workspace_id, AssetCreate(upload_id=upload.upload_id, name=upload.filename)
    )
    return result


async def test_upload_assets_enforces_scope_retirement_paths_and_size(service, runs_kit, scripted_model, monkeypatch):
    transfer, actor, _ = await fixture(service, runs_kit, scripted_model, monkeypatch)
    item = await asset(service, actor)
    native = AsyncMock()
    result = await transfer.upload(native, "input", (f"asset:{item.id}",), revision="fresh")
    assert "Workspace asset" in result
    transfer.provider.upload_file.assert_awaited_once_with(transfer.state, "input.txt", b"upload bytes")
    native.upload.assert_awaited_once_with("input", ("/private/uploads/file.txt",), revision="fresh")
    transfer.provider.upload_file.reset_mock()
    with pytest.raises(BrowserError, match="asset references"):
        await transfer.upload(native, "input", ("/etc/passwd",), revision="fresh")
    bounded = replace(
        transfer,
        runtime=replace(
            transfer.runtime,
            settings=transfer.runtime.settings.model_copy(
                update={"objects": transfer.runtime.settings.objects.model_copy(update={"max_bytes": 1})}
            ),
        ),
    )
    with pytest.raises(BrowserError, match="size limit"):
        await bounded.upload(native, "input", (f"asset:{item.id}",), revision="fresh")
    response = await service.client.post(
        f"/api/v1/organizations/{service.tenant.organization_id}/workspaces", json={"name": "Other"}
    )
    assert response.status_code == 201, response.text
    other = await asset(service, actor, response.json()["id"])
    with pytest.raises(ServiceError, match="not usable"):
        await transfer.upload(native, "input", (f"asset:{other.id}",), revision="fresh")
    async with transaction(service.runtime.storage) as session:
        row = await session.get(AssetRow, item.id)
        from datetime import UTC, datetime

        row.retired_at = datetime.now(UTC)
    with pytest.raises(ServiceError, match="not usable"):
        await transfer.upload(native, "input", (f"asset:{item.id}",), revision="fresh")
    transfer.provider.upload_file.assert_not_awaited()


async def test_download_publication_is_deduplicated_scoped_and_hides_paths(
    service, runs_kit, scripted_model, monkeypatch
):
    transfer, actor, _ = await fixture(service, runs_kit, scripted_model, monkeypatch)
    native = AsyncMock()
    native.downloads.return_value = json.dumps(
        {
            "downloads": [
                {"name": "report.txt", "state": "completed", "path": "/private/downloads/guid-one"},
                {"name": "pending.txt", "state": "inProgress", "path": None},
            ],
            "omitted": 2,
        }
    )
    first = json.loads(await transfer.downloads(native))
    second = json.loads(await transfer.downloads(native))
    assert first == second and first["omitted"] == 2
    assert "path" not in first["downloads"][0] and "path" not in first["downloads"][1]
    item, content = await read_asset_content(
        service.runtime.storage,
        service.runtime.objects,
        actor,
        transfer.lease.workspace_id,
        first["downloads"][0]["asset_id"],
    )
    assert content == b"downloaded"
    assert item.source["run_id"] == transfer.lease.run_id
    assert item.source["tool_call_id"] == "file-call"
    assert [call.args[1] for call in transfer.provider.download_file.await_args_list] == ["guid-one"]
    # The frozen Run ceiling, not a broader current actor grant, owns publication authority.
    original = files.lock_owner

    async def limited(*args):
        run, principal = await original(*args)
        return SimpleNamespace(authority={**run.authority, "verbs": ["read", "run"]}), principal

    monkeypatch.setattr(files, "lock_owner", limited)
    transfer.provider.download_file.reset_mock()
    with pytest.raises(ServiceError, match="does not cover"):
        await transfer.downloads(native)
    transfer.provider.download_file.assert_not_awaited()


async def test_unsupported_provider_never_exposes_host_download_paths(service, runs_kit, scripted_model, monkeypatch):
    transfer, _, binding = await fixture(service, runs_kit, scripted_model, monkeypatch)
    check = SimpleNamespace(tool_call=AsyncMock())
    async with execution.open_backend(
        service.runtime, transfer.lease, binding, check, frozenset({"tabs", "files"})
    ) as backend:
        await backend.tabs("new")
        with pytest.raises(BrowserError, match="authorized file transfer"):
            await backend.downloads()
        with pytest.raises(BrowserError, match="authorized file transfer"):
            await backend.upload("input", ("/private/file",), revision="fresh")


@pytest.mark.parametrize("failure", ["asset_refused", "known", "unknown"])
async def test_file_failures_preserve_only_unknown_permits(service, runs_kit, scripted_model, monkeypatch, failure):
    from a13n_service.infra.db import short_session
    from a13n_service.providers.browser.definition import BrowserProviderError
    from a13n_service.runs.browsers import lifecycle

    transfer, _, binding = await fixture(service, runs_kit, scripted_model, monkeypatch)
    error = (
        ServiceError("forbidden", "Asset access refused")
        if failure == "asset_refused"
        else BrowserProviderError("file_failed", certainty=failure)
    )
    async with execution.open_backend(
        service.runtime, transfer.lease, binding, SimpleNamespace(tool_call=AsyncMock()), frozenset({"tabs", "files"})
    ) as backend:
        await backend.tabs("new")
        backend.files = SimpleNamespace(downloads=AsyncMock(side_effect=error))
        with pytest.raises(BrowserError) as raised:
            await backend.downloads()
        assert (raised.value.dispatch == "unknown") == (failure == "unknown")
    async with short_session(service.runtime.storage) as session:
        assert bool(await lifecycle.unsettled(session, binding.browser_id)) == (failure == "unknown")


async def test_download_identity_is_shared_across_concurrent_principals(service, runs_kit, scripted_model, monkeypatch):
    import asyncio

    from a13n_service.infra.db import short_session
    from a13n_service.runs.browsers.tables import BrowserDownloadRow
    from sqlalchemy import select

    from .test_metrics_queries import builder

    first, actor, _ = await fixture(service, runs_kit, scripted_model, monkeypatch)
    second_actor = await builder(service)
    second = replace(first)
    # Isolate publication arbitration from attempt authorization, covered above with real frozen ceilings.
    monkeypatch.setattr(first, "authorize", AsyncMock(return_value=actor))
    monkeypatch.setattr(second, "authorize", AsyncMock(return_value=second_actor))
    native = AsyncMock()
    native.downloads.return_value = json.dumps(
        {"downloads": [{"name": "shared.txt", "state": "completed", "path": "/private/downloads/shared-guid"}]}
    )
    both_read = asyncio.Event()
    readers = 0

    async def download(*args, **kwargs):
        nonlocal readers
        readers += 1
        if readers == 2:
            both_read.set()
        await asyncio.wait_for(both_read.wait(), 5)
        return b"shared bytes"

    first.provider.download_file.side_effect = download
    reports = await asyncio.gather(first.downloads(native), second.downloads(native))
    assert reports[0] == reports[1]
    async with short_session(service.runtime.storage) as session:
        canonical = (await session.scalars(select(BrowserDownloadRow))).one()
        asset = (await session.scalars(select(AssetRow))).one()
        assert asset.content_ref.endswith("/" + canonical.upload_id)
    first.provider.download_file.reset_mock()
    assert await second.downloads(native) == reports[0]
    first.provider.download_file.assert_not_awaited()
    first.authorize.assert_awaited()
    second.authorize.assert_awaited()
