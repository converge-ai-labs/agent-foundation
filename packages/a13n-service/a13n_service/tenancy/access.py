"""Access: who is asking and what they may do right now.

A distribution supplies how requests authenticate, its role vocabulary and any additional grant sources as one
`Access` value. This module loads current principals under it, resolves the organization and workspace paths a
caller may see, and runs administrative changes with the `admin` check inside the changing transaction.
"""

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from typing import Protocol

from a13n_logging import exception_details, get_logger
from sqlalchemy import ColumnElement, Select, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from a13n_service.infra.audit import Scoped, record
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.tenancy.authorize import (
    VERBS,
    Grant,
    Principal,
    Scope,
    Verb,
    WorkspaceScope,
    allowed_verbs,
    authorize,
)
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PrincipalRow, WorkspaceRow

logger = get_logger(__name__)

_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def unauthenticated() -> ServiceError:
    return ServiceError("unauthenticated", "Authentication is required")


def login_session_required() -> ServiceError:
    """How every operation only a person's login session may perform refuses any other credential."""
    return ServiceError("forbidden", "This operation requires a login session")


def require_login_session(actor: Principal) -> None:
    """Refuse API keys, which are always confined to a workspace, where only a user's own login may act: changing
    the account, and minting anything that would outlive the key's expiry and revocation (keys, invitations) or
    hand a person a link to act on (browser authorization flows)."""
    if actor.kind != "user" or actor.confinement is not None:
        raise login_session_required()


@dataclass(frozen=True, slots=True)
class Authenticated:
    principal: Principal
    credential_id: str
    # The authenticator's own name for the credential; the local authenticator issues `session` and `key`.
    kind: str


class Authenticator(Protocol):
    """How requests prove who they are; a distribution may replace the local password/session/API-key one."""

    async def authenticate(self, request: Request, response: Response) -> Authenticated | None:
        """The request's validated identity and confinement; None when it presents no credential. Headers set on
        `response`, such as a renewed login cookie, reach every response to the request."""
        ...

    async def recheck(self, session: AsyncSession, credential: Authenticated) -> None:
        """Raise `unauthenticated` unless the credential is still live; read-only, for long-lived requests."""
        ...

    async def logout(self, request: Request, response: Response, credential: Authenticated) -> None:
        """End the credential's login."""
        ...


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """A role at an organization or one of its workspaces, as a grant source supplies it."""

    organization_id: str
    workspace_id: str | None
    role: str


class GrantSource(Protocol):
    async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]:
        """Additional tenant-scoped grants, unioned with stored ones under credential confinement.

        Called inside short transactions: answer from a bounded cache refreshed elsewhere, never from the
        network, and fail closed (raise or return nothing) when that cache is stale.
        """
        ...


@dataclass(frozen=True, slots=True)
class Access:
    """A distribution's authenticator, role vocabulary and additional grant sources, fixed at assembly."""

    authenticator: Authenticator
    roles: Mapping[str, frozenset[Verb]]
    sources: tuple[GrantSource, ...] = ()

    def __post_init__(self) -> None:
        for name, verbs in self.roles.items():
            if not _ROLE_NAME.fullmatch(name) or not verbs or not verbs <= VERBS:
                raise ValueError(f"Invalid role definition: {name!r}")

    def check_role(self, role: str) -> None:
        if role not in self.roles:
            raise invalid("role", "unknown_role")

    def roles_with(self, verb: Verb) -> list[str]:
        return sorted(name for name, verbs in self.roles.items() if verb in verbs)

    def resolve(self, grant: RoleGrant | GrantRow) -> Grant:
        """A role grant as verbs; a role no definition names fails closed."""
        verbs = self.roles.get(grant.role)
        if verbs is None:
            raise ServiceError("forbidden", "Principal has an unknown role")
        return Grant(grant.organization_id, grant.workspace_id, verbs)


async def principal_for(
    session: AsyncSession,
    access: Access,
    principal_id: str,
    *,
    confinement: WorkspaceScope | None = None,
    share: bool = False,
) -> Principal:
    """The principal's current status and grants, narrowed to `confinement`.

    `share` is for an authority check inside a change. Principal rows are locked in three modes, by what a
    transaction changes:
    - an authority check (`share`) holds `FOR KEY SHARE` until its transaction ends, after its caller took the
      organization lock that every grant change there takes;
    - a status change (a disable, including a service account retired for lack of grants) locks `FOR UPDATE`,
      so it either commits before a concurrent authority check and is seen there, or waits for it, in any
      organization;
    - a grant change locks its member only `FOR NO KEY UPDATE` (`lock_member`): the organization lock already
      orders it with authority checks there, so a check never waits for one, and two administrators changing
      each other's grants in different organizations cannot deadlock.
    """
    row = await session.get(
        PrincipalRow,
        principal_id,
        with_for_update={"read": True, "key_share": True} if share else None,
        populate_existing=share,
    )
    if row is None or row.status != "active":
        raise unauthenticated()
    home = await session.get_one(WorkspaceRow, row.home_workspace_id) if row.kind == "service_account" else None
    grants = (await session.scalars(select(GrantRow).where(GrantRow.principal_id == row.id))).all()
    return await _principal(access, row, home, grants, confinement)


