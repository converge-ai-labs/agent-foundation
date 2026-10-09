from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from a13n_harness import (
    HarnessBuilder,
    HarnessEvent,
    RunBindings,
)
from a13n_harness.environment import EnvironmentError
from a13n_harness.tools import (
    CanonicalResource,
    CredentialLease,
    HarnessTool,
    HarnessToolMetadata,
    InvocationGrantRef,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ManagedToolProviderError,
    ToolOutputPolicy,
    current_invocation_scope,
)
from a13n_harness.tools._output import _apply_result_policy
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


def _model(tool_name: str, args: dict[str, Any]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {0: DeltaToolCall(name=tool_name, json_args=json.dumps(args), tool_call_id="call-1")}
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


def _metadata(
    *,
    idempotency: str = "read_only",
    output_policy: ToolOutputPolicy | None = None,
    resolver=None,
    audiences: tuple[str, ...] = (),
) -> HarnessToolMetadata:
    return HarnessToolMetadata(
        tool_id="managed.tool",
        effects=frozenset({"read"}) if idempotency == "read_only" else frozenset({"write"}),
        credential_audiences=audiences,
        idempotency=idempotency,
        output_policy=output_policy or ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
        resource_resolver=resolver,
    )


@dataclass
class _Allow:
    seen: list[Any]

    async def __call__(self, invocation, metadata, *, context):
        del metadata, context
        self.seen.append(invocation)
        return InvocationPolicyDecision.allow()


async def _run(tool, policy: InvocationPolicyCapability):
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(tool.name, {"value": "3"} if "value" in tool.function_schema.json_schema["properties"] else {}),
        capabilities=(Capability(tools=[tool], id="test-tools"),),
    )
    return await executable.run("go", bindings=RunBindings.embedded(capabilities=(policy,)))


async def test_failed_resolver_logs_suppressed_root_without_leaking_tool_content(caplog):
    from a13n_environment.e2b.errors import sdk_errors

    async def resolve(arguments, *, context):
        with sdk_errors():
            try:
                raise ConnectionError("secret provider response")
            except ConnectionError:
                raise RuntimeError("secret tool payload") from None

    def read(value: int) -> int:
        raise AssertionError("resource failure must prevent dispatch")

    result = await _run(
        HarnessTool(read, harness_metadata=_metadata(resolver=resolve)),
        InvocationPolicyCapability(evaluator=_Allow([])),
    )
    assert result.status == "completed"
    record = next(record for record in caplog.records if record.msg == "managed_tool_resource_resolution_failed")
    assert record.run_id and record.tool_call_id == "call-1" and record.tool_name == "read"
    assert [item["type"] for item in record.exception_chain] == [
        "a13n_environment.errors.EnvironmentProviderError",
        "builtins.RuntimeError",
        "builtins.ConnectionError",
    ]
    assert "secret" not in json.dumps(record.exception_chain)
    assert record.exc_info is None


async def test_resource_resolution_precedes_policy_and_uses_typed_arguments() -> None:
    order: list[Any] = []

    async def resolve(arguments, *, context):
        del context
        order.append(("resolver", arguments["value"], type(arguments["value"])))
        return (CanonicalResource(namespace="env", kind="file", identifier=f"/{arguments['value']}"),)

    @dataclass
    class Policy:
        async def __call__(self, invocation, metadata, *, context):
            del metadata, context
            order.append(("policy", invocation.resources[0].identifier))
            return InvocationPolicyDecision.allow()

    def read(value: int) -> int:
        order.append(("tool", value))
        return value

    result = await _run(
        HarnessTool(read, harness_metadata=_metadata(resolver=resolve)),
        InvocationPolicyCapability(evaluator=Policy()),
    )

    assert result.status == "completed"
    assert order == [("resolver", 3, int), ("policy", "/3"), ("tool", 3)]


@pytest.mark.parametrize("known", [True, False])
async def test_resource_resolution_failures_are_safe_and_never_dispatch(known: bool) -> None:
    seen: list[Any] = []
    executed: list[bool] = []

    async def resolve(arguments, *, context):
        del arguments, context
        if known:
            raise EnvironmentError(
                "private provider diagnostic",
                code="environment_request_invalid",
                details={
                    "field": "path",
                    "reason": "invalid_path",
                    "hint": "Use an absolute mounted path.",
                    "private": "must not appear",
                },
                retry_hint="request_change",
            )
        raise RuntimeError("private provider diagnostic")

    def read() -> str:
        executed.append(True)
        return "unexpected"

    tool = HarnessTool(read, harness_metadata=_metadata(resolver=resolve))
    result = await _run(tool, InvocationPolicyCapability(evaluator=_Allow(seen)))
    assert result.status == "completed"
    assert seen == executed == []
    returns = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 1
    if known:
        assert returns[0].content == {
            "ok": False,
            "error": {
                "code": "environment_request_invalid",
                "message": "Environment operation input is invalid.",
                "retry_hint": "request_change",
                "details": {"field": "path", "reason": "invalid_path", "hint": "Use an absolute mounted path."},
            },
        }
    else:
        assert "Managed tool resources could not be resolved" in str(returns[0].content)
    assert "private" not in str(returns[0].content)


