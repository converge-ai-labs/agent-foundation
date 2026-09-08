"""Acquire accepted input through fresh, authorized Worker resources."""

from __future__ import annotations

import posixpath
from collections.abc import AsyncIterable, AsyncIterator

import httpx2
from a13n_harness import AgentContext
from a13n_harness.environment.providers import BoundEnvironment
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.assets.models import AssetRecord
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam.authorization import WorkspaceAction, authorize_persisted_workspace_principal_action
from a13n_service.storage import short_session

from .domain import Run
from .harness_control import RunControlCapability
from .input import AcquiredBinary, AgentInputError, BinaryContentSource, PathBinarySource, UrlBinarySource


class WorkerInputSources:
    """Keep Environment access inside the logical Harness lifetime and clear it at cleanup."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        assets: AssetObjectStore,
        http: httpx2.AsyncClient,
        endpoint_policy: EndpointPolicy,
        run: Run,
        workspace_id: str,
    ) -> None:
        self._sessions = sessions
        self._assets = assets
        self._http = http
        self._endpoint_policy = endpoint_policy
        self._run = run
        self._workspace_id = workspace_id
        self.environment: BoundEnvironment | None = None

    async def open(self, source: BinaryContentSource, *, max_bytes: int) -> AcquiredBinary:
        if isinstance(source, UrlBinarySource):
            url = await self._endpoint_policy.validate(source.url, resolve_dns=True)

            # Open inside the iterator so cancellation always closes the HTTP response.
            async def chunks() -> AsyncIterator[bytes]:
                async with self._http.stream("GET", url, follow_redirects=False) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        yield chunk

            return AcquiredBinary(chunks(), None)
        if isinstance(source, PathBinarySource):
            environment = self._require_environment()
            selection = await environment.resolve_files(source.path, alias=source.environment_binding)

            async def path_chunks() -> AsyncIterator[bytes]:
                async with environment.open_files(selection) as files:
                    async for chunk in files.read_bytes_stream(selection.logical_path):
                        yield chunk

            return AcquiredBinary(path_chunks(), None)
        async with short_session(self._sessions) as session:
            await authorize_persisted_workspace_principal_action(
                session,
                principal=self._run.authority_principal,
                organization_id=self._run.organization_id,
                workspace_id=self._workspace_id,
                action=WorkspaceAction.asset_use,
            )
            row = await session.get(AssetRecord, source.asset_id)
            if row is None or row.workspace_id != self._workspace_id or row.deleted_at is not None:
                raise AgentInputError("input_asset_unavailable", "The accepted Asset is unavailable")
            asset = row.to_resource()
        if asset.size_bytes > max_bytes:
            raise AgentInputError("input_binary_too_large", "The Asset exceeds the accepted input limit")

        async def asset_chunks() -> AsyncIterator[bytes]:
            # The mapper can reject media metadata without iterating. Stage lazily
            # so that such a rejection cannot strand a temporary Asset file.
            staged = await self._assets.prepare_verified_content(asset)
            try:
                async for chunk in staged.chunks():
                    yield chunk
            finally:
                await staged.remove()

        return AcquiredBinary(asset_chunks(), asset.media_type)

    async def replace(self, path: str, chunks: AsyncIterable[bytes]) -> None:
        files = self._require_environment().files
        await files.mkdir(posixpath.dirname(path), parents=True, exist_ok=True)
        await files.write_bytes_stream(path, chunks, mode="upsert")

    def close(self) -> None:
        self.environment = None

    def _require_environment(self) -> BoundEnvironment:
        if self.environment is None:
            raise AgentInputError("input_environment_unavailable", "The Run Environment is not entered")
        return self.environment


class WorkerInputCapability(AbstractCapability[AgentContext]):
    """Bind only the current logical Run's entered input Environment."""

    id = "a13n.service.input-environment"

    def __init__(self, sources: WorkerInputSources) -> None:
        self._sources = sources

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(RunControlCapability,))

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        self._sources.environment = ctx.deps.environment
        return self
