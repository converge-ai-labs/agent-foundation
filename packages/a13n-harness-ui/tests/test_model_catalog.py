"""Directory suggestions stay isolated from model configuration and execution."""

import pytest
from a13n_harness_ui import model_catalog as module
from a13n_harness_ui.model_catalog import ModelCatalog, bundled_models, parse_directory
from anyio import Event, create_task_group, fail_after, sleep_forever


def directory(**models: object) -> dict[str, object]:
    return {"providers": {"openai": {"models": models}}}


def test_directory_parses_display_metadata_without_changing_subscription_choices() -> None:
    payload = directory(
        custom={
            "id": "new-api-model",
            "name": "New model",
            "release_date": "2026-06-01",
            "modalities": {"input": ["text", "image"], "output": ["text"]},
            "limit": {"context": 1000000},
            "tool_call": True,
        }
    )
    payload["providers"]["xai"] = {"models": {"new-grok": {"modalities": {"output": ["text"]}}}}
    items = parse_directory(payload)
    item = next(item for item in items if item.connection == "openai-responses" and item.model_id == "new-api-model")
    assert item.context_window == 1000000
    assert item.input_modalities == ("text", "image")
    assert item.supports_tools is True
    assert not item.recommended
    for connection in ("codex", "grok-subscription"):
        assert {item.model_id for item in items if item.connection == connection} == {
            item.model_id for item in bundled_models() if item.connection == connection
        }
    assert any(item.connection == "xai" and item.model_id == "new-grok" for item in items)


def test_directory_skips_malformed_rows_and_keeps_valid_models() -> None:
    items = parse_directory(
        directory(
            valid={"modalities": {"input": None, "output": ["text"]}, "release_date": None, "limit": {"context": True}},
            null_output={"modalities": {"output": None}},
            string_output={"modalities": {"output": "text"}},
            invalid_id={"id": "bad model", "modalities": {"output": ["text"]}},
            nontext={"modalities": {"output": ["image"]}},
        )
    )
    remote = [item for item in items if item.source == "directory"]
    assert {item.model_id for item in remote} == {"valid"}
    assert all(item.context_window is None and item.released is None for item in remote)
    with pytest.raises(ValueError, match="no supported"):
        parse_directory(directory())


@pytest.mark.anyio
async def test_catalog_refresh_is_single_flight_and_retains_last_good(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    calls = 0
    entered, release = Event(), Event()
    items = parse_directory(directory(custom={"modalities": {"output": ["text"]}}))

    async def fetch():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        if calls > 1:
            raise ValueError("unavailable")
        return items

    monkeypatch.setattr(module, "monotonic", lambda: now)
    monkeypatch.setattr(module, "fetch_directory", fetch)
    catalog, results = ModelCatalog(), []

    async def read():
        results.append(await catalog.read())

    async with create_task_group() as group:
        for _ in range(5):
            group.start_soon(read)
        await entered.wait()
        release.set()
    assert calls == 1
    assert all(result.status == "ready" and result.items == items for result in results)
    now += 3601
    stale = await catalog.read()
    assert stale.status == "stale" and stale.items == items
    assert stale.updated_at == results[0].updated_at
    await catalog.read()
    assert calls == 2
    now += 61
    await catalog.read()
    assert calls == 3


@pytest.mark.anyio
async def test_catalog_offline_and_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail():
        raise ValueError("offline")

    monkeypatch.setattr(module, "fetch_directory", fail)
    snapshot = await ModelCatalog().read()
    assert snapshot.status == "unavailable"
    assert snapshot.items == bundled_models()
    assert snapshot.updated_at is None

    async def wait():
        await sleep_forever()

    monkeypatch.setattr(module, "fetch_directory", wait)
    catalog = ModelCatalog()
    with pytest.raises(TimeoutError), fail_after(0.01):
        await catalog.read()
    monkeypatch.setattr(module, "fetch_directory", fail)
    assert (await catalog.read()).status == "unavailable"
