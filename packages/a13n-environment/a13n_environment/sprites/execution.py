"""Sprites execution implementation."""

import asyncio
import json
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..native.configuration import NamedTargetState
from ..native.environment import NativeExecution
from ..native.errors import failure
from .shared import SpritesEnvironmentConfiguration, SpritesReference


class SpritesExecution(SpritesReference, NativeExecution[SpritesEnvironmentConfiguration, NamedTargetState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        info = await self.lookup()
        if info is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        await self.open_operations(self.execute, info.id, execution_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        launcher = [self.config.python, "-I", "-c", "import json,os,sys; a=json.load(sys.stdin); os.execvp(a[0],a)"]
        query = urlencode([*(("cmd", arg) for arg in launcher), ("path", launcher[0]), ("stdin", "true")])
        url = "wss://api.sprites.dev" + self.path + "/exec?" + query
        output = bytearray()
        try:
            async with asyncio.timeout(timeout):
                async with connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {self.token.get_secret_value()}"},
                    max_size=1024 * 1024,
                    close_timeout=1,
                ) as socket:
                    await socket.send(b"\x00" + json.dumps(argv).encode())
                    await socket.send(b"\x04")
                    async for message in socket:
                        if isinstance(message, bytes):
                            if not message:
                                continue
                            channel, payload = message[0], message[1:]
                            if channel == 1:
                                output.extend(payload)
                                if len(output) > 24 * 1024 * 1024:
                                    raise failure(
                                        self.provider_key,
                                        "provider_response_invalid",
                                        Category.UNKNOWN_OUTCOME,
                                        certainty=Certainty.UNKNOWN,
                                    )
                            elif channel == 3:
                                if len(payload) != 1:
                                    break
                                if payload[0] != 0:
                                    raise failure(
                                        self.provider_key,
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
                                        self.provider_key,
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
            self.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
        )
