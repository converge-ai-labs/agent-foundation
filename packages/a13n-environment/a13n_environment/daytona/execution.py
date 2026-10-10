"""Daytona execution implementation."""

import math
import shlex
from urllib.parse import urlsplit

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import TargetState
from ..native.environment import NativeExecution, NativeRuntime
from ..native.errors import failure
from ..native.http import NativeHTTP, decode_response
from .shared import DaytonaEnvironmentConfiguration, DaytonaReference, Execution, Proxy


class DaytonaExecution(DaytonaReference, NativeExecution[DaytonaEnvironmentConfiguration, TargetState]):
    def __init__(
        self,
        config: DaytonaEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
    ):
        super().__init__(config, environment_id, state, runtime)
        self.toolbox: NativeHTTP | None = None

    async def close_transport(self) -> None:
        try:
            if self.toolbox:
                await self.toolbox.close()
        finally:
            await super().close_transport()

    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        target = await self.lookup()
        if target is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if target.state != "started":
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        # Organization policy may impose a hard TTL even when creation requests none.
        if target.autoDestroyAt is not None:
            raise failure(self.provider_key, "provider_expiry_unsupported", Category.UNSUPPORTED)
        proxy = target.toolboxProxyUrl
        if proxy is None:
            proxy = decode_response(
                Proxy, await self.transport().request("GET", self.path + "/toolbox-proxy-url"), self.provider_key
            ).url
        parsed = urlsplit(proxy)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise failure(
                self.provider_key, "provider_response_invalid", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
            )
        if self.toolbox:
            await self.toolbox.close()
        self.toolbox = NativeHTTP(
            self.provider_key,
            proxy.rstrip("/") + "/",
            self.token.get_secret_value(),
            self.config.request_timeout_seconds + 10,
        )
        await self.open_operations(self.execute, target.id, execution_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.target and self.toolbox
        result = decode_response(
            Execution,
            await self.toolbox.request(
                "POST",
                f"{self.target.target_id}/process/execute",
                body={"command": shlex.join(argv), "timeout": math.ceil(timeout)},
            ),
            self.provider_key,
            mutation=True,
        )
        if result.exitCode != 0:
            raise failure(
                self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE, certainty=Certainty.KNOWN
            )
        return result.result
