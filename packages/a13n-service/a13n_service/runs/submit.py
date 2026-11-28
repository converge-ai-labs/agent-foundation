"""Idempotent message commands: create a thread, submit to a thread, fork a run.

Each is one short transaction that resolves the request key, appends the entry and tries to accept it from the
source its validation loaded. The unique request index arbitrates concurrent duplicates. The loser rolls back
everything it created, including a tentative thread or session, whether it lost the index or was refused by the state
the winner committed while it waited for a lock, then replays the winner.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from a13n_harness.configuration import RunConfiguration
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.app_provenance import AppProvenance
from a13n_service.execution_policy import matches
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid
from a13n_service.infra.http import IdempotencyKey
from a13n_service.providers.realtime import PROTOCOLS
from a13n_service.resources.agents import stored
from a13n_service.resources.agents.schemas import AgentOverride
from a13n_service.resources.assets.service import require_usable
from a13n_service.resources.connections.service import validate_caller_headers
from a13n_service.resources.memories.schemas import MemoryMounts
from a13n_service.resources.models.service import resolve_model
from a13n_service.runs import checkpoints, definitions
from a13n_service.runs.accept import (
    Source,
    accept,
    primary_source,
    require_message_source,
    require_thread_tool_names,
    run_revision,
    snapshot_options,
)
from a13n_service.runs.attachments import asset_fields, require_readable
from a13n_service.runs.environments.mounts import PRIMARY, mount_environments, shared_mounts, thread_has_primary
from a13n_service.runs.environments.schemas import MountCreate
from a13n_service.runs.inbox import Request, append_message, check_replay, compatible_steer, find_request
from a13n_service.runs.memories.mounts import mount_memories, shared_memory_mounts
from a13n_service.runs.memories.session_memory import configure
from a13n_service.runs.runs import run_view
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    EntryView,
    ExistingRunEnvironment,
    Fork,
    InitialMounts,
    Message,
    NewThread,
    RunOptions,
    Submitted,
    ThreadView,
)
from a13n_service.runs.sessions import find_session, new_session, require_conversation
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.threads import get_run, get_thread, new_thread, refresh_version, require_open
from a13n_service.tenancy.access import lock_organization, principal_for, workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope, execution_authority
from a13n_service.tenancy.tables import WorkspaceRow

# The request as the resolved workspace names it, so a replay matches however the path spelled the workspace.
type Requested = Callable[[WorkspaceScope], Request]
type Create = Callable[[AsyncSession, WorkspaceScope, Request], Awaitable[tuple[ThreadRow, Source]]]


async def validate_message(
    session: AsyncSession,
    runtime: Runtime,
    principal: Principal,
    scope: WorkspaceScope,
    thread: ThreadRow,
    message: Message,
    *,
    authority: ExecutionAuthority,
    initial_environments: InitialMounts = (),
) -> tuple[definitions.Definition, AgentOverride | None, RunOptions]:
    """Malformed or unauthorized input fails before anything is appended, and a pending edit before it applies.
    Returns the selected definition, validated overrides and resolved execution options.

    Each asset must reach the model of the run the message would start on `thread`, with the primary environment
    the thread mounts or acceptance would reserve. Acceptance checks the assets again against the mounts it
    freezes; an acceptance in a later transaction validates the overrides again too, under the authority current
    then.
    """
    options = message.options
    revision_id = message.agent_revision_id
    receiving_options = None
    if thread.agent_snapshot is not None:
        options = await snapshot_options(session, thread, options)
    elif message.delivery == "steer" and thread.current_run_id is not None:
        current = await session.get_one(RunRow, thread.current_run_id)
        accepted = stored.require(RunOptions, current.options, kind="run", id=current.id)
        if options.configuration is not None and options.configuration != (
            accepted.configuration or RunConfiguration()
        ):
            raise conflict("run", current.id, "run_configuration_immutable")
        if not matches(accepted.execution, options.execution):
            raise conflict("run", current.id, "execution_policy_immutable")
        if compatible_steer(message, options, current, accepted):
            revision_id = current.agent_revision_id
            receiving_options = accepted
    if thread.agent_snapshot is not None:
        revision = await definitions.resolve(
            session, thread, message.agent_id, message.agent_revision_id, message.options.overrides, accepting=True
        )
        overrides = None
    else:
        revision, overrides = await run_revision(
            session,
            runtime,
            principal,
            scope,
            message.agent_id,
            revision_id,
            message.options.overrides,
            authority=authority,
        )
    if receiving_options is not None:
        overrides = receiving_options.overrides
    effective = stored.require_effective(revision.config, overrides, revision_id=revision.definition_id)
    selected = await resolve_model(session, principal, scope, effective.model, authority=authority)
    if selected.config.model_api in PROTOCOLS:
        raise invalid("model", "This model requires a realtime conversation")
    if initial_environments:
        # Acceptance can share-lock another existing primary. Lock it together with the initial mounts
        # so another Thread taking exclusive locks in ID order (desktop handback) cannot deadlock.
        selection = options.environment
        primary_id = None
        if isinstance(selection, ExistingRunEnvironment):
            primary_id = selection.environment_id
        elif selection is None and all(mount.name != PRIMARY for mount in initial_environments):
            primary = await primary_source(session, thread, revision.config)
            if isinstance(primary, MountCreate):
                primary_id = primary.environment_id
        await mount_environments(session, thread, initial_environments, principal=principal, run_primary_id=primary_id)
    await require_thread_tool_names(session, thread, revision, overrides)
    await require_usable(session, scope.workspace_id, asset_fields(message.payload))
    primary = (
        options.environment.mode != "none"
        if options.environment is not None
        else await thread_has_primary(session, thread)
        or await primary_source(session, thread, revision.config) is not None
    )
    await require_readable(
        session, principal, scope, revision, overrides, message.payload, authority=authority, primary=primary
    )
    return revision, overrides, options


async def prepare_message(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    thread: ThreadRow,
    message: Message,
    request: Request | None,
    *,
    authority: ExecutionAuthority,
    schedule_occurrence_id: str | None = None,
    initial_environments: InitialMounts = (),
    trigger_app_id: str | None = None,
) -> Source:
    """Validate and append the command's message; the source it returns starts a run if the thread accepts now."""
    await require_message_source(session, thread, message.agent_id)
    revision, overrides, options = await validate_message(
        session,
        runtime,
        actor,
        scope,
        thread,
        message,
        authority=authority,
        initial_environments=initial_environments,
    )
    entry = await append_message(
        session,
        thread,
        message,
        principal_id=actor.id,
        authority=authority,
        request=request,
        control=runtime.settings.control,
        schedule_occurrence_id=schedule_occurrence_id,
        app_id=trigger_app_id,
    )
    runtime.mailbox.notify(session, entry)
    return Source.message(entry, "input", actor, revision, overrides, options=options)


