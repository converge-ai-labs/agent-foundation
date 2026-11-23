"""Change desired resources while preserving historical Run bindings."""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from a13n_envd_client.eip.v1 import EgressDestinations, EgressMode
from a13n_harness.environment.models import EnvironmentPermissionSet
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from a13n_service.infra.audit import record
from a13n_service.infra.errors import conflict, invalid, not_found
from a13n_service.resources.egress_credentials.schemas import SecretReference
from a13n_service.resources.egress_credentials.service import validate_references
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.resources.environment_instances.access import lock_environments
from a13n_service.resources.environment_instances.session_configuration import EgressConfiguration
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.browsers.service import replace_binding
from a13n_service.runs.browsers.tables import ThreadBrowserRow
from a13n_service.runs.environments import bindings, mounts
from a13n_service.runs.environments.access import audit
from a13n_service.runs.environments.desktop.delivery import suppress
from a13n_service.runs.environments.lifecycle import begin, reserve
from a13n_service.runs.environments.schemas import MountCreate, SessionConfiguration
from a13n_service.runs.environments.tables import ThreadEnvironmentRow, ThreadSessionBindingRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_thread, require_open
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

from ._access import app_workspace, run_actor


class _Omitted(Enum):
    VALUE = 0


async def _lock_shared_tree(session: AsyncSession, root: ThreadRow) -> list[ThreadRow]:
    queue = deque([root])
    selected = []
    while queue:
        parent = queue.popleft()
        if parent.archived_at is None:
            selected.append(parent)
        # Spawn locks parent before inserting child. Leave unrelated edge types and forks untouched.
        children = (
            await session.scalars(
                select(ThreadRow.id)
                .where(
                    ThreadRow.workspace_id == root.workspace_id,
                    ThreadRow.origin_thread_id == parent.id,
                    ThreadRow.origin == "child",
                )
                .order_by(ThreadRow.id)
            )
        ).all()
        for child_id in children:
            child = await get_thread(session, root.workspace_id, child_id, lock="acceptance", history=False)
            # Dynamic children always inherit their parent Run's mounts and have no named edge.
            if (
                child.agent_snapshot is not None
                or (await mounts.child_environment_policy(session, child)).mode == "shared"
            ):
                queue.append(child)
    return selected


async def _require_quiescent(session: AsyncSession, threads: list[ThreadRow]) -> None:
    for thread in threads:
        last_status = (
            await session.scalar(select(RunRow.status).where(RunRow.id == thread.last_run_id))
            if thread.last_run_id is not None
            else None
        )
        pending = (
            select(InboxEntryRow.id)
            .where(
                InboxEntryRow.thread_id == thread.id,
                InboxEntryRow.status == "pending",
            )
            .exists()
        )
        if thread.current_run_id or last_status == "waiting" or await session.scalar(select(pending)):
            raise conflict("thread", thread.id, "thread_resources_busy")


async def _replace_primary(
    session: AsyncSession,
    actor: Principal,
    thread: ThreadRow,
    target: str | None,
    configuration: SessionConfiguration | _Omitted,
) -> None:
    old = await session.get(ThreadEnvironmentRow, (thread.id, mounts.PRIMARY))
    if old is None and target is None:
        return
    name = old.session_binding if old is not None else "default"
    if old is not None:
        await bindings.require_idle(
            session,
            thread.id,
            EnvironmentMount(
                name=mounts.PRIMARY,
                environment_id=old.environment_id,
                session_binding=name,
            ),
        )
    if target is not None:
        binding = await session.get(ThreadSessionBindingRow, (thread.id, target, name))
        desired = configuration if isinstance(configuration, SessionConfiguration) else None
        if desired is None and old is not None and old.environment_id != target:
            prior = await session.get_one(ThreadSessionBindingRow, (thread.id, old.environment_id, name))
            desired = SessionConfiguration.model_validate(prior.configuration)
        if desired is not None:
            await validate_references(
                session,
                actor,
                WorkspaceScope(thread.organization_id, thread.workspace_id),
                desired.credential_references,
            )
        if binding is None:
            await bindings.ensure(session, thread, target, name, desired)
        elif desired is not None and desired != SessionConfiguration.model_validate(binding.configuration):
            if old is None or old.environment_id != target:
                await bindings.require_idle(
                    session,
                    thread.id,
                    EnvironmentMount(
                        name=mounts.PRIMARY,
                        environment_id=target,
                        session_binding=name,
                    ),
                )
            binding.configuration = desired.model_dump(mode="json")
            binding.version += 1
            thread.version += 1
    await suppress(session, thread.id, mount_name=mounts.PRIMARY)
    if old is not None and target != old.environment_id:
        await session.delete(old)
        await session.flush()
    if target is not None and (old is None or target != old.environment_id):
        mount = MountCreate(name=mounts.PRIMARY, environment_id=target, session_binding=name)
        if old is not None:
            mount = MountCreate(
                name=mounts.PRIMARY,
                environment_id=target,
                session_binding=name,
                working_directory=old.working_directory,
                permission_ceiling=EnvironmentPermissionSet.model_validate(old.permission_ceiling),
            )
        await mounts.mount_environments(session, thread, [mount], principal=actor)


