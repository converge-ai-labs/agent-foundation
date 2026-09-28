"""Scope truth table and durable authority ceilings shared by every caller."""

from dataclasses import replace

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.tenancy.access import Access, RoleGrant
from a13n_service.tenancy.authenticate import LocalAuthenticator
from a13n_service.tenancy.authorize import (
    BUILT_IN_ROLES,
    ExecutionAuthority,
    Grant,
    Principal,
    Scope,
    WorkspaceScope,
    allowed_verbs,
    authorize,
    execution_authority,
)

ADMIN, RUNNER, BUILDER = BUILT_IN_ROLES["admin"], BUILT_IN_ROLES["runner"], BUILT_IN_ROLES["builder"]


@pytest.mark.parametrize(
    ("grant_scope", "resource", "expected"),
    [
        (None, Scope("org_a", "ws_a"), {"read", "run", "write", "admin"}),
        ("ws_a", Scope("org_a", "ws_a"), {"read", "run", "write", "admin"}),
        ("ws_b", Scope("org_a", "ws_a"), set()),
        (None, Scope("org_a"), {"read", "run", "write", "admin"}),
        ("ws_a", Scope("org_a"), {"read"}),
        (None, Scope("org_b", "ws_b"), set()),
    ],
)
def test_grant_scope_rule(grant_scope, resource, expected):
    principal = Principal("usr_a", "user", (Grant("org_a", grant_scope, ADMIN),))
    assert allowed_verbs(principal, resource) == expected


@pytest.mark.parametrize(
    "role,expected", [("viewer", {"read"}), ("runner", {"read", "run"}), ("builder", {"read", "run", "write"})]
)
def test_roles_union_without_changing_scope(role, expected):
    grants = (Grant("org_a", "ws_a", BUILT_IN_ROLES[role]), Grant("org_b", None, ADMIN))
    principal = Principal("usr_a", "user", grants)
    assert allowed_verbs(principal, Scope("org_a", "ws_a")) == expected
    assert allowed_verbs(principal, Scope("org_a")) == {"read"}


def test_workspace_key_never_inherits_cross_workspace_or_organization_authority():
    principal = Principal(
        "usr_a", "user", (Grant("org_a", None, ADMIN), Grant("org_b", None, ADMIN)), WorkspaceScope("org_a", "ws_a")
    )
    assert allowed_verbs(principal, Scope("org_a", "ws_a")) == {"read", "run", "write", "admin"}
    assert allowed_verbs(principal, Scope("org_a", "ws_b")) == set()
    assert allowed_verbs(principal, Scope("org_b", "ws_a")) == set()
    assert allowed_verbs(principal, Scope("org_a")) == {"read"}
    with pytest.raises(ServiceError, match="cannot perform"):
        authorize(principal, Scope("org_a"), "run")


def test_role_vocabulary_is_validated_and_unknown_roles_fail_closed():
    access = Access(LocalAuthenticator(), BUILT_IN_ROLES)
    assert access.resolve(RoleGrant("org_a", "ws_a", "runner")) == Grant("org_a", "ws_a", RUNNER)
    with pytest.raises(ServiceError, match="unknown role"):
        access.resolve(RoleGrant("org_a", "ws_a", "removed_role"))
    for invalid in ({"Bad Name": frozenset({"read"})}, {"empty": frozenset()}, {"owner": frozenset({"own"})}):
        with pytest.raises(ValueError, match="Invalid role"):
            Access(LocalAuthenticator(), invalid)  # type: ignore[arg-type]


def test_removed_grants_stop_accepted_authority():
    scope = WorkspaceScope("org_a", "ws_a")
    principal = Principal("usr_a", "user", (Grant("org_a", None, BUILDER),))
    authority = execution_authority(principal, scope)
    with pytest.raises(ServiceError):
        authorize(replace(principal, grants=()), scope, "run", authority=authority)


def test_persisted_ceiling_cannot_widen_when_current_grants_increase():
    scope = WorkspaceScope("org_a", "ws_a")
    principal = Principal("usr_a", "user", (Grant("org_a", "ws_a", RUNNER),))
    accepted = execution_authority(principal, scope)
    restored = ExecutionAuthority.model_validate_json(accepted.model_dump_json())
    upgraded = replace(principal, grants=(Grant("org_a", None, ADMIN),))
    authorize(upgraded, scope, "run", authority=restored)
    # The authority covers only its workspace, never the organization scope.
    for target, verb in [(scope, "write"), (Scope("org_a", "ws_b"), "run"), (Scope("org_a"), "read")]:
        with pytest.raises(ServiceError, match="delegation"):
            authorize(upgraded, target, verb, authority=restored)
    with pytest.raises(ServiceError, match="delegation"):
        authorize(replace(upgraded, id="usr_b"), scope, "run", authority=restored)
