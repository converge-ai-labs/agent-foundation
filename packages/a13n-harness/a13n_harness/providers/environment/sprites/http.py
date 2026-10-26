"""EIP over an owned, private Sprites TCP tunnel; no daemon route is made public."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from pydantic import SecretStr
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from a13n_harness.environment.attachments import EIPSessionBinding
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
)
from a13n_harness.providers.environment.remote_envd.environment import REQUIRED_METHODS, RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.http import HttpEnvdProviderRuntime
from a13n_harness.providers.environment.sprites.commands import command
from a13n_harness.providers.environment.sprites.shared import SpritesReference, SpritesTargetState


class HttpReference(SpritesReference):
    async def close(self) -> None:
        await self.close_transport()

    def observe(self, state: SpritesTargetState) -> None:
        if state != self.target:
            raise failure(self.provider_key, "provider_target_changed", Category.CONFLICT)


@asynccontextmanager
async def private_tunnel(reference: SpritesReference) -> AsyncIterator[str]:
    """One attachment owns its listener, sockets and pumps. No cross-account cache."""
    await reference.backend._endpoint_policy.validate("https://api.sprites.dev")
    tasks: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        if len(tasks) >= 32:
            writer.close()
            return
        tasks.add(task)
        try:
            async with connect(
                "wss://api.sprites.dev" + reference.path + "/proxy",
                additional_headers={"Authorization": "Bearer " + reference.token.get_secret_value()},
                open_timeout=reference.config.request_timeout_seconds,
                close_timeout=1,
                max_size=4 * 1024 * 1024,
            ) as socket:
                await socket.send(json.dumps({"host": "127.0.0.1", "port": 8888}))
                async with asyncio.timeout(reference.config.request_timeout_seconds):
                    reply = json.loads(await socket.recv())
                    if not isinstance(reply, dict) or reply.get("status") != "connected":
                        return

                async def upstream() -> None:
                    while chunk := await reader.read(65536):
                        await socket.send(chunk)

                async def downstream() -> None:
                    async for chunk in socket:
                        if not isinstance(chunk, bytes):
                            raise ValueError("Invalid tunnel frame")
                        writer.write(chunk)
                        await writer.drain()

                pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                try:
                    await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for pump in pumps:
                        pump.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)
        except (WebSocketException, OSError, TimeoutError, ValueError):
            # EOF fails the owning EIP request; never replay a possibly dispatched operation.
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        # Python 3.13 waits for accepted streams too; close handlers before joining the listener.
        pending = tuple(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()


@asynccontextmanager
async def http_session(
    reference: SpritesReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> AsyncIterator[EIPSessionBinding]:
    try:
        reference.require_target()
        await reference.backend._endpoint_policy.validate("https://api.sprites.dev")
        if await reference.lookup() is None:
            raise failure("sprites", "provider_target_missing", Category.MISSING)
        raw = await command(
            reference,
            [
                "sudo",
                "-n",
                "python3",
                "-I",
                "-c",
                "import sys; sys.stdout.write(open('/var/lib/a13n-envd/http-token').read(4097))",
            ],
            15,
            max_output=4096,
        )
        try:
            credential = HttpEnvdCredential(token=SecretStr(raw))
        except ValueError:
            raise failure("sprites", "envd_credential_invalid", Category.UNAVAILABLE) from None
        async with private_tunnel(reference) as endpoint:
            # Policy applies to the actual cloud gateway above, not this ephemeral local carrier.
            connection = HttpEnvdConnectionConfiguration(
                endpoint=endpoint, request_timeout=reference.config.request_timeout_seconds
            )
            async with HttpEnvdProviderRuntime(connection, credential) as runtime:
                async with runtime.open_session(
                    expected_device_id=reference.owner,
                    session_recovery=session_recovery,
                    working_directory="/workspace",
                    required_methods=REQUIRED_METHODS | frozenset({"computer.describe", "computer.observe"}),
                ) as session:
                    yield session
    finally:
        await reference.close_transport()


def http_execution(
    reference: SpritesReference, *, session_recovery: EnvdSessionRecovery | None = None
) -> RemoteEnvdExecution:
    reference.require_target()
    assert reference.state is not None
    if not reference.target or reference.target.desktop_phase != "ready":
        raise failure("sprites", "desktop_not_ready", Category.UNAVAILABLE)
    return RemoteEnvdExecution(
        environment=reference.config.environment,
        provider_key="sprites",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference, session_recovery=session_recovery),
    )
