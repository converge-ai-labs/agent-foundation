"""Attempt-fenced publication from a selected Agent's active Environment."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated

from a13n_harness import AgentContext
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment, FileScopeSelection
from a13n_harness.tools import current_invocation_scope
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import Field, JsonValue
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentConfig
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.iam import AuthenticatedActor, PrincipalRef, WorkspaceAction, authorize_agent, authorize_workspace
from a13n_service.iam.domain import AuthorizationError, PrincipalType
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptContext,
    lock_attempt_lease,
    read_attempt_lease,
)
from a13n_service.interactions.models import SessionRecord
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .audit import asset_audit_record
from .domain import Asset, AssetRef, RunOutputAssetSource
from .errors import AssetError, asset_content_invalid, asset_idempotency_conflict
from .models import AssetRecord
from .objects import AssetObjectStore, asset_content_key
from .publication import AssetPublisher

_TOOL_ID = "service.publish_asset"


@dataclass(frozen=True, slots=True)
class PublicationSelection:
    """Trusted accepted-graph binding; never reconstructed from model arguments."""

    workspace_id: str
    agent_id: str
    agent_revision_id: str
    effective_config_digest: str


class AssetRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        publisher: AssetPublisher,
        objects: AssetObjectStore,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._publisher = publisher
        self._objects = objects
        self._clock = clock

    async def publish(
        self,
        *,
        current_context: Callable[[], AttemptContext],
        selection: PublicationSelection,
        invocation_id: str,
        environment: BoundEnvironment,
        path: str,
        filename: str | None,
        media_type: str | None,
    ) -> AssetRef:
        if not 1 <= len(invocation_id) <= 128:
            raise asset_content_invalid()
        authority = current_context()
        async with short_session(self._sessions) as session:
            await self._authorize(session, authority, selection, lock=False)
        route = await environment.resolve_files(path)
        default = next(
            (mount for mount in environment.snapshot.mounts if mount.name == environment.snapshot.default_mount), None
        )
        if (
            default is None
            or route.resolved_path.mount_id
            != environment.select_files(default.mount_path or f"/environment/{default.name}").resolved_path.mount_id
        ):
            raise asset_content_invalid()
        async with self._publisher.stage(
            organization_id=authority.organization_id,
            workspace_id=selection.workspace_id,
            source=RunOutputAssetSource(run_id=authority.run_id),
            filename=PurePosixPath(path).name if filename is None else filename,
            media_type=media_type,
            body=_file_contents(environment, route),
            content_length=None,
        ) as candidate:
            try:
                await self._publisher.publish(candidate)
                current = current_context()
                if current.run_attempt_id != authority.run_attempt_id:
                    raise asset_content_invalid()
                return await self._commit(candidate.asset, current, selection, invocation_id)
            finally:
                # Unknown commit outcomes are reconciled through relational ownership.
                await self._discard_unowned(candidate.asset)

    async def _commit(
        self, asset: Asset, authority: AttemptContext, selection: PublicationSelection, invocation_id: str
    ) -> AssetRef:
        now = self._clock()
        async with transaction(self._sessions) as session:
            actor = await self._authorize(session, authority, selection, lock=True)
            existing = await session.scalar(
                select(AssetRecord).where(
                    AssetRecord.source_kind == "run_output",
                    AssetRecord.source_run_attempt_id == authority.run_attempt_id,
                    AssetRecord.source_invocation_id == invocation_id,
                )
            )
            if existing is not None:
                replay = existing.to_resource(source_run_id=authority.run_id)
                if _content_evidence(replay) != _content_evidence(asset):
                    raise asset_idempotency_conflict()
                return AssetRef.from_asset(replay)
            await require_object_publications(
                session,
                (
                    asset_content_key(
                        organization_id=asset.organization_id,
                        workspace_id=asset.workspace_id,
                        asset_id=asset.id,
                    ),
                ),
            )
            session.add(
                AssetRecord(
                    id=asset.id,
                    organization_id=asset.organization_id,
                    workspace_id=asset.workspace_id,
                    filename=asset.filename,
                    media_type=asset.media_type,
                    size_bytes=asset.size_bytes,
                    content_sha256=asset.content_sha256,
                    source_kind="run_output",
                    source_principal_type=None,
                    source_principal_id=None,
                    source_run_attempt_id=authority.run_attempt_id,
                    source_invocation_id=invocation_id,
                    created_at=now,
                    deleted_at=None,
                )
            )
            session.add(
                asset_audit_record(
                    actor=actor,
                    organization_id=asset.organization_id,
                    workspace_id=asset.workspace_id,
                    asset_id=asset.id,
                    action="asset.create",
                    source_kind="run_output",
                    now=now,
                )
            )
            await session.flush()
        return AssetRef.from_asset(asset)

    async def _authorize(
        self, session: AsyncSession, authority: AttemptContext, selection: PublicationSelection, *, lock: bool
    ) -> AuthenticatedActor:
        run, attempt, _ = await (lock_attempt_lease if lock else read_attempt_lease)(session, authority, self._clock())
        owner = await session.get(SessionRecord, run.session_id)
        revision = await session.get(AgentRevisionRecord, selection.agent_revision_id)
        if (
            attempt.status != "running"
            or owner is None
            or owner.workspace_id != selection.workspace_id
            or run.effective_agent_config_digest != selection.effective_config_digest
            or revision is None
            or revision.organization_id != run.organization_id
            or revision.workspace_id != selection.workspace_id
            or revision.agent_id != selection.agent_id
            or AgentConfig.model_validate(revision.config).asset_publication is None
        ):
            raise asset_content_invalid()
        actor = AuthenticatedActor(
            principal=PrincipalRef(
                principal_type=PrincipalType(run.authority_principal_type), principal_id=run.authority_principal_id
            ),
            auth_method="internal",
            credential_id="attempt-assets",
            boundary_workspace_id=selection.workspace_id,
        )
        for agent_id in {run.agent_id, selection.agent_id}:
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=selection.workspace_id,
                agent_id=agent_id,
                action=WorkspaceAction.agent_invoke,
            )
        await authorize_workspace(
            session, actor=actor, workspace_id=selection.workspace_id, action=WorkspaceAction.asset_create
        )
        return actor

    async def _discard_unowned(self, asset: Asset) -> None:
        try:
            await self._objects.delete_candidate(
                sessions=self._sessions,
                asset_id=asset.id,
                organization_id=asset.organization_id,
                workspace_id=asset.workspace_id,
            )
        except Exception:
            # Orphan reconciliation owns cleanup when database authority is unavailable.
            return


async def _file_contents(environment: BoundEnvironment, route: FileScopeSelection) -> AsyncIterator[bytes]:
    """Complete the provider-owned confined file read before any relational commit."""
    async with environment.open_files(route) as files:
        if (await files.stat(route.logical_path)).kind != "file":
            raise asset_content_invalid()
        async for chunk in files.read_bytes_stream(route.logical_path):
            yield chunk


def _content_evidence(asset: Asset) -> tuple[object, ...]:
    return (
        asset.organization_id,
        asset.workspace_id,
        asset.filename,
        asset.media_type,
        asset.size_bytes,
        asset.content_sha256,
        asset.source,
    )


class AssetCapability(AbstractCapability[AgentContext]):
    """Expose exactly one effectful tool for one selected accepted Agent definition."""

    id = "a13n.service.assets"

    def __init__(
        self, runtime: AssetRuntime, current_context: Callable[[], AttemptContext], selection: PublicationSelection
    ) -> None:
        self._runtime = runtime
        self._current_context = current_context
        self._selection = selection

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.publish_asset,
                    name="publish_asset",
                    description="Publish a regular file from the default Environment as an immutable Asset.",
                    harness_metadata=HarnessToolMetadata(
                        tool_id=_TOOL_ID,
                        effects=frozenset({"read", "write"}),
                        credential_audiences=(),
                        idempotency="none",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate", redact=True
                        ),
                    ),
                )
            ],
            id="service-asset-tools",
        )

    async def publish_asset(
        self,
        ctx: RunContext[AgentContext],
        path: Annotated[str, Field(min_length=1, max_length=4096)],
        filename: Annotated[str | None, Field(max_length=1024)] = None,
        media_type: Annotated[str | None, Field(max_length=255)] = None,
    ) -> dict[str, JsonValue]:
        invocation = current_invocation_scope().invocation
        if (
            invocation.tool_id != _TOOL_ID
            or invocation.run_id != ctx.deps.run_id
            or invocation.instance != ctx.deps.instance
            or invocation.tool_name != "publish_asset"
        ):
            raise asset_content_invalid()
        try:
            reference = await self._runtime.publish(
                current_context=self._current_context,
                selection=self._selection,
                invocation_id=invocation.invocation_id,
                environment=ctx.deps.environment,
                path=path,
                filename=filename,
                media_type=media_type,
            )
        except (AssetError, EnvironmentError, AttemptAuthorityError, AuthorizationError) as error:
            raise ModelRetry("Asset publication is unavailable or no longer authorized.") from error
        return reference.model_dump(mode="json")