async def principals_for(
    session: AsyncSession, access: Access, identities: Iterable[tuple[str, WorkspaceScope]]
) -> dict[tuple[str, WorkspaceScope], Principal | ServiceError]:
    """`principal_for` without locks for many principal IDs and confinements, with set reads. A refusal is
    returned in its identity's place; `unavailable` is raised, since it refuses nothing."""
    identities = set(identities)
    ids = {principal_id for principal_id, _ in identities}
    rows = {
        row.id: (row, home)
        for row, home in (
            await session.execute(
                select(PrincipalRow, WorkspaceRow)
                .outerjoin(WorkspaceRow, WorkspaceRow.id == PrincipalRow.home_workspace_id)
                .where(PrincipalRow.id.in_(ids))
            )
        ).tuples()
    }
    grants: dict[str, list[GrantRow]] = defaultdict(list)
    for grant in await session.scalars(select(GrantRow).where(GrantRow.principal_id.in_(ids))):
        grants[grant.principal_id].append(grant)
    principals: dict[tuple[str, WorkspaceScope], Principal | ServiceError] = {}
    for principal_id, confinement in identities:
        row, home = rows.get(principal_id, (None, None))
        try:
            if row is None or row.status != "active":
                raise unauthenticated()
            principals[principal_id, confinement] = await _principal(
                access, row, home, grants[principal_id], confinement
            )
        except ServiceError as error:
            if error.code == "unavailable":
                raise
            principals[principal_id, confinement] = error
    return principals


async def _principal(
    access: Access,
    row: PrincipalRow,
    home: WorkspaceRow | None,
    grants: Sequence[GrantRow],
    confinement: WorkspaceScope | None,
) -> Principal:
    """An active principal from its row, its home workspace when it is a service account, and its stored grants."""
    if row.kind == "service_account":
        assert home is not None  # The `account_home` constraint gives every service account a home.
        home_scope = WorkspaceScope(home.organization_id, home.id)
        if confinement not in {None, home_scope}:
            raise ServiceError("forbidden", "Service account cannot leave its home workspace")
        confinement = home_scope
    principal = Principal(
        row.id,
        "user" if row.kind == "user" else "service_account",
        tuple(access.resolve(grant) for grant in grants),
        confinement,
        name=row.name,
        email=row.email,
    )
    extra = [access.resolve(grant) for source in access.sources for grant in await source.grants_for(principal)]
    return replace(principal, grants=principal.grants + tuple(extra)) if extra else principal


async def reauthenticate(session: AsyncSession, access: Access, credential: Authenticated) -> Principal:
    """Read-only re-check for long-lived requests: the credential is still live; status and grants are current."""
    await access.authenticator.recheck(session, credential)
    principal = credential.principal
    return await principal_for(session, access, principal.id, confinement=principal.confinement)


def _member(principal: Principal, organization_id: str) -> bool:
    return any(grant.organization_id == organization_id for grant in principal.grants)


async def resolve_organization(session: AsyncSession, principal: Principal, organization_id: str) -> OrganizationRow:
    """An organization the principal holds a grant in; any other is not found, so a path reveals nothing."""
    organization = await session.get(OrganizationRow, organization_id)
    if organization is None or not _member(principal, organization.id):
        raise not_found("organization", organization_id)
    return organization


async def resolve_workspace(session: AsyncSession, principal: Principal, reference: str) -> WorkspaceRow:
    """A workspace by ID in one of the principal's organizations, or by key among the workspaces it can read.

    Anything else is not found, so a path never reveals another tenant's workspaces.
    """
    workspace = await session.get(WorkspaceRow, reference)
    if workspace is not None and _member(principal, workspace.organization_id):
        return workspace
    rows = (
        await session.scalars(
            select(WorkspaceRow)
            .where(WorkspaceRow.key == reference, WorkspaceRow.id.in_(readable_workspaces(principal)))
            .limit(2)
        )
    ).all()
    if not rows:
        raise not_found("workspace", reference)
    if len(rows) > 1:
        raise conflict("workspace", reference, "ambiguous_key")
    return rows[0]


async def workspace_scope(
    session: AsyncSession, principal: Principal, workspace_id: str, verb: Verb, *, require_active: bool = True
) -> WorkspaceScope:
    """Resolve and authorize a workspace path; an archived workspace refuses every verb but read."""
    workspace = await resolve_workspace(session, principal, workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace.id)
    authorize(principal, scope, verb)
    if require_active and verb != "read":
        refuse_archived(workspace)
    return scope


def refuse_archived(workspace: WorkspaceRow) -> None:
    """An archived workspace refuses every change."""
    if workspace.archived_at is not None:
        raise ServiceError("disabled", "Workspace is archived", {"kind": "workspace", "id": workspace.id})


