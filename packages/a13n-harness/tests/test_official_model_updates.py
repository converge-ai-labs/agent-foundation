from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from importlib.resources import files

import anyio
import httpx2
import pytest
import yaml
from a13n_harness import AgentSpec, HarnessBuilder, _official_data, pricing
from a13n_harness import model_catalog_updates as updates
from a13n_harness.model_catalog import get_official_model_catalog
from genai_prices.data_snapshot import DataSnapshot, get_snapshot, set_custom_snapshot
from genai_prices.types import ClauseEquals, ModelInfo, ModelPrice, Provider


@pytest.fixture(autouse=True)
def isolated_catalogs(monkeypatch):
    previous = get_snapshot()
    set_custom_snapshot(None)
    monkeypatch.setattr(_official_data, "_current", None)
    monkeypatch.setattr(updates, "_state", updates._RefreshState())
    monkeypatch.setattr(updates, "uniform", lambda low, high: high)
    monkeypatch.setattr(updates, "monotonic", lambda: 100.0)
    yield
    set_custom_snapshot(previous)


def document():
    return yaml.safe_load(files("a13n_harness").joinpath("data/official-models.yaml").read_text())


def payload(value=None):
    return yaml.safe_dump(document() if value is None else value).encode()


def test_bundled_document_has_one_context_owner_and_optional_independent_views():
    raw = document()
    assert raw["schema_version"] == 2
    for value in raw["models"].values():
        assert "context_window" not in value.get("pricing", {})
    candidate = _official_data.parse_official_data("""
schema_version: 2
models:
  test:media:
    characteristics: {capabilities: []}
    source_url: https://example.com/model
  test:price:
    pricing:
      source: harness_overlay
      source_revision: fixture
      rules:
        - rule_id: standard
          prices: [{price_key: input_mtok, price: 1}]
""")
    assert set(candidate.models) == {"test:media"}
    assert set(candidate.pricing) == {"test:price"}
    with pytest.raises(TypeError):
        candidate.models["other"] = candidate.models["test:media"]


@pytest.mark.anyio
async def test_refresh_publishes_both_views_and_keeps_old_capabilities_and_bundled_snapshot():
    old_facts = get_official_model_catalog()
    bundled = pricing.get_default_pricing_catalog()
    old_cost = pricing.CatalogModelCostCapability()
    changed = document()
    model = changed["models"]["openai:gpt-5.5"]
    model["characteristics"]["context_window_tokens"] = 123456
    model["pricing"]["rules"][0]["prices"][0]["price"] = 123

    async def fetch():
        return payload(changed)

    await updates._refresh(fetch)
    assert get_official_model_catalog()["openai:gpt-5.5"].characteristics.context_window_tokens == 123456
    assert pricing.get_current_pricing_catalog()["openai:gpt-5.5"].rules[0].prices[0].price == 123
    assert old_facts["openai:gpt-5.5"].characteristics.context_window_tokens == 1050000
    assert old_cost.catalog["openai:gpt-5.5"].rules[0].prices[0].price != 123
    assert pricing.get_default_pricing_catalog() is bundled
    revision = pricing.get_current_pricing_catalog().revision
    updates._state.next_attempt = 0
    await updates._refresh(fetch)
    assert pricing.get_current_pricing_catalog().revision == revision


@pytest.mark.anyio
async def test_upstream_standard_prices_win_but_official_tiers_refresh_and_host_override_wins():
    set_custom_snapshot(
        DataSnapshot(
            [
                Provider(
                    id="openai",
                    name="OpenAI",
                    api_pattern="https://api.openai.com/.*",
                    models=[
                        ModelInfo(
                            id="gpt-5.5",
                            match=ClauseEquals(equals="gpt-5.5"),
                            prices=ModelPrice(input_mtok=Decimal(99)),
                        )
                    ],
                )
            ],
            from_auto_update=True,
        )
    )
    changed = document()
    model = changed["models"]["openai:gpt-5.5"]
    priority = next(rule for rule in model["pricing"]["rules"] if rule.get("service_tier") == "priority")
    priority["prices"][0]["price"] = 123
    _official_data.publish_official_data(_official_data.parse_official_data(payload(changed)))
    current = pricing.get_current_pricing_catalog()
    assert current["openai:gpt-5.5"].rules[0].prices[0].price == 99
    assert (
        next(rule for rule in current["openai:gpt-5.5"].rules if rule.service_tier == "priority").prices[0].price == 123
    )
    explicit = pricing.get_default_pricing_catalog()["openai:gpt-5.5"]
    cost = pricing.CatalogModelCostCapability(pricing_updates={explicit.key: explicit})
    assert cost.catalog[explicit.key] == explicit
    # A new complete official document can remove a supplement; do not retain old tiers.
    del changed["models"]["openai:gpt-5.5"]["pricing"]
    _official_data.publish_official_data(_official_data.parse_official_data(payload(changed)))
    assert not any(rule.service_tier for rule in pricing.get_current_pricing_catalog()[explicit.key].rules)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["network", "schema", "pricing", "media", "empty", "duplicate", "size"])
