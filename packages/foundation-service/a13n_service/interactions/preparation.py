"""Organization-scoped loading of the exact dependencies needed by a claimed Attempt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from a13n_harness import SafeFailure
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentRevision, EffectiveAgentConfig, PluginRuntimeMode, canonical_digest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.agents.resolution import MAX_SUBAGENT_NODES
from a13n_service.iam import AuthorizationError, WorkspaceAction
from a13n_service.iam.authorization import authorize_persisted_agent_principal_actions
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .attempts import AttemptContext, AttemptPreparationError, read_attempt_lease
from .domain import Run
from .models import SessionRecord
from .objects import StoredRunState, run_state_key


@dataclass(frozen=True, slots=True)
class AttemptDependencies:
    """Detached accepted facts; no session, credential, or independent authority."""

    run: Run
    workspace_id: str
    root_revision: AgentRevision
    child_revisions: Mapping[str, AgentRevision]


class AttemptDependencyLoader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, clock: Clock = utc_now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def load(self, authority: AttemptContext, state: StoredRunState) -> AttemptDependencies:
        """Reauthorize persisted Principal and load only the frozen Revision graph."""

        try:
            async with short_session(self._sessions) as database:
                record, _, _ = await read_attempt_lease(database, authority, self._clock())
                run = record.to_resource()
                _validate_run_state(run, state)
                session = await database.scalar(
                    select(SessionRecord).where(
                        SessionRecord.organization_id == authority.organization_id, SessionRecord.id == run.session_id
                    )
                )
                if session is None:
                    raise _invalid("run_session_unavailable")
                workspace_id = session.workspace_id
                await _authorize(database, run, workspace_id, run.agent_id)
                root = await _require_revision(database, run, workspace_id, run.agent_id, run.agent_revision_id)
                children = await self._load_children(
                    database, run, workspace_id, state.envelope.effective_agent_config, root.plugin_runtime_mode
                )
                return AttemptDependencies(run, workspace_id, root, MappingProxyType(children))
        except AuthorizationError as error:
            raise _invalid("run_authorization_denied") from error
        except ValidationError as error:
            raise _invalid("run_dependency_invalid") from error

    @staticmethod
    async def _load_children(
        database: AsyncSession,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        runtime_mode: PluginRuntimeMode,
    ) -> dict[str, AgentRevision]:
        pending = list(config.resolved_subagents)
        revisions: dict[str, AgentRevision] = {}
        while pending:
            edge = pending.pop()
            retained = revisions.get(edge.child_agent_revision_id)
            if retained is not None:
                if retained.agent_id != edge.child_agent_id:
                    raise _invalid("subagent_revision_identity_mismatch")
                continue
            if len(revisions) >= MAX_SUBAGENT_NODES - 1:
                raise _invalid("subagent_graph_too_large")
            await _authorize(database, run, workspace_id, edge.child_agent_id)
            revision = await _require_revision(
                database, run, workspace_id, edge.child_agent_id, edge.child_agent_revision_id
            )
            if revision.plugin_runtime_mode != runtime_mode:
                raise _invalid("subagent_runtime_mode_mismatch")
            revisions[revision.id] = revision
            pending.extend(revision.resolved_subagents)
        return revisions


async def _authorize(database: AsyncSession, run: Run, workspace_id: str, agent_id: str) -> None:
    await authorize_persisted_agent_principal_actions(
        database,
        principal=run.authority_principal,
        organization_id=run.organization_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        actions=frozenset({WorkspaceAction.agent_invoke}),
    )


async def _require_revision(
    database: AsyncSession, run: Run, workspace_id: str, agent_id: str, revision_id: str
) -> AgentRevision:
    row = (
        await database.execute(
            select(AgentRecord, AgentRevisionRecord)
            .join(
                AgentRevisionRecord,
                (AgentRevisionRecord.agent_id == AgentRecord.id)
                & (AgentRevisionRecord.organization_id == AgentRecord.organization_id)
                & (AgentRevisionRecord.workspace_id == AgentRecord.workspace_id),
            )
            .where(
                AgentRecord.organization_id == run.organization_id,
                AgentRecord.workspace_id == workspace_id,
                AgentRecord.id == agent_id,
                AgentRevisionRecord.id == revision_id,
            )
        )
    ).one_or_none()
    if row is None or not row[0].enabled or row[0].archived_at is not None:
        raise _invalid("agent_revision_unavailable")
    return row[1].to_resource()


def _validate_run_state(run: Run, state: StoredRunState) -> None:
    envelope = state.envelope
    if (
        state.info.key != run_state_key(run.organization_id, run.id)
        or envelope.run_id != run.id
        or envelope.thread_id != run.thread_id
        or envelope.agent_id != run.agent_id
        or envelope.agent_revision_id != run.agent_revision_id
        or envelope.runtime_lock_digest != run.runtime_lock_digest
        or envelope.effective_agent_config.content_digest != run.effective_agent_config_digest
        or canonical_digest(
            envelope.effective_agent_config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
        )
        != run.effective_agent_config_digest
        or envelope.effective_agent_config.resolved_model.execution.observation() != run.model_execution_observation
    ):
        raise _invalid("run_state_identity_mismatch")


def _invalid(code: str) -> AttemptPreparationError:
    return AttemptPreparationError(
        SafeFailure(code=code, message="The accepted Run dependencies are unavailable or no longer authorized."),
        retryable=False,
    )


__all__ = ["AttemptDependencies", "AttemptDependencyLoader"]