async def test_resource_resolver_cannot_mutate_digested_dispatch_arguments() -> None:
    executed: list[int] = []
    seen: list[Any] = []

    async def resolve(arguments, *, context):
        del context
        arguments["payload"]["value"] = 99
        return ()

    def inspect_payload(payload: dict[str, int]) -> int:
        executed.append(payload["value"])
        return payload["value"]

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model("inspect_payload", {"payload": {"value": 3}}),
        capabilities=(
            Capability(
                tools=[HarnessTool(inspect_payload, harness_metadata=_metadata(resolver=resolve))],
                id="test-tools",
            ),
        ),
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_Allow(seen)),)),
    )

    assert result.status == "completed"
    assert executed == [3]
    assert seen[0].normalized_arguments == {"payload": {"value": 3}}


async def test_credential_and_grant_are_task_local_and_leases_close() -> None:
    closed: list[str] = []

    @dataclass
    class Credentials:
        async def acquire(self, audience, invocation, *, context):
            del invocation, context

            async def close() -> None:
                closed.append(audience)

            return CredentialLease(audience=audience, value=f"credential:{audience}", close_callback=close)

    @dataclass
    class Grants:
        async def issue(self, invocation, metadata, *, context):
            del metadata, context
            return InvocationGrantRef(
                grant_id="grant-1",
                audience="provider",
                claims_digest=invocation.arguments_digest,
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
            )

    def inspect_scope() -> dict[str, Any]:
        scope = current_invocation_scope()
        return {
            "credential": scope.credentials["storage"],
            "grant": scope.grant.grant_id if scope.grant is not None else None,
        }

    policy = _Allow([])
    result = await _run(
        HarnessTool(inspect_scope, harness_metadata=_metadata(audiences=("storage",))),
        InvocationPolicyCapability(
            evaluator=policy,
            credential_broker=Credentials(),
            grant_broker=Grants(),
        ),
    )

    assert result.status == "completed"
    assert closed == ["storage"]
    with pytest.raises(RuntimeError, match="No managed tool invocation"):
        current_invocation_scope()
    serialized = result.all_messages_json() if hasattr(result, "all_messages_json") else repr(result.all_messages())
    assert "credential:storage" not in serialized


@pytest.mark.parametrize(
    ("idempotency", "expected_calls"),
    [("read_only", 2), ("provider_key", 2), ("none", 1)],
)
async def test_retry_requires_typed_failure_and_replay_safe_semantics(
    idempotency: str,
    expected_calls: int,
) -> None:
    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ManagedToolProviderError(retryable=True, outcome_known=True)
        return "ok"

    result = await _run(
        HarnessTool(flaky, harness_metadata=_metadata(idempotency=idempotency)),
        InvocationPolicyCapability(evaluator=_Allow([]), max_dispatch_retries=1),
    )

    assert calls == expected_calls
    assert result.status == "completed"


async def test_unknown_provider_outcome_is_safe_and_observable() -> None:
    def uncertain() -> str:
        raise ManagedToolProviderError(outcome_known=False)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model("uncertain", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(uncertain, harness_metadata=_metadata(idempotency="none"))],
                id="test-tools",
            ),
        ),
    )
    async with executable.stream(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_Allow([])),)),
    ) as stream:
        items = [item async for item in stream]

    phases = [
        item.event.payload["phase"]
        for item in items
        if isinstance(item, HarnessEvent) and hasattr(item.event, "kind") and item.event.kind == "invocation"
    ]
    assert "unknown_outcome" in phases
    assert items[-1].result.status == "completed"


async def test_managed_dispatch_cancellation_releases_credentials_and_preserves_cancellation() -> None:
    started = asyncio.Event()
    closed = asyncio.Event()

    @dataclass
    class Credentials:
        async def acquire(self, audience, invocation, *, context):
            del invocation, context

            async def close() -> None:
                closed.set()

            return CredentialLease(audience=audience, value=object(), close_callback=close)

    async def blocking() -> str:
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model("blocking", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(blocking, harness_metadata=_metadata(audiences=("storage",)))],
                id="test-tools",
            ),
        ),
    )

    async with executable.stream(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Allow([]), credential_broker=Credentials()),)
        ),
    ) as stream:

        async def consume():
            return [item async for item in stream]

        consumer = asyncio.create_task(consume())
        await started.wait()
        stream.cancel()
        items = await asyncio.wait_for(consumer, timeout=2)

    assert items[-1].result.status == "cancelled"
    assert closed.is_set()


async def test_result_redaction_and_unavailable_spill_fallback_are_bounded() -> None:
    def produce() -> dict[str, str]:
        return {"token": "top-secret", "content": "x" * 2000, "hint": "continue"}

    result = await _run(
        HarnessTool(
            produce,
            harness_metadata=_metadata(
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=512,
                    max_output_bytes=1024,
                    overflow="spill",
                    redact=True,
                )
            ),
        ),
        InvocationPolicyCapability(evaluator=_Allow([])),
    )

    returns = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    content = returns[-1].content
    assert isinstance(content, dict)
    assert content["truncated"] is True
    assert content["output_file_path"] is None
    assert content["output_bytes"] > 1024
    assert isinstance(content["result"], dict)
    assert content["result"]["hint"] == "continue"
    assert len(json.dumps(content).encode()) <= 512
    assert "top-secret" not in repr(content)


async def test_managed_results_reject_non_native_values_before_json_projection() -> None:
    with pytest.raises(ToolFailed, match="invalid result"):
        await _apply_result_policy(tuple(range(10_000)), _metadata().output_policy)
