from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from time import monotonic

import anyio
import httpx
import httpx2
import pytest
from a13n_harness.providers.memory.builtins import MEM0_OSS, MEM0_PLATFORM
from a13n_harness.providers.memory.mem0_oss import Mem0OSSBackend, open_mem0_oss
from a13n_harness.providers.memory.mem0_platform import open_mem0_platform
from mem0 import AsyncMemoryClient

pytestmark = pytest.mark.anyio


@dataclass
class CloseObservation:
    stall: bool = False
    calls: int = 0
    completed: anyio.Event = field(default_factory=anyio.Event)

    async def close(self):
        self.calls += 1
        # Unlike the client's is_closed flag, this observes actual async cleanup.
        await anyio.sleep(0)
        if self.stall:
            await anyio.sleep_forever()
        self.completed.set()


@pytest.fixture(params=["oss_direct", "oss_definition", "platform_direct", "platform_definition"])
def native_lifetime(request, monkeypatch):
    kind, entry = request.param.split("_")
    observation = CloseObservation()
    http = httpx2 if kind == "oss" else httpx

    class Transport(http.AsyncBaseTransport):
        async def handle_async_request(self, request):
            pytest.fail("Lifetime construction must not make a vendor request")

        async def aclose(self):
            await observation.close()

    client = http.AsyncClient(transport=Transport())
    monkeypatch.setattr(http, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setattr(
        AsyncMemoryClient, "_validate_api_key", lambda self: pytest.fail("Synchronous constructor ping")
    )
    if entry == "definition":
        definition = MEM0_OSS if kind == "oss" else MEM0_PLATFORM
        lifetime = definition.open({"base_url": "http://memory"}, {"api_key": "test-key"})
    elif kind == "oss":
        lifetime = open_mem0_oss(base_url="http://memory", api_key="test-key")
    else:
        lifetime = open_mem0_platform(base_url="http://memory", api_key="test-key")
    return lifetime, client, observation


async def test_native_transport_finishes_closing_under_real_cancellation(native_lifetime):
    lifetime, client, observation = native_lifetime
    with anyio.CancelScope() as scope:
        async with lifetime:
            scope.cancel()
            await anyio.sleep_forever()
        pytest.fail("Cancellation must propagate through the provider lifetime")
    assert scope.cancelled_caught
    assert client.is_closed
    assert observation.completed.is_set()
    assert observation.calls == 1


async def test_native_transport_cleanup_is_bounded_under_cancellation(native_lifetime):
    lifetime, client, observation = native_lifetime
    observation.stall = True
    started = monotonic()
    with anyio.CancelScope() as scope:
        async with lifetime:
            scope.cancel()
            await anyio.sleep_forever()
        pytest.fail("Cleanup timeout must not suppress caller cancellation")
    assert scope.cancelled_caught
    assert 0.8 <= monotonic() - started < 3
    assert client.is_closed
    assert not observation.completed.is_set()
    assert observation.calls == 1


@pytest.mark.parametrize("fail", [False, True])
async def test_native_transport_closes_once_on_normal_and_exceptional_exit(native_lifetime, fail):
    lifetime, client, observation = native_lifetime
    error = ValueError("body failed")
    try:
        async with lifetime:
            if fail:
                raise error
    except ValueError as caught:
        assert fail and caught is error
    else:
        assert not fail
    assert client.is_closed
    assert observation.completed.is_set()
    assert observation.calls == 1


@pytest.mark.parametrize("stall", [False, True])
async def test_plugin_owns_bounded_cleanup_without_closing_borrowed_client(stall):
    observation = CloseObservation(stall=stall)
    async with httpx2.AsyncClient() as borrowed:

        @asynccontextmanager
        async def open_backend(configuration, credential):
            try:
                yield Mem0OSSBackend(borrowed)
            finally:
                with anyio.move_on_after(1, shield=True):
                    await observation.close()

        definition = replace(MEM0_OSS, open_backend=open_backend)
        started = monotonic()
        with anyio.CancelScope() as scope:
            async with definition.open({"base_url": "http://memory"}, {"api_key": "test-key"}):
                scope.cancel()
                await anyio.sleep_forever()
            pytest.fail("Definition must propagate cancellation")
        assert scope.cancelled_caught
        assert observation.completed.is_set() is not stall
        assert observation.calls == 1
        assert not borrowed.is_closed
        assert monotonic() - started < 3
        if stall:
            assert monotonic() - started >= 0.8


@pytest.mark.parametrize("invalid_backend", [False, True])
async def test_definition_passes_body_or_validation_error_to_plugin_before_cleanup(invalid_backend):
    observation = CloseObservation()
    seen = []
    body_error = ValueError("caller failed")
    async with httpx2.AsyncClient() as borrowed:

        @asynccontextmanager
        async def open_backend(configuration, credential):
            try:
                yield object() if invalid_backend else Mem0OSSBackend(borrowed)
            except Exception as error:
                seen.append(error)
                raise
            finally:
                with anyio.move_on_after(1, shield=True):
                    await observation.close()

        definition = replace(MEM0_OSS, open_backend=open_backend)
        with pytest.raises(TypeError if invalid_backend else ValueError) as raised:
            async with definition.open({"base_url": "http://memory"}, {"api_key": "test-key"}):
                raise body_error
        assert seen == [raised.value]
        if not invalid_backend:
            assert raised.value is body_error
        assert observation.completed.is_set()
        assert observation.calls == 1
        assert not borrowed.is_closed
