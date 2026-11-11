"""Bounded, stdin-carried Sprites command dispatch; credentials never enter argv."""

import asyncio
import base64
import json
import shlex
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.sprites.shared import SpritesReference


async def command(
    reference: SpritesReference,
    argv: list[str],
    timeout: float,
    *,
    max_output: int = 24 * 1024 * 1024,
    stdin: str | None = None,
) -> str:
    # Carry the quoted program through stdin rather than the provider URL. The
    # guest only needs POSIX sh; Python remains an optional native execution tool.
    program = "exec " + shlex.join(argv)
    if stdin is not None:
        encoded = base64.b64encode(stdin.encode()).decode()
        program = f"printf %s {shlex.quote(encoded)} | base64 -d | " + shlex.join(argv)
    query = urlencode([("cmd", "/bin/sh"), ("path", "/bin/sh"), ("stdin", "true")])
    url = "wss://api.sprites.dev" + reference.path + "/exec?" + query
    output = bytearray()
    try:
        async with asyncio.timeout(timeout):
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {reference.token.get_secret_value()}"},
                max_size=1024 * 1024,
                close_timeout=1,
            ) as socket:
                await socket.send(b"\x00" + (program + "\n").encode())
                await socket.send(b"\x04")
                async for message in socket:
                    if isinstance(message, bytes):
                        if not message:
                            continue
                        channel, payload = message[0], message[1:]
                        if channel == 1:
                            output.extend(payload)
                            if len(output) > max_output:
                                raise failure(
                                    reference.provider_key,
                                    "provider_response_invalid",
                                    Category.UNKNOWN_OUTCOME,
                                    certainty=Certainty.UNKNOWN,
                                )
                        elif channel == 3:
                            if len(payload) != 1:
                                break
                            if payload[0] != 0:
                                raise failure(
                                    reference.provider_key,
                                    "provider_command_failed",
                                    Category.PROVIDER_FAILURE,
                                    certainty=Certainty.KNOWN,
                                )
                            return output.decode()
                    else:
                        event = json.loads(message)
                        if event.get("type") == "exit":
                            if type(event["exit_code"]) is not int:
                                raise ValueError("Invalid native exit code")
                            if event["exit_code"] != 0:
                                raise failure(
                                    reference.provider_key,
                                    "provider_command_failed",
                                    Category.PROVIDER_FAILURE,
                                    certainty=Certainty.KNOWN,
                                )
                            return output.decode()
                        if event.get("type") == "error":
                            break
    except (WebSocketException, TimeoutError, OSError, ValueError, TypeError, KeyError):
        pass
    raise failure(
        reference.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
    )
