"""Development probe isolation, native async behavior, privacy and startup opt-in."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from a13n_harness.providers.environment.modal.diagnostics import desktop_operation

from dev.service import applications
from dev.service import modal_sdk_timings as probe


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_wrappers_preserve_results_errors_cancellation_and_context(caplog):
    caplog.set_level(logging.INFO, logger=probe._logger.name)

    async def call(value):
        await asyncio.sleep(0)
        if isinstance(value, BaseException):
            raise value
        return value

    wrapped = probe.instrument(call, "call")
    value = object()
    assert await wrapped(value) is value
    assert not caplog.records
    with desktop_operation("dtop-one", "describe"), probe.span("parent"):
        assert await wrapped(value) is value
        for error in (ValueError("secret-exception"), asyncio.CancelledError("secret-cancel")):
            with pytest.raises(type(error)) as raised:
                await wrapped(error)
            assert raised.value is error
    records = [r for r in caplog.records if r.name == probe._logger.name]
    assert [r.outcome for r in records] == ["ok", "error", "interrupted", "ok"]
    assert all(r.sdk_parent_id == records[-1].sdk_span_id for r in records[:-1])
    assert all(r.desktop_operation_id == "dtop-one" for r in records)
    assert "secret" not in repr([r.__dict__ for r in records])
    assert probe._parent.get() is None


@pytest.mark.anyio
async def test_network_spans_are_task_local_and_hooks_restore(caplog):
    caplog.set_level(logging.INFO, logger=probe._logger.name)
    original = asyncio.BaseEventLoop.create_connection
    restore = probe.install()
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)

    async def connect():
        _, writer = await asyncio.open_connection("localhost", server.sockets[0].getsockname()[1])
        writer.close()
        await writer.wait_closed()

    async def observed():
        with desktop_operation("dtop-network", "describe"), probe.span("sdk"):
            await connect()

    try:
        await asyncio.gather(observed(), connect())
    finally:
        server.close()
        await server.wait_closed()
        restore()
    records = [r for r in caplog.records if r.name == probe._logger.name]
    stages = [r.sdk_stage for r in records]
    assert stages.count("network_connection") == 1
    assert {"dns", "tcp_connect", "transport_tls", "sdk"} <= set(stages)
    assert all(r.desktop_operation_id == "dtop-network" for r in records)
    assert asyncio.BaseEventLoop.create_connection is original


def test_sync_probe_does_not_log_arguments_or_results(caplog):
    caplog.set_level(logging.INFO, logger=probe._logger.name)
    call = probe.instrument(lambda value: value, "proxy_selection", nested=True)
    with desktop_operation("dtop-sync", "describe"):
        assert call("secret-url") == "secret-url"
        assert not caplog.records
        with probe.span("sdk"):
            assert call("secret-url") == "secret-url"
    records = [r for r in caplog.records if r.name == probe._logger.name]
    assert [r.sdk_stage for r in records] == ["proxy_selection", "sdk"]
    assert "secret-url" not in repr([r.__dict__ for r in records])


def test_version_mismatch_changes_no_hooks(monkeypatch):
    original = asyncio.BaseEventLoop.create_connection
    monkeypatch.setattr(probe, "version", lambda _: "next-version")
    with pytest.raises(RuntimeError, match="reviewed SDK version"):
        probe.install()
    assert asyncio.BaseEventLoop.create_connection is original


@pytest.mark.anyio
async def test_rpc_attempts_are_allowlisted_and_do_not_expose_payloads(caplog, monkeypatch):
    import grpclib.client

    caplog.set_level(logging.INFO, logger=probe._logger.name)
    count = 0

    async def original(self, payload):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("secret-network-detail")
        return payload

    monkeypatch.setattr(grpclib.client.UnaryUnaryMethod, "__call__", original)
    restore = probe.install()
    try:
        with desktop_operation("dtop-rpc", "settle"):
            rpc = grpclib.client.UnaryUnaryMethod.__call__
            known = SimpleNamespace(name="/service/TaskExecStart")
            with pytest.raises(OSError):
                await rpc(known, "secret-request")
            assert await rpc(known, "secret-request") == "secret-request"
            await rpc(SimpleNamespace(name="/service/secret-unknown"), "secret-request")
    finally:
        restore()
    records = [r for r in caplog.records if r.name == probe._logger.name]
    assert [(r.sdk_stage, r.outcome) for r in records] == [("rpc_TaskExecStart", "error"), ("rpc_TaskExecStart", "ok")]
    assert "secret" not in repr([r.__dict__ for r in records])
    assert grpclib.client.UnaryUnaryMethod.__call__ is original


def test_only_explicit_local_run_uses_probe(monkeypatch, tmp_path):
    checkout = SimpleNamespace(settings_file=tmp_path / "settings.toml")
    monkeypatch.delenv("MODAL_SDK_TIMINGS", raising=False)
    assert "dev.service.modal_sdk_timings" not in applications.service_command(checkout, "run")
    monkeypatch.setenv("MODAL_SDK_TIMINGS", "1")
    assert applications.service_command(checkout, "run")[1:3] == ("-m", "dev.service.modal_sdk_timings")
    assert "dev.service.modal_sdk_timings" not in applications.service_command(checkout, "migrate")