async def receipt(session: AsyncSession, thread: ThreadRow, entry: InboxEntryRow) -> Submitted:
    """The entry's current disposition and the run that owns it, if any."""
    await refresh_version(session, thread)
    run = await session.get(RunRow, entry.assigned_run_id) if entry.assigned_run_id is not None else None
    return Submitted(
        thread=ThreadView.model_validate(thread),
        entry=stored.read(EntryView, entry, kind="inbox_entry", id=entry.id),
        run=await run_view(session, run) if run is not None else None,
    )


async def _replay(
    session: AsyncSession,
    entry: InboxEntryRow,
    request: Request,
    *,
    trigger_app_id: str | None = None,
    provenance: AppProvenance | None = None,
    reuse_session: bool = False,
) -> Submitted:
    if entry.app_id != trigger_app_id:
        raise conflict("request", request.key, "idempotency_key_reused")
    check_replay(entry, request)
    thread = await session.get(ThreadRow, entry.thread_id)
    assert thread is not None
    if provenance is not None:
        matches = (
            provenance.matches_owner(thread)
            if reuse_session
            else (thread.app_id, thread.app_context) == (provenance.app_id, provenance.app_context)
        )
        if not matches:
            raise conflict("request", request.key, "idempotency_key_reused")
    return await receipt(session, thread, entry)


