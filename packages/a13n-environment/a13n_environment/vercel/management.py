"""Vercel management implementation."""

import asyncio
import math
from datetime import UTC, datetime

from ..errors import EnvironmentProviderErrorCategory as Category
from ..models import EnvironmentState
from ..native.configuration import NamedTargetState
from ..native.environment import NativeManagement, NativeRuntime
from ..native.errors import failure
from ..native.http import decode_response
from .shared import SandboxResponse, VercelEnvironmentConfiguration, VercelReference


class VercelManagement(VercelReference, NativeManagement[VercelEnvironmentConfiguration, NamedTargetState]):
    def __init__(
        self,
        config: VercelEnvironmentConfiguration,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: NativeRuntime,
        operation_id: str,
    ):
        super().__init__(config, environment_id, state, runtime)
        del operation_id

    async def create(self) -> None:
        response = await self.lookup()
        if response is None:
            if self.target is not None:
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
            self.accept(response)
        await self.start()

    async def start(self) -> None:
        response = await self.lookup()
        if response is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        if response.session.status == "stopped":
            raw = await self.transport().request(
                "GET",
                self.path,
                params={"projectId": self.backend.project_id, "resume": "true"},
            )
            response = decode_response(SandboxResponse, raw, self.provider_key, mutation=True)
            self.accept(response)
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while response is not None and response.session.status in {"pending", "stopping", "snapshotting"}:
                await asyncio.sleep(0.5)
                response = await self.lookup()
        if response is None or response.session.status != "running":
            raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)

    async def stop(self) -> None:
        response = await self.lookup()
        if response is None or response.session.status == "stopped":
            return
        # Vercel refuses a POST without a JSON body as an unsupported media type.
        await self.transport().request("POST", f"/v2/sandboxes/sessions/{response.session.id}/stop", body={})
        async with asyncio.timeout(self.config.request_timeout_seconds):
            while True:
                response = await self.lookup()
                if response is None or response.session.status == "stopped":
                    return
                if response.session.status in {"failed", "aborted"}:
                    raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
                await asyncio.sleep(0.5)

    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime:
        response = await self.lookup()
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
            response = await self.lookup()
            if response is None or response.session.startedAt is None:
                raise failure(self.provider_key, "provider_unavailable", Category.UNAVAILABLE)
            expires_ms = response.session.startedAt + response.session.timeout
        expiry = datetime.fromtimestamp(expires_ms / 1000, UTC)
        if expiry < deadline:
            raise failure(self.provider_key, "provider_keepalive_unsatisfied", Category.UNAVAILABLE)
        return expiry

    async def destroy(self) -> None:
        if await self.lookup() is not None:
            await self.transport().request(
                "DELETE",
                self.path,
                params={"projectId": self.backend.project_id, "deleteOrphanSnapshots": "true"},
                missing=True,
            )

        await self.confirm_deleted(self.lookup)
