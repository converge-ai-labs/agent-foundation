"""Fixed-target Daytona routing and credentials for the shared Envd connection."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from a13n_envd_client import EIPSession
from pydantic import BaseModel, Field, SecretStr

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.daytona.shared import DaytonaReference, Execution
from a13n_harness.providers.environment.native.configuration import TargetState
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.http import decode_response
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdConnectionConfiguration,
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.environment import RemoteEnvdExecution
from a13n_harness.providers.environment.remote_envd.managed import managed_http_session


class Preview(BaseModel):
    url: str
    token: SecretStr = Field(min_length=1, max_length=4096)


class HttpReference(DaytonaReference):
    def observe(self, state: TargetState) -> None:
        if state != self.target:
            raise failure(self.provider_key, "provider_target_changed", Category.CONFLICT)

    async def close(self) -> None:
        await self.close_transport()


@asynccontextmanager
async def http_session(reference: DaytonaReference) -> AsyncIterator[EIPSession]:
    try:
        reference.require_target()
        target = await reference.lookup()
        if target is None:
            raise failure("daytona", "provider_target_missing", Category.MISSING)
        if target.state != "started":
            raise failure("daytona", "provider_unavailable", Category.UNAVAILABLE)
        if target.autoDestroyAt:
            raise failure("daytona", "provider_target_expiring", Category.UNSUPPORTED)
        preview = decode_response(
            Preview,
            await reference.transport().request("GET", reference.path + "/ports/8888/preview-url"),
            "daytona",
        )
        url = urlsplit(preview.url)
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise failure("daytona", "provider_response_invalid", Category.UNAVAILABLE)
        connection = HttpEnvdConnectionConfiguration(
            endpoint=preview.url,
            request_timeout=reference.config.request_timeout_seconds,
        )

        async def credential() -> HttpEnvdCredential:
            toolbox = await reference.open_toolbox(target)
            result = decode_response(
                Execution,
                await toolbox.request(
                    "POST",
                    target.id + "/process/execute",
                    body={
                        "command": "sudo -n python3 -I -c \"import sys; sys.stdout.write(open('/var/lib/a13n-envd/http-token').read(4097))\"",
                        "timeout": 10,
                    },
                ),
                "daytona",
            )
            if result.exitCode != 0:
                raise failure("daytona", "envd_credential_unavailable", Category.UNAVAILABLE)
            try:
                return HttpEnvdCredential(
                    token=SecretStr(result.result),
                    headers={"X-Daytona-Preview-Token": preview.token},
                )
            except ValueError:
                raise failure("daytona", "envd_credential_invalid", Category.UNAVAILABLE) from None

        async with managed_http_session(
            provider_key="daytona",
            connection=connection,
            endpoint_policy=reference.backend._endpoint_policy,
            load_credential=credential,
            expected_device_id=reference.owner,
            configuration=RemoteEnvdEnvironmentConfiguration(
                working_directory="/workspace",
                required_methods=("computer.describe", "computer.observe") if reference.config.desktop else (),
            ),
        ) as session:
            yield session
    finally:
        await reference.close_transport()


def http_execution(reference: DaytonaReference) -> RemoteEnvdExecution:
    reference.require_target()
    assert reference.state is not None
    return RemoteEnvdExecution(
        provider_key="daytona",
        environment_id=reference.environment_id,
        state=reference.state,
        working_directory="/workspace",
        session_context=http_session(reference),
    )
