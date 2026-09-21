"""Vercel named sandboxes; native sessions are execution incarnations, not ownership."""

import asyncio
import json
import math
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import EnvironmentProviderOutcomeCertainty as Certainty
from ..models import EnvironmentState
from ..native.configuration import CommandConfiguration, NamedTargetState, TokenCredential
from ..native.environment import NativeEnvironment, NativeRuntime, native_definition
from ..native.errors import failure
from ..native.http import NativeHTTP, decode_response


class VercelConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    team_id: str = Field(min_length=1, max_length=128, title="Team ID")
    project_id: str = Field(min_length=1, max_length=128, title="Project ID")


class VercelEnvironmentConfiguration(CommandConfiguration):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_extra={"x-primary-fields": ["runtime", "timeout_seconds", "vcpus"]}
    )
    runtime: str = Field(default="python3.13", min_length=1, max_length=128)
    timeout_seconds: int = Field(default=3600, ge=300, le=18000)
    vcpus: int = Field(default=2, ge=2, le=8)
    root: str = "/vercel/sandbox"
    python: str = "/vercel/runtimes/python/bin/python3"


class Sandbox(BaseModel):
    name: str
    persistent: bool
    status: str
    createdAt: int
    tags: dict[str, str] = {}


class Session(BaseModel):
    id: str
    status: str
    timeout: int
    startedAt: int | None = None


class SandboxResponse(BaseModel):
    sandbox: Sandbox
    session: Session


