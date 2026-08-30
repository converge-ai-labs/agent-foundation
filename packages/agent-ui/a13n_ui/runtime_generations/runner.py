"""Passive Agent UI runtime Runner process implementation."""

from __future__ import annotations

import asyncio
import os
import platform
from importlib.metadata import PackageNotFoundError, version

from .models import RuntimeReadiness
from .protocol import ControlChannel, ControlProtocolError, require_generation, require_string

_ENV_HOST = "A13N_UI_RUNNER_CONTROL_HOST"
_ENV_PORT = "A13N_UI_RUNNER_CONTROL_PORT"
_ENV_TOKEN = "A13N_UI_RUNNER_CONTROL_TOKEN"
_ENV_GENERATION = "A13N_UI_RUNNER_GENERATION_ID"
_ENV_MAX_MESSAGE = "A13N_UI_RUNNER_MAX_MESSAGE_BYTES"
_RUNTIME_PROTOCOL_VERSION = "1"


def current_runtime_readiness() -> RuntimeReadiness:
    """Describe the passive Runner code loaded in this interpreter."""

    names = ("a13n-ui", "a13n-harness", "a13n-stream-protocol", "a13n-environment-provider")
    provenance: list[str] = []
    for name in names:
        try:
            provenance.append(f"{name}=={version(name)}")
        except PackageNotFoundError:
            provenance.append(f"{name}==unavailable")
    return RuntimeReadiness(
        protocol_version=_RUNTIME_PROTOCOL_VERSION,
        agent_ui_version=version("a13n-ui"),
        python_version=platform.python_version(),
        loaded_provenance=tuple(provenance),
    )


async def run_passive_runtime_runner() -> int:
    """Authenticate to the parent and execute the passive lifecycle."""

    try:
        host = os.environ.pop(_ENV_HOST)
        port = int(os.environ.pop(_ENV_PORT))
        token = os.environ.pop(_ENV_TOKEN)
        generation_id = os.environ.pop(_ENV_GENERATION)
        max_message_bytes = int(os.environ.pop(_ENV_MAX_MESSAGE))
    except (KeyError, ValueError):
        return 2

    try:
        reader, writer = await asyncio.open_connection(host, port, limit=max_message_bytes)
        channel = ControlChannel(reader, writer, max_message_bytes=max_message_bytes)
        await channel.send("HELLO", token=token, generation_id=generation_id)
        token = ""
        welcome = await channel.receive(expected_type="WELCOME")
        require_generation(welcome, generation_id)
        readiness = current_runtime_readiness()
        await channel.send(
            "READY",
            generation_id=generation_id,
            **readiness.model_dump(mode="json"),
        )
        state = "ready"
        while True:
            message = await channel.receive()
            message_type = require_string(message, "type", max_length=32)
            if message_type == "ACTIVATE" and state == "ready":
                state = "active"
                await channel.send("ACTIVE", generation_id=generation_id)
            elif message_type == "DRAIN" and state == "active":
                state = "drained"
                await channel.send("DRAINED", generation_id=generation_id)
            elif message_type == "SHUTDOWN" and state in {"ready", "drained"}:
                await channel.send("EXITING", generation_id=generation_id)
                await channel.close()
                return 0
            else:
                await channel.send("FAILED", code="invalid_transition", generation_id=generation_id)
                await channel.close()
                return 3
    except (ConnectionError, OSError, ControlProtocolError):
        return 4


def main() -> None:
    raise SystemExit(asyncio.run(run_passive_runtime_runner()))


if __name__ == "__main__":
    main()


__all__ = ["current_runtime_readiness", "main", "run_passive_runtime_runner"]