@dataclass(frozen=True, slots=True)
class UpdatedThreadResources:
    """Resolved desired primary Environment and Browser for the root Thread."""

    environment_id: str | None
    browser_id: str | None


async def update_thread_resources(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    principal_id: str,
    root_thread_id: str,
    environment_id: str | _Omitted | None = _Omitted.VALUE,
    environment_template_id: str | _Omitted | None = _Omitted.VALUE,
    session_configuration: SessionConfiguration | _Omitted | None = _Omitted.VALUE,
    browser_id: str | _Omitted | None = _Omitted.VALUE,
    template_name: str | None = None,
) -> UpdatedThreadResources:
    """Replace desired primary mounts for the native shared subtree; Browser edits affect only the root.

    Omitted values preserve the corresponding selection; None clears a Browser or Environment, or resets
    the Session configuration. A template queues creation of a new primary Environment shared by the selected
    Threads. The returned IDs are the root's desired bindings in the caller's transaction.
    """
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    if isinstance(environment_id, str) and isinstance(environment_template_id, str):
        raise invalid("environment_template_id", "mutually_exclusive_with_environment_id")
    root = await get_thread(session, workspace_id, root_thread_id, lock="acceptance", history=False)
    require_open(root)
    environment_edit = (
        environment_id is not _Omitted.VALUE
        or environment_template_id is not _Omitted.VALUE
        or session_configuration is not _Omitted.VALUE
    )
    threads = await _lock_shared_tree(session, root) if environment_edit else []
    if environment_edit:
        await _require_quiescent(session, threads)
    if isinstance(browser_id, _Omitted):
        binding = await session.get(ThreadBrowserRow, root.id, populate_existing=True)
        resolved_browser_id = binding.browser_id if binding is not None else None
    else:
        changed = await replace_binding(
            session, actor, scope, root, BrowserBinding(browser_id=browser_id) if browser_id else None
        )
        resolved_browser_id = changed.browser_id if changed is not None else None
    if not environment_edit:
        primary = await session.get(ThreadEnvironmentRow, (root.id, mounts.PRIMARY), populate_existing=True)
        return UpdatedThreadResources(primary.environment_id if primary is not None else None, resolved_browser_id)
    if isinstance(environment_template_id, str):
        row = await reserve(
            session,
            actor,
            scope,
            environment_template_id,
            limit=runtime.settings.environments.managed_count,
            name=template_name,
        )
        await begin(session, row, "creating")
        await session.flush()
        audit(session, actor, row, "create")
        environment_id = row.id
    elif environment_template_id is None and isinstance(environment_id, _Omitted):
        environment_id = None
    if session_configuration is None:
        session_configuration = SessionConfiguration()
    targets = []
    for thread in threads:
        old = await session.get(ThreadEnvironmentRow, (thread.id, mounts.PRIMARY))
        targets.append(
            (old.environment_id if old is not None else None)
            if isinstance(environment_id, _Omitted)
            else environment_id
        )
    if isinstance(session_configuration, SessionConfiguration):
        if targets[0] is None:
            raise invalid("session_configuration", "requires_primary_environment")
    await lock_environments(
        session, workspace_id, [target for target in targets if target is not None], principal_id=actor.id
    )
    for thread, target in zip(threads, targets, strict=True):
        await _replace_primary(session, actor, thread, target, session_configuration)
    await session.flush()
    return UpdatedThreadResources(targets[0], resolved_browser_id)


