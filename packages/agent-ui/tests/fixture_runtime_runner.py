"""Cross-platform subprocess fixture for runtime-generation lifecycle tests."""

from __future__ import annotations

import asyncio
import os
import signal

from a13n_ui.runtime_generations.protocol import ControlChannel, require_generation, require_string
from a13n_ui.runtime_generations.runner import current_runtime_readiness


async def main() -> int:
    host = os.environ.pop("A13N_UI_RUNNER_CONTROL_HOST")
    port = int(os.environ.pop("A13N_UI_RUNNER_CONTROL_PORT"))
    token = os.environ.pop("A13N_UI_RUNNER_CONTROL_TOKEN")
    generation_id = os.environ.pop("A13N_UI_RUNNER_GENERATION_ID")
    max_message_bytes = int(os.environ.pop("A13N_UI_RUNNER_MAX_MESSAGE_BYTES"))
    behavior = os.environ.get("A13N_UI_TEST_RUNNER_BEHAVIOR", "passive")

    reader, writer = await asyncio.open_connection(host, port, limit=max_message_bytes)
    channel = ControlChannel(reader, writer, max_message_bytes=max_message_bytes)
    await channel.send("HELLO", token=token, generation_id=generation_id)
    welcome = await channel.receive(expected_type="WELCOME")
    require_generation(welcome, generation_id)
    await channel.send("READY", generation_id=generation_id)
    state = "ready"
    while True:
        message = await channel.receive()
        message_type = require_string(message, "type", max_length=32)
        if message_type == "PROBE" and state == "ready":
            readiness = current_runtime_readiness()
            await channel.send(
                "RUNTIME_READY",
                generation_id=generation_id,
                **readiness.model_dump(mode="json"),
            )
        elif message_type == "PREPARE" and state == "ready":
            state = "prepared"
            await channel.send("PREPARED", generation_id=generation_id)
        elif message_type == "COMMIT" and state == "prepared":
            state = "active"
            await channel.send("ACTIVE", generation_id=generation_id)
        elif message_type == "DRAIN" and state == "active" and behavior == "stall_drain":
            if os.name != "nt":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            await asyncio.sleep(60)
        elif message_type == "DRAIN" and state == "active":
            state = "drained"
            await channel.send("DRAINED", generation_id=generation_id)
        elif message_type == "SHUTDOWN" and state in {"ready", "prepared", "drained"}:
            await channel.send("EXITING", generation_id=generation_id)
            await channel.close()
            return 0
        elif message_type == "ABORT" and state in {"ready", "prepared"}:
            await channel.send("ABORTED", generation_id=generation_id)
            await channel.close()
            return 0
        else:
            return 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
