"""Application service for continuation-backed Sessions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from uuid import uuid4

from a13n_harness import HarnessState
from pydantic import TypeAdapter, ValidationError
from pydantic_ai.tools import DeferredToolRequests

from a13n_ui.composition import CompositionService, ResolvedAgentSnapshot, SnapshotReference
from a13n_ui.errors import SessionError, StoreIntegrityError
from a13n_ui.storage.objects import ObjectKind, ObjectRef
from a13n_ui.storage.runtime import LocalStore

from .models import (
    ContinuationRef,
    LocalSession,
    SessionAgentSkillSelection,
    SessionForkRef,
    SessionSummary,
    SessionUpdate,
    StoredSessionContinuation,
)
from .repository import SessionRepository


class SessionService:
    """Persist Session metadata and complete Harness continuations."""

    def __init__(self, store: LocalStore, composition: CompositionService) -> None:
        self._store = store
        self._composition = composition
        self._repository = SessionRepository(store)

    @property
    def repository(self) -> SessionRepository:
        return self._repository

    async def create(
        self,
        *,
        agent_snapshot: SnapshotReference,
        environment_snapshot: SnapshotReference,
        title: str | None = None,
        skill_selections: Sequence[SessionAgentSkillSelection] = (),
    ) -> LocalSession:
        agent = await self._composition.agent(agent_snapshot)
        environment = await self._composition.environment(environment_snapshot)
        await self._composition.compatibility(agent_snapshot, environment_snapshot)
        selections = self._validate_skill_selections(agent, skill_selections)
        continuation = await self.publish_continuation(HarnessState.new())
        return await self._repository.create(
            session_id=f"session-{uuid4().hex}",
            agent_snapshot=agent_snapshot,
            environment_snapshot=environment_snapshot,
            resolved_environment=environment,
            title=title,
            skill_selections=selections,
            continuation=continuation,
        )

    async def fork(
        self,
        source_session_id: str,
        *,
        agent_snapshot: SnapshotReference | None = None,
        environment_snapshot: SnapshotReference | None = None,
        title: str | None = None,
        skill_selections: Sequence[SessionAgentSkillSelection] | None = None,
    ) -> LocalSession:
        source = await self._repository.get(source_session_id)
        source_state, _deferred = await self.load_continuation(source.continuation)
        target_agent_ref = agent_snapshot or source.agent_snapshot
        target_environment_ref = environment_snapshot or source.environment_snapshot
        target_agent = await self._composition.agent(target_agent_ref)
        target_environment = await self._composition.environment(target_environment_ref)
        await self._composition.compatibility(target_agent_ref, target_environment_ref)
        state = (
            source_state.fork()
            if target_agent_ref.logical_digest == source.agent_snapshot.logical_digest
            else HarnessState.new(message_history=source_state.message_history)
        )
        selections = self._validate_skill_selections(
            target_agent,
            source.skill_selections if skill_selections is None else skill_selections,
        )
        continuation = await self.publish_continuation(state)
        return await self._repository.create(
            session_id=f"session-{uuid4().hex}",
            agent_snapshot=target_agent_ref,
            environment_snapshot=target_environment_ref,
            resolved_environment=target_environment,
            title=title,
            skill_selections=selections,
            continuation=continuation,
            parent_fork=SessionForkRef(
                source_session_id=source.session_id,
                source_continuation_digest=source.continuation.object_digest,
                source_agent_digest=source.agent_snapshot.logical_digest,
                source_environment_digest=source.environment_snapshot.logical_digest,
            ),
        )

    async def get(self, session_id: str) -> LocalSession:
        return await self._repository.get(session_id)

    async def list(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[SessionSummary, ...]:
        return await self._repository.list(include_archived=include_archived, limit=limit)

    async def update(self, session_id: str, *, update: SessionUpdate) -> LocalSession:
        return await self._repository.update(session_id, update)

    async def delete(self, session_id: str) -> None:
        await self._repository.hard_delete(session_id)

    async def selected_state(self, session_id: str) -> HarnessState:
        session = await self.get(session_id)
        state, _deferred = await self.load_continuation(session.continuation)
        return state

    async def selected_deferred_requests(self, session_id: str) -> DeferredToolRequests:
        session = await self.get(session_id)
        _state, deferred = await self.load_continuation(session.continuation)
        if deferred is None:
            raise SessionError(
                "The selected continuation has no deferred requests.",
                code="deferred_request_missing",
            )
        return deferred

    async def publish_continuation(
        self,
        state: HarnessState,
        deferred_requests: DeferredToolRequests | None = None,
    ) -> ContinuationRef:
        deferred_adapter = TypeAdapter(DeferredToolRequests)
        stored = StoredSessionContinuation(
            harness_release=_package_version("a13n-harness"),
            harness_state=state.model_dump(mode="json"),
            deferred_requests=(
                deferred_adapter.dump_python(deferred_requests, mode="json", by_alias=True)
                if deferred_requests is not None
                else None
            ),
            created_at=datetime.now(UTC),
        )
        reference = await self._store.publish_object(
            object_kind=ObjectKind.session_continuation,
            object_schema_version="1",
            payload=stored.model_dump(mode="json"),
        )
        return ContinuationRef(object_digest=reference.logical_digest)

    async def select_continuation(self, session_id: str, continuation: ContinuationRef) -> LocalSession:
        return await self._repository.select_continuation(session_id, continuation)

    async def load_continuation(
        self,
        continuation: ContinuationRef,
    ) -> tuple[HarnessState, DeferredToolRequests | None]:
        envelope = await self._store.read_object(
            ObjectRef(
                object_kind=ObjectKind.session_continuation,
                object_schema_version="1",
                logical_digest=continuation.object_digest,
            )
        )
        deferred_adapter = TypeAdapter(DeferredToolRequests)
        try:
            stored = StoredSessionContinuation.model_validate_json(json.dumps(envelope.payload))
            state = HarnessState.model_validate(stored.harness_state)
            deferred = (
                deferred_adapter.validate_python(stored.deferred_requests)
                if stored.deferred_requests is not None
                else None
            )
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected Session continuation is invalid.",
                code="continuation_invalid",
                details={"validation_error_count": exc.error_count()},
            ) from exc
        if stored.harness_release != _package_version("a13n-harness"):
            raise StoreIntegrityError(
                "A selected Session continuation requires another Harness release.",
                code="continuation_harness_mismatch",
            )
        return state, deferred

    def _validate_skill_selections(
        self,
        agent: ResolvedAgentSnapshot,
        selections: Sequence[SessionAgentSkillSelection],
    ) -> tuple[SessionAgentSkillSelection, ...]:
        selected = tuple(selections)
        by_node = {item.agent_node_id: item for item in selected}
        if len(by_node) != len(selected):
            raise SessionError(
                "Session Skill selections must name each Agent node at most once.",
                code="skill_selection_invalid",
            )
        nodes = {node.agent_id: node for node in agent.resolved_agents}
        if not set(by_node).issubset(nodes):
            raise SessionError(
                "A Session Skill selection names an Agent outside the pinned graph.",
                code="skill_selection_invalid",
            )
        for node_id, selection in by_node.items():
            available = {skill.definition.skill_name for skill in nodes[node_id].skills}
            if selection.mode == "exact" and not set(selection.names).issubset(available):
                raise SessionError(
                    "A Session Skill selection contains an unavailable name.",
                    code="skill_selection_invalid",
                    details={"agent_node_id": node_id},
                )
        return tuple(sorted(selected, key=lambda item: item.agent_node_id))


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError as exc:
        raise StoreIntegrityError(
            "The selected Harness distribution is unavailable.",
            code="harness_release_missing",
        ) from exc


__all__ = ["SessionService"]