@pytest.mark.parametrize("published", [False, True])
async def test_fail_open_keeps_whole_snapshot_and_backs_off(failure, published):
    if published:
        _official_data.publish_official_data(_official_data.parse_official_data(payload()))
    before = _official_data.current_official_data()
    value = deepcopy(document())
    if failure == "schema":
        value["schema_version"] = 1
    elif failure == "pricing":
        value["models"]["openai:gpt-5.5"]["pricing"]["rules"] = []
    elif failure == "media":
        value["models"]["openai:gpt-5.5"]["characteristics"]["capabilities"] = ["invented"]
    elif failure == "empty":
        value["models"] = {}
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        if failure == "network":
            raise httpx2.ConnectError("offline")
        if failure == "duplicate":
            return b"schema_version: 2\nschema_version: 2\nmodels: {}"
        if failure == "size":
            return b"x" * (updates.MAX_BYTES + 1)
        return payload(value)

    for attempt in range(9):
        await updates._refresh(fetch)
        assert _official_data.current_official_data() is before
        assert updates._state.next_attempt - 100 == min(3600, 60 * 2**attempt)
        await updates._refresh(fetch)
        assert calls == attempt + 1
        updates._state.next_attempt = 0


@pytest.mark.anyio
async def test_concurrent_hosts_share_attempt_and_cancellation_releases_claim():
    started = anyio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as group:
        group.start_soon(updates._refresh, fetch)
        await started.wait()
        for _ in range(20):
            await updates._refresh(fetch)
        assert calls == 1
        group.cancel_scope.cancel()
    assert not updates._state.active
    assert updates._state.next_attempt == 160
    assert _official_data._current is None


@pytest.mark.anyio
@pytest.mark.parametrize("disabled", ["0", "false", "OFF", "no"])
async def test_disabled_loop_does_not_fetch_or_discard_published_data(monkeypatch, disabled):
    previous = _official_data.parse_official_data(payload())
    _official_data.publish_official_data(previous)
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", disabled)

    async def fetch():
        pytest.fail("disabled updater fetched")

    await updates.run_official_model_updates(fetch)
    assert _official_data.current_official_data() is previous


def test_default_enabled_and_reads_and_builds_never_start_downloader(monkeypatch):
    monkeypatch.delenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", raising=False)
    assert updates.official_model_updates_enabled()
    monkeypatch.setattr(httpx2, "AsyncClient", lambda *args, **kwargs: pytest.fail("unexpected network"))
    get_official_model_catalog()
    pricing.get_current_pricing_catalog()
    HarnessBuilder().build(AgentSpec(model="test"), output_type=str)


@pytest.mark.anyio
async def test_http_retry_after_and_recovery_reset_backoff():
    async def unavailable():
        response = httpx2.Response(
            429, headers={"Retry-After": "7200"}, request=httpx2.Request("GET", updates.OFFICIAL_MODELS_URL)
        )
        response.raise_for_status()
        return b""

    await updates._refresh(unavailable)
    assert updates._state.next_attempt == 7300
    updates._state.next_attempt = 0

    async def success():
        return payload()

    await updates._refresh(success)
    assert updates._state.failures == 0
    assert updates._state.next_attempt == 100 + 3600 * 1.1


@pytest.mark.anyio
@pytest.mark.parametrize("tls_setting,verify", [(None, True), ("false", False)])
async def test_real_http_fetcher_uses_fixed_url_and_tls_policy(monkeypatch, tls_setting, verify):
    client_type = httpx2.AsyncClient
    if tls_setting is None:
        monkeypatch.delenv("A13N_OUTBOUND_TLS_VERIFY", raising=False)
    else:
        monkeypatch.setenv("A13N_OUTBOUND_TLS_VERIFY", tls_setting)

    def serve(request):
        assert str(request.url) == updates.OFFICIAL_MODELS_URL
        return httpx2.Response(200, content=payload())

    def client(**kwargs):
        assert kwargs["verify"] is verify
        return client_type(transport=httpx2.MockTransport(serve), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    await updates._refresh(updates._fetch)
    assert _official_data._current is not None


@pytest.mark.anyio
async def test_enabled_loop_waits_between_failure_recovery_and_cancellation(monkeypatch):
    monkeypatch.delenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", raising=False)
    now = 100.0
    waits = []
    calls = 0
    content = payload()
    monkeypatch.setattr(updates, "monotonic", lambda: now)

    async def fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx2.ConnectError("offline")
        return content

    with anyio.CancelScope() as scope:

        async def sleep(delay):
            nonlocal now
            waits.append(delay)
            now += delay
            if len(waits) == 3:
                scope.cancel()
            await anyio.lowlevel.checkpoint()

        monkeypatch.setattr(updates.anyio, "sleep", sleep)
        await updates.run_official_model_updates(fetch)

    assert waits == [30, 60, 3960]
    assert calls == 2
    assert _official_data._current is not None
    assert not updates._state.active


@pytest.mark.anyio
async def test_timeout_retains_snapshot_and_releases_attempt(monkeypatch):
    monkeypatch.setattr(updates, "FETCH_SECONDS", 0.01)
    previous = _official_data.current_official_data()

    async def fetch():
        await anyio.sleep_forever()

    await updates._refresh(fetch)
    assert _official_data.current_official_data() is previous
    assert not updates._state.active
    assert updates._state.next_attempt == 160