async def require_active_workspace(session: AsyncSession, workspace_id: str) -> None:
    """Acceptance refuses an archived workspace as every change does; execution rechecks it with its authority."""
    workspace = await session.get(WorkspaceRow, workspace_id)
    if workspace is None:
        raise not_found("workspace", workspace_id)
    refuse_archived(workspace)


def readable_workspaces(principal: Principal) -> Select[tuple[str]]:
    predicates: list[ColumnElement[bool]] = []
    for grant in principal.grants:
        if "read" not in allowed_verbs(principal, Scope(grant.organization_id, grant.workspace_id)):
            continue
        clause = WorkspaceRow.organization_id == grant.organization_id
        if grant.workspace_id is not None:
            clause = and_(clause, WorkspaceRow.id == grant.workspace_id)
        predicates.append(clause)
    query = select(WorkspaceRow.id).where(or_(*predicates) if predicates else false())
    if principal.confinement is not None:
        query = query.where(WorkspaceRow.id == principal.confinement.workspace_id)
    return query


async def lock_member(session: AsyncSession, principal_id: str) -> PrincipalRow:
    """Lock an existing principal whose grants change; concurrent authority checks of it (`principal_for` with
    `share`) do not wait."""
    return await session.get_one(
        PrincipalRow, principal_id, with_for_update={"key_share": True}, populate_existing=True
    )


async def lock_organization(session: AsyncSession, organization_id: str) -> None:
    """Serialize membership and administration changes within one organization.

    Taken before any principal or grant lock. `FOR NO KEY UPDATE` leaves rows that merely reference the
    organization free to be written meanwhile.
    """
    await session.execute(
        select(OrganizationRow.id).where(OrganizationRow.id == organization_id).with_for_update(key_share=True)
    )


@dataclass(frozen=True, slots=True)
class OrganizationPath:
    """An organization a route names by ID."""

    organization_id: str

    async def resolve(self, session: AsyncSession, principal: Principal) -> tuple[Scope, None]:
        return Scope((await resolve_organization(session, principal, self.organization_id)).id), None


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    """A workspace a route names by ID or key."""

    workspace: str

    async def resolve(self, session: AsyncSession, principal: Principal) -> tuple[WorkspaceScope, WorkspaceRow]:
        workspace = await resolve_workspace(session, principal, self.workspace)
        return WorkspaceScope(workspace.organization_id, workspace.id), workspace


# Where an administrative operation acts: an organization, or one of its workspaces.
type AdminPath = OrganizationPath | WorkspacePath


class _Resolving[S: Scoped](Protocol):
    async def resolve(self, session: AsyncSession, principal: Principal) -> tuple[S, WorkspaceRow | None]: ...


@asynccontextmanager
async def administering[S: Scoped](
    storage: Storage,
    access: Access,
    actor: Principal,
    path: _Resolving[S],
    *,
    action: str,
    reading: bool = False,
    require_active: bool = True,
) -> AsyncIterator[tuple[AsyncSession, S]]:
    """A transaction in which the actor currently holds `admin` at an organization or workspace path.

    For a change, the check runs inside the changing transaction, after the organization lock, on the actor's
    re-read and share-locked principal, and an archived workspace refuses it unless `require_active=False`.
    Admin-only reads (`reading`) check the actor's current grants without locks and see archived workspaces.
    A denial is recorded in its own bounded transaction after this one ends, so the refusal never rolls back
    the only evidence.
    """
    async with transaction(storage) as session:
        scope, workspace = await path.resolve(session, actor)
        if not reading:
            await lock_organization(session, scope.organization_id)
        current = await principal_for(session, access, actor.id, confinement=actor.confinement, share=not reading)
        if "admin" in allowed_verbs(current, scope):
            if not reading and require_active and workspace is not None:
                # Archiving serializes on the organization lock too; see the state it left.
                await session.refresh(workspace)
                refuse_archived(workspace)
            yield session, scope
            return
    await _record_denial(storage, actor, scope, action)
    raise ServiceError("forbidden", "Principal cannot perform this operation", {"verb": "admin"})


def administering_workspace(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace: str,
    *,
    action: str,
    reading: bool = False,
    require_active: bool = True,
) -> AbstractAsyncContextManager[tuple[AsyncSession, WorkspaceScope]]:
    """`administering` at a workspace path."""
    return administering(
        storage,
        access,
        actor,
        WorkspacePath(workspace),
        action=action,
        reading=reading,
        require_active=require_active,
    )


async def _record_denial(storage: Storage, actor: Principal, scope: Scoped, action: str) -> None:
    try:
        async with asyncio.timeout(2), transaction(storage) as session:
            record(
                session,
                scope,
                actor_id=actor.id,
                action=action,
                target_kind="workspace" if scope.workspace_id is not None else "organization",
                target_id=scope.workspace_id or scope.organization_id,
                outcome="denied",
                details={
                    "verb": "admin",
                    "credential_workspace_id": actor.confinement.workspace_id if actor.confinement else None,
                },
            )
    except Exception as failure:
        logger.error(
            "Admin denial audit failed",
            extra={"error_type": type(failure).__name__, "exception_details": exception_details(failure)},
        )