async def _submit(
    runtime: Runtime, actor: Principal, workspace_id: str, requested: Requested, create: Create
) -> tuple[Submitted, bool]:
    """Returns the receipt and whether this call created it (201) rather than replayed it (200)."""
    try:
        async with transaction(runtime.storage) as session:
            scope, request, found = await _lookup(session, actor, workspace_id, requested)
            if found is not None:
                return await _replay(session, found, request), False
            thread, source = await create(session, scope, request)
            await accept(session, runtime, thread, explicit=source)
            assert source.entry is not None
            return await receipt(session, thread, source.entry), True
    except (IntegrityError, ServiceError):
        # A concurrent request with the same key may have committed first. This one then lost the unique request
        # index, or waited for a lock the winner held and was refused by the state the winner left, such as a full
        # inbox. Either way the key is now known, and a known key replays.
        async with transaction(runtime.storage) as session:
            _, request, found = await _lookup(session, actor, workspace_id, requested)
            if found is not None:
                return await _replay(session, found, request), False
        raise


async def _lookup(
    session: AsyncSession, actor: Principal, workspace_id: str, requested: Requested
) -> tuple[WorkspaceScope, Request, InboxEntryRow | None]:
    """Authentication and current permission precede replay lookup; lookup precedes state validation."""
    scope = await workspace_scope(session, actor, workspace_id, "run")
    request = requested(scope)
    return scope, request, await find_request(session, scope.workspace_id, actor.id, request.key)


_MESSAGE_KEY = TypeAdapter(IdempotencyKey)


def _message_key(request_key: str) -> None:
    try:
        _MESSAGE_KEY.validate_python(request_key)
    except ValidationError as error:
        raise invalid("request_key", "must be 1-512 printable ASCII characters without spaces") from error


async def _create_message(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    thread_id: str,
    message: Message,
    request: Request,
    *,
    trigger_app_id: str | None = None,
) -> tuple[ThreadRow, Source]:
    thread = await get_thread(session, scope.workspace_id, thread_id, lock="acceptance")
    require_open(thread)
    if trigger_app_id is not None and thread.app_id != trigger_app_id:
        raise conflict("thread", thread.id, "app_owner_mismatch")
    source = await prepare_message(
        session,
        runtime,
        actor,
        scope,
        thread,
        message,
        request,
        authority=execution_authority(actor, scope),
        trigger_app_id=trigger_app_id,
    )
    return thread, source


async def submit_message(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    message: Message,
    *,
    request_key: str,
) -> tuple[Submitted, bool]:
    """Submit using the authenticated request principal; receipt is not execution completion."""
    _message_key(request_key)

    async def create(session: AsyncSession, scope: WorkspaceScope, request: Request) -> tuple[ThreadRow, Source]:
        return await _create_message(session, runtime, actor, scope, thread_id, message, request)

    return await _submit(
        runtime, actor, workspace_id, lambda _: Request.of(request_key, "message", thread_id, message), create
    )


@dataclass(frozen=True, slots=True)
class Submission:
    """Admission bound to one Service transaction and workspace."""

    _session: AsyncSession
    _runtime: Runtime
    _workspace_id: str
    _actor: Principal
    _provenance: AppProvenance | None = None


async def prepare_submission(
    session: AsyncSession, runtime: Runtime, workspace_id: str, principal_id: str
) -> Submission:
    """Lock current actor authority before taking caller rows or the Thread acceptance lock.

    This takes the organization FOR NO KEY UPDATE lock. Keep the enclosing transaction short.
    """
    if not session.info.get("a13n.transaction") or not session.in_transaction() or session.in_nested_transaction():
        raise RuntimeError("Submission requires the outer Service transaction")
    workspace = await session.get(WorkspaceRow, workspace_id)
    if workspace is None:
        from a13n_service.infra.errors import not_found

        raise not_found("workspace", workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace.id)
    await lock_organization(session, scope.organization_id)
    actor = await principal_for(session, runtime.access, principal_id, confinement=scope, share=True)
    await session.refresh(workspace)
    await workspace_scope(session, actor, workspace_id, "run")
    admission = Submission(session, runtime, workspace_id, actor)
    session.info["a13n.submission"] = admission
    return admission


# App SDK keeps its existing entry point and ownership checks around native admission.
AppSubmission = Submission
prepare_app_submission = prepare_submission


