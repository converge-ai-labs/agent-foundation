"""Accept new Agent selections on root, continued, or forked Threads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

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
from a13n_service.iam.operation import authorization_operation
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
    initialize_fork_state,
    initialize_start_state,
)
from a13n_service.interactions.models import SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .command_evidence import (
    RunRequest,
    require_idempotency_key,
)
from .command_preparation import CommandInput
from .command_values import (
    ContinueRunCommand,
    ForkRunCommand,
    StartRunCommand,
)
from .errors import InteractionCommandError, command_not_found
from .initialization import NewRunPolicy
from .session_scope import SessionScope
from .sources import RunSource, ThreadSource, load_run_source, load_thread_source

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

    @authorization_operation
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
        evidence = RunRequest(
            self._sessions,
            actor=actor,
            operation="run.start",
            scope_id=workspace_id,
            supplied_key=idempotency_key,
            binding=transaction_hook,
        )
        replay = await evidence.replay()
        if replay is not None:
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

        run_id = new_run_id()
        session_id = request.session_id or new_session_id()
        session = None
        session_scope = None
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
            session_scope = await self._require_session(
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
            request_key=evidence.key,
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

        try:
            return await self._acceptance.accept_new_thread(
                session=session,
                session_scope=session_scope,
                thread=thread,
                run=run,
                state=state,
                hook_subscription=request.hook_subscription,
                environment=requested_environment(environment, default=EnvironmentDefault.agent),
                transaction_hook=evidence.commit,
                thread_label_overrides=request.thread_labels,
                run_label_overrides=request.labels,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    @authorization_operation
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
        evidence = RunRequest(
            self._sessions,
            actor=actor,
            operation="run.continue_from",
            scope_id=source_run_id,
            supplied_key=idempotency_key,
            binding=transaction_hook,
        )
        replay = await evidence.replay()
        if replay is not None:
            return replay

        try:
            return await self.accept_continuation(
                actor=actor,
                source_run_id=source_run_id,
                request=request,
                request_key=evidence.key,
                inherit_parent_environment=inherit_parent_environment,
                transaction_hook=evidence.commit,
                prepared_assets=prepared_assets,
                origin=origin,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    @authorization_operation
    async def accept_continuation(
        self,
        *,
        actor: AuthenticatedActor,
        observed_source: RunSource | None = None,
        source_run_id: str,
        request_key: str,
        request: ContinueRunCommand,
        inherit_parent_environment: bool = True,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        """Accept prepared command intent; the calling entry point owns replay evidence."""
        environment = request.environment
        source, thread, session_scope = await self._load_continue_source(
            actor=actor, source_run_id=source_run_id, observed_source=observed_source
        )
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
            request_key=request_key,
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

        return await self._acceptance.advance_thread(
            label_overrides=request.labels,
            session_scope=session_scope,
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

    @authorization_operation
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
        evidence = RunRequest(
            self._sessions,
            actor=actor,
            operation="run.continue_empty",
            scope_id=thread_id,
            supplied_key=idempotency_key,
            binding=transaction_hook,
        )
        replay = await evidence.replay()
        if replay is not None:
            return replay

        try:
            return await self.accept_empty_thread(
                actor=actor,
                thread_id=thread_id,
                request=request,
                request_key=evidence.key,
                transaction_hook=evidence.commit,
                prepared_assets=prepared_assets,
                origin=origin,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    @authorization_operation
    async def accept_empty_thread(
        self,
        *,
        actor: AuthenticatedActor,
        observed_source: ThreadSource | None = None,
        thread_id: str,
        request_key: str,
        request: ContinueRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        origin: SubmissionOrigin = _USER_INPUT_ORIGIN,
    ) -> RunAcceptanceReceipt:
        """Accept prepared command intent; the calling entry point owns replay evidence."""
        environment = request.environment
        source, thread, session_scope = await self._load_empty_thread_source(
            actor=actor, thread_id=thread_id, agent_id=request.agent_id, observed_source=observed_source
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

        run_id = new_run_id()
        state = initialize_start_state(
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
            request_key=request_key,
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

        return await self._acceptance.advance_thread(
            label_overrides=request.labels,
            session_scope=session_scope,
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

    @authorization_operation
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
        evidence = RunRequest(
            self._sessions,
            actor=actor,
            operation="run.fork",
            scope_id=run_id,
            supplied_key=idempotency_key,
            binding=transaction_hook,
        )
        replay = await evidence.replay()
        if replay is not None:
            return replay

        source, session_scope = await self._load_fork_source(actor=actor, run_id=run_id)
        source_state = await self._states.read_run(source)
        reuse_exact_source = (
            request.agent_id is None
            and request.agent_revision_id is None
            and request.expected_default_revision_id is None
            and request.config_override is None
        )
        if reuse_exact_source:
            invocation = FrozenAgentInvocation(
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                selector_kind=AgentSelectorKind.exact,
                effective_config=source_state.envelope.effective_agent_config,
                connection_selections=tuple(
                    ConnectionRunSelection.model_validate(item) for item in source.connection_selections
                ),
            )
            run_input = await self._inputs.accept(
                actor=actor,
                workspace_id=actor.workspace_id,
                submitted=request.input,
                frozen=invocation,
                environment=environment,
                inherited_environment_id=source.environment_id,
                inherited_environment_working_directory=source.environment_working_directory,
            )
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

            invocation, run_input = prepared_input.frozen, prepared_input.input

        new_run_id_value = new_run_id()
        new_thread_id_value = new_thread_id()
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
            request_key=evidence.key,
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
                if reuse_exact_source:
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

        try:
            return await self._acceptance.accept_new_thread(
                session=None,
                session_scope=session_scope,
                thread=thread,
                run=forked_run,
                state=state,
                hook_subscription=request.hook_subscription,
                environment=requested_environment(
                    environment, default=RetainedRunEnvironment(source.id, source.thread_id)
                ),
                final_validator=validate_final,
                transaction_hook=evidence.commit,
                thread_label_overrides=request.thread_labels,
                run_label_overrides=request.labels,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def _load_continue_source(
        self, *, actor: AuthenticatedActor, source_run_id: str, observed_source: RunSource | None = None
    ) -> tuple[Run, Thread, SessionScope]:
        async with short_session(self._sessions) as database:
            observed = observed_source or await load_run_source(
                database, workspace_id=actor.workspace_id, run_id=source_run_id
            )
            observed.require_scope(workspace_id=actor.workspace_id, run_id=source_run_id)
            source = observed.run
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
            if source.status is not RunStatus.completed:
                raise InteractionCommandError(
                    "run_not_continuable",
                    "The selected Run is not a completed continuation source.",
                    category=ErrorCategory.conflict,
                )
            return source, observed.thread, observed.session_scope

    async def _load_empty_thread_source(
        self,
        *,
        actor: AuthenticatedActor,
        thread_id: str,
        agent_id: str | None,
        observed_source: ThreadSource | None = None,
    ) -> tuple[Run | None, Thread, SessionScope]:
        async with short_session(self._sessions) as database:
            observed = observed_source or await load_thread_source(
                database, workspace_id=actor.workspace_id, thread_id=thread_id
            )
            observed.require_scope(workspace_id=actor.workspace_id, thread_id=thread_id)
            source, thread = observed.current, observed.thread
            target_agent_id = agent_id or (source.agent_id if source else None)
            if target_agent_id is None:
                raise InteractionCommandError(
                    "agent_required", "First input requires an Agent selection.", category=ErrorCategory.invalid_request
                )
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
            if thread.head_run_id is not None or (
                source is not None and source.status not in {RunStatus.failed, RunStatus.cancelled}
            ):
                raise InteractionCommandError(
                    "thread_not_root_continuable",
                    "The Thread does not have an empty continuation head.",
                    category=ErrorCategory.conflict,
                )
            return source, thread, observed.session_scope

    async def _load_fork_source(self, *, actor: AuthenticatedActor, run_id: str) -> tuple[Run, SessionScope]:
        async with short_session(self._sessions) as database:
            observed = await load_run_source(database, workspace_id=actor.workspace_id, run_id=run_id)
            source = observed.run
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_fork,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            if source.status is not RunStatus.completed:
                raise InteractionCommandError(
                    "run_not_forkable",
                    "The selected Run is not a completed fork source.",
                    category=ErrorCategory.conflict,
                )
            return source, observed.session_scope

    async def _require_session(self, *, organization_id: str, workspace_id: str, session_id: str) -> SessionScope:
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
        return SessionScope.from_record(row[0])
