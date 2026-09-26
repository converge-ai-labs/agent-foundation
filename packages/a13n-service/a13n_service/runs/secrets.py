"""Secrets for managed tools: a tool uses only the secrets its agent node declares, each revealed per call.

The Harness asks the invocation policy before every managed tool call, then the credential broker for each
audience the tool names. An audience is a secret requirement key of the node running the call: an inline
subagent runs under its own definition ID, its agent revision ID, so it never borrows its parent's secrets.
A value is read fresh when the call is authorized, lives only in that call's lease and is cleared when the call
ends; no value reaches logs, errors, events or Harness state.
"""

from collections.abc import Mapping, Sequence

from a13n_harness import AgentContext
from a13n_harness.tools import (
    CredentialLease,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolInvocationContext,
)
from a13n_harness.tools.metadata import HarnessToolMetadata

from a13n_service.resources.secrets.schemas import SecretRequirement
from a13n_service.resources.secrets.service import resolve_secrets
from a13n_service.runs.runtime import Runtime

# Requirements by the definition ID of the agent node declaring them: the run's revision and inline children's.
type Requirements = Mapping[str, Sequence[SecretRequirement]]


async def require_secrets(runtime: Runtime, workspace_id: str, principal_id: str, requirements: Requirements) -> None:
    """Fail before the run starts, with the missing key, when a declared secret is not set."""
    declared = {(item.key, item.scope): item for items in requirements.values() for item in items}
    await resolve_secrets(
        runtime.storage,
        runtime.keys,
        workspace_id=workspace_id,
        principal_id=principal_id,
        requirements=list(declared.values()),
    )


def secrets_policy(
    runtime: Runtime, workspace_id: str, principal_id: str, root_revision_id: str, requirements: Requirements
) -> InvocationPolicyCapability:
    """The run's invocation policy, for `RunBindings.capabilities`; inline children inherit it."""
    broker = _Broker(runtime, workspace_id, principal_id, root_revision_id, requirements)
    # No automatic redispatch, as without a policy: a repeated provider call could repeat its effect and cost.
    return InvocationPolicyCapability(evaluator=broker, credential_broker=broker, max_dispatch_retries=0)


class _Broker:
    def __init__(
        self, runtime: Runtime, workspace_id: str, principal_id: str, root_revision_id: str, requirements: Requirements
    ):
        self.runtime = runtime
        self.workspace_id, self.principal_id, self.root = workspace_id, principal_id, root_revision_id
        self.requirements = {node: {item.key: item for item in items} for node, items in requirements.items()}

    async def __call__(
        self, invocation: ToolInvocationContext, metadata: HarnessToolMetadata, *, context: AgentContext
    ) -> InvocationPolicyDecision:
        if set(metadata.credential_audiences) <= self._declared(context).keys():
            return InvocationPolicyDecision.allow()
        return InvocationPolicyDecision.deny("The tool needs a secret its agent does not declare.")

    async def acquire(
        self, audience: str, invocation: ToolInvocationContext, *, context: AgentContext
    ) -> CredentialLease:
        # The evaluator allowed only declared audiences; the Harness reports any failure here as unavailable.
        requirement = self._declared(context)[audience]
        values = await resolve_secrets(
            self.runtime.storage,
            self.runtime.keys,
            workspace_id=self.workspace_id,
            principal_id=self.principal_id,
            requirements=[requirement],
        )
        lease = CredentialLease(audience=audience, value=values[audience].get_secret_value())

        async def clear() -> None:
            lease.value = None

        lease.close_callback = clear
        return lease

    def _declared(self, context: AgentContext) -> Mapping[str, SecretRequirement]:
        """The requirements of the node running the call; an unknown node declares none."""
        if context.instance.parent_agent_instance_id is None:
            node = self.root
        else:
            # The Harness derives an inline child's identity with its definition ID as `agent_id`.
            node = context.identity.get_claim("agent_id")
        return self.requirements.get(node or "", {})