async def submit_app_message(
    runtime: Runtime, principal_id: str, workspace_id: str, thread_id: str, message: Message, *, request_key: str
) -> tuple[Submitted, bool]:
    """Submit as the App-configured principal using its current workspace-confined authority."""
    async with transaction(runtime.storage) as session:
        admission = await prepare_app_submission(session, runtime, workspace_id, principal_id)
        return await submit_message_in_transaction(
            session, runtime, admission, workspace_id, thread_id, message, request_key=request_key
        )


def submission_actor(session: AsyncSession, runtime: Runtime, admission: Submission, workspace_id: str) -> Principal:
    if (
        not session.info.get("a13n.transaction")
        or not session.in_transaction()
        or session.in_nested_transaction()
        or session.info.get("a13n.submission") is not admission
        or admission._session is not session
        or admission._runtime is not runtime
        or admission._workspace_id != workspace_id
    ):
        raise RuntimeError("Call prepare_submission in the same Service transaction before locking delivery rows")
    return admission._actor


async def submit_message_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    admission: Submission,
    workspace_id: str,
    thread_id: str,
    message: Message,
    *,
    request_key: str,
) -> tuple[Submitted, bool]:
    """Submit in a caller-owned transaction; abort and retry the whole transaction on contention.

    Call prepare_submission before taking the Thread lock. This uses the principal's current
    grants, not a fenced Run's delegation, and must not be used as a delegated Run operation.
    The receipt is committed only if the caller commits; abort and retry the whole transaction on contention.
    """
    _message_key(request_key)
    actor = submission_actor(session, runtime, admission, workspace_id)
    scope, request, found = await _lookup(
        session, actor, workspace_id, lambda _: Request.of(request_key, "message", thread_id, message)
    )
    app_id = admission._provenance.app_id if admission._provenance else None
    if found is not None:
        return await _replay(session, found, request, trigger_app_id=app_id), False
    thread, source = await _create_message(
        session, runtime, actor, scope, thread_id, message, request, trigger_app_id=app_id
    )
    await accept(session, runtime, thread, explicit=source)
    assert source.entry is not None
    return await receipt(session, thread, source.entry), True


async def prepare_thread(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    body: NewThread,
    *,
    request: Request | None,
    authority: ExecutionAuthority,
    schedule_occurrence_id: str | None = None,
    require_first_run: bool = False,
    provenance: AppProvenance | None = None,
) -> tuple[ThreadRow, Source]:
    """Create a new Thread and validated first Entry in the caller's transaction, ready for ordinary acceptance."""
    await validate_caller_headers(session, scope.workspace_id, body.mcp_headers)
    if body.session_id is not None:
        owner = await find_session(session, scope.workspace_id, body.session_id)
        require_conversation(owner)
        if not (provenance.matches_owner(owner) if provenance else owner.app_id is None):
            raise conflict("session", owner.id, "app_managed_session" if provenance is None else "app_owner_mismatch")
    else:
        owner = new_session(scope.organization_id, scope.workspace_id, actor.id, body.session, provenance=provenance)
        session.add(owner)
    thread = new_thread(
        owner,
        mcp_headers=body.mcp_headers,
        execution=body.execution,
        message_history=body.message_history,
        schedule_occurrence_id=schedule_occurrence_id,
    )
    session.add(thread)
    await session.flush()
    if body.session is not None and body.session.memory is not None:
        await configure(session, owner, body.session.memory)
        await session.flush()
    if body.browser_binding is not None:
        from a13n_service.runs.browsers.service import bind_initial

        await bind_initial(session, actor, thread, body.browser_binding, authority=authority)
    atomic = (
        require_first_run
        or body.browser_binding is not None
        or (schedule_occurrence_id is not None and bool(body.environments))
    )
    if not atomic:
        await mount_environments(session, thread, body.environments, principal=actor)
    await mount_memories(session, thread, body.memories, limit=runtime.settings.memory.mounts_per_thread)
    source = await prepare_message(
        session,
        runtime,
        actor,
        scope,
        thread,
        body,
        request,
        authority=authority,
        schedule_occurrence_id=schedule_occurrence_id,
        initial_environments=body.environments if atomic else (),
        trigger_app_id=provenance.app_id if provenance else None,
    )
    if atomic:
        source = replace(source, atomic_initial_acceptance=True)
    return thread, source


