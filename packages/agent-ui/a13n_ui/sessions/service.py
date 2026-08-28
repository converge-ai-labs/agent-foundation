"""Application service for durable Session composition and Thread lineage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from uuid import uuid4

from a13n_harness import HarnessState
from pydantic import TypeAdapter, ValidationError
from pydantic_ai.tools import DeferredToolRequests

from a13n_ui.composition import CompositionService, ResolvedAgentSnapshot, SnapshotReference
from a13n_ui.errors import AgentUiError, SessionError, StoreIntegrityError
from a13n_ui.storage.objects import ObjectKind, ObjectRef
from a13n_ui.storage.runtime import LocalStore

from .models import (
    CheckpointRef,
    LocalSession,
    PendingDeferredRef,
    SessionAgentSkillSelection,
    SessionForkRef,
    SessionLifecycleState,
    SessionSummary,
    SessionUpdate,
    StoredDeferredRequests,
    StoredHarnessState,
    TurnState,
)
from .repository import SessionRepository


class SessionService:
    """Validate immutable composition and persist exact Session lineages."""

    def __init__(self, store: LocalStore, composition: CompositionService) -> None:
        self._store = store
        self._composition = composition
        self._repository = SessionRepository(store)

    @property
    def repository(self) -> SessionRepository:
        """Return the feature repository to sibling application services."""

        return self._repository

    async def initialize(self) -> int:
        """Interrupt prior work and fail closed invalid selected Session authority."""

        interrupted = await self._repository.interrupt_prior_process_turns()
        for session_id in await self._repository.recovery_session_ids():
            try:
                await self.validate_selected_authority(session_id)
            except AgentUiError as exc:
                await self._store.record_recovery_diagnostic(
                    code="session_authority_invalid",
                    detail=f"{session_id}:{exc.code}",
                )
                await self._repository.fail_closed(
                    session_id,
                    failure={
                        "code": "session_authority_invalid",
                        "authority_error": exc.code,
                    },
                )
        return interrupted

    async def validate_selected_authority(self, session_id: str) -> None:
        """Verify exact checkpoint and waiting-deferred authority for one Session."""

        session = await self._repository.get(session_id)
        checkpoint = session.root.selected_checkpoint
        if checkpoint is None:
            raise StoreIntegrityError(
                "A retained Session has no selected checkpoint.",
                code="checkpoint_missing",
            )
        await self.load_state(session_id, checkpoint)
        for turn in session.root.turns:
            if turn.state is not TurnState.waiting:
                continue
            if turn.selected_checkpoint is None or turn.pending_deferred is None:
                raise StoreIntegrityError(
                    "A waiting Turn has incomplete selected continuation authority.",
                    code="deferred_request_missing",
                )
            await self.load_state(session_id, turn.selected_checkpoint)
            await self.load_deferred_requests(
                session_id=session_id,
                thread_id=turn.thread_id,
                turn_id=turn.turn_id,
                agent_snapshot_digest=session.agent_snapshot.logical_digest,
                reference=turn.pending_deferred,
            )

    async def create(
        self,
        *,
        agent_snapshot: SnapshotReference,
        environment_snapshot: SnapshotReference,
        creation_request_id: str | None = None,
        title: str | None = None,
        skill_selections: Sequence[SessionAgentSkillSelection] = (),
    ) -> LocalSession:
        agent = await self._composition.agent(agent_snapshot)
        environment = await self._composition.environment(environment_snapshot)
        await self._composition.compatibility(agent_snapshot, environment_snapshot)
        selections = self._validate_skill_selections(agent, skill_selections)
        state = HarnessState.new()
        session_id = f"session-{uuid4().hex}"
        checkpoint_id = f"checkpoint-{uuid4().hex}"
        checkpoint = await self._publish_state(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            owner_kind="session_baseline",
            turn_id=None,
            state=state,
        )
        return await self._repository.create(
            session_id=session_id,
            creation_request_id=creation_request_id or f"create-{uuid4().hex}",
            thread_id=state.thread_id,
            agent_snapshot=agent_snapshot,
            environment_snapshot=environment_snapshot,
            resolved_environment=environment,
            title=title,
            skill_selections=selections,
            baseline_checkpoint=checkpoint,
            baseline_owner_kind="session_baseline",
        )

    async def fork(
        self,
        source_session_id: str,
        *,
        expected_source_revision: int,
        agent_snapshot: SnapshotReference | None = None,
        environment_snapshot: SnapshotReference | None = None,
        creation_request_id: str | None = None,
        title: str | None = None,
        skill_selections: Sequence[SessionAgentSkillSelection] | None = None,
    ) -> LocalSession:
        source = await self._repository.get(source_session_id)
        if source.control_revision != expected_source_revision:
            raise SessionError(
                "The source Session control revision is stale.",
                code="session_revision_conflict",
                details={"current_revision": source.control_revision},
            )
        source_checkpoint = source.root.selected_checkpoint
        if source_checkpoint is None:
            raise StoreIntegrityError(
                "The source Session has no complete checkpoint to fork.",
                code="checkpoint_missing",
            )
        source_state = await self.load_state(source.session_id, source_checkpoint)
        target_agent_ref = agent_snapshot or source.agent_snapshot
        target_environment_ref = environment_snapshot or source.environment_snapshot
        target_agent = await self._composition.agent(target_agent_ref)
        target_environment = await self._composition.environment(target_environment_ref)
        await self._composition.compatibility(target_agent_ref, target_environment_ref)
        if target_agent_ref.logical_digest == source.agent_snapshot.logical_digest:
            forked_state = source_state.fork()
        else:
            forked_state = HarnessState.new(message_history=source_state.message_history)
        selections = self._validate_skill_selections(
            target_agent,
            source.skill_selections if skill_selections is None else skill_selections,
        )
        session_id = f"session-{uuid4().hex}"
        checkpoint_id = f"checkpoint-{uuid4().hex}"
        checkpoint = await self._publish_state(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            owner_kind="session_fork",
            turn_id=None,
            state=forked_state,
        )
        parent = SessionForkRef(
            source_session_id=source.session_id,
            source_thread_id=source.root.thread_id,
            source_thread_commit_revision=source.root.commit_revision,
            source_checkpoint_id=source_checkpoint.checkpoint_id,
            source_agent_digest=source.agent_snapshot.logical_digest,
            source_environment_digest=source.environment_snapshot.logical_digest,
        )
        return await self._repository.create(
            session_id=session_id,
            creation_request_id=creation_request_id or f"fork-{uuid4().hex}",
            thread_id=forked_state.thread_id,
            agent_snapshot=target_agent_ref,
            environment_snapshot=target_environment_ref,
            resolved_environment=target_environment,
            title=title,
            skill_selections=selections,
            baseline_checkpoint=checkpoint,
            baseline_owner_kind="session_fork",
            parent_fork=parent,
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

    async def update(
        self,
        session_id: str,
        *,
        expected_revision: int,
        update: SessionUpdate,
    ) -> LocalSession:
        return await self._repository.update(session_id, expected_revision, update)

    async def set_lifecycle(
        self,
        session_id: str,
        *,
        expected_revision: int,
        state: SessionLifecycleState,
        failure: object | None = None,
    ) -> LocalSession:
        from a13n_ui.configuration.models import canonical_json_value

        return await self._repository.set_lifecycle(
            session_id,
            expected_revision=expected_revision,
            state=state,
            failure=canonical_json_value(failure) if failure is not None else None,
        )

    async def delete(self, session_id: str, *, expected_revision: int) -> None:
        """Delete metadata after the Environment service has released assignments."""

        await self._repository.hard_delete(session_id, expected_revision)

    async def load_state(self, session_id: str, checkpoint: CheckpointRef) -> HarnessState:
        envelope = await self._store.read_object(
            ObjectRef(
                object_kind=ObjectKind.harness_state,
                object_schema_version="1",
                logical_digest=checkpoint.state_object_digest,
            )
        )
        try:
            stored = StoredHarnessState.model_validate_json(json.dumps(envelope.payload))
            state = HarnessState.model_validate_json(json.dumps(stored.harness_state))
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected Harness checkpoint is invalid.",
                code="checkpoint_invalid",
                details={"validation_error_count": exc.error_count()},
            ) from exc
        if (
            stored.session_id != session_id
            or stored.thread_id != checkpoint.thread_id
            or stored.checkpoint_id != checkpoint.checkpoint_id
            or stored.harness_release != checkpoint.harness_release
            or state.thread_id != checkpoint.thread_id
            or state.schema_version != stored.harness_state_schema
        ):
            raise StoreIntegrityError(
                "A selected Harness checkpoint does not match its durable index.",
                code="checkpoint_reference_mismatch",
            )
        return state

    async def publish_turn_state(
        self,
        *,
        session_id: str,
        turn_id: str,
        state: HarnessState,
    ) -> CheckpointRef:
        return await self._publish_state(
            session_id=session_id,
            checkpoint_id=f"checkpoint-{uuid4().hex}",
            owner_kind="root_turn",
            turn_id=turn_id,
            state=state,
        )

    async def publish_deferred_requests(
        self,
        *,
        session_id: str,
        thread_id: str,
        turn_id: str,
        source_run_id: str,
        agent_snapshot_digest: str,
        requests: DeferredToolRequests,
    ) -> PendingDeferredRef:
        adapter = TypeAdapter(DeferredToolRequests)
        request_json = adapter.dump_json(requests, by_alias=True)
        request_digest = hashlib.sha256(request_json).hexdigest()
        codec_version = _package_version("pydantic-ai-slim")
        stored = StoredDeferredRequests(
            session_id=session_id,
            thread_id=thread_id,
            turn_id=turn_id,
            source_run_id=source_run_id,
            agent_snapshot_digest=agent_snapshot_digest,
            harness_release=_package_version("a13n-harness"),
            request_codec_version=codec_version,
            request_digest=request_digest,
            tool_surface_digest=agent_snapshot_digest,
            requests=adapter.dump_python(requests, mode="json", by_alias=True),
            exported_at=datetime.now(UTC),
        )
        reference = await self._store.publish_object(
            object_kind=ObjectKind.deferred_requests,
            object_schema_version="1",
            payload=stored.model_dump(mode="json"),
        )
        return PendingDeferredRef(
            object_digest=reference.logical_digest,
            request_digest=request_digest,
            request_codec_version=codec_version,
            source_run_id=source_run_id,
        )

    async def load_deferred_requests(
        self,
        *,
        session_id: str,
        thread_id: str,
        turn_id: str,
        agent_snapshot_digest: str,
        reference: PendingDeferredRef,
    ) -> DeferredToolRequests:
        envelope = await self._store.read_object(
            ObjectRef(
                object_kind=ObjectKind.deferred_requests,
                object_schema_version="1",
                logical_digest=reference.object_digest,
            )
        )
        adapter = TypeAdapter(DeferredToolRequests)
        try:
            stored = StoredDeferredRequests.model_validate_json(json.dumps(envelope.payload))
            requests = adapter.validate_python(stored.requests)
            request_digest = hashlib.sha256(adapter.dump_json(requests, by_alias=True)).hexdigest()
        except ValidationError as exc:
            raise StoreIntegrityError(
                "A selected deferred request object is invalid.",
                code="deferred_request_invalid",
            ) from exc
        if (
            stored.session_id != session_id
            or stored.thread_id != thread_id
            or stored.turn_id != turn_id
            or stored.source_run_id != reference.source_run_id
            or stored.agent_snapshot_digest != agent_snapshot_digest
            or stored.tool_surface_digest != agent_snapshot_digest
            or stored.harness_release != _package_version("a13n-harness")
            or stored.request_codec_version != reference.request_codec_version
            or stored.request_codec_version != _package_version("pydantic-ai-slim")
            or stored.request_digest != reference.request_digest
            or request_digest != reference.request_digest
        ):
            raise StoreIntegrityError(
                "A selected deferred request object does not match its durable index.",
                code="deferred_request_reference_mismatch",
            )
        return requests

    async def _publish_state(
        self,
        *,
        session_id: str,
        checkpoint_id: str,
        owner_kind: str,
        turn_id: str | None,
        state: HarnessState,
    ) -> CheckpointRef:
        harness_release = _package_version("a13n-harness")
        stored = StoredHarnessState(
            session_id=session_id,
            thread_id=state.thread_id,
            owner_kind=owner_kind,  # type: ignore[arg-type]
            turn_id=turn_id,
            checkpoint_id=checkpoint_id,
            harness_release=harness_release,
            harness_state_schema=state.schema_version,
            harness_state=state.model_dump(mode="json"),
            exported_at=datetime.now(UTC),
        )
        reference = await self._store.publish_object(
            object_kind=ObjectKind.harness_state,
            object_schema_version="1",
            payload=stored.model_dump(mode="json"),
        )
        return CheckpointRef(
            checkpoint_id=checkpoint_id,
            thread_id=state.thread_id,
            state_object_digest=reference.logical_digest,
            harness_release=harness_release,
        )

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
