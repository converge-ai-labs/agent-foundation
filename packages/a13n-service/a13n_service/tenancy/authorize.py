"""Detached grants, the built-in role vocabulary, credential confinement and the one scope/verb rule."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.infra.audit import Scoped
from a13n_service.infra.errors import ServiceError

type Verb = Literal["read", "run", "write", "admin"]

VERBS: frozenset[Verb] = frozenset({"read", "run", "write", "admin"})
# What a workspace grant, or a credential confined to a workspace, may do at its organization's own scope.
ORGANIZATION_READ: frozenset[Verb] = frozenset({"read"})

BUILT_IN_ROLES: Mapping[str, frozenset[Verb]] = MappingProxyType(
    {
        "viewer": frozenset({"read"}),
        "runner": frozenset({"read", "run"}),
        "builder": frozenset({"read", "run", "write"}),
        "admin": VERBS,
    }
)


@dataclass(frozen=True, slots=True)
class Scope:
    organization_id: str
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """A resolved workspace: the scope of every execution, of workspace-owned resources and of every API key."""

    organization_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class Grant:
    """The verbs a role gives at an organization or one of its workspaces, resolved when the principal loads."""

    organization_id: str
    workspace_id: str | None
    verbs: frozenset[Verb]


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    kind: Literal["user", "service_account"]
    grants: tuple[Grant, ...]
    # An API key's workspace; a service account is always confined to its home workspace.
    confinement: WorkspaceScope | None = None
    name: str = ""
    email: str | None = None


class ExecutionAuthority(BaseModel):
    """Accepted delegation ceiling; current status/grants can only narrow it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str = Field(min_length=1, max_length=72)
    organization_id: str = Field(min_length=1, max_length=72)
    workspace_id: str = Field(min_length=1, max_length=72)
    verbs: frozenset[Verb]


def allowed_verbs(principal: Principal, resource: Scoped) -> frozenset[Verb]:
    confinement = principal.confinement
    if confinement is not None and (
        confinement.organization_id != resource.organization_id
        or resource.workspace_id not in {None, confinement.workspace_id}
    ):
        return frozenset()
    verbs: set[Verb] = set()
    for grant in principal.grants:
        if grant.organization_id != resource.organization_id:
            continue
        if resource.workspace_id is None:
            verbs.update(grant.verbs if grant.workspace_id is None else grant.verbs & ORGANIZATION_READ)
        elif grant.workspace_id in {None, resource.workspace_id}:
            verbs.update(grant.verbs)
    if confinement is not None and resource.workspace_id is None:
        verbs.intersection_update(ORGANIZATION_READ)
    return frozenset(verbs)


def authorize(
    principal: Principal, resource: Scoped, verb: Verb, *, authority: ExecutionAuthority | None = None
) -> None:
    if authority is not None and (
        authority.principal_id != principal.id
        or authority.organization_id != resource.organization_id
        or resource.workspace_id != authority.workspace_id
        or verb not in authority.verbs
    ):
        raise ServiceError("forbidden", "Execution delegation does not cover this operation", {"verb": verb})
    if verb not in allowed_verbs(principal, resource):
        raise ServiceError("forbidden", "Principal cannot perform this operation", {"verb": verb})


def execution_authority(principal: Principal, scope: WorkspaceScope) -> ExecutionAuthority:
    authorize(principal, scope, "run")
    return ExecutionAuthority(
        principal_id=principal.id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        verbs=allowed_verbs(principal, scope),
    )