class VercelEnvironment(NativeEnvironment[VercelEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        config: VercelEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        allow_create: bool,
        operation_id: str,
    ):
        del operation_id
        super().__init__("vercel", config, environment_id, state, managed=allow_create, state_model=NamedTargetState)
        assert isinstance(runtime.configuration, VercelConnectionConfiguration)
        assert isinstance(runtime.credential, TokenCredential)
        self.backend = runtime.configuration
        self.token = runtime.credential.api_key
        self.http: NativeHTTP | None = None
        self.session: Session | None = None

    def transport(self) -> NativeHTTP:
        if self.http is None:
            self.http = NativeHTTP(
                self.provider_key,
                "https://api.vercel.com",
                self.token.get_secret_value(),
                self.config.request_timeout_seconds + 10,
                params={"teamId": self.backend.team_id},
            )
        return self.http

    @property
    def path(self) -> str:
        return "/v2/sandboxes/" + quote(self.target.target_id if self.target else self.name, safe="")

    def accept(self, response: SandboxResponse, *, creating: bool = False) -> None:
        identity = f"{response.sandbox.name}:{response.sandbox.createdAt}"
        if self.target and not creating and self.target.backing_id != identity:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self.validate_labels(response.sandbox.tags)
        if not response.sandbox.persistent:
            raise failure(self.provider_key, "provider_target_conflict", Category.CONFLICT)
        self.remember(
            NamedTargetState(
                backing_id=identity,
                target_id=response.sandbox.name,
                environment_id=self.owner,
                configuration_fingerprint=self.config.fingerprint,
            )
        )
        self.session = response.session

    async def inspect(self, *, resume: bool = False) -> SandboxResponse | None:
        raw = await self.transport().request(
            "GET", self.path, params={"projectId": self.backend.project_id, "resume": str(resume).lower()}, missing=True
        )
        if raw is None:
            return None
        response = decode_response(SandboxResponse, raw, self.provider_key, mutation=resume)
        self.accept(response)
        return response

    async def _prepare(self, *, mount_id: str) -> None:
        response = await self.inspect()
        if response is None:
            if not self.managed:
                raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
            raw = await self.transport().request(
                "POST",
                "/v2/sandboxes",
                body={
                    "name": self.name,
                    "projectId": self.backend.project_id,
                    "runtime": self.config.runtime,
                    "persistent": True,
                    "timeout": self.config.timeout_seconds * 1000,
                    "resources": {"vcpus": self.config.vcpus},
                    "snapshotExpiration": 0,
                    "tags": self.labels,
                },
            )
            response = decode_response(SandboxResponse, raw, self.provider_key, mutation=True)
            self.accept(response, creating=True)
        elif response.session.status == "stopped":
            response = await self.inspect(resume=True)
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while response is not None and response.session.status in {"pending", "stopping", "snapshotting"}:
                await asyncio.sleep(0.5)
                response = await self.inspect()
        if response is None or response.session.status != "running":
            raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
        identity = f"{response.sandbox.name}:{response.sandbox.createdAt}"
        await self.open_operations(self.execute, identity, mount_id, incarnation=response.session.id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.session
        output: list[str] = []
        size = 0
        async with self.transport().stream(
            "POST",
            f"/v2/sandboxes/sessions/{self.session.id}/cmd",
            body={
                "command": argv[0],
                "args": argv[1:],
                "env": {},
                "sudo": False,
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
                            raise ValueError("Invalid native exit code")
                        if code != 0:
                            raise failure(
                                self.provider_key,
                                "provider_command_failed",
                                Category.PROVIDER_FAILURE,
                                certainty=Certainty.KNOWN,
                            )
                        return "".join(output)
                elif event.get("stream") == "stdout":
                    data = event["data"]
                    size += len(data.encode())
                    if size > 24 * 1024 * 1024:
                        raise failure(
                            self.provider_key,
                            "provider_response_invalid",
                            Category.UNKNOWN_OUTCOME,
                            certainty=Certainty.UNKNOWN,
                        )
                    output.append(data)
                elif event.get("stream") == "error":
                    break
        raise failure(
            self.provider_key, "provider_unknown_outcome", Category.UNKNOWN_OUTCOME, certainty=Certainty.UNKNOWN
        )

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        response = await self.inspect()
        if response is None:
            return "absent"
        if response.session.status == "running":
            return "running"
        if response.session.status == "stopped":
            return "stopped"
        raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def _stop(self) -> None:
        response = await self.inspect()
        if response is None or response.session.status == "stopped":
            return
        await self.transport().request("POST", f"/v2/sandboxes/sessions/{response.session.id}/stop")
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                response = await self.inspect()
                if response is None or response.session.status == "stopped":
                    return
                if response.session.status in {"failed", "aborted"}:
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        response = await self.inspect()
        if response is None or response.session.status != "running" or response.session.startedAt is None:
            raise failure(self.provider_key, "provider_target_stopped", Category.CONFLICT)
        if deadline.tzinfo is None or not operation_id:
            raise failure(self.provider_key, "provider_keepalive_invalid", Category.INVALID)
        session = response.session
        assert session.startedAt is not None
        expires_ms = session.startedAt + session.timeout
        needed = math.ceil(deadline.timestamp() * 1000) - expires_ms
        if needed > 0:
            if session.timeout + needed > self.config.timeout_seconds * 1000:
                raise failure(self.provider_key, "provider_keepalive_limit", Category.UNSUPPORTED)
            # A relative extension is never retried here after an uncertain response.
            await self.transport().request(
                "POST", f"/v2/sandboxes/sessions/{session.id}/extend-timeout", body={"duration": needed}
            )
            response = await self.inspect()
            if response is None or response.session.startedAt is None:
                raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
            expires_ms = response.session.startedAt + response.session.timeout
        expiry = datetime.fromtimestamp(expires_ms / 1000, UTC)
        if expiry < deadline:
            raise failure(self.provider_key, "provider_keepalive_unsatisfied", Category.UNAVAILABLE)
        return expiry

    async def _destroy(self) -> None:
        if not self.managed:
            raise failure(self.provider_key, "provider_denied", Category.DENIED)
        if await self.inspect() is not None:
            await self.transport().request(
                "DELETE",
                self.path,
                params={"projectId": self.backend.project_id, "deleteOrphanSnapshots": "true"},
                missing=True,
            )

        await self.confirm_deleted(self.inspect)

    async def close_transport(self) -> None:
        if self.http:
            await self.http.close()


VERCEL = native_definition(
    type="vercel",
    display_name="Vercel Sandbox",
    configuration_model=VercelConnectionConfiguration,
    credential_model=TokenCredential,
    environment_model=VercelEnvironmentConfiguration,
    state_model=NamedTargetState,
    environment_type=VercelEnvironment,
    supports_stop=True,
    requires_keepalive=True,
)
