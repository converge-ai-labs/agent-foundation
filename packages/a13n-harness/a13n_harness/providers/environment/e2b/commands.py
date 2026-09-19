"""E2B command composition shared by operation facets."""

from __future__ import annotations

import itertools
import json
import shlex
from collections.abc import AsyncIterator
from importlib.resources import files
from typing import TYPE_CHECKING, BinaryIO, Literal

from pydantic import JsonValue, TypeAdapter

from .._guest_commands import file_helper_source
from ..models import EnvironmentError, EnvironmentOperationReceipt
from .configuration import E2BEnvironmentConfiguration
from .errors import sdk_errors

if TYPE_CHECKING:
    from e2b import AsyncSandbox

_OBJECT = TypeAdapter(dict[str, JsonValue])


class GuestCommands:
    def __init__(self, sandbox: AsyncSandbox, configuration: E2BEnvironmentConfiguration) -> None:
        self.sandbox = sandbox
        self.configuration = configuration
        self.generation = "unprepared"
        self.mount_id = "mount-prepare"
        self.closed = False
        self._operations = itertools.count(1)
        self._sources = {
            "files": file_helper_source(),
            "ports": files(__package__).joinpath("guest", "ports.py").read_text(),
        }

    def command(self, module: Literal["files", "ports"], arguments: dict[str, JsonValue]) -> str:
        if self.closed:
            raise EnvironmentError("E2B operations are closed.", code="environment_closed")
        return shlex.join(
            (self.configuration.python, "-I", "-c", self._sources[module], json.dumps(arguments, separators=(",", ":")))
        )

    async def _execute(
        self, module: Literal["files", "ports"], arguments: dict[str, JsonValue], *, mutation: bool
    ) -> dict[str, JsonValue]:
        from e2b.sandbox.commands.command_handle import CommandExitException

        command = self.command(module, arguments)
        with sdk_errors(mutation=mutation):
            try:
                result = await self.sandbox.commands.run(
                    command,
                    user=self.configuration.user,
                    timeout=self.configuration.request_timeout_seconds + 5,
                    request_timeout=self.configuration.request_timeout_seconds,
                )
            except CommandExitException:
                raise EnvironmentError("E2B command helper failed.", code="environment_provider_failure") from None
        try:
            value = _OBJECT.validate_json(result.stdout)
        except ValueError:
            raise EnvironmentError(
                "E2B returned an invalid operation result.", code="environment_provider_failure"
            ) from None
        error = value.get("error")
        if isinstance(error, str):
            details = value.get("details")
            safe_details = (
                {
                    key: item
                    for key, item in details.items()
                    if key in {"field", "reason", "hint"} and isinstance(item, str)
                }
                if isinstance(details, dict)
                else {}
            )
            raise EnvironmentError("E2B operation failed.", code=error, details=safe_details)
        return value

    async def files(
        self, action: str, arguments: dict[str, JsonValue], *, mutation: bool = False
    ) -> dict[str, JsonValue]:
        return await self._execute(
            "files",
            {
                "configuration": self.configuration.model_dump(mode="json"),
                "action": action,
                "arguments": arguments,
            },
            mutation=mutation,
        )

    async def port(self, port: int) -> dict[str, JsonValue]:
        return await self._execute("ports", {"port": port}, mutation=False)

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        with sdk_errors():
            reader = await self.sandbox.files.read(
                path,
                format="stream",
                user=self.configuration.user,
                request_timeout=self.configuration.request_timeout_seconds,
            )
            async with reader:
                async for chunk in reader:
                    yield chunk

    async def write_stream(self, path: str, source: BinaryIO) -> None:
        with sdk_errors(mutation=True):
            await self.sandbox.files.write(
                path,
                source,
                user=self.configuration.user,
                request_timeout=self.configuration.request_timeout_seconds,
            )

    def receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            mount_id=self.mount_id,
            observed_generation=self.generation,
            operation_id=f"operation-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )
