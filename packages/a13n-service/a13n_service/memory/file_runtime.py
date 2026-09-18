"""Attempt-authorized filesystem stores using retained Environment bindings."""

import json
from collections.abc import Awaitable, Callable
from hashlib import sha256

from a13n_environment.files import FileCommitOperator, FileCommitRequest, FileEntriesResult, FileMutationResult
from a13n_harness.context import AgentContext
from a13n_harness.document_memory import MemoryDocumentError
from a13n_harness.environment.providers import BoundEnvironment, FileScopeSelection
from a13n_harness.filesystem_memory import FilesystemMemoryStore
from a13n_harness.memory import MemoryScope
from a13n_harness.memory_file_commit import EnvironmentMemoryFileCoordinator
from a13n_harness.memory_plugins import FilesystemMemoryConfiguration
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from a13n_service.agents.models import AgentRecord
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent, authorize_workspace
from a13n_service.iam.domain import PrincipalType
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import Run
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .domain import InlineMemoryBackend, MemoryEntrySelection
from .models import MemoryStorageRecord, RunMemoryStorageRecord
from .resources import require_provider
from .scopes import memory_subject
from .service import MemoryService
from .sources import authorize_sources


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _Files:
    """Open the same captured mount for each operation; never reroute by alias."""

    def __init__(self, environment: BoundEnvironment, selection: FileScopeSelection) -> None:
        self.environment, self.selection = environment, selection

    async def read_bytes(self, path: str, *, offset: int = 0, length: int | None = None) -> bytes:
        async with self.environment.open_files(self.selection) as files:
            return await files.read_bytes(path, offset=offset, length=length)

    async def list(
        self, path: str, *, offset: int = 0, max_results: int, include_hidden: bool = False
    ) -> FileEntriesResult:
        async with self.environment.open_files(self.selection) as files:
            return await files.list(path, offset=offset, max_results=max_results, include_hidden=include_hidden)

    async def commit(self, request: FileCommitRequest) -> FileMutationResult:
        async with self.environment.open_files(self.selection) as files:
            if not isinstance(files, FileCommitOperator):
                raise MemoryDocumentError("memory_write_unsupported")
            return await files.commit(request)


async def filesystem_store(
    context: AgentContext,
    service: MemoryService,
    *,
    run: Run,
    workspace_id: str,
    agent_id: str,
    entry: MemoryEntrySelection,
    current_context: Callable[[], AttemptContext],
) -> FilesystemMemoryStore:
    scope = entry.scope or MemoryScope.THREAD
    if scope is MemoryScope.USER and run.authority_principal.principal_type is not PrincipalType.user:
        raise MemoryDocumentError("memory_scope_unavailable")
    inline = isinstance(entry.backend, InlineMemoryBackend)
    provider_identity = (
        "a13n.filesystem" if isinstance(entry.backend, InlineMemoryBackend) else entry.backend.provider_id
    )
    subject_id = {
        MemoryScope.THREAD: run.thread_id,
        MemoryScope.AGENT: agent_id,
        MemoryScope.USER: run.authority_principal.principal_id,
    }[scope]
    subject = memory_subject(run.organization_id, workspace_id, provider_identity, scope, subject_id).value

    async def authorize(write: bool) -> None:
        attempt = current_context()
        async with short_session(service.authorizer.sessions) as session:
            current, _, _ = await read_attempt_authority(session, attempt, utc_now())
            if (
                current.id != run.id
                or current.authority_principal_id != run.authority_principal.principal_id
                or current.authority_principal_type != run.authority_principal.principal_type.value
            ):
                raise MemoryDocumentError("memory_scope_unavailable")
            actor = AuthenticatedActor(
                principal=run.authority_principal,
                auth_method="internal",
                credential_id="attempt-memory",
                boundary_workspace_id=workspace_id,
            )
            for selected in {run.agent_id, agent_id}:
                await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=selected,
                    action=WorkspaceAction.agent_invoke,
                    snapshot=attempt.authorization.snapshot,
                )
                agent = await session.get(AgentRecord, selected)
                if (
                    agent is None
                    or agent.organization_id != run.organization_id
                    or agent.workspace_id != workspace_id
                    or not agent.enabled
                    or agent.archived_at is not None
                ):
                    raise MemoryDocumentError("memory_scope_unavailable")
            action = WorkspaceAction.memory_write if write else WorkspaceAction.memory_read
            if scope is MemoryScope.USER:
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=action,
                    snapshot=attempt.authorization.snapshot,
                )
            else:
                await authorize_agent(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=run.agent_id if scope is MemoryScope.THREAD else agent_id,
                    action=action,
                    snapshot=attempt.authorization.snapshot,
                )
            if not inline:
                await require_provider(
                    session,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    provider_id=provider_identity,
                    eligible=True,
                    catalog=service.catalog,
                )

    await authorize(False)
    if isinstance(entry.backend, InlineMemoryBackend):
        configuration = FilesystemMemoryConfiguration.model_validate(entry.backend.configuration)
    else:
        async with short_session(service.authorizer.sessions) as session:
            provider = await require_provider(
                session,
                organization_id=run.organization_id,
                workspace_id=workspace_id,
                provider_id=provider_identity,
                eligible=True,
                catalog=service.catalog,
            )
            if provider.type != "a13n.filesystem":
                raise MemoryDocumentError("memory_documents_unsupported")
            configuration = FilesystemMemoryConfiguration.model_validate(provider.configuration)
    return await bind_filesystem_store(
        context,
        service,
        run=run,
        workspace_id=workspace_id,
        configuration=configuration,
        provider_identity=provider_identity,
        subject=subject,
        authorize=authorize,
        current_context=current_context,
        scope_kind=scope.value,
        subject_id=subject_id,
        organization_policy={"agent_id": agent_id, "entry": entry.model_dump(mode="json")}
        if entry.auto_organize
        else None,
    )


