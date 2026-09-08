"""Fresh, invocation-scoped Secret leases for managed Harness tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from a13n_harness import AgentContext
from a13n_harness.tools.metadata import HarnessToolMetadata
from a13n_harness.tools.policy import (
    CredentialLease,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolInvocationContext,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.execution_graph import inline_child_executions
from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_agent
from a13n_service.interactions.attempts import AttemptContext, read_attempt_lease
from a13n_service.interactions.domain import Run
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .agent_inputs import (
    AgentSecretSnapshot,
    graph_secret_requirements,
    require_secret,
    secret_unavailable,
    validate_secret_bindings,
)
from .crypto import SecretProtectionError, SecretProtector
from .domain import AgentSecretBinding


class AgentSecretRuntime:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], protector: SecretProtector, *, clock: Clock = utc_now
    ) -> None:
        self._sessions = sessions
        self._protector = protector
        self._clock = clock

    def bind(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        bindings: tuple[AgentSecretBinding, ...],
        current_attempt: Callable[[], AttemptContext],
    ) -> BoundAgentSecrets:
        return BoundAgentSecrets(self, run, workspace_id, config, bindings, current_attempt)


class BoundAgentSecrets:
    def __init__(
        self,
        runtime: AgentSecretRuntime,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        bindings: tuple[AgentSecretBinding, ...],
        current_attempt: Callable[[], AttemptContext],
    ) -> None:
        self._runtime = runtime
        self._run = run
        self._workspace_id = workspace_id
        self._current_attempt = current_attempt
        self._config = config
        self._bindings = {binding.key: binding for binding in bindings}
        validate_secret_bindings(bindings, graph_secret_requirements(config))
        self._children: dict[str, list[tuple[str, EffectiveAgentConfig]]] = {}
        for edge, child in inline_child_executions(config).values():
            identity = f"agent-config-{child.revision_content_digest[:24]}"
            self._children.setdefault(identity, []).append((edge.child_agent_id, child.effective_config))

    async def validate(self) -> None:
        async with short_session(self._runtime._sessions) as database:
            actor = await self._authorize(database, (self._run.agent_id,))
            for binding in self._bindings.values():
                await require_secret(database, actor=actor, binding=binding, accepting=False)

    def capability(self) -> InvocationPolicyCapability:
        return InvocationPolicyCapability(evaluator=self, credential_broker=self)

    async def __call__(
        self,
        invocation: ToolInvocationContext,
        metadata: HarnessToolMetadata,
        *,
        context: AgentContext,
    ) -> InvocationPolicyDecision:
        if not metadata.credential_audiences:
            return InvocationPolicyDecision.allow()
        agent_id, config = self._node(context)
        declared = {requirement.key for requirement in config.secret_requirements}
        if not set(metadata.credential_audiences) <= declared & self._bindings.keys():
            return InvocationPolicyDecision.deny("A required Agent Secret is unavailable.")
        async with short_session(self._runtime._sessions) as database:
            await self._authorize(database, agent_id)
        return InvocationPolicyDecision.allow()

    async def acquire(
        self, audience: str, invocation: ToolInvocationContext, *, context: AgentContext
    ) -> CredentialLease:
        agent_id, config = self._node(context)
        if audience not in {requirement.key for requirement in config.secret_requirements}:
            raise secret_unavailable()
        binding = self._bindings.get(audience)
        if binding is None:
            raise secret_unavailable()
        async with short_session(self._runtime._sessions) as database:
            actor = await self._authorize(database, agent_id)
            snapshot = AgentSecretSnapshot.from_record(
                await require_secret(database, actor=actor, binding=binding, accepting=False)
            )
        try:
            value = self._runtime._protector.decrypt(**asdict(snapshot))
        except SecretProtectionError as error:
            raise secret_unavailable() from error
        lease = CredentialLease(audience=audience, value=value)

        async def clear() -> None:
            lease.value = None

        lease.close_callback = clear
        return lease

    def _node(self, context: AgentContext) -> tuple[tuple[str, ...], EffectiveAgentConfig]:
        if context.instance.parent_agent_instance_id is None:
            return (self._run.agent_id,), self._config
        selected = self._children.get(context.identity.get_claim("agent_id") or "")
        if selected is None:
            raise secret_unavailable()
        # Identical frozen definitions share a Harness identity. Require every
        # matching Agent to remain eligible rather than guessing its owner.
        if any(config != selected[0][1] for _, config in selected):
            raise secret_unavailable()
        return tuple(agent_id for agent_id, _ in selected), selected[0][1]

    async def _authorize(self, database: AsyncSession, agent_ids: tuple[str, ...]) -> AuthenticatedActor:
        run, _, _ = await read_attempt_lease(database, self._current_attempt(), self._runtime._clock())
        if (
            run.id != self._run.id
            or run.organization_id != self._run.organization_id
            or run.authority_principal_id != self._run.authority_principal.principal_id
            or run.authority_principal_type != self._run.authority_principal.principal_type.value
        ):
            raise secret_unavailable()
        actor = AuthenticatedActor(
            principal=self._run.authority_principal,
            auth_method="internal",
            credential_id="attempt-secrets",
            boundary_workspace_id=self._workspace_id,
        )
        for selected in {self._run.agent_id, *agent_ids}:
            await authorize_agent(
                database,
                actor=actor,
                workspace_id=self._workspace_id,
                agent_id=selected,
                action=WorkspaceAction.agent_invoke,
            )
        return actor
