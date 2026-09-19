"""Accept new Agent selections on root, continued, or forked Threads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import partial

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.invocation_resolution import (
    AgentInvocationResolver,
    AgentSelectorKind,
    FrozenAgentInvocation,
)
from a13n_service.application_errors import ErrorCategory
from a13n_service.assets import Asset
from a13n_service.connectivity.selection_domain import ConnectionRunSelection
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
)
from a13n_service.interactions.acceptance import RunAcceptanceError, RunAcceptanceReceipt, RunAcceptanceService
from a13n_service.interactions.domain import (
    Run,
    RunLineageKind,
    RunStatus,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
    new_run_id,
    new_session_id,
    new_thread_id,
)
from a13n_service.interactions.environment_selection import (
    EnvironmentDefault,
    RetainedRunEnvironment,
    requested_environment,
)
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_empty_thread_state,
    initialize_fork_state,
    initialize_start_state,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .command_evidence import (
    RunCommandEvidence,
    fingerprint_request,
    require_idempotency_key,
)
from .command_preparation import CommandInput, PreparedCommandInput, validate_invocation
from .command_values import (
    ContinueRunCommand,
    ForkRunCommand,
    StartRunCommand,
)
from .errors import InteractionCommandError, command_not_found
from .initialization import NewRunPolicy

_USER_INPUT_ORIGIN = SubmissionOrigin()


class RunCommands:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        invocations: AgentInvocationResolver,
        acceptance: RunAcceptanceService,
        states: RunStateStore,
        inputs: CommandInput,
        policy: NewRunPolicy,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._invocations = invocations
        self._acceptance = acceptance
        self._states = states
        self._inputs = inputs
        self._policy = policy
        self._clock = clock

    async def start(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: StartRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        environment = request.environment
        if request.session_id is not None and "session_purpose" in request.model_fields_set:
            raise InteractionCommandError(
                "session_purpose_immutable",
                "Session purpose can only be supplied when creating a Session.",
                category=ErrorCategory.invalid_request,
            )
        if workspace_id != actor.workspace_id:
            raise command_not_found()
        require_idempotency_key(idempotency_key)
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.start",
            scope_id=workspace_id,
            supplied_key=idempotency_key,
            fingerprint=fingerprint_request(request),
            binding=transaction_hook,
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            await self._acceptance.validate_retained(replay.run_id)
            return replay

        prepared_input = await self._inputs.prepare(
            self._invocations,
            actor=actor,
            agent_id=request.agent_id,
            agent_revision_id=request.agent_revision_id,
            expected_default_revision_id=request.expected_default_revision_id,
            config_override=request.config_override,
            submitted=request.input,
            environment=environment,
            prepared_assets=prepared_assets,
        )

        async def accept(prepared_input: PreparedCommandInput) -> RunAcceptanceReceipt:
            run_id = new_run_id()
            session_id = request.session_id or new_session_id()
            session = None
            if request.session_id is None:
                now = self._clock()
                session = Session(
                    purpose=request.session_purpose,
                    id=session_id,
                    organization_id=prepared_input.invocation.organization_id,
                    workspace_id=workspace_id,
                    labels=request.session_labels,
                    created_at=now,
                    updated_at=now,
                )
            else:
                await self._require_session(
                    organization_id=prepared_input.invocation.organization_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                )
                if "session_labels" in request.model_fields_set:
                    raise InteractionCommandError(
                        "creation_labels_not_allowed",
                        "Session labels can only be supplied when creating a Session.",
                        category=ErrorCategory.invalid_request,
                    )

            thread_id = new_thread_id()
            now = self._clock()
            state = initialize_start_state(
                RunStateSeed.from_invocation(
                    run_id=run_id,
                    invocation=prepared_input.frozen,
                    input=prepared_input.input,
                    protocol_context=request.protocol_context,
                ),
                thread_id=thread_id,
            )
            run = self._policy.create(
                now=now,
                id=run_id,
                organization_id=prepared_input.invocation.organization_id,
                authority_principal=actor.principal,
                session_id=session_id,
                thread_id=thread_id,
                parent_run_id=None,
                lineage_kind=RunLineageKind.root,
                request_fingerprint=evidence.fingerprint,
                invocation=prepared_input.frozen,
                input=prepared_input.input,
                origin=origin,
            )
            thread = Thread(
                id=thread_id,
                version=1,
                queue_version=0,
                organization_id=prepared_input.invocation.organization_id,
                session_id=session_id,
                role=ThreadRole.root,
                origin_kind=ThreadOriginKind.new,
                origin_thread_id=None,
                origin_run_id=None,
                head_run_id=None,
                current_run_id=run_id,
                created_at=now,
                updated_at=now,
            )

            async def validate_final(database: AsyncSession) -> None:
                await validate_invocation(
                    database, self._invocations, prepared=prepared_input.invocation, frozen=prepared_input.frozen
                )

            try:
                return await self._acceptance.accept_new_thread(
                    session=session,
                    thread=thread,
                    run=run,
                    state=state,
                    hook_subscription=request.hook_subscription,
                    environment=requested_environment(environment, default=EnvironmentDefault.agent),
                    final_validator=validate_final,
                    transaction_hook=partial(evidence.commit, now=self._clock()),
                    thread_label_overrides=request.thread_labels,
                    run_label_overrides=request.labels,
                )
            except RunAcceptanceError as error:
                return await evidence.reconcile(error)

        return await self._inputs.accept_with_skill_refresh(self._invocations, prepared_input, accept)

    async def continue_from(
        self,
        *,
        actor: AuthenticatedActor,
        source_run_id: str,
        idempotency_key: str,
        request: ContinueRunCommand,
        inherit_parent_environment: bool = True,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        require_idempotency_key(idempotency_key)
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.continue_from",
            scope_id=source_run_id,
            supplied_key=idempotency_key,
            fingerprint=fingerprint_request(request),
            binding=transaction_hook,
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            await self._acceptance.validate_retained(replay.run_id)
            return replay

        try:
            return await self.accept_continuation(
                actor=actor,
                source_run_id=source_run_id,
                request=request,
                request_fingerprint=evidence.fingerprint,
                inherit_parent_environment=inherit_parent_environment,
                transaction_hook=partial(evidence.commit, now=self._clock()),
                prepared_assets=prepared_assets,
                origin=origin,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def accept_continuation(
        self,
        *,
        actor: AuthenticatedActor,
        source_run_id: str,
        request_fingerprint: str,
        request: ContinueRunCommand,
        inherit_parent_environment: bool = True,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        """Accept prepared command intent; the calling entry point owns replay evidence."""
        environment = request.environment
        source, thread = await self._load_continue_source(actor=actor, source_run_id=source_run_id)
        source_state = await self._states.read_run(source)
        prepared_input = await self._inputs.prepare(
            self._invocations,
            actor=actor,
            agent_id=request.agent_id or source.agent_id,
            agent_revision_id=request.agent_revision_id,
            expected_default_revision_id=request.expected_default_revision_id,
            config_override=request.config_override,
            submitted=request.input,
            environment=environment,
            inherited_environment_id=source.environment_id
            if inherit_parent_environment
            else thread.default_environment_id,
            inherited_environment_working_directory=source.environment_working_directory
            if inherit_parent_environment
            else thread.default_environment_working_directory,
            prepared_assets=prepared_assets,
        )

        async def accept(prepared_input: PreparedCommandInput) -> RunAcceptanceReceipt:
            run_id = new_run_id()
            now = self._clock()
            state = initialize_completed_continuation_state(
                RunStateSeed.from_invocation(
                    run_id=run_id,
                    invocation=prepared_input.frozen,
                    input=prepared_input.input,
                    protocol_context=request.protocol_context,
                ),
                source_state.envelope,
            )
            run = self._policy.create(
                now=now,
                id=run_id,
                organization_id=source.organization_id,
                authority_principal=actor.principal,
                session_id=source.session_id,
                thread_id=source.thread_id,
                parent_run_id=source.id,
                lineage_kind=RunLineageKind.continue_,
                request_fingerprint=request_fingerprint,
                invocation=prepared_input.frozen,
                input=prepared_input.input,
                origin=origin,
            )

            async def validate_final(database: AsyncSession) -> None:
                try:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        agent_id=source.agent_id,
                        action=WorkspaceAction.run_continue,
                    )
                except AuthorizationError as error:
                    raise command_not_found() from error
                await validate_invocation(
                    database, self._invocations, prepared=prepared_input.invocation, frozen=prepared_input.frozen
                )

            return await self._acceptance.advance_thread(
                label_overrides=request.labels,
                run=run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=thread.current_run_id,
                expected_head_run_id=thread.head_run_id,
                next_head_run_id=source.id,
                hook_subscription=request.hook_subscription,
                environment=requested_environment(
                    environment,
                    default=RetainedRunEnvironment(source.id, source.thread_id)
                    if inherit_parent_environment
                    else EnvironmentDefault.thread,
                ),
                final_validator=validate_final,
                transaction_hook=transaction_hook,
            )

        return await self._inputs.accept_with_skill_refresh(self._invocations, prepared_input, accept)

    async def continue_empty_thread(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        idempotency_key: str,
        request: ContinueRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        require_idempotency_key(idempotency_key)
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.continue_empty",
            scope_id=thread_id,
            supplied_key=idempotency_key,
            fingerprint=fingerprint_request(request),
            binding=transaction_hook,
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            await self._acceptance.validate_retained(replay.run_id)
            return replay

        try:
            return await self.accept_empty_thread(
                actor=actor,
                thread_id=thread_id,
                request=request,
                request_fingerprint=evidence.fingerprint,
                transaction_hook=partial(evidence.commit, now=self._clock()),
                prepared_assets=prepared_assets,
                origin=origin,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def accept_empty_thread(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        request_fingerprint: str,
        request: ContinueRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        """Accept prepared command intent; the calling entry point owns replay evidence."""
        environment = request.environment
        source, thread = await self._load_empty_thread_source(
            actor=actor, thread_id=thread_id, agent_id=request.agent_id
        )
        target_agent_id = request.agent_id or (source.agent_id if source else None)
        assert target_agent_id is not None
        prepared_input = await self._inputs.prepare(
            self._invocations,
            actor=actor,
            agent_id=target_agent_id,
            agent_revision_id=request.agent_revision_id,
            expected_default_revision_id=request.expected_default_revision_id,
            config_override=request.config_override,
            submitted=request.input,
            environment=environment,
            inherited_environment_id=thread.default_environment_id,
            inherited_environment_working_directory=thread.default_environment_working_directory,
            prepared_assets=prepared_assets,
        )

        async def accept(prepared_input: PreparedCommandInput) -> RunAcceptanceReceipt:
            run_id = new_run_id()
            state = initialize_empty_thread_state(
                RunStateSeed.from_invocation(
                    run_id=run_id,
                    invocation=prepared_input.frozen,
                    input=prepared_input.input,
                    protocol_context=request.protocol_context,
                ),
                thread_id=thread.id,
            )
            now = self._clock()
            run = self._policy.create(
                now=now,
                id=run_id,
                organization_id=thread.organization_id,
                authority_principal=actor.principal,
                session_id=thread.session_id,
                thread_id=thread.id,
                parent_run_id=None,
                lineage_kind=RunLineageKind.root,
                request_fingerprint=request_fingerprint,
                invocation=prepared_input.frozen,
                input=prepared_input.input,
                origin=origin,
            )

            async def validate_final(database: AsyncSession) -> None:
                try:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        agent_id=source.agent_id if source else target_agent_id,
                        action=WorkspaceAction.run_continue,
                    )
                except AuthorizationError as error:
                    raise command_not_found() from error
                await validate_invocation(
                    database, self._invocations, prepared=prepared_input.invocation, frozen=prepared_input.frozen
                )

            return await self._acceptance.advance_thread(
                label_overrides=request.labels,
                run=run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=thread.current_run_id,
                expected_head_run_id=None,
                next_head_run_id=None,
                hook_subscription=request.hook_subscription,
                environment=requested_environment(environment, default=EnvironmentDefault.thread),
                final_validator=validate_final,
                transaction_hook=transaction_hook,
            )

        return await self._inputs.accept_with_skill_refresh(self._invocations, prepared_input, accept)

    async def fork(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: ForkRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
    ) -> RunAcceptanceReceipt:
        environment = request.environment
        require_idempotency_key(idempotency_key)
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.fork",
            scope_id=run_id,
            supplied_key=idempotency_key,
            fingerprint=fingerprint_request(request),
            binding=transaction_hook,
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            await self._acceptance.validate_retained(replay.run_id)
            return replay

        source = await self._load_fork_source(actor=actor, run_id=run_id)
        source_state = await self._states.read_run(source)
        reuse_exact_source = (
            request.agent_id is None
            and request.agent_revision_id is None
            and request.expected_default_revision_id is None
            and request.config_override is None
        )
        frozen = None
        accepted_input = None
        if reuse_exact_source:
            frozen = FrozenAgentInvocation(
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                selector_kind=AgentSelectorKind.exact,
                effective_config=source_state.envelope.effective_agent_config,
                connection_selections=tuple(
                    ConnectionRunSelection.model_validate(item) for item in source.connection_selections
                ),
            )
            accepted_input = await self._inputs.accept(
                actor=actor,
                workspace_id=actor.workspace_id,
                submitted=request.input,
                frozen=frozen,
                environment=environment,
                inherited_environment_id=source.environment_id,
                inherited_environment_working_directory=source.environment_working_directory,
            )
            prepared_input = None
        else:
            prepared_input = await self._inputs.prepare(
                self._invocations,
                actor=actor,
                agent_id=request.agent_id or source.agent_id,
                agent_revision_id=source.agent_revision_id
                if request.agent_id is None and request.agent_revision_id is None
                else request.agent_revision_id,
                expected_default_revision_id=request.expected_default_revision_id,
                config_override=request.config_override,
                submitted=request.input,
                environment=environment,
                inherited_environment_id=source.environment_id,
                inherited_environment_working_directory=source.environment_working_directory,
            )

        async def accept(selected: PreparedCommandInput | None) -> RunAcceptanceReceipt:
            new_run_id_value = new_run_id()
            new_thread_id_value = new_thread_id()
            if selected is None:
                assert frozen is not None and accepted_input is not None
                invocation, run_input = frozen, accepted_input
            else:
                invocation, run_input = selected.frozen, selected.input
            state = initialize_fork_state(
                RunStateSeed.from_invocation(
                    run_id=new_run_id_value,
                    invocation=invocation,
                    input=run_input,
                    protocol_context=request.protocol_context,
                ),
                source_state.envelope,
                thread_id=new_thread_id_value,
            )
            now = self._clock()
            forked_run = self._policy.create(
                now=now,
                id=new_run_id_value,
                organization_id=source.organization_id,
                authority_principal=actor.principal,
                session_id=source.session_id,
                thread_id=new_thread_id_value,
                parent_run_id=source.id,
                lineage_kind=RunLineageKind.fork,
                request_fingerprint=evidence.fingerprint,
                invocation=invocation,
                input=run_input,
                origin=_USER_INPUT_ORIGIN,
            )
            thread = Thread(
                id=new_thread_id_value,
                version=1,
                queue_version=0,
                organization_id=source.organization_id,
                session_id=source.session_id,
                role=ThreadRole.child,
                origin_kind=ThreadOriginKind.fork,
                origin_thread_id=source.thread_id,
                origin_run_id=source.id,
                head_run_id=None,
                current_run_id=new_run_id_value,
                created_at=now,
                updated_at=now,
            )

            async def validate_final(database: AsyncSession) -> None:
                try:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        agent_id=source.agent_id,
                        action=WorkspaceAction.run_fork,
                    )
                    if selected is None:
                        await authorize_agent(
                            database,
                            actor=actor,
                            workspace_id=actor.workspace_id,
                            agent_id=source.agent_id,
                            action=WorkspaceAction.agent_invoke,
                        )
                        return
                except AuthorizationError as error:
                    raise command_not_found() from error
                await validate_invocation(
                    database, self._invocations, prepared=selected.invocation, frozen=selected.frozen
                )

            try:
                return await self._acceptance.accept_new_thread(
                    session=None,
                    thread=thread,
                    run=forked_run,
                    state=state,
                    hook_subscription=request.hook_subscription,
                    environment=requested_environment(
                        environment, default=RetainedRunEnvironment(source.id, source.thread_id)
                    ),
                    final_validator=validate_final,
                    transaction_hook=partial(evidence.commit, now=self._clock()),
                    thread_label_overrides=request.thread_labels,
                    run_label_overrides=request.labels,
                )
            except RunAcceptanceError as error:
                return await evidence.reconcile(error)

        if prepared_input is None:
            return await accept(None)
        return await self._inputs.accept_with_skill_refresh(self._invocations, prepared_input, accept)

    async def _load_continue_source(self, *, actor: AuthenticatedActor, source_run_id: str):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == source_run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            source_record, thread_record = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=source_record.agent_id,
                    action=WorkspaceAction.run_continue,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            source = source_record.to_resource()
            if source.status is not RunStatus.completed:
                raise InteractionCommandError(
                    "run_not_continuable",
                    "The selected Run is not a completed continuation source.",
                    category=ErrorCategory.conflict,
                )
            return source, thread_record.to_resource()

    async def _load_empty_thread_source(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        agent_id: str | None,
    ) -> tuple[Run | None, Thread]:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .select_from(ThreadRecord)
                    .outerjoin(
                        RunRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.current_run_id == RunRecord.id,
                        ),
                    )
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == ThreadRecord.organization_id,
                            SessionRecord.id == ThreadRecord.session_id,
                        ),
                    )
                    .where(
                        ThreadRecord.id == thread_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            source_record, thread_record = row
            target_agent_id = agent_id or (source_record.agent_id if source_record else None)
            if target_agent_id is None:
                raise InteractionCommandError(
                    "agent_required", "First input requires an Agent selection.", category=ErrorCategory.invalid_request
                )
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=source_record.agent_id if source_record else target_agent_id,
                    action=WorkspaceAction.run_continue,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            if thread_record.head_run_id is not None or (
                source_record is not None
                and source_record.status
                not in {
                    RunStatus.failed.value,
                    RunStatus.cancelled.value,
                }
            ):
                raise InteractionCommandError(
                    "thread_not_root_continuable",
                    "The Thread does not have an empty continuation head.",
                    category=ErrorCategory.conflict,
                )
            return source_record.to_resource() if source_record else None, thread_record.to_resource()

    async def _load_fork_source(self, *, actor: AuthenticatedActor, run_id: str) -> Run:
        async with short_session(self._sessions) as database:
            source_record = (
                await database.execute(
                    select(RunRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if source_record is None:
                raise command_not_found()
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=source_record.agent_id,
                    action=WorkspaceAction.run_fork,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            source = source_record.to_resource()
            if source.status is not RunStatus.completed:
                raise InteractionCommandError(
                    "run_not_forkable",
                    "The selected Run is not a completed fork source.",
                    category=ErrorCategory.conflict,
                )
            return source

    async def _require_session(self, *, organization_id: str, workspace_id: str, session_id: str) -> None:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(SessionRecord, ThreadRecord.id)
                    .outerjoin(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == SessionRecord.organization_id,
                            ThreadRecord.session_id == SessionRecord.id,
                            ThreadRecord.role == ThreadRole.root.value,
                        ),
                    )
                    .where(
                        SessionRecord.id == session_id,
                        SessionRecord.organization_id == organization_id,
                        SessionRecord.workspace_id == workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise command_not_found()
        if row[1] is not None:
            raise InteractionCommandError(
                "session_root_exists",
                "The selected Session already has its root Thread.",
                category=ErrorCategory.conflict,
            )