async def bind_filesystem_store(
    context: AgentContext,
    service: MemoryService,
    *,
    run: Run,
    workspace_id: str,
    configuration: FilesystemMemoryConfiguration,
    provider_identity: str,
    subject: str,
    authorize: Callable[[bool], Awaitable[None]],
    current_context: Callable[[], AttemptContext],
    scope_kind: str,
    subject_id: str,
    organization_policy: dict[str, object] | None = None,
) -> FilesystemMemoryStore:
    environment_id = configuration.storage.environment_id or run.environment_id
    if environment_id is None:
        raise MemoryDocumentError("memory_storage_unavailable")
    selection_digest = _digest([provider_identity, subject, configuration.model_dump(mode="json")])
    async with short_session(service.authorizer.sessions) as session:
        retained = await session.get(RunMemoryStorageRecord, (run.id, selection_digest))
        previous = await session.get(MemoryStorageRecord, retained.storage_id) if retained else None
        if previous is not None:
            environment_id = previous.environment_id
        mount_name = (
            "workspace"
            if environment_id == run.environment_id
            else await session.scalar(
                select(RunEnvironmentMountRecord.name).where(
                    RunEnvironmentMountRecord.run_id == run.id,
                    RunEnvironmentMountRecord.environment_id == environment_id,
                )
            )
        )
    if mount_name is None:
        raise MemoryDocumentError("memory_storage_unavailable")
    # Explicit selections use an accepted mount; file permission cannot acquire
    # an arbitrary Environment or silently use this Worker's filesystem.
    mount = next((item for item in context.environment.snapshot.mounts if item.name == mount_name), None)
    if mount is None:
        raise MemoryDocumentError("memory_storage_unavailable")
    mount_path = mount.mount_path or (
        "/workspace" if context.environment.snapshot.default_mount == mount_name else f"/environment/{mount_name}"
    )
    root = (mount_path.rstrip("/") + configuration.storage.root).rstrip("/") or "/"
    selected = await context.environment.resolve_files(root)
    mount = next(item for item in context.environment.snapshot.mounts if item.name == mount_name)
    backing = mount.descriptor.backing_identity
    if backing is None:
        raise MemoryDocumentError("memory_storage_unavailable")
    target_digest = _digest(
        [run.organization_id, workspace_id, provider_identity, subject, environment_id, configuration.storage.root]
    )
    async with short_session(service.authorizer.sessions) as session:
        existing = await session.scalar(
            select(MemoryStorageRecord.id).where(MemoryStorageRecord.target_digest == target_digest)
        )
    if existing is None:
        await authorize(True)  # Read-only callers cannot admit a new corpus.
    storage: MemoryStorageRecord | None = None
    created = False
    for retry in range(2):
        created = False
        try:
            async with transaction(service.authorizer.sessions) as session:
                await read_attempt_authority(session, current_context(), utc_now())
                storage = await session.scalar(
                    select(MemoryStorageRecord).where(MemoryStorageRecord.target_digest == target_digest)
                )
                if storage is None:
                    created = True
                    storage = MemoryStorageRecord(
                        id=new_object_id("mstore"),
                        target_digest=target_digest,
                        organization_id=run.organization_id,
                        workspace_id=workspace_id,
                        provider_identity=provider_identity,
                        subject=subject,
                        scope_kind=scope_kind,
                        subject_id=subject_id,
                        environment_id=environment_id,
                        root=configuration.storage.root,
                        backing_identity=backing,
                        initialized=False,
                    )
                    session.add(storage)
                    await session.flush()
                if storage.backing_identity != backing or (previous is not None and previous.id != storage.id):
                    raise MemoryDocumentError("memory_storage_unavailable")
                binding = await session.get(RunMemoryStorageRecord, (run.id, selection_digest))
                if binding is None:
                    session.add(
                        RunMemoryStorageRecord(
                            run_id=run.id,
                            selection_digest=selection_digest,
                            storage_id=storage.id,
                            organization_policy=organization_policy,
                        )
                    )
                elif binding.storage_id != storage.id:
                    raise MemoryDocumentError("memory_storage_unavailable")
            break
        except IntegrityError:
            if retry:
                raise
    assert storage is not None

    async def sources(references: tuple[str, ...]) -> None:
        if not references:
            return
        await authorize(False)
        actor = AuthenticatedActor(
            principal=run.authority_principal,
            auth_method="internal",
            credential_id="memory-source",
            boundary_workspace_id=workspace_id,
        )
        async with short_session(service.authorizer.sessions) as session:
            await authorize_sources(
                session, actor=actor, storage_id=store.store_id, workspace_id=workspace_id, references=references
            )

    class AuthorizedFiles(_Files):
        async def commit(self, request: FileCommitRequest) -> FileMutationResult:
            await authorize(True)
            return await super().commit(request)

    files = AuthorizedFiles(context.environment, selected)
    store = FilesystemMemoryStore(
        files=files,
        root=root,
        scope=subject,
        store_id=storage.id,
        principal=run.authority_principal.principal_id,
        authorize=authorize,
        authorize_sources=sources,
    )
    store.coordinator = EnvironmentMemoryFileCoordinator(
        files, root=store.subject_root, store_id=storage.id, scope=subject
    )
    if not storage.initialized:
        if created:
            await store.initialize()
        else:
            await store.index()  # A retained pending binding must already have its marker.
        async with transaction(service.authorizer.sessions) as session:
            await read_attempt_authority(session, current_context(), utc_now())
            record = await session.get(MemoryStorageRecord, storage.id)
            if record is None:
                raise MemoryDocumentError("memory_storage_unavailable")
            record.initialized = True
    if organization_policy is not None:
        token = await store.organization_token()
        async with transaction(service.authorizer.sessions) as session:
            await read_attempt_authority(session, current_context(), utc_now())
            binding = await session.get(RunMemoryStorageRecord, (run.id, selection_digest), with_for_update=True)
            if (
                binding is not None
                and binding.organization_policy is not None
                and "erasure_digest" not in binding.organization_policy
            ):
                binding.organization_policy = {
                    **binding.organization_policy,
                    "erasure_digest": sha256((token or "").encode()).hexdigest(),
                }
    return store
