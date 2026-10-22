"""Bounded commands on an exact Vercel session; never replay a dispatched mutation."""

import asyncio
import json
import math

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.vercel.shared import VercelReference


async def command(
    reference: VercelReference,
    session_id: str,
    argv: list[str],
    *,
    timeout: float,
    sudo: bool = False,
    env: dict[str, str] | None = None,
    max_output: int = 24 * 1024 * 1024,
) -> str:
    output: list[str] = []
    size = 0
    try:
        async with asyncio.timeout(timeout + 5):
            async with reference.transport().stream(
                "POST",
                f"/v2/sandboxes/sessions/{session_id}/cmd",
                body={
                    "command": argv[0],
                    "args": argv[1:],
                    "env": env or {},
                    "sudo": sudo,
                    "wait": True,
                    "logs": True,
                    "timeout": math.ceil(timeout * 1000),
                },
            ) as response:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if "command" in event:
                        code = event["command"].get("exitCode")
                        if code is not None:
                            if type(code) is not int:
                                raise ValueError
                            if code != 0:
                                raise failure(
                                    "vercel",
                                    "provider_command_failed",
                                    Category.PROVIDER_FAILURE,
                                    certainty=Certainty.KNOWN,
                                )
                            return "".join(output)
                    elif event.get("stream") == "stdout":
                        data = event["data"]
                        size += len(data.encode())
                        if size > max_output:
                            raise ValueError
                        output.append(data)
                    elif event.get("stream") == "error":
                        break
    except (ValueError, TypeError, KeyError, TimeoutError):
        pass
    raise failure("vercel", "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN)
