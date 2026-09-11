"""Offline safety boundaries for the real E2B suite and its cleanup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from e2b import AsyncSandbox
from pydantic import SecretStr

from ..environment.e2b_support import E2BSandboxes


def test_e2b_offline_selection_never_reads_private_configuration(monkeypatch):
    from ..conftest import e2b_settings
    from ..providers import provider_config

    def unexpected_read():
        raise AssertionError("Offline check read private E2B configuration")

    monkeypatch.setattr(provider_config, "load_provider_settings", unexpected_read)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda option: False))
    with pytest.raises(pytest.skip.Exception, match="live-environments"):
        e2b_settings.__wrapped__(request)


@pytest.mark.anyio
async def test_e2b_cleanup_discovers_lost_state_and_continues_after_failures(monkeypatch):
    pool = E2BSandboxes(SimpleNamespace(template="base", api_key=SecretStr("private-key")))
    pool.identities.add("env-owned")
    # No adapter state knows these targets. The second discovery page represents a paused sandbox.
    pool.adapters.append(SimpleNamespace(dump_state=lambda: None, close=AsyncMock(side_effect=RuntimeError())))

    class Pages:
        has_next = True
        calls = 0

        async def next_items(self):
            self.calls += 1
            self.has_next = self.calls < 2
            return [SimpleNamespace(sandbox_id="sandbox-" + str(self.calls))]

    pages = Pages()

    def discover(*, query, **options):
        assert query.metadata == {"a13n_environment": "env-owned"}
        return pages

    kill = AsyncMock(side_effect=[RuntimeError("first cleanup failed"), True])
    monkeypatch.setattr(AsyncSandbox, "list", discover)
    monkeypatch.setattr(AsyncSandbox, "kill", kill)
    monkeypatch.setattr(pool, "state", AsyncMock())
    with pytest.raises(AssertionError, match="sandbox-1 cleanup") as caught:
        await pool.cleanup()
    assert pages.calls == 2
    assert [call.args[0] for call in kill.await_args_list] == ["sandbox-1", "sandbox-2"]
    pool.state.assert_awaited_once_with("sandbox-2", "absent")
    assert "close: RuntimeError" in str(caught.value) and "private-key" not in str(caught.value)


@pytest.mark.anyio
async def test_e2b_probe_rejects_unowned_targets_before_sdk_dispatch(monkeypatch):
    pool = E2BSandboxes(SimpleNamespace(template="base", api_key=SecretStr("private-key")))
    probe = AsyncMock(side_effect=AssertionError("Unowned target reached SDK"))
    monkeypatch.setattr(AsyncSandbox, "get_info", probe)
    with pytest.raises(AssertionError, match="owned"):
        await pool.info("someone-elses-sandbox")
    probe.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["recovers", "exhausted", "non_transport"])
async def test_e2b_read_probe_retries_only_transport_errors_with_a_bound(monkeypatch, caplog, outcome):
    from ..environment import e2b_support

    pool = E2BSandboxes(SimpleNamespace(template="base", api_key=SecretStr("private-key")))
    pool.sandbox_ids.add("owned")
    transport = httpx.RemoteProtocolError("private-key must not appear in retry logs")
    result = object()
    effects = {
        "recovers": [transport, result],
        "exhausted": [transport, transport, transport],
        "non_transport": [ValueError("Invalid response")],
    }
    probe = AsyncMock(side_effect=effects[outcome])
    monkeypatch.setattr(AsyncSandbox, "get_info", probe)
    monkeypatch.setattr(e2b_support.anyio, "sleep", AsyncMock())
    if outcome == "recovers":
        assert await pool.info("owned") is result
        assert probe.await_count == 2
    else:
        with pytest.raises(httpx.RemoteProtocolError if outcome == "exhausted" else ValueError):
            await pool.info("owned")
        assert probe.await_count == (3 if outcome == "exhausted" else 1)
    assert "private-key" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["retention_finishes", "retention_wins_race", "command_fails"])
async def test_service_cleanup_waits_for_retention_but_reports_failed_commands(monkeypatch, outcome):
    from ..environment import e2b_support
    from ..environment.test_34_e2b_service_lifecycle import ServiceSandboxes

    running = {"status": "running", "operation_id": None}
    busy = {**running, "operation_id": "retention"}
    deleted = {"status": "deleted", "operation_id": None}
    response = httpx.Response(
        409 if outcome == "retention_wins_race" else 202,
        json={"error": {"code": "environment_busy"}} if outcome == "retention_wins_race" else {"id": "cleanup"},
    )
    live = SimpleNamespace(
        config={"workspace_id": "ws"},
        http=SimpleNamespace(post=AsyncMock(return_value=response)),
        request=AsyncMock(return_value={"status": "failed"}),
    )
    service = ServiceSandboxes(SimpleNamespace(client=live), None)
    rows = {
        "retention_finishes": [busy, deleted],
        "retention_wins_race": [running, busy, deleted],
        "command_fails": [running, running],
    }
    monkeypatch.setattr(service, "record", AsyncMock(side_effect=rows[outcome]))
    monkeypatch.setattr(e2b_support.anyio, "sleep", AsyncMock())
    if outcome == "command_fails":
        with pytest.raises(AssertionError, match="Cleanup delete command failed"):
            await service.delete("owned")
    else:
        await service.delete("owned")
    assert live.http.post.await_count == (0 if outcome == "retention_finishes" else 1)
