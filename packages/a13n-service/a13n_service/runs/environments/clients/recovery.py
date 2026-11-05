"""Recheck failed client mounts only at the root's next natural model request."""

import asyncio
from dataclasses import replace
from typing import Any

from a13n_harness import AgentContext, EnvironmentMount
from a13n_harness.environment.providers import EnvironmentRuntime
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import CapabilityOrdering, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import ToolDefinition
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import Lease
from a13n_service.runs.environments.clients.observations import Incarnation, Observation, current
from a13n_service.runs.environments.clients.service import Attachment, validate_attachment
from a13n_service.runs.environments.execution import Source
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tool_executions.authority import prove


class Cycle(BaseModel):
    call_id: str
    failed_epoch: str | None = None
    failed_scope: str | None = None
    notified_epoch: str | None = None
    unknown_outcome: bool = False


class State(BaseModel):
    run_id: str
    mounts: dict[str, Cycle] = Field(default_factory=dict)
    observations: dict[str, Observation] = Field(default_factory=dict)


_NAMESPACE = "service.client_environment_recovery"


class Recovery(AbstractModelContextCapability):
    id = _NAMESPACE

    def __init__(
        self,
        runtime: Runtime,
        lease: Lease,
        environment: EnvironmentRuntime,
        mounts: dict[str, EnvironmentMount],
        sessions: dict[str, EnvdSessionRecovery],
    ):
        self.runtime, self.lease, self.environment = runtime, lease, environment
        self.sessions = sessions
        self.mounts = {
            name: (mount, mount.source)
            for name, mount in mounts.items()
            if isinstance(mount.source, Source) and mount.source.client is not None
        }
        self.state_lock = asyncio.Lock()
        # Healthy successes need no checkpoint entry. Keep their batch order until
        # a failure requires persisting the observations, or model projection consumes them.
        self.successes: dict[str, Observation] = {}

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        observation = Observation()
        token = current.set(observation)
        try:
            result = await handler(args)
            # Preparation may fail before the RPC observer runs. Preserve this
            # batch too; _record still requires connection-loss evidence before notifying.
            error = result.get("error") if isinstance(result, dict) and result.get("ok") is False else None
            details = error.get("details") if isinstance(error, dict) else None
            connection_failure = isinstance(error, dict) and (
                error.get("code") in {"environment_unavailable", "environment_unknown_outcome"}
                or (isinstance(details, dict) and details.get("reason") == "client_offline")
            )
            if observation.disconnected or observation.sessions_lost or observation.successes or connection_failure:
                async with self.state_lock:
                    state = await self._state(ctx)
                    if (
                        observation.disconnected
                        or observation.sessions_lost
                        or connection_failure
                        or state.mounts
                        or state.observations
                    ):
                        state.observations.update(self.successes)
                        self.successes.clear()
                        state.observations[call.tool_call_id] = observation
                        await ctx.deps.state.write(_NAMESPACE, state, version="1")
                    else:
                        self.successes[call.tool_call_id] = observation
            return result
        finally:
            current.reset(token)

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        async with self.state_lock:
            notices = await self._refresh(ctx, request)
        if not notices:
            return projection
        return ModelContextProjection(
            blocks=(
                *projection.blocks,
                ModelContextBlock(
                    source_id=_NAMESPACE,
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content="\n".join(notices),
                ),
            )
        )

    async def _state(self, ctx: RunContext[AgentContext]) -> State:
        state = await ctx.deps.state.read(_NAMESPACE, State, version="1")
        return state if state is not None and state.run_id == self.lease.run_id else State(run_id=self.lease.run_id)

    async def _refresh(self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest) -> list[str]:
        state = await self._state(ctx)
        original = state.model_dump()
        # A failed pre-model file discovery is not a tool result. Only canonical
        # failures actually returned to this model create a notification cycle.
        if ctx.messages and isinstance(ctx.messages[-1], ModelRequest):
            for part in ctx.messages[-1].parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id in request.tool_call_ids:
                    seen = self.successes.pop(part.tool_call_id, None)
                    await self._record(state, part, seen)
        notices = []
        for name, (mount, source) in self.mounts.items():
            pending = state.mounts.get(name)
            client = source.client
            assert client is not None
            if not client.retry_activation and (pending is None or pending.notified_epoch is not None):
                continue
            try:
                online = await self.runtime.client_connections.presence(source.environment_id)
                if online is None or online.descriptor.device_id != client.device_id:
                    continue
                if client.retry_activation and online.epoch == client.epoch:
                    continue
                if pending is not None and pending.failed_scope is None and online.epoch == pending.failed_epoch:
                    continue
                await prove(self.runtime, self.lease)
                await validate_attachment(
                    self.runtime,
                    Attachment(source.environment_id, online.descriptor.device_id, online.credential_version),
                )
            except (RedisError, SQLAlchemyError, ServiceError):
                # This observation alone never interrupts a run or widens access.
                continue
            if client.retry_activation:
                await self.environment.replace(name, mount)
                client.retry_activation = False
            elif (
                pending is not None
                and client.epoch is not None
                and (client.epoch != online.epoch or pending.failed_scope == client.scope_id)
            ):
                # The old execution may still be retiring. Its close must not erase
                # the new mount's hint or replay work against the new connection.
                recovery = self.sessions[name]
                if client.session is not None:
                    client.session.detach_host_binding()
                incarnation = Incarnation(client.device_id)
                replacement = replace(source, client=incarnation, session_recovery=recovery, activated=False)
                fresh = replace(
                    mount,
                    source=replacement,
                    observer=incarnation.observe,
                    operation_guard=replacement.operation_guard,
                    computer_guard=replacement.computer_guard,
                )
                await self.environment.replace(name, fresh)
                self.mounts[name] = fresh, replacement
            if pending is not None and pending.notified_epoch != online.epoch:
                pending.notified_epoch = online.epoch
                notice = (
                    f"Client mount {name} can prepare a new execution Session. Tool readiness has not been verified."
                    if pending.failed_scope is not None
                    else f"Client mount {name} has reconnected. Tool readiness has not been verified."
                )
                notices.append(
                    notice
                    + (
                        " The previous operation outcome is unknown. Inspect its result before retrying."
                        if pending.unknown_outcome
                        else " You may try it again."
                    )
                )
        if state.model_dump() != original:
            await ctx.deps.state.write(_NAMESPACE, state, version="1")
        return notices

    async def _record(self, state: State, part: ToolReturnPart, seen: Observation | None = None) -> None:
        seen = state.observations.pop(part.tool_call_id, seen)
        content = part.content
        losses: dict[str, str | None] = {}
        session_losses: dict[str, str] = {}
        unknown_outcome = False
        if isinstance(content, dict) and content.get("ok") is False:
            if seen is not None:
                losses.update(seen.disconnected)
                session_losses.update(seen.sessions_lost)
            error = content.get("error")
            details = error.get("details") if isinstance(error, dict) else None
            if isinstance(details, dict) and isinstance(error, dict):
                unknown_outcome = error.get("code") == "environment_unknown_outcome" or (
                    details.get("dispatch_stage") == "unknown"
                )
                name = details.get("mount")
                if isinstance(name, str) and name in self.mounts:
                    source = self.mounts[name][1]
                    client = source.client
                    assert client is not None
                    if details.get("reason") == "client_offline":
                        losses[name] = client.epoch
                    elif (
                        name not in losses
                        and client.epoch is not None
                        and error.get("code") in {"environment_unavailable", "environment_unknown_outcome"}
                    ):
                        # Closed local Sessions can fail readiness before issuing RPC.
                        # Require evidence of connection loss, not just a tool error.
                        try:
                            now = await self.runtime.client_connections.presence(source.environment_id)
                        except RedisError:
                            return
                        if now is None or now.epoch != client.epoch:
                            losses[name] = client.epoch
                        elif client.session_failed and client.scope_id is not None:
                            session_losses[name] = client.scope_id
        elif seen is not None:
            for name in seen.successes:
                state.mounts.pop(name, None)
        for name in session_losses:
            if name in self.mounts:
                client = self.mounts[name][1].client
                assert client is not None
                losses.setdefault(name, client.epoch)
        for name, epoch in losses.items():
            previous = state.mounts.get(name)
            if previous is None or previous.call_id != part.tool_call_id:
                state.mounts[name] = Cycle(
                    call_id=part.tool_call_id,
                    failed_epoch=epoch,
                    failed_scope=session_losses.get(name),
                    unknown_outcome=unknown_outcome
                    or (previous is not None and previous.notified_epoch is None and previous.unknown_outcome),
                )