async def update_app_egress_secrets(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    principal_id: str,
    root_thread_id: str,
    set_secrets: Sequence[SecretReference] = (),
    remove_envs: Sequence[str] = (),
    initialize_destinations: EgressDestinations | None = None,
) -> None:
    """Patch this App's references in each desired primary binding of a quiescent shared subtree.

    An addition may initialize a binding with both egress fields unset only when destinations are supplied.
    """
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    upserts = {reference.env: reference for reference in set_secrets}
    removals = set(remove_envs)
    if len(upserts) != len(set_secrets) or upserts.keys() & removals:
        raise invalid("set_secrets", "Credential names must be unique and cannot also be removed")
    root = await get_thread(session, workspace_id, root_thread_id, lock="acceptance", history=False)
    require_open(root)
    if not upserts and not removals:
        return
    threads = await _lock_shared_tree(session, root)
    await _require_quiescent(session, threads)

    desired = []
    changed_envs = upserts.keys() | removals
    owned_ids = {reference.credential_id for reference in set_secrets}
    for thread in threads:
        primary = await session.get(ThreadEnvironmentRow, (thread.id, mounts.PRIMARY), populate_existing=True)
        if primary is None:
            if thread.id == root.id and upserts:
                raise invalid("set_secrets", "requires_primary_environment")
            continue
        binding = await session.get_one(
            ThreadSessionBindingRow,
            (thread.id, primary.environment_id, primary.session_binding),
            populate_existing=True,
        )
        configuration = SessionConfiguration.model_validate(binding.configuration)
        desired.append((thread, binding, configuration))
        owned_ids.update(ref.credential_id for ref in configuration.credential_references if ref.env in changed_envs)

    await lock_environments(
        session, workspace_id, [binding.environment_id for _, binding, _ in desired], principal_id=actor.id
    )
    # An App may have loaded credentials before taking the organization lock. Refresh their
    # metadata under the lock and keep the rows alive through native reference validation.
    owned = {}
    if owned_ids:
        owned = {
            row.id: row
            for row in await session.scalars(
                select(CredentialRow)
                .where(
                    CredentialRow.workspace_id == workspace_id,
                    CredentialRow.owner_app_id == app_id,
                    CredentialRow.id.in_(owned_ids),
                )
                .options(load_only(CredentialRow.id, CredentialRow.state, CredentialRow.allowed_hosts))
                .execution_options(populate_existing=True)
            )
        }
    for reference in set_secrets:
        if reference.credential_id not in owned:
            raise not_found(CredentialRow.KIND, reference.credential_id)
    await validate_references(session, actor, scope, set_secrets)

    # Validate all edits before changing any row; a descendant conflict must not apply a partial patch.
    changes = []
    for thread, binding, configuration in desired:
        current = {reference.env: reference for reference in configuration.credential_references}
        if any(current[env].credential_id not in owned for env in current.keys() & changed_envs):
            raise conflict("session_binding", binding.name, "egress_secret_owner_conflict", thread_id=thread.id)
        next_configuration = _patch_secrets(configuration, upserts, removals, initialize_destinations)
        if configuration != next_configuration:
            await bindings.require_idle(
                session,
                thread.id,
                EnvironmentMount(
                    name=mounts.PRIMARY,
                    environment_id=binding.environment_id,
                    session_binding=binding.name,
                ),
            )
            changes.append((thread, binding, next_configuration))

    for thread, binding, configuration in changes:
        binding.configuration = configuration.model_dump(mode="json")
        binding.version += 1
        thread.version += 1
        record(
            session,
            scope,
            actor_id=actor.id,
            action="thread_session_binding.update",
            target_kind="thread",
            target_id=thread.id,
            details={
                "app_id": app_id,
                "environment_id": binding.environment_id,
                "name": binding.name,
                "version": binding.version,
            },
        )
    await session.flush()


def _patch_secrets(
    configuration: SessionConfiguration,
    upserts: dict[str, SecretReference],
    removals: set[str],
    initialize_destinations: EgressDestinations | None,
) -> SessionConfiguration:
    egress = configuration.egress
    mode = configuration.egress_mode
    if not upserts and not any(ref.env in removals for ref in configuration.credential_references):
        return configuration
    if mode is None and egress is None and upserts and initialize_destinations is not None:
        destinations = initialize_destinations
    elif mode == EgressMode.CONTROLLED and egress is not None:
        destinations = egress.destinations
    else:
        raise invalid("set_secrets", "Credential injection requires controlled egress with a destination policy")
    secrets = {ref.env: ref for ref in configuration.credential_references if ref.env not in removals}
    secrets.update(upserts)
    try:
        egress = EgressConfiguration(destinations=destinations, secrets=tuple(secrets.values()))
    except ValidationError as error:
        raise invalid("set_secrets", error.errors(include_input=False, include_url=False)[0]["msg"]) from None
    return configuration.model_copy(update={"egress_mode": EgressMode.CONTROLLED, "egress": egress})
