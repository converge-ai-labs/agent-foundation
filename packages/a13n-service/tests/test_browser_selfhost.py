"""Allocation recovery preserves known non-dispatch versus unknown effects."""

import json
from unittest.mock import AsyncMock

import pytest
from a13n_service.providers.browser import runtime
from a13n_service.providers.browser.definition import BrowserProviderError, LiveBrowser
from a13n_service.providers.browser.native import Native
from a13n_service.providers.browser.selfhost import Recipe, SelfHosted

pytestmark = pytest.mark.anyio


async def test_create_can_retry_known_pre_dispatch_failure_but_not_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(SelfHosted, "__abstractmethods__", frozenset())
    provider = SelfHosted(tmp_path, "test")
    provider.provision = AsyncMock(side_effect=BrowserProviderError("image_missing", certainty="not_dispatched"))
    provider.launch = AsyncMock(return_value=LiveBrowser({}))
    provider.observe = AsyncMock(return_value=None)
    with pytest.raises(BrowserProviderError):
        await provider.create("browser_retry", Recipe())
    assert json.loads((tmp_path / "browser_retry/allocation.json").read_text()) == {}
    provider.provision.side_effect = None
    await provider.create("browser_retry", Recipe())
    assert provider.launch.await_count == 1

    provider.provision.side_effect = BrowserProviderError("transport_lost")
    with pytest.raises(BrowserProviderError):
        await provider.create("browser_unknown", Recipe())
    provider.provision.reset_mock()
    with pytest.raises(BrowserProviderError) as failed:
        await provider.create("browser_unknown", Recipe())
    assert failed.value.code == "allocation_create_unresolved"
    provider.provision.assert_not_awaited()
    await provider.delete("browser_unknown", None)
    with pytest.raises(BrowserProviderError) as deleted:
        await provider.create("browser_unknown", Recipe())
    assert deleted.value.code == "allocation_deleted"


async def test_native_unpublished_runtime_cannot_be_observed_stopped_or_restarted(tmp_path, monkeypatch):
    provider = Native(tmp_path, "chromium")
    monkeypatch.setattr(runtime, "group_alive", lambda pid: pid == 4567)
    async with provider.allocation("browser_pending") as allocation:
        allocation.record = {"backend": "native", "recipe": Recipe().model_dump(), "generation": 1}
        await provider.provision(allocation)
        from pathlib import Path

        allocation.record.update(
            boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip(), pid=4567, started="123"
        )
        await allocation.save()
        with pytest.raises(BrowserProviderError) as pending:
            await provider.observe(allocation)
        assert pending.value.code == "native_start_pending"
        with pytest.raises(BrowserProviderError) as refused:
            await provider.launch(allocation)
        assert refused.value.code == "native_writer_unconfirmed"
        assert refused.value.certainty == "not_dispatched"