async def create_thread_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    admission: Submission,
    workspace_id: str,
    body: NewThread,
    *,
    request_key: str,
) -> Submitted:
    """Commit the new Thread only with its first accepted Run."""
    _message_key(request_key)
    actor = submission_actor(session, runtime, admission, workspace_id)
    scope, request, found = await _lookup(
        session, actor, workspace_id, lambda scope: Request.of(request_key, "thread", scope.workspace_id, body)
    )
    if found is not None:
        return await _replay(
            session,
            found,
            request,
            trigger_app_id=admission._provenance.app_id if admission._provenance else None,
            provenance=admission._provenance,
            reuse_session=body.session_id is not None,
        )
    thread, source = await prepare_thread(
        session,
        runtime,
        actor,
        scope,
        body,
        request=request,
        authority=execution_authority(actor, scope),
        require_first_run=True,
        provenance=admission._provenance,
    )
    run = await accept(session, runtime, thread, explicit=source)
    if run is None:
        raise conflict("thread", thread.id, "initial_run_not_accepted")
    assert source.entry is not None
    return await receipt(session, thread, source.entry)


async def create_thread(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    body: NewThread,
    *,
    request_key: str,
) -> tuple[Submitted, bool]:
    async def create(session: AsyncSession, scope: WorkspaceScope, request: Request) -> tuple[ThreadRow, Source]:
        return await prepare_thread(
            session,
            runtime,
            actor,
            scope,
            body,
            request=request,
            authority=execution_authority(actor, scope),
        )

    return await _submit(
        runtime, actor, workspace_id, lambda scope: Request.of(request_key, "thread", scope.workspace_id, body), create
    )


async def fork_thread(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    origin: RunRow,
    agent_id: str,
    revision_id: str | None,
    *,
    fresh_environments: bool,
    environments: InitialMounts = (),
    memories: MemoryMounts = (),
    thread_id: str | None = None,
) -> ThreadRow:
    origin_thread = await get_thread(session, scope.workspace_id, origin.thread_id, lock="acceptance")
    # A fork continues a sealed run's last checkpoint, whatever its outcome.
    if origin.sealed_at is None:
        raise conflict("run", origin.id, f"run_{origin.status}")
    checkpoints.require_compatible(origin)
    owner = await session.get(SessionRow, origin.session_id)
    assert owner is not None
    require_conversation(owner)
    thread = new_thread(
        owner,
        mcp_headers=origin_thread.mcp_headers,
        origin="fork",
        origin_thread_id=origin_thread.id,
        origin_run_id=origin.id,
    )
    retained = origin_thread.agent_snapshot is not None and agent_id == origin.agent_id and revision_id is None
    if retained and fresh_environments:
        raise invalid(
            "fresh_environments",
            "Snapshot forks retain shared environments; select a published revision for fresh environments",
        )
    if retained:
        thread.agent_snapshot = origin_thread.agent_snapshot
    if thread_id is not None:
        thread.id = thread_id
    session.add(thread)
    await session.flush()
    shared = [] if fresh_environments else await shared_mounts(session, origin_thread)
    await mount_environments(session, thread, [*shared, *environments], principal=actor)
    memory_mounts = [*await shared_memory_mounts(session, origin_thread), *memories]
    await mount_memories(session, thread, memory_mounts, limit=runtime.settings.memory.mounts_per_thread)
    return thread


async def fork(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: Fork, *, request_key: str
) -> tuple[Submitted, bool]:
    """A new thread in the origin's session whose first run continues the origin's committed history."""

    async def create(session: AsyncSession, scope: WorkspaceScope, request: Request) -> tuple[ThreadRow, Source]:
        origin = await get_run(session, scope.workspace_id, run_id)
        thread = await fork_thread(
            session,
            runtime,
            actor,
            scope,
            origin,
            body.agent_id,
            body.agent_revision_id,
            fresh_environments=body.fresh_environments,
            environments=body.environments,
            memories=body.memories,
        )
        return thread, await prepare_message(
            session, runtime, actor, scope, thread, body, request, authority=execution_authority(actor, scope)
        )

    return await _submit(runtime, actor, workspace_id, lambda _: Request.of(request_key, "fork", run_id, body), create)
