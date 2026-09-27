from __future__ import annotations

import asyncio

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_apps.context import AppContextUpdate
from pydantic import ValidationError

from . import test_mcp_apps_operations as fixtures

apps = fixtures.apps


def context(text: str) -> AppContextUpdate:
    return AppContextUpdate.model_validate(
        {"content": [{"type": "text", "text": text}], "structuredContent": {"selected": text}}
    )


def test_context_accepts_bounded_text_and_structure_not_unsupported_media() -> None:
    assert context("one").structured_content == {"selected": "one"}
    with pytest.raises(ValidationError):
        AppContextUpdate.model_validate({"content": [{"type": "image", "data": "AA==", "mimeType": "image/png"}]})
    with pytest.raises(ValidationError, match="64 KiB"):
        context("x" * (64 * 1024))


@pytest.mark.anyio
async def test_context_is_latest_view_local_explicit_and_discardable(apps) -> None:
    operations, reference = apps
    first = await operations.activate(reference)
    second = await operations.activate(reference)
    assert await operations.capture_context("thread-1", ()) == ()
    old = await operations.update_context("thread-1", first.view_id, context("old selection"))
    latest = await operations.update_context("thread-1", first.view_id, context("latest selection"))
    assert old.reference.context_id != latest.reference.context_id
    with pytest.raises(HarnessUiError, match="changed"):
        await operations.capture_context("thread-1", (old.reference,))
    with pytest.raises(HarnessUiError, match="changed"):
        await operations.capture_context("thread-1", (latest.reference.model_copy(update={"view_id": second.view_id}),))
    with pytest.raises(HarnessUiError, match="another conversation"):
        await operations.capture_context("another-thread", (latest.reference,))
    captured = await operations.capture_context("thread-1", (latest.reference,))
    assert "latest selection" in captured[0]
    assert "external data" in captured[0]
    assert reference.app_id in captured[0]
    operations.discard_context("thread-1", first.view_id)
    with pytest.raises(HarnessUiError, match="changed"):
        await operations.capture_context("thread-1", (latest.reference,))
    new = await operations.update_context("thread-1", first.view_id, context("new selection"))
    await fixtures._policy(operations, "deny")
    with pytest.raises(HarnessUiError, match="denies"):
        await operations.capture_context("thread-1", (new.reference,))
    operations.close_view("thread-1", first.view_id)
    with pytest.raises(HarnessUiError, match="closed"):
        await operations.update_context("thread-1", first.view_id, context("closed"))


@pytest.mark.anyio
async def test_context_capture_detaches_all_values_before_awaiting_current_authority(apps, monkeypatch) -> None:
    operations, reference = apps
    view = await operations.activate(reference)
    original = await operations.update_context("thread-1", view.view_id, context("captured before await"))
    entered, release = asyncio.Event(), asyncio.Event()
    admit = operations._admit

    async def gated(*args, **kwargs):
        entered.set()
        await release.wait()
        return await admit(*args, **kwargs)

    monkeypatch.setattr(operations, "_admit", gated)
    captured = asyncio.create_task(operations.capture_context("thread-1", (original.reference,)))
    await entered.wait()
    monkeypatch.setattr(operations, "_admit", admit)
    await operations.update_context("thread-1", view.view_id, context("arrived during capture"))
    release.set()
    assert "captured before await" in (await captured)[0]
    assert "arrived during capture" not in (await captured)[0]
