"""Authenticated management readiness retries observation, never target mutations."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError, provider_error
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.modal import http as modal_http
from a13n_harness.providers.environment.remote_envd import managed
from a13n_harness.providers.environment.sprites import http as sprites_http
from a13n_harness.providers.environment.vercel import http as vercel_http
from a13n_service.providers.environment.envd_readiness import wait_until_ready

pytestmark = pytest.mark.anyio


def scope(*, failure=None, desktop_ready=True):
    return SimpleNamespace(
        open=AsyncMock(side_effect=failure),
        check_ready=AsyncMock(),
        close=AsyncMock(),
        operations=SimpleNamespace(
            computer=SimpleNamespace(
                describe=AsyncMock(
                    return_value=SimpleNamespace(
                        observe_ready=desktop_ready, input_ready=desktop_ready, targets=["display"]
                    )
                )
            )
        ),
    )


async def test_transient_connection_failure_retries_a_fresh_authenticated_scope():
    first = scope(failure=provider_error("test", "connection_failed", Category.UNAVAILABLE))
    second = scope()
    factory = Mock(side_effect=[first, second])
    await wait_until_ready(factory, provider_key="test", desktop=False, timeout=1)
    assert factory.call_count == 2
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    second.check_ready.assert_awaited_once_with(frozenset())
    second.operations.computer.describe.assert_not_awaited()


@pytest.mark.parametrize("category", [Category.INVALID, Category.DENIED, Category.UNKNOWN_OUTCOME, Category.CONFLICT])
async def test_incompatible_unauthorized_and_uncertain_outcomes_are_not_retried(category):
    error = provider_error("test", "refused", category)
    execution = scope(failure=error)
    factory = Mock(return_value=execution)
    with pytest.raises(EnvironmentProviderError) as raised:
        await wait_until_ready(factory, provider_key="test", desktop=False, timeout=1)
    assert raised.value is error
    factory.assert_called_once()
    execution.close.assert_awaited_once()


async def test_desktop_requires_observation_and_input_then_closes_every_probe():
    first, second = scope(desktop_ready=False), scope()
    factory = Mock(side_effect=[first, second])
    await wait_until_ready(factory, provider_key="test", desktop=True, timeout=1)
    for execution in (first, second):
        execution.check_ready.assert_awaited_once_with(frozenset({"computer"}))
        execution.close.assert_awaited_once()


async def test_readiness_deadline_is_typed_and_closes_the_scope():
    execution = scope()

    async def pending(**kwargs):
        await asyncio.sleep(10)

    execution.open.side_effect = pending
    with pytest.raises(EnvironmentProviderError) as raised:
        await wait_until_ready(lambda: execution, provider_key="test", desktop=False, timeout=0.01)
    assert raised.value.category == Category.TIMEOUT
    execution.close.assert_awaited_once()


async def test_cancellation_propagates_after_closing_scope():
    execution = scope(failure=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await wait_until_ready(lambda: execution, provider_key="test", desktop=False, timeout=1)
    execution.close.assert_awaited_once()


@pytest.mark.parametrize("provider", ["modal", "vercel", "sprites"])
@pytest.mark.parametrize("command_failure", [False, True])
async def test_first_boot_token_retries_only_an_unpublished_file(monkeypatch, tmp_path, provider, command_failure):
    """Exercise each real credential loader and its shell read through the readiness loop."""
    module = {"modal": modal_http, "vercel": vercel_http, "sprites": sprites_http}[provider]
    token_file = tmp_path / "http-token"
    calls = []
    failure = provider_error(provider, "provider_command_failed", Category.PROVIDER_FAILURE)

    async def command(*args, **kwargs):
        argv = args[1] if provider != "vercel" else args[2]
        if argv[:2] == ["sudo", "-n"]:
            argv = argv[2:]
        assert tuple(argv) == managed.HTTP_TOKEN_READ
        calls.append(argv)
        if command_failure:
            raise failure
        process = await asyncio.create_subprocess_exec(*argv[:-1], str(token_file), stdout=asyncio.subprocess.PIPE)
        output, _ = await process.communicate()
        assert process.returncode == 0
        # Startup publishes the token between the first and second observations.
        token_file.write_text("fixture-token")
        return output.decode()

    class Runtime:
        def __init__(self, connection, credential):
            assert credential.token.get_secret_value() == "fixture-token"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @asynccontextmanager
        async def open_session(self, **kwargs):
            assert kwargs["expected_device_id"] == "env-fixture"
            yield object()

    @asynccontextmanager
    async def tunnel(reference):
        yield "http://127.0.0.1:8888"

    sandbox = SimpleNamespace(
        tunnels=SimpleNamespace(aio=AsyncMock(return_value={8888: SimpleNamespace(url="https://envd.example")}))
    )
    reference = SimpleNamespace(
        require_target=lambda: None,
        lookup=AsyncMock(return_value=sandbox),
        running_session=AsyncMock(return_value="session-fixture"),
        endpoint=lambda port: "https://envd.example",
        close_transport=AsyncMock(),
        discard_transport=Mock(),
        backend=SimpleNamespace(_endpoint_policy=SimpleNamespace(validate=AsyncMock())),
        target=SimpleNamespace(target_id="target-fixture"),
        owner="env-fixture",
        config=SimpleNamespace(request_timeout_seconds=1, desktop=False),
        _lease=None,
    )
    monkeypatch.setattr(module, "execute_command" if provider == "modal" else "command", command)
    monkeypatch.setattr(managed, "HttpEnvdProviderRuntime", Runtime)
    monkeypatch.setattr(sprites_http, "HttpEnvdProviderRuntime", Runtime)
    monkeypatch.setattr(sprites_http, "private_tunnel", tunnel)

    def execution():
        context = module.http_session(reference)
        execution = scope()

        async def open(**kwargs):
            await context.__aenter__()

        async def close():
            await context.__aexit__(None, None, None)

        execution.open.side_effect = open
        execution.close.side_effect = close
        return execution

    if command_failure:
        with pytest.raises(EnvironmentProviderError) as raised:
            await wait_until_ready(execution, provider_key=provider, desktop=False, timeout=1)
        assert raised.value is failure and len(calls) == 1
    else:
        await wait_until_ready(execution, provider_key=provider, desktop=False, timeout=1)
        assert len(calls) == 2
    assert reference.close_transport.await_count == len(calls)


async def test_token_read_keeps_existing_file_errors(tmp_path):
    # A directory exists but head cannot read it; only absence becomes an empty credential.
    process = await asyncio.create_subprocess_exec(
        *managed.HTTP_TOKEN_READ[:-1],
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    assert process.returncode != 0


@pytest.mark.parametrize("fault", ["token_loop", "parent_loop", "unsearchable_parent", "missing_parent"])
async def test_token_read_distinguishes_absence_from_path_lookup_errors(tmp_path, fault):
    parent = tmp_path / "runtime"
    path = parent / "http-token"
    if fault == "parent_loop":
        parent.symlink_to(parent.name)
    elif fault != "missing_parent":
        parent.mkdir()
        if fault == "token_loop":
            path.symlink_to(path.name)
        else:
            path.write_text("fixture-token")
            parent.chmod(0)
    try:
        process = await asyncio.create_subprocess_exec(
            *managed.HTTP_TOKEN_READ[:-1],
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, _ = await process.communicate()
        if fault == "missing_parent":
            assert process.returncode == 0 and output == b""
        else:
            assert process.returncode != 0
    finally:
        if fault == "unsearchable_parent":
            parent.chmod(0o700)
