from types import MappingProxyType

import pytest
from a13n_harness import (
    AgentIdentityRef,
    IdentityError,
    SubagentIdentityPolicy,
    derive_child_identity,
)


def test_agent_identity_accepts_immutable_string_claims() -> None:
    identity = AgentIdentityRef(
        issuer="test",
        subject="workload",
        user_id="user-1",
        agent_id="agent-1",
        tenant_id="tenant-1",
    )

    assert identity.claims == {
        "agent_id": "agent-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
    }
    assert isinstance(identity.claims, MappingProxyType)
    assert identity.get_claim("user_id") == "user-1"
    assert identity.require_claim("agent_id") == "agent-1"
    with pytest.raises(TypeError):
        identity.claims["user_id"] = "changed"  # type: ignore[index]


def test_agent_identity_claim_order_does_not_change_identity() -> None:
    first = AgentIdentityRef(issuer="test", subject="workload", user_id="user-1", agent_id="agent-1")
    second = AgentIdentityRef(issuer="test", subject="workload", agent_id="agent-1", user_id="user-1")

    assert first == second
    assert hash(first) == hash(second)


@pytest.mark.parametrize(
    "arguments",
    [
        {"issuer": "", "subject": "workload"},
        {"issuer": "test", "subject": ""},
        {"issuer": "test", "subject": "workload", "user_id": ""},
        {"issuer": "test", "subject": "workload", "user_id": 1},
    ],
)
def test_agent_identity_rejects_invalid_fixed_values_and_claims(arguments: dict[str, object]) -> None:
    with pytest.raises(IdentityError) as error:
        AgentIdentityRef(**arguments)  # type: ignore[arg-type]

    assert error.value.code == "identity_invalid"


def test_agent_identity_require_claim_reports_missing_claim() -> None:
    identity = AgentIdentityRef(issuer="test", subject="workload")

    with pytest.raises(IdentityError) as error:
        identity.require_claim("user_id")

    assert error.value.code == "identity_claim_missing"
    assert error.value.details == {"claim": "user_id"}


def test_child_identity_inherits_workload_and_claims_but_replaces_agent_id_by_default() -> None:
    parent = AgentIdentityRef(
        issuer="test",
        subject="workload",
        user_id="user-1",
        agent_id="parent-agent",
        tenant_id="tenant-1",
    )

    child = derive_child_identity(parent, "child-agent")

    assert child.issuer == "test"
    assert child.subject == "workload"
    assert child.claims == {
        "agent_id": "child-agent",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
    }


def test_child_identity_can_inherit_agent_id() -> None:
    parent = AgentIdentityRef(
        issuer="test",
        subject="workload",
        user_id="user-1",
        agent_id="parent-agent",
    )

    child = derive_child_identity(
        parent,
        "child-agent",
        SubagentIdentityPolicy(inherit_agent_id=True),
    )

    assert child == parent
