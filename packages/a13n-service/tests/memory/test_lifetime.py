"""Structured provider scopes remain nested through direct and managed hosts."""

from contextlib import asynccontextmanager
from dataclasses import replace

import anyio
import httpx2
import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory.builtins import MEM0_OSS
from a13n_harness.providers.memory.mem0_oss import Mem0OSSBackend
from a13n_service.memory.execution import MemoryProviderAccess, open_memory_backend

from .support import protector

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("outcome", ["normal", "cancel", "error", "suppressed", "invalid"])
async def test_structured_plugin_lifetime(managed, outcome):
    completed = anyio.Event()
    entered = anyio.Event()
    seen = []
    body_error = ValueError("body failed")
    closes = 0

    async def child():
        entered.set()
        await anyio.sleep_forever()

    async with httpx2.AsyncClient() as borrowed:

        @asynccontextmanager
        async def open_backend(configuration, credential):
            nonlocal closes
            async with anyio.create_task_group() as group:
                group.start_soon(child)
                await entered.wait()
                try:
                    yield object() if outcome == "invalid" else Mem0OSSBackend(borrowed)
                except BaseException as error:
                    seen.append(error)
                    if outcome != "suppressed":
                        raise
                finally:
                    group.cancel_scope.cancel()
                    # The acquiring plugin protects its own teardown within its scope.
                    with anyio.move_on_after(1, shield=True):
                        await anyio.sleep(0)
                        closes += 1
                        completed.set()

        definition = replace(MEM0_OSS, open_backend=open_backend)
        config = {"base_url": "http://fixture"}
        secret = {"api_key": "fixture"}
        if managed:
            # Real managed open path; optional auth avoids unrelated encryption setup.
            from a13n_harness.providers.authentication import Authentication, CredentialMode

            definition = replace(definition, authentication=Authentication(mode=CredentialMode.optional))
            context = open_memory_backend(
                MemoryProviderAccess(definition.type, config, "memory_fixture", None),
                ProviderCatalog((definition,)),
                protector(),
            )
        else:
            context = definition.open(config, secret)

        async def body():
            async with context:
                if outcome == "cancel":
                    scope.cancel()
                    await anyio.sleep_forever()
                elif outcome in {"error", "suppressed"}:
                    raise body_error

        with anyio.CancelScope() as scope:
            if outcome in {"error", "invalid"}:
                with pytest.raises(ExceptionGroup) as raised:
                    await body()
                assert raised.value.exceptions == tuple(seen)
                assert isinstance(seen[0], TypeError) if outcome == "invalid" else seen[0] is body_error
            else:
                await body()
        assert scope.cancelled_caught is (outcome == "cancel")
        assert completed.is_set() and closes == 1
        assert not borrowed.is_closed
        if outcome == "suppressed":
            assert seen == [body_error]
